import logging
from enum import Enum
from typing import Optional

from sharpy.interfaces import IGatherPointSolver, IZoneManager, IEnemyUnitsManager, IGameAnalyzer
from sharpy.managers.extensions import GameAnalyzer
from sharpy.plans.acts import ActBase
from sharpy.managers.extensions.game_states.advantage import (
    at_least_small_disadvantage,
    at_least_small_advantage,
    at_least_clear_advantage,
    at_least_clear_disadvantage,
)
from sharpy.general.zone import Zone
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from sharpy.managers.core.roles import UnitTask
from sharpy.combat import MoveType
from sharpy.general.extended_power import ExtendedPower
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sharpy.managers.core import *
    from sharpy.knowledges import Knowledge

# vibecraft: module-level logger 用于玩家覆盖的 intent 变化追踪
logger = logging.getLogger(__name__)

ENEMY_TOTAL_POWER_MULTIPLIER = 1.2

RETREAT_TIME = 20

RETREAT_STOP_DISTANCE = 5
RETREAT_STOP_DISTANCE_SQUARED = RETREAT_STOP_DISTANCE * RETREAT_STOP_DISTANCE


class AttackStatus(Enum):
    NotActive = 0
    GatheringForAttack = 1  # Not in use yet
    Attacking = 2
    MovingToExpansion = 3  # NYI, moving to hold enemy expansion
    ProtectingExpansion = 4  # NYI, holding enemy expansion and preventing enemy expansions
    Retreat = 10  # Prefers to escape without fighting
    Withdraw = 11  # Fights any enemies while escaping


