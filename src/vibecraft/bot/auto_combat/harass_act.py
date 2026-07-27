"""HarassWorkerLineAct: 通用骚扰微操 act —— 把骚扰单位送进对方矿区杀农民。

立项背景
========
各快攻 build 只「把兵造出来」是不够的 —— 造出兵 ≠ 骚扰到位。女妖 / 死神 /
恶火 / 飞龙造出来后若只是混在主力里一起 A 上去打对方军队,根本没碰到农民,
L3 骚扰验收(harass_damage:对方农民阵亡数)就过不了。

第一版用「attack-move 进场 + 血少全撤回家」,实测能杀农民但极不到位:撤一次
就是 20-30s 来回空跑,单位反复送、骚扰窗口全废。本版改成真正的 hit-and-run:
单位留在战区打了就退、退完再上,只在血量危急时才全撤保命。

适用范围
========
只管「能直接攻击地面农民」的骚扰单位:女妖(BANSHEE) / 死神(REAPER) /
恶火(HELLION) / 飞龙(MUTALISK)。不适用:凤凰(只能对空,要 Graviton Beam)、
隐刀(已有 VibeCraftMicroDarkTemplar)、棱镜空投(PrismWarpDropAct)、炮 rush。

行为(每 tick,逐单位)
======================
1. 标 Reserved —— 独占控制权,不让 PlanZoneGather / ZoneAttack 拽进主力。
2. wait_upgrade 设了且没好(如女妖隐形)→ 在家待命,不裸送。
3. HP 危急(< bail_hp_ratio)→ 全撤回家保命(带回血滞回防抖)。
4. 否则 hit-and-run:
   - 算出「能打到自己」的威胁(按 air/ground 匹配,农民不算威胁)。
   - 武器冷却中 且 有威胁逼近 → 往远离威胁方向退一步(留在战区,不回家)。
   - 武器好了 或 安全 → 打农民:射程内有农民直接打,否则压上去;
     完全没视野 → 直奔对方主基地。

接线
====
放进 plan 的 tactics SequentialList,排在 PlanZoneGather / *ZoneAttack 之前。
execute() 恒返回 True —— 它只下指令、不是 gate。
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterable
from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts import ActBase

logger = logging.getLogger(__name__)

# 敌方农民单位类型 —— 骚扰目标(也用于把农民排除出「威胁」)。
_WORKER_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE}
)

# 静态防御建筑 —— 算作威胁,骚扰单位要风筝开。
_STATIC_DEFENSE: frozenset[UnitTypeId] = frozenset(
    {
        UnitTypeId.PHOTONCANNON,
        UnitTypeId.SPINECRAWLER,
        UnitTypeId.SPORECRAWLER,
        UnitTypeId.MISSILETURRET,
        UnitTypeId.BUNKER,
        UnitTypeId.PLANETARYFORTRESS,
    }
)

# 到对方主基地的距离 <= 此值 → 视为「已到矿区」,切 hit-and-run;否则直推进场。
_ARRIVE_DIST: float = 22.0
# 威胁逼近到这个距离内 → 风筝后撤（2026-06-17 用户「撤退不够及时易死」：9→11 提前退）。
# 注:这是「取不到单位射程时」的 fallback 默认值,实际每单位按射程自适应(见 _pm_kite_params)。
_KITE_TRIGGER: float = 11.0
# 风筝后撤一步的距离(同上,fallback 默认值)。
_KITE_STEP: float = 5.0


def _pm_kite_params(unit: Any) -> tuple[float, float]:
    """按单位**实际射程**自适应 kite 触发距离 / 后撤步长。

    根因(推理图谱 F82):固定 _KITE_STEP=5 是给死神/女妖(射程 5-6)调的硬编码值。
    射程仅 3 的飞龙遇二矿女王(对空)后撤 5 格 → **退出自己射程 3** → 够不着农民、
    骚扰失效。这里按单位射程算:飞行单位取 air_range、地面单位取 ground_range,
    取不到 → fallback 原 _KITE_TRIGGER/_KITE_STEP(死神/女妖行为不变)。

    公式(原则:短射程单位后撤后不能超出自己射程):
      - trigger = max(射程*2, 6)  → 飞龙(3)~6、死神/女妖(5-6)~10-12,近原 11。
      - step    = max(射程*0.9, 1.5) → 飞龙(3)~2.7 (< 射程 3,不退出);
                  死神(5)~4.5、女妖走 air_range=0 fallback 5,均近原 5、行为不大变。
    """
    try:
        rng = float(unit.air_range if getattr(unit, "is_flying", False) else unit.ground_range)
    except Exception:
        return _KITE_TRIGGER, _KITE_STEP
    if rng <= 0:
        # 拿不到有效射程(如女妖 is_flying=True 但 air_range=0)→ fallback 原值,向后兼容。
        return _KITE_TRIGGER, _KITE_STEP
    return max(rng * 2.0, 6.0), max(rng * 0.9, 1.5)


# player_claim 模式默认血量阈值（偏死神友好：死神有回血，recover=0.99 能等满血再出击）。
# 局限：女妖/恶火无回血，撤退后血量不会自动回满 → 单位会长期待命不出击，直到受到治疗。
_PLAYER_BAIL_HP: float = 0.6
_PLAYER_RECOVER_HP: float = 0.99


# ------------------------------------------------------------------
# 模块级微操工具函数（供 director 的 player_claim 路径复用，逻辑与 HarassWorkerLineAct 相同）
# ------------------------------------------------------------------


def _pm_hp_ratio(unit: Any) -> float:
    try:
        mx = float(unit.health_max) + float(unit.shield_max)
        if mx <= 0:
            return 1.0
        return (float(unit.health) + float(unit.shield)) / mx
    except Exception:
        return 1.0


def _pm_is_threat_to(enemy: Any, unit: Any) -> bool:
    try:
        if unit.is_flying:
            return bool(enemy.can_attack_air)
        return bool(enemy.can_attack_ground)
    except Exception:
        return True


def _pm_nearest_threat(unit: Any, threats: list[Any]) -> tuple[Any | None, float]:
    best_pos: Any | None = None
    best_d = 1e9
    for e in threats:
        if not _pm_is_threat_to(e, unit):
            continue
        try:
            d = float(e.distance_to(unit))
        except Exception:
            continue
        if d < best_d:
            best_d = d
            best_pos = e.position
    return best_pos, best_d


def _pm_nearest_attacker(unit: Any, workers: Any, threats: list[Any]) -> tuple[Any | None, float]:
    best_pos: Any | None = None
    best_d = 1e9
    candidates: list[Any] = []
    if workers:
        with contextlib.suppress(Exception):
            candidates.extend(workers)
    candidates.extend(threats)
    for e in candidates:
        if not _pm_is_threat_to(e, unit):
            continue
        try:
            d = float(e.distance_to(unit))
        except Exception:
            continue
        if d < best_d:
            best_d = d
            best_pos = e.position
    return best_pos, best_d


def _pm_worker_in_range(unit: Any, workers: Any) -> Any | None:
    if not workers:
        return None
    try:
        for w in workers:
            if unit.target_in_range(w):
                return w
    except Exception:
        return None
    return None


def player_should_bail(
    unit: Any,
    bailing_tags: set[int],
    bail_hp: float = _PLAYER_BAIL_HP,
    recover_hp: float = _PLAYER_RECOVER_HP,
) -> bool:
    """玩家 claim 单位的撤退判断（带回血滞回防抖），bailing_tags 由 director 持有。"""
    ratio = _pm_hp_ratio(unit)
    if unit.tag in bailing_tags:
        if ratio >= recover_hp:
            bailing_tags.discard(unit.tag)
            return False
        return True
    if ratio < bail_hp:
        bailing_tags.add(unit.tag)
        return True
    return False


def player_harass_micro(
    unit: Any,
    workers: Any,
    enemy_main: Any,
    threats: list[Any],
    bailing_tags: set[int],
    start_location: Any,
    bail_hp: float = _PLAYER_BAIL_HP,
    recover_hp: float = _PLAYER_RECOVER_HP,
) -> None:
    """对单个玩家 claim 单位执行 hit-and-run 打农民微操（逻辑与 HarassWorkerLineAct._micro 相同）。"""
    if player_should_bail(unit, bailing_tags, bail_hp, recover_hp):
        with contextlib.suppress(Exception):
            unit.move(start_location)
        return

    far = True
    if enemy_main is not None:
        with contextlib.suppress(Exception):
            far = unit.distance_to(enemy_main) > _ARRIVE_DIST

    if far:
        if enemy_main is not None:
            with contextlib.suppress(Exception):
                unit.move(enemy_main)
        return

    cooling = float(getattr(unit, "weapon_cooldown", 0.0)) > 0.0
    hp = _pm_hp_ratio(unit)
    # 按单位射程自适应 kite(根因 F82:固定 5 格步长让射程 3 的飞龙退出自己射程)。
    kite_trigger, kite_step = _pm_kite_params(unit)
    atk_pos, atk_d = _pm_nearest_attacker(unit, workers, threats)
    if cooling and atk_pos is not None and atk_d < kite_trigger:
        with contextlib.suppress(Exception):
            unit.move(unit.position.towards(atk_pos, -kite_step))
        return
    threat_pos, threat_d = _pm_nearest_threat(unit, threats)
    if threat_pos is not None and threat_d < kite_trigger and hp < recover_hp:
        with contextlib.suppress(Exception):
            unit.move(unit.position.towards(threat_pos, -kite_step))
        return

    with contextlib.suppress(Exception):
        target = _pm_worker_in_range(unit, workers)
        if target is not None:
            unit.attack(target)
        elif workers:
            unit.attack(workers.closest_to(unit))
        elif enemy_main is not None:
            unit.move(enemy_main)


class HarassWorkerLineAct(ActBase):  # type: ignore[misc]
    """通用骚扰微操:把指定类型的骚扰单位送进对方矿区 hit-and-run 点农民。"""

    def __init__(
        self,
        unit_types: Iterable[UnitTypeId],
        bail_hp_ratio: float = 0.25,
        recover_hp_ratio: float = 0.55,
        wait_upgrade: Any | None = None,
        release_after: float | None = None,
    ) -> None:
        """
        unit_types      : 要操控的骚扰单位类型(如 {BANSHEE})。
        bail_hp_ratio   : 血量(HP+护盾)比例低于此值 → 撤退保命。
        recover_hp_ratio: 已撤退的单位血量回到此值以上才重新出击(回血滞回)。
            保命侦查型(如死神 —— 会回血)设高(~0.95):退出去回满血再回去,
            尽量别死。
        wait_upgrade : 设了的话,此升级完成前骚扰单位在家待命(如 BANSHEECLOAK)。
        release_after: game-seconds;到点后本 act 放手 —— 不再 Reserved、不再
            下指令,骚扰单位归队主力。给「早期骚扰、后期并入主力推进」的 build
            用(如 hellion_expand:恶火骚扰完要回去凑 bio 一波)。不设 = 永不放手
            (纯骚扰 build,如 banshee_harass)。
        """
        super().__init__()
        self._types: frozenset[UnitTypeId] = frozenset(unit_types)
        self._bail_hp: float = float(bail_hp_ratio)
        self._recover_hp: float = float(recover_hp_ratio)
        self._wait_upgrade = wait_upgrade
        self._release_after = release_after
        # 正在全撤回家的单位 tag —— 维护回血滞回。
        self._bailing: set[int] = set()

    async def execute(self) -> bool:
        try:
            units = self.ai.units.of_type(set(self._types)).ready
        except Exception:
            return True
        if not units:
            return True

        # 骚扰窗口结束 → 放手:不再 Reserved、不再下指令,骚扰单位归队主力。
        if self._release_after is not None:
            with contextlib.suppress(Exception):
                if float(self.ai.time) >= self._release_after:
                    return True

        waiting = self._upgrade_pending()
        enemy_main = self._enemy_main()
        workers = self._visible_enemy_workers()
        threats = None if waiting else self._threats()

        for unit in units:
            self._reserve(unit)
            if waiting:
                continue  # 升级(如隐形)没好 → Reserved 在家待命,不裸送
            if self._should_bail(unit):
                with contextlib.suppress(Exception):
                    unit.move(self.ai.start_location)
                continue
            self._micro(unit, workers, enemy_main, threats)
        return True

    # ------------------------------------------------------------------
    # 微操
    # ------------------------------------------------------------------

    def _micro(self, unit: Any, workers: Any, enemy_main: Any, threats: list[Any] | None) -> None:
        """离对方主基地远 → 直推进场;到了矿区 → hit-and-run。"""
        # 阶段按「到对方主基地的距离」判,不按「全局是否看见农民」——
        # 后者一旦 bot 任意视野里冒出个敌方农民,全体骚扰单位就从直推切成
        # attack-move、沿途逢敌就停,过不去 / 极慢(实测早期击杀方差的根因)。
        far = True
        if enemy_main is not None:
            with contextlib.suppress(Exception):
                far = unit.distance_to(enemy_main) > _ARRIVE_DIST

        # ---- 进场:离矿区还远 → 直推,不被沿途威胁带偏(不 kite)----
        if far:
            if enemy_main is not None:
                with contextlib.suppress(Exception):
                    unit.move(enemy_main)
            return

        # ---- 已到对方矿区:hit-and-run（死神永不站撸，每打一枪就退）----
        cooling = float(getattr(unit, "weapon_cooldown", 0.0)) > 0.0
        hp = self._hp_ratio(unit)
        # 最近的「能打到我」的敌人 —— **含农民**(2026-06-17 用户:农民 A 过来会围、会反打,
        # 原来农民被排除在威胁外 → 死神站着对撸被围死)。死神/女妖脆皮,不能跟任何东西站撸。
        atk_pos, atk_d = self._nearest_attacker(unit, workers, threats)
        # 按单位射程自适应 kite(根因 F82,与 player_harass_micro 镜像同步):
        # 固定 5 格步长让射程 3 的飞龙后撤后退出自己射程 → 够不着农民,改按射程自适应。
        kite_trigger, kite_step = _pm_kite_params(unit)
        # 核心(2026-06-17 用户「死神永远不要站在原地打，每打一枪都要往后退一点，
        # 攻击冷却时要和别人保持距离」)：**武器冷却中 + 有敌人逼近 → 立刻后撤保持距离**，
        # 绝不站在原地。射程内打完一枪进冷却 → 退一步 → 武器好了再贴上来打 → 真·风筝。
        if cooling and atk_pos is not None and atk_d < kite_trigger:
            with contextlib.suppress(Exception):
                unit.move(unit.position.towards(atk_pos, -kite_step))
            return
        # 受伤 + 战斗威胁逼近 → 即便武器好也退（额外自保，宁可不打这一枪也别挨揍）。
        threat_pos, threat_d = self._nearest_threat(unit, threats)
        if threat_pos is not None and threat_d < kite_trigger and hp < self._recover_hp:
            with contextlib.suppress(Exception):
                unit.move(unit.position.towards(threat_pos, -kite_step))
            return

        # 武器好了 + 没贴脸危险 → 打农民
        with contextlib.suppress(Exception):
            target = self._worker_in_range(unit, workers)
            if target is not None:
                unit.attack(target)
            elif workers:
                unit.attack(workers.closest_to(unit))
            elif enemy_main is not None:
                unit.move(enemy_main)  # 已到矿区但暂无农民视野 → 回中心找

    # ------------------------------------------------------------------
    # 决策 / 查询
    # ------------------------------------------------------------------

    def _should_bail(self, unit: Any) -> bool:
        """血量危急 → 全撤回家,带回血滞回防抖。"""
        ratio = self._hp_ratio(unit)
        if unit.tag in self._bailing:
            if ratio >= self._recover_hp:
                self._bailing.discard(unit.tag)
                return False
            return True
        if ratio < self._bail_hp:
            self._bailing.add(unit.tag)
            return True
        return False

    def _hp_ratio(self, unit: Any) -> float:
        """(HP + 护盾) / 满值,取不到时按满血处理(不误撤)。"""
        try:
            mx = float(unit.health_max) + float(unit.shield_max)
            if mx <= 0:
                return 1.0
            return (float(unit.health) + float(unit.shield)) / mx
        except Exception:
            return 1.0

    def _is_threat_to(self, enemy: Any, unit: Any) -> bool:
        """enemy 能否打到 unit —— 按 unit 是空中/地面匹配对空/对地能力。

        飞行骚扰单位(女妖/飞龙)不该被打不到空的小狗/坦克吓退;地面骚扰单位
        (死神/恶火)也不该被纯对空单位吓退。
        """
        try:
            if unit.is_flying:
                return bool(enemy.can_attack_air)
            return bool(enemy.can_attack_ground)
        except Exception:
            return True  # 拿不准 → 当威胁(保守)

    def _nearest_threat(self, unit: Any, threats: list[Any] | None) -> tuple[Any | None, float]:
        """离 unit 最近的、能打到它的威胁 → (位置, 距离)。"""
        if not threats:
            return None, 1e9
        best_pos: Any | None = None
        best_d = 1e9
        for e in threats:
            if not self._is_threat_to(e, unit):
                continue
            try:
                d = float(e.distance_to(unit))
            except Exception:
                continue
            if d < best_d:
                best_d = d
                best_pos = e.position
        return best_pos, best_d

    def _nearest_attacker(
        self, unit: Any, workers: Any, threats: list[Any] | None
    ) -> tuple[Any | None, float]:
        """离 unit 最近的、能打到它的**任意**敌人 → (位置, 距离)。

        与 _nearest_threat 的区别：**把农民也算进来**。死神在矿区 farm 农民时,
        被 A 过来的农民贴脸会被围死 —— 冷却期间要连农民一起躲。农民(SCV/Drone/Probe)
        can_attack_ground=True,_is_threat_to 自然把它们当地面攻击源。
        """
        best_pos: Any | None = None
        best_d = 1e9
        candidates: list[Any] = []
        if workers:
            with contextlib.suppress(Exception):
                candidates.extend(workers)
        if threats:
            candidates.extend(threats)
        for e in candidates:
            if not self._is_threat_to(e, unit):
                continue
            try:
                d = float(e.distance_to(unit))
            except Exception:
                continue
            if d < best_d:
                best_d = d
                best_pos = e.position
        return best_pos, best_d

    def _worker_in_range(self, unit: Any, workers: Any) -> Any | None:
        """武器射程内的敌方农民(有就直接打,不必走过去)。"""
        if not workers:
            return None
        try:
            for w in workers:
                if unit.target_in_range(w):
                    return w
        except Exception:
            return None
        return None

    def _upgrade_pending(self) -> bool:
        """wait_upgrade 设了且尚未研出 → True(骚扰单位应在家待命)。"""
        if self._wait_upgrade is None:
            return False
        try:
            return self._wait_upgrade not in self.ai.state.upgrades
        except Exception:
            return False

    def _threats(self) -> list[Any]:
        """视野内的敌方战斗单位 + 静态防御(农民不算)。"""
        out: list[Any] = []
        with contextlib.suppress(Exception):
            out.extend(
                self.ai.enemy_units.filter(
                    lambda u: u.type_id not in _WORKER_TYPES and not u.is_structure
                )
            )
        with contextlib.suppress(Exception):
            out.extend(self.ai.enemy_structures.filter(lambda s: s.type_id in _STATIC_DEFENSE))
        return out

    def _visible_enemy_workers(self) -> Any:
        """当前视野内的敌方农民。"""
        try:
            return self.ai.enemy_units.filter(lambda u: u.type_id in _WORKER_TYPES)
        except Exception:
            return None

    def _enemy_main(self) -> Any:
        try:
            return self.ai.enemy_start_locations[0]
        except Exception:
            return None

    def _reserve(self, unit: Any) -> None:
        """标 Reserved —— 每 tick 重设,独占控制权。"""
        with contextlib.suppress(Exception):
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, unit)
