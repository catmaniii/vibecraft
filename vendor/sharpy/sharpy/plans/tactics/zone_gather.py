from typing import Optional, List

from sc2.bot_ai import BotAI
from sc2.data import Race
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId

from sharpy.combat import MoveType
from sharpy.interfaces import IGatherPointSolver, IBuildingSolver, IEnemyUnitsManager
from sharpy.plans.acts import ActBase
from sc2.position import Point2
from sc2.unit import Unit

from sharpy.managers.core.roles import UnitTask
from sharpy.knowledges import Knowledge
from sharpy.managers.core import UnitValue


class PlanZoneGather(ActBase):
    gather_point_solver: IGatherPointSolver
    building_solver: IBuildingSolver
    enemy_units_manager: IEnemyUnitsManager

    def __init__(self, set_gather_points: bool = True):
        super().__init__()
        self.gather_move_type = MoveType.Assault
        self.gather_set: List[int] = []
        self.blocker_tag: Optional[int] = None
        self.current_gather_point = Point2((0, 0))
        self.close_gates = True
        self.set_gather_points = set_gather_points

    @property
    def gather_point(self) -> Point2:
        return self.current_gather_point_solver.gather_point

    async def start(self, knowledge: Knowledge):
        await super().start(knowledge)
        self.building_solver = knowledge.get_required_manager(IBuildingSolver)
        self.enemy_units_manager = knowledge.get_required_manager(IEnemyUnitsManager)
        self.current_gather_point_solver = self.knowledge.get_manager(IGatherPointSolver)

        self.my_race = self.ai.race
        self.defender_types: list
        self.knowledge = knowledge
        self.unit_values: UnitValue = knowledge.unit_values
        self.base_ramp = self.zone_manager.expansion_zones[0].ramp
        self.close_gates = self.ai.enemy_race == Race.Zerg and self.ai.race != Race.Zerg

    def _vbc_forward_defense_point(self) -> Point2:
        # vibecraft: 2026-06-03 无目标 defend 的默认守点 = 离敌方主基地最近的己方
        # 分矿中心(前沿防守)。enemy main = expansion_zones[-1]。无己方分矿 / zone
        # 数据缺失 → start_location 兜底。
        try:
            zones = self.zone_manager.expansion_zones
            enemy_main = zones[-1].center_location
            our_zones = [z for z in zones if z.is_ours]
            if our_zones:
                fwd = min(
                    our_zones,
                    key=lambda z: z.center_location.distance_to(enemy_main),
                )
                return fwd.center_location
        except Exception:
            pass
        return self.ai.start_location

    def _vbc_threatened_zone(self) -> Optional[Point2]:
        # vibecraft: 2026-06-13 威胁感知守点 — 遍历己方 zone，返回
        # assaulting_enemy_power（danger_radius 范围敌军）最大的 zone center；
        # 无威胁 → None（defend 优先级回落到 hold_gather_point / forward point）。
        # 滞回：记上次选的威胁 zone center + power，只有旧 zone 已无敌
        # OR 新 zone 敌军强度 ≥ 旧 1.5x 时才切换，防聚团点在边界跳动。
        # state 挂 self（惰性 getattr 初始化，不需改 __init__）。
        try:
            zones = self.zone_manager.expansion_zones
            last_center = getattr(self, "_vbc_threat_zone_center", None)

            # 收集所有有威胁的己方 zone：(power, center_location)
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
                # 无威胁 → 清状态，返回 None
                self._vbc_threat_zone_center = None
                self._vbc_threat_zone_power = 0.0
                return None

            # 当前最大威胁 zone
            best_power, best_center = max(threatened, key=lambda t: t[0])

            if last_center is not None:
                # 查旧 zone 当前是否仍有威胁
                current_last_power = next(
                    (p for p, c in threatened if c == last_center),
                    0.0,
                )
                if current_last_power > 0:
                    # 旧 zone 仍有威胁 → 只有新 zone 强度 ≥ 1.5x 才切换
                    if best_center != last_center and best_power >= current_last_power * 1.5:
                        self._vbc_threat_zone_center = best_center
                        self._vbc_threat_zone_power = best_power
                        return best_center
                    else:
                        # 保持旧 zone，更新其最新 power
                        self._vbc_threat_zone_power = current_last_power
                        return last_center
                # 旧 zone 已无威胁 → 落到下面选最强

            self._vbc_threat_zone_center = best_center
            self._vbc_threat_zone_power = best_power
            return best_center
        except Exception:
            return None

    def should_hold_position(self, target_position: Point2) -> bool:
        close_enemies = self.ai.all_enemy_units.filter(lambda u: not u.is_flying and not u.is_structure)
        if close_enemies.exists:
            enemy_near = close_enemies.closest_distance_to(target_position) < 7
            if not enemy_near:
                return False

            attackers = self.roles.attacking_units
            if attackers:
                attacker_near = attackers.closest_distance_to(target_position) < 5
                return not attacker_near

            return True

        # No non-flying enemies around
        return False

    async def execute(self) -> bool:
        # vibecraft: 2026-05-27 玩家点全军撤退后,新追猎从 Gateway spawn 仍朝前
        # rally —— PlanZoneGather 把 Gateway 的 RALLY_BUILDING 设到
        # gather_point_solver.gather_point(natural / 前沿矿),不读 vibecraft intent。
        # intent in (retreat/defend) 时把 effective gather point 改 start_location
        # (主基地中心),让新单位 rally home 不前压。current_gather_point 变化时
        # gather_set.clear() 自动重新对所有 Gateway 设 rally。
        #
        # vibecraft: 2026-05-28 hold 分支 — 不回家,聚团到指定点(target_area 或
        # current army_center 锁住)。Director 算好聚团点写到
        # knowledge.vibecraft.hold_gather_point。读不到时 fallback start_location
        # (兼容老路径 / 防 None 错)。
        vbc = getattr(self.knowledge, "vibecraft", None)
        intent = getattr(vbc, "combat_intent_override", None)
        if intent == "hold":
            hold_pt = getattr(vbc, "hold_gather_point", None)
            effective_gp = hold_pt if hold_pt is not None else self.ai.start_location
        elif intent == "defend":
            # vibecraft: 2026-06-13 威胁感知守点 — 优先级:
            #   1. 有敌军逼近任何己方 zone(assaulting_enemy_power.power > 0,
            #      danger_radius 范围) → 迎击威胁最大的 zone
            #   2. 无威胁 + 玩家指定点(hold_gather_point) → 守该点
            #   3. 无威胁 + 无指定 → 前沿分矿(_vbc_forward_defense_point)
            #   依据:用户"防守 = 守所有己方基地，敌人到任何基地附近都要主动迎击"
            threatened_pt = self._vbc_threatened_zone()
            if threatened_pt is not None:
                effective_gp = threatened_pt
            else:
                hold_pt = getattr(vbc, "hold_gather_point", None)
                effective_gp = hold_pt if hold_pt is not None else self._vbc_forward_defense_point()
        elif intent == "retreat":
            effective_gp = self.ai.start_location
        else:
            effective_gp = self.gather_point

        unit: Unit
        if self.current_gather_point != effective_gp:
            self.gather_set.clear()
            self.current_gather_point = effective_gp

        unit: Unit
        if self.set_gather_points:
            for unit in self.cache.own([UnitTypeId.GATEWAY, UnitTypeId.ROBOTICSFACILITY]).tags_not_in(self.gather_set):
                # Rally point is set to prevent units from spawning on the wrong side of wall in
                pos: Point2 = unit.position
                pos = pos.towards(self.current_gather_point, 3)
                unit(AbilityId.RALLY_BUILDING, pos)
                self.gather_set.append(unit.tag)

        await self.manage_blocker()

        units = []
        units.extend(self.roles.idle)

        for unit in units:
            if self.unit_values.should_attack(unit):
                d2 = unit.position.distance_to(self.current_gather_point)
                if d2 > 6.5:
                    self.combat.add_unit(unit)

        self.combat.execute(self.current_gather_point, self.gather_move_type)
        return True  # Always non blocking

    def update_gates(self):
        if self.close_gates:
            lings = self.enemy_units_manager.unit_count(UnitTypeId.ZERGLING)
            if (
                self.enemy_units_manager.unit_count(UnitTypeId.ROACH) > lings
                or self.enemy_units_manager.unit_count(UnitTypeId.HYDRALISK) > lings
            ):
                self.close_gates = False

    async def manage_blocker(self):
        target_position = self.building_solver.zealot
        if target_position is not None:
            if self.blocker_tag is not None:
                unit = self.cache.by_tag(self.blocker_tag)
                if unit is not None and self.close_gates:
                    self.roles.set_task(UnitTask.Reserved, unit)

                    if unit.type_id in {UnitTypeId.STALKER, UnitTypeId.IMMORTAL} and self.cache.own(UnitTypeId.ZEALOT):
                        # Swap expensive blocker to a zaalot
                        new_blocker = self.get_blocker(self.ai, target_position)
                        if new_blocker is not None:
                            self.roles.clear_task(unit)
                            # Register tag
                            unit = new_blocker
                            self.blocker_tag = unit.tag
                            self.roles.set_task(UnitTask.Reserved, unit)

                    if self.should_hold_position(target_position):
                        if unit.distance_to(target_position) < 0.2:
                            unit.hold_position()
                        elif self.ai.enemy_units.exists and self.ai.enemy_units.closest_distance_to(unit) < 2:
                            unit.attack(target_position)
                        else:
                            unit.move(target_position)
                    else:
                        if self.natural_wall:
                            chill_position = target_position
                        else:
                            top_center = self.base_ramp.top_center
                            chill_position = target_position.towards(top_center, -1)

                        if unit.distance_to(chill_position) > 4:
                            unit.move(chill_position)
                        elif unit.orders and unit.orders[0].ability.id == AbilityId.HOLDPOSITION:
                            unit.stop()
                else:
                    await self.remove_gate_keeper()

            elif self.close_gates:
                # We need someone to block our wall.
                unit = self.get_blocker(self.ai, target_position)
                if unit is not None:
                    # Register tag
                    self.blocker_tag = unit.tag
                    self.roles.set_task(UnitTask.Reserved, unit)
                    unit.attack(target_position)

    @property
    def natural_wall(self) -> bool:
        natural = self.zone_manager.expansion_zones[1]
        return natural.is_ours and natural.our_wall()

    async def remove_gate_keeper(self):
        if self.blocker_tag is not None:
            unit = self.cache.by_tag(self.blocker_tag)
            if unit is not None:
                unit.attack(self.current_gather_point)
            self.roles.clear_task(self.blocker_tag)
            self.blocker_tag = None

        main_zone = self.zone_manager.expansion_zones[0]

        for unit in main_zone.known_enemy_units:  # type: Unit
            if unit.is_flying or self.unit_values.defense_value(unit.type_id) == 0 or self.unit_values.is_worker(unit):
                # Unit doesn't require removing gate keeper
                continue

            # Dangerous enemy near our base!
            if self.knowledge.ai.get_terrain_height(unit) < main_zone.height:
                # It hasn't gone up the ramp yet.
                continue

            if self.base_ramp.top_center.distance_to(unit.position) < 3.16:
                # Enemy is probaly stuck in the ramp entrance
                continue
            # Enemy is inside our base, remove gate keeper!
            return False

        return True

    def get_blocker(self, ai, position: Point2) -> Optional[Unit]:
        unit = self.get_blocker_type(UnitTypeId.ZEALOT, ai, position)
        if unit is None:
            unit = self.get_blocker_type(UnitTypeId.ADEPT, ai, position)
        # if unit is None:
        #     unit = self.get_blocker_type(sc2.UnitTypeId.STALKER, ai, position)
        if unit is None:
            unit = self.get_blocker_type(UnitTypeId.DARKTEMPLAR, ai, position)
        # if unit is None:
        #     unit = self.get_blocker_type(sc2.UnitTypeId.IMMORTAL, ai, position)
        return unit

    def get_blocker_type(self, unit_type: UnitTypeId, ai: BotAI, position: Point2) -> Optional[Unit]:
        units = self.roles.free_units(unit_type).closer_than(15, position)
        if units.exists:
            return units.closest_to(position)
        return None