class PlanZoneAttack(ActBase):
    gather_point_solver: IGatherPointSolver
    zone_manager: IZoneManager
    enemy_units_manager: IEnemyUnitsManager
    game_analyzer: Optional[IGameAnalyzer]
    pather: "PathingManager"

    DISTANCE_TO_INCLUDE = 18
    DISTANCE2_TO_INCLUDE = 18 * 18
    RETREAT_POWER_PERCENTAGE = 0.8

    def __init__(self, start_attack_power: float = 20):
        assert isinstance(start_attack_power, float) or isinstance(start_attack_power, int)
        super().__init__()
        self.retreat_multiplier = PlanZoneAttack.RETREAT_POWER_PERCENTAGE
        self.attack_retreat_started: Optional[float] = None

        self.start_attack_power = start_attack_power
        self.attack_on_advantage = True
        self.status = AttackStatus.NotActive
        # vibecraft: 玩家覆盖支持(force_attack 跳过 power 比较;_logged_intent 调试)
        self.force_attack: bool = False
        self._logged_intent: Optional[str] = "__sentinel__"

    async def start(self, knowledge: "Knowledge"):
        await super().start(knowledge)
        self.unit_values = knowledge.unit_values
        self.pather = self.knowledge.pathing_manager
        self.game_analyzer = self.knowledge.get_manager(IGameAnalyzer)
        if self.game_analyzer is None:
            self.print(f"IGameAnalyzer not found, turning attack_on_advantage off.")
            self.attack_on_advantage = False
        self.gather_point_solver = knowledge.get_required_manager(IGatherPointSolver)
        self.zone_manager = knowledge.get_required_manager(IZoneManager)
        self.enemy_units_manager = knowledge.get_required_manager(IEnemyUnitsManager)

    async def execute(self) -> bool:
        target = self._get_target()

        if target is None:
            # Enemy known bases destroyed.
            self.status = AttackStatus.NotActive
            return True

        unit: Unit
        if self.status == AttackStatus.Attacking:
            self.handle_attack(target)

        elif self.attack_retreat_started is not None:
            # vibecraft: 玩家从 retreat 切回 attack 时，reset retreat 状态让下 tick 走 attack 分支
            # (否则 attack_retreat_started 保持，execute() 永远在 retreat 分支循环，
            #  最多等 RETREAT_TIME=20s 才靠自然到期 — Issue 2 bug)
            vbc_intent = getattr(getattr(self.knowledge, "vibecraft", None), "combat_intent_override", None)
            if vbc_intent == "attack":
                logger.warning(  # vibecraft: warning 让 stdout 看到(loguru root WARNING)
                    "PlanZoneAttack retreat→attack: resetting retreat state (status=%s, started=%s)",
                    self.status, self.attack_retreat_started,
                )
                self.status = AttackStatus.NotActive
                self.attack_retreat_started = None
                self.roles.attack_ended()
                return False  # let next tick re-evaluate via else 分支
            # vibecraft: intent=retreat 时 retreat target 写死 home(self.ai.start_location),
            # 不读 dynamic gather_point(避免 vibecraft 自定义 act 改 gather_point 偷换
            # retreat target — 见 docs/sharpy-patches.md ForwardRallyStalker 案例)
            if vbc_intent == "retreat":
                retreat_target = self.ai.start_location
            elif vbc_intent == "defend":
                # vibecraft: 2026-06-13 威胁感知撤退目标 — 优先级:
                #   1. 有敌军逼近己方 zone → 撤退到该 zone 迎击
                #   2. 无威胁 + 玩家指定点(hold_gather_point) → 退到该点
                #   3. 无威胁 + 无指定 → **最前沿基地**(距敌主基最近的己方 zone)
                #   依据(2026-06-17 用户):防守 = 敌人接近某基地优先守该基地;无敌则守最靠近敌方的
                #   己方基地。原 fallback 用 gather_point(natural rally)→ 主力守在 natural 而非最前沿,
                #   且与 PlanZoneGather 的 forward_defense_point 不一致。改成同款 _vbc_forward_defense_point。
                _threatened = self._vbc_defend_target()
                if _threatened is not None:
                    retreat_target = _threatened
                else:
                    _hp = getattr(getattr(self.knowledge, "vibecraft", None), "hold_gather_point", None)
                    retreat_target = _hp if _hp is not None else self._vbc_forward_defense_point()
            else:
                retreat_target = self.gather_point_solver.gather_point

            attacking_units = self.roles.attacking_units
            self.roles.refresh_tasks(attacking_units)

            for unit in attacking_units:
                pos: Point2 = unit.position
                # at_gather_point 距离判断也用 retreat_target(intent=retreat 时 = home)
                at_gather_point = pos.distance_to(retreat_target) < RETREAT_STOP_DISTANCE_SQUARED
                if at_gather_point:
                    # self.print(f"Unit {unit.type_id} {unit.tag} has reached gather point. Stopping retreat.")
                    self.roles.clear_task(unit)
                elif self.status == AttackStatus.Withdraw:
                    self.combat.add_unit(unit)
                else:
                    self.combat.add_unit(unit)

            self.combat.execute(retreat_target, MoveType.DefensiveRetreat)

            if self.attack_retreat_started + RETREAT_TIME < self.ai.time:
                # Stop retreat next turn
                self._stop_retreat()
        else:
            self.roles.attack_ended()
            attackers = Units([], self.ai)
            attacker_count = 0  # vibecraft: 普通计数,不依赖 Units.exists(测试里 Units=MagicMock 恒真)
            for unit in self.roles.free_units:
                if self.unit_values.should_attack(unit):
                    attackers.append(unit)
                    attacker_count += 1

            own_power = self.unit_values.calc_total_power(attackers)

            # vibecraft: 无可攻击的自由单位时一律不进攻。否则 intent=attack/all_in/
            # supply>190 等分支让 _should_attack 返 True → _start_attack(0 兵) → 下 tick
            # handle_attack "No attacking units" → retreat → 又 attack 的 1Hz 空转 flip-flop
            # (玩家把全军编队/claim 成 Reserved 后 free_units 空 → 这个空转把被 claim 单位
            #  也搅得抖动 + debug 线乱跳;2026-06-06 虚空"回家防守"后跳舞根因之一)。
            if attacker_count > 0 and self._should_attack(own_power):
                self._start_attack(own_power, attackers)

        return False  # Blocks!

    async def debug_actions(self):
        if self.status == AttackStatus.NotActive:
            return

        if self.status == AttackStatus.Retreat:
            attacking_status = moving_status = "retreating"
        elif self.status == AttackStatus.Withdraw:
            attacking_status = moving_status = "withdrawing"
        elif self.status == AttackStatus.Attacking:
            moving_status = "moving"
            attacking_status = "attacking"
        elif self.status == AttackStatus.ProtectingExpansion:
            moving_status = "moving"
            attacking_status = "preventing"
        else:
            attacking_status = moving_status = "unknown attack task"

        for unit in self.roles.units(UnitTask.Moving):
            self.client.debug_text_world(moving_status, unit.position3d)
        for unit in self.roles.units(UnitTask.Attacking):
            self.client.debug_text_world(attacking_status, unit.position3d)

    def handle_attack(self, target):
        already_attacking: Units = self.roles.units(UnitTask.Attacking)
        if not already_attacking.exists:
            self.print("No attacking units, starting retreat")
            # All attacking units have been destroyed.
            self._start_retreat(AttackStatus.Retreat)
            return True

        center = already_attacking.center
        front_runner = already_attacking.closest_to(target)

        for unit in already_attacking:
            # Only units in group are included to current combat force
            self.combat.add_unit(unit)

        self.roles.refresh_tasks(already_attacking)

        for unit in self.roles.free_units:
            if self.unit_values.should_attack(unit):
                if not self.roles.is_in_role(UnitTask.Attacking, unit) and (
                    unit.distance_to(center) > 20 or unit.distance_to(front_runner) > 20
                ):
                    self.roles.set_task(UnitTask.Moving, unit)
                    # Unit should start moving to target position.
                    self.combat.add_unit(unit)
                else:
                    self.roles.set_task(UnitTask.Attacking, unit)
                    already_attacking.append(unit)
                    # Unit should start moving to target position.
                    self.combat.add_unit(unit)

        # Execute
        self.combat.execute(target, MoveType.Assault)

        retreat = self._should_retreat(front_runner.position, already_attacking)

        if retreat != AttackStatus.NotActive:
            self._start_retreat(retreat)

    def _should_attack(self, power: ExtendedPower) -> bool:
        # vibecraft: 优先读 combat_intent_override / stance_override / force_attack
        _vbc_skip_force_check = False
        intent = getattr(getattr(self.knowledge, "vibecraft", None), "combat_intent_override", None)
        if intent == "attack":
            mode = getattr(getattr(self.knowledge, "vibecraft", None), "attack_mode_override", None)
            if mode != "probe":
                # mode="all_in" 或 None(plan 默认) → 直接 True
                return True
            # vibecraft: 2026-05-28 probe 聚团门 — 用户反馈"试探/侦查时部队先聚团再走"。
            # 部队散开时不允许 attack(让 PlanZoneGather 集结);15s 超时 bypass 防卡死。
            # Director 在玩家发 probe 时 set regroup_started_at=now。
            vbc = getattr(self.knowledge, "vibecraft", None)
            started = getattr(vbc, "regroup_started_at", None)
            if started is not None:
                try:
                    elapsed = float(self.ai.time) - float(started)
                except Exception:
                    elapsed = 999.0
                if elapsed < 15.0 and not self._vbc_is_regrouped(threshold=8.0):
                    return False  # 等聚团,让 PlanZoneGather 接管
            # mode="probe" → 试探性,走 sharpy power 判定(劣势撤退),跳过 force_attack
            _vbc_skip_force_check = True
        elif intent in ("defend", "hold", "retreat", "vision"):
            return False
        else:
            # intent is None or unknown: check stance_override
            stance = getattr(getattr(self.knowledge, "vibecraft", None), "stance_override", None)
            if stance in ("hold", "defend", "retreat"):
                return False
        # plan 自声明 all-in:跳过 sharpy 父类的 enemy_total_power vs my power 比较。
        # 玩家 intent / stance 已在上面 check,优先级 > force_attack。
        if not _vbc_skip_force_check and getattr(self, "force_attack", False):
            return True

        if self.attack_on_advantage and self.ai.supply_used < 190:
            if (
                self.game_analyzer.our_army_predict in at_least_clear_advantage
                and self.game_analyzer.our_income_advantage in at_least_small_disadvantage
            ) or (
                self.game_analyzer.our_army_predict in at_least_small_advantage
                and self.game_analyzer.our_income_advantage in at_least_clear_disadvantage
            ):
                # Our army is bigger but economy is weaker, attack!
                return True

            if (
                self.game_analyzer.our_army_predict in at_least_small_disadvantage
                and self.game_analyzer.our_income_advantage in at_least_clear_advantage
            ) or (
                self.game_analyzer.our_army_predict in at_least_clear_disadvantage
                and self.game_analyzer.our_income_advantage in at_least_small_advantage
            ):
                # Our army is smaller but economy is better, focus on defence!
                return False

        enemy_total_power: ExtendedPower = self.enemy_units_manager.enemy_total_power
        enemy_total_power.multiply(ENEMY_TOTAL_POWER_MULTIPLIER)
        multiplier = ENEMY_TOTAL_POWER_MULTIPLIER

        zone_count = 0
        for zone in self.zone_manager.expansion_zones:  # type: Zone
            if zone.is_enemys:
                zone_count += 1

        enemy_main: Zone = self.zone_manager.expansion_zones[-1]
        enemy_natural: Zone = self.zone_manager.expansion_zones[-2]

        if zone_count == 1 and enemy_main.is_enemys:
            # We should seriously consider whether we want to crash and burn against a one base defense
            enemy_total_power.add_units(enemy_main.enemy_static_defenses)
            # multiplier *= 2

        elif zone_count == 2 and enemy_natural.is_enemys:
            enemy_total_power.add_units(enemy_natural.enemy_static_defenses)

            # if (self.knowledge.enemy_race == Race.Terran
            #         and self.knowledge.enemy_units_manager.unit_count(UnitTypeId.SIEGETANK) > 1):
            #     multiplier = 1.6

        enemy_total_power.power = max(self.start_attack_power, enemy_total_power.power)

        if power.is_enough_for(enemy_total_power, 1 / multiplier):
            self.print(
                f"Power {power.power:.2f} is larger than required attack power {enemy_total_power.power:.2f} -> attack!"
            )
            return True
        if self.ai.supply_used > 190:
            self.print(f"Supply is {self.ai.supply_used} -> attack!")
            return True
        return False

    # vibecraft: 2026-06-17 撤退滞回阈值(游戏秒)。非 probe 实攻时,撤退条件需**持续**这么久
    # 才真退,防 bio 大军接敌瞬时散开导致的进攻/撤退振荡。经验值,可用 override_acceptance
    # attack case 标定。详见 _should_retreat。
    RETREAT_HYSTERESIS_S: float = 2.5

    def _start_attack(self, power: ExtendedPower, attackers: Units):
        self.roles.set_tasks(UnitTask.Attacking, attackers)
        self.status = AttackStatus.Attacking
        # vibecraft: 2026-06-17 每个进攻 episode 从干净状态计撤退滞回,防上个 episode 的 stale
        # 时间戳让重接敌秒退、旁路滞回(独立评审必改项①)。
        self._retreat_pending_since = None
        self.print(f"Attack started at {power.power:.2f} power.")

    def _should_retreat(self, fight_center: Point2, already_attacking: Units) -> AttackStatus:
        # vibecraft: 玩家显式 retreat/defend/hold intent → 强制 Retreat,优先级高于 force_attack
        intent = getattr(getattr(self.knowledge, "vibecraft", None), "combat_intent_override", None)
        # vibecraft: intent 变化时 log 一次(调试用,确认 facade 写入是否生效)
        # warning level 让 stdout 看到(loguru root level WARNING)
        last_logged = getattr(self, "_logged_intent", "__sentinel__")
        if intent != last_logged:
            logger.warning(
                "PlanZoneAttack intent change: %s → %s (status=%s,force_attack=%s)",
                last_logged, intent, getattr(self, "status", "?"),
                getattr(self, "force_attack", False),
            )
            self._logged_intent = intent
        if intent in ("retreat", "defend", "hold"):
            logger.warning("PlanZoneAttack _should_retreat → Retreat (intent=%s)", intent)
            return AttackStatus.Retreat
        # vibecraft: 2026-05-28 闪追风筝 — BlinkKiteRetreatAct 检测前线 blink CD 都没好
        # + 平均护盾低 → set kite_retreat=True,这里触发战术撤退拖 CD。
        if getattr(getattr(self.knowledge, "vibecraft", None), "kite_retreat", False):
            logger.warning("PlanZoneAttack _should_retreat → Retreat (kite_retreat=True 拖 blink CD)")
            return AttackStatus.Retreat
        # vibecraft: attack_mode_override probe/all_in 覆盖 plan 默认 force_attack
        mode = getattr(getattr(self.knowledge, "vibecraft", None), "attack_mode_override", None)
        if mode == "probe":
            pass  # fall through to sharpy default logic
        elif mode == "all_in":
            return AttackStatus.NotActive
        elif getattr(self, "force_attack", False):
            return AttackStatus.NotActive

        enemy_local_units: Units = self.ai.all_enemy_units.closer_than(PlanZoneAttack.DISTANCE_TO_INCLUDE, fight_center)

        if self.unit_values.enemy_worker_type is not None:
            enemy_local_units = enemy_local_units.exclude_type(self.unit_values.enemy_worker_type)

        # vibecraft: own_local_power 计入正在赶来的 Moving 掉队单位(慢速航母/母舰/坦克),
        # 不只数前排 already_attacking。混速大军里快单位(虚空 3.5 > 航母/母舰 2.62)冲在
        # 最前先接敌,handle_attack 把离群 >20 的标成 Moving(不算 Attacking),若 retreat
        # 判定只数前排 → 局部以少打多 → 触发撤退 → 慢速主力还没到 → 撤了又来恶性抖动
        # (玩家观感:航母/虚空/母舰严重脱节 + 不听强制进攻)。只计 fight_center 30 格内的
        # Moving(马上到的援军,不含满地图乱跑的)→ 大军原地顶住等主力到齐再打。
        own_power_units = already_attacking
        try:
            moving_near = self.roles.units(UnitTask.Moving).closer_than(30, fight_center)
            if moving_near.exists:
                extra = [u for u in moving_near if u.tag not in already_attacking.tags]
                if extra:
                    own_power_units = Units(list(already_attacking) + extra, self.ai)
        except Exception:
            own_power_units = already_attacking
        own_local_power = self.unit_values.calc_total_power(own_power_units)
        enemy_local_power = self.unit_values.calc_total_power(enemy_local_units)

        if self.attack_on_advantage and enemy_local_power.power < 2:
            if (
                self.game_analyzer.our_army_predict in at_least_clear_advantage
                and self.game_analyzer.our_income_advantage in at_least_small_disadvantage
            ) or (
                self.game_analyzer.our_army_predict in at_least_small_advantage
                and self.game_analyzer.our_income_advantage in at_least_clear_disadvantage
            ):
                # Our army is bigger but economy is weaker, attack!
                return AttackStatus.NotActive

            # if ((self.game_analyzer.our_army_predict in at_least_small_disadvantage
            #      and self.game_analyzer.our_income_advantage in at_least_clear_advantage)
            #         or (self.game_analyzer.our_army_predict in at_least_clear_disadvantage
            #             and self.game_analyzer.our_income_advantage in at_least_small_advantage)):
            #     # Our army is smaller but economy is better, focus on defence!
            #     self.print(f'Retreat started because of army {self.game_analyzer.our_army_predict.name}.'
            #                f' {own_local_power.power:.2f} own local power '
            #                f'against {enemy_local_power.power:.2f} enemy local power.')
            #     return AttackStatus.Withdraw

        # vibecraft: 2026-05-28 probe 撤退激进化 — 用户反馈"试探性进攻就是占便宜
        # 就占,占不到就撤"。sharpy 默认 retreat_multiplier=0.8(enemy ≥ own × 1.25
        # 才撤,等明显劣势)。probe 改 1.0(enemy ≥ own × 1.0 就撤,对等就跑)。
        # all_in 不走这判定(line 324-325 已 NotActive),不受影响。
        mode = getattr(getattr(self.knowledge, "vibecraft", None), "attack_mode_override", None)
        effective_retreat_mult = 1.0 if mode == "probe" else self.retreat_multiplier
        if enemy_local_power.is_enough_for(own_local_power, effective_retreat_mult):
            # vibecraft: 2026-06-17 撤退滞回 — bio 大军接敌散开会让 fight_center 局部兵力**瞬时**
            # 掉到阈值下,原本立刻 Retreat → 退 RETREAT_TIME → 兵力恢复 → 再 attack → 又散 → 振荡
            # (真局 32 attack/25 retreat,玩家观感"大部队+医疗艇原地拉扯无法前进")。改:非 probe
            # 实攻时,撤退条件需**持续 ≥ RETREAT_HYSTERESIS_S 游戏秒**才真退;瞬时掉(散开抖动)
            # 不退,大军原地顶住等队形。intent=retreat/defend/hold + kite_retreat 已在前面 early-return,
            # all_in/force_attack 已 NotActive,都不到这里。
            # probe(火力侦查"对等就撤")**豁免滞回**,保持立即退(语义 + test_probe_retreat_multiplier
            # _aggressive 单测,独立评审必改项②)。时间戳只在此 is_enough_for 为真分支内读 ai.time
            # (必改项③:避免无条件碰 ai.time 打穿用 MagicMock 的现有单测)。
            if mode == "probe":
                self._retreat_pending_since = None
                self.print(
                    f"Retreat started at {own_local_power.power:.2f} own local power "
                    f"against {enemy_local_power.power:.2f} enemy local power."
                )
                return AttackStatus.Retreat
            now = float(self.ai.time)
            if getattr(self, "_retreat_pending_since", None) is None:
                self._retreat_pending_since = now
            if now - self._retreat_pending_since >= self.RETREAT_HYSTERESIS_S:
                self.print(
                    f"Retreat started at {own_local_power.power:.2f} own local power "
                    f"against {enemy_local_power.power:.2f} enemy local power "
                    f"(held {now - self._retreat_pending_since:.1f}s >= hysteresis)."
                )
                return AttackStatus.Retreat
            # 劣势未持续够久(瞬时散开抖动)→ 本帧不退,顶住
            return AttackStatus.NotActive

        # 撤退条件不成立 → 清滞回计时
        self._retreat_pending_since = None
        return AttackStatus.NotActive

    def _vbc_forward_defense_point(self) -> Point2:
        # vibecraft: 2026-06-17 无威胁 defend 的默认守点 = 距敌方主基地(expansion_zones[-1])
        # 最近的己方 zone 中心(= 最前沿基地)。与 PlanZoneGather._vbc_forward_defense_point
        # 同款,让主力部队(本类 retreat_target)和 idle 单位(gather)在"无敌"时聚到同一最前沿点
        # ——之前本类 fallback 用 gather_point_solver.gather_point(natural rally),跟 gather 路径
        # 不一致、不符用户"无敌→守最靠近敌方的己方基地"。min 按距离确定性,无己方分矿兜底 start。
        try:
            zones = self.zone_manager.expansion_zones
            enemy_main = zones[-1].center_location
            our_zones = [z for z in zones if z.is_ours]
            if our_zones:
                fwd = min(our_zones, key=lambda z: z.center_location.distance_to(enemy_main))
                return fwd.center_location
        except Exception:
            pass
        return self.ai.start_location

    def _vbc_defend_target(self) -> Optional[Point2]:
        # vibecraft: 2026-06-13 defend intent 的撤退目标 — 威胁感知版。
        # 遍历己方 zone，返回 assaulting_enemy_power 最大的 zone center；
        # 无威胁 → None（回落到 hold_gather_point 或 gather_point）。
        # 滞回：旧 zone 仍有威胁时只有新 zone ≥ 1.5x 才切换，防边界抖动。
        # state 挂 self（惰性 getattr 初始化）；方法逻辑与
        # PlanZoneGather._vbc_threatened_zone 对称。
        try:
            zones = self.zone_manager.expansion_zones
            last_center = getattr(self, "_vbc_defend_threat_center", None)

            threatened = [
                (
                    getattr(getattr(z, "assaulting_enemy_power", None), "power", 0.0),
                    z.center_location,
                )
                for z in zones
                if getattr(z, "is_ours", False)
                and getattr(getattr(z, "assaulting_enemy_power", None), "power", 0.0) > 3.0  # vibecraft: 阈值滤掉散兵游勇
            ]

            if not threatened:
                self._vbc_defend_threat_center = None
                self._vbc_defend_threat_power = 0.0
                return None

            best_power, best_center = max(threatened, key=lambda t: t[0])

            if last_center is not None:
                current_last_power = next(
                    (p for p, c in threatened if c == last_center),
                    0.0,
                )
                if current_last_power > 0:
                    if best_center != last_center and best_power >= current_last_power * 1.5:
                        self._vbc_defend_threat_center = best_center
                        self._vbc_defend_threat_power = best_power
                        return best_center
                    else:
                        self._vbc_defend_threat_power = current_last_power
                        return last_center

            self._vbc_defend_threat_center = best_center
            self._vbc_defend_threat_power = best_power
            return best_center
        except Exception:
            return None

    def _vbc_is_regrouped(self, threshold: float = 8.0) -> bool:
        # vibecraft: 部队是否聚团 helper(probe 聚团门 / recon 用)。散开时 _should_attack
        # 返 False 让 PlanZoneGather 集结。
        # 2026-06-02 放宽(skytoss 混速空军永远散 >8 → probe 必卡满 15s):
        #   1. 排除超慢旗舰(母舰)——它永远跟不上,不该卡住整支大军的聚团门;
        #   2. 自适应阈值 eff = threshold + sqrt(n)——大军占地本就更大;
        #   3. 中位数质心(抗离群:远掉队单位不会把质心拽偏)+ 70% 在 eff 内即算聚团
        #      (用占比而非 max,不被个别掉队单位卡死)。
        try:
            free = self.roles.free_units
            if not free or len(free) < 2:
                return True  # 单位太少不算散开
            core = [
                u for u in free
                if getattr(getattr(u, "type_id", None), "name", "") != "MOTHERSHIP"
            ]
            if len(core) < 2:
                core = list(free)
            n = len(core)
            xs = sorted(u.position.x for u in core)
            ys = sorted(u.position.y for u in core)
            mid = n // 2
            if n % 2:
                cx, cy = xs[mid], ys[mid]
            else:
                cx, cy = (xs[mid - 1] + xs[mid]) / 2, (ys[mid - 1] + ys[mid]) / 2
            eff = threshold + n ** 0.5
            within = 0
            for u in core:
                dx = u.position.x - cx
                dy = u.position.y - cy
                if (dx * dx + dy * dy) ** 0.5 <= eff:
                    within += 1
            return within >= 0.7 * n
        except Exception:
            return True  # 异常默认聚团 ok,不卡 attack

    def _start_retreat(self, status: AttackStatus):
        self.status = status
        self.attack_retreat_started = self.ai.time

    def _stop_retreat(self):
        # vibecraft: 玩家显式 retreat intent 时不让 sharpy 内部 RETREAT_TIME=20s 自动 stop
        intent = getattr(getattr(self.knowledge, "vibecraft", None), "combat_intent_override", None)
        if intent == "retreat":
            return
        self.status = AttackStatus.NotActive
        self.attack_retreat_started = None
        self.roles.attack_ended()
        self.print("Retreat stopped.")

    def _get_target(self) -> Optional[Point2]:
        # vibecraft: attack_target_override 优先(L2 view / 手动指定目标)
        override = getattr(getattr(self.knowledge, "vibecraft", None), "attack_target_override", None)
        if override is not None:
            if isinstance(override, tuple) and len(override) == 2:
                return Point2(override)
            return override

        our_main = self.zone_manager.expansion_zones[0].center_location
        proxy_buildings = self.ai.enemy_structures.closer_than(70, our_main)

        if proxy_buildings.exists:
            return proxy_buildings.closest_to(our_main).position

        # Select expansion to attack.
        # Enemy main zone should the last element in expansion_zones.
        enemy_zones = list(filter(lambda z: z.is_enemys, self.zone_manager.expansion_zones))

        best_zone = None
        best_score = 100000
        start_position = self.gather_point_solver.gather_point
        if self.roles.attacking_units:
            start_position = self.roles.attacking_units.center

        for zone in enemy_zones:  # type: Zone
            not_like_points = zone.center_location.distance_to(start_position)
            not_like_points += zone.enemy_static_power.power * 5
            if not_like_points < best_score:
                best_zone = zone
                best_score = not_like_points

        if best_zone is not None:
            return best_zone.center_location

        if self.ai.enemy_structures.exists:
            return self.ai.enemy_structures.closest_to(our_main).position

        return None
