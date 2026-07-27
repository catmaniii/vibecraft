"""PhoenixSquadMicro: 凤凰骚扰纯逻辑 helper（2026-07-20 按用户 6 规则 + 保存实力原则重写）。

统领原则（图谱 D42，所有高机动骚扰通用）：**保存实力第一，绝不送**。杀不到农民宁可
撤/绕，也不把凤凰送掉；活着的凤凰=威慑=己方开矿安全（图谱 F88）。唯一可承受战损时机=
判断打光对方对空后能杀大量农民（这由 fight gate 保证：对空少才 fight）。

整队 fight-or-flee 状态机（图谱 D41 规则 2/3/5）
=================================================
每 tick 先算**整队一个 posture**（不再每只各自决策），再按 posture 分派：
- **approach**（还没到矿后区）→ 全队沿 caller 给的 approach_wp 前进（rule 4 的矿后侧切
  路径由 caller 用 plan_avoid_path 算好；micro 只负责"到了没、该打该跑"）。
- **fight**（到矿后区 + 对空够少 can_fight）→ 整队一起打（rule 2）：能 lift 的抬（对空
  优先、其次农民，rule 5 顺序）；不能 lift 的**贴身 attack-move**（move-shot 自动开火，
  绝不 kite 保距——保距=DPS 不够，rule 2）。
- **flee**（到矿后区但对空太多打不过）→ 全队一起绕敌撤（rule 3）：以对空威胁为圆心 orbit
  到安全半径外的另一角，**不原路返回**（防来回拉扯）；同时寻找下一个下手角度（rule 5
  "没好机会别硬切、别反复试"）。
- 个体保命永远最高优先：任一凤凰血危（< bail_hp）直接回家（保存实力，图谱 D42）。

fight gate（can_fight，D89/D90 用游戏数值定阈值）= 可抬对空(女王)数 < 凤凰数 且 带能量凤凰数 ≥
可抬对空数（能一次抬光→零对空火力→无损）；静态防空(抬不掉)≤ 硬闯预算(护盾扛+早撤全身而退)。
这条同时兑现"保存实力"（打不过=撤）+ rule 5（有优势果断抬清对空再杀农民）+ 用户 2026-07-26 三条修正。

本模块是纯逻辑类，不继承 sharpy，不依赖真实 SC2 API（单测友好）。caller (PhoenixSquadAct)
负责 rule 1（≥5 才出）、rule 4（矿后侧切接近路径）、rule 6（不骚扰时归队），见 act。
"""

from __future__ import annotations

import contextlib
import logging
import math
from typing import Any

from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

REJOIN_DIST: float = (
    20.0  # phoenix_control._ready_to_push 出门凝聚 gate 用（最远凤凰距 center < 20 才出门）
)
PATROL_RADIUS: float = 8.0  # 保留（旧引用兼容）；本版绕飞靠 flee orbit，不再用固定绕主基半径
KITE_MARGIN: float = 0.5  # 停在射程边缘用（射程 - 0.5）
LIFT_ENERGY: float = 50.0  # GRAVITONBEAM 能量阈值
LIFT_GATE_RATIO: float = (
    0.5  # fight gate + lift 对空兵 gate：对空威胁 <= 能lift凤凰 × 0.5 → 可打/可抬
)
LIFT_SCAN_RANGE: float = 14.0  # 扫 lift 目标半径
MIN_NEARBY_FOR_LIFT_WORKER: int = 2  # lift 农民 gate: 周围能 lift 凤凰 >= 2 才抬农民
ANTI_AIR_SCAN_RANGE: float = 10.0  # 单只 lift 判定的对空扫描半径
BAIL_HP: float = 0.3  # 撤退血量阈值(血危兜底：护盾没了、血也掉到这就必退)
BAIL_SHIELD: float = 0.2  # 撤退护盾阈值(D61：护盾当 buffer，掉到这就退回回盾，别耗到掉血送命)
RECOVER_HP: float = 0.6  # 回盾滞回阈值(护盾回到此比例才再出击)
_ARRIVE_DIST: float = 22.0  # 到矿后区判定（质心距 harass_anchor <= 此值 = 已到）

# ── 整队 posture 判定（2026-07-20 重写）─────────────────────────────────────────
_SQUAD_AA_SCAN: float = 12.0  # can_fight 判定：以 squad_center 为心扫对空威胁半径
# D90 静态防空硬闯预算(游戏数值算得)：凤凰180有效血,单座静态防空~18DPS,飞出射程7约~2s→
# 硬闯挨~静态数×36伤。≤2 座时护盾扛+没盾早撤能全身而退杀几农民;≥3 座焦点火力~1s穿盾撤不出→不闯。
_STATIC_RAID_MAX: int = 2
_FLEE_SCAN: float = 14.0  # flee orbit：算威胁圆心的对空扫描半径
_FLEE_SAFE_RADIUS: float = 11.0  # flee orbit：拿不到威胁 air_range 时的兜底安全半径
_FLEE_MARGIN: float = 2.0  # flee orbit：安全半径额外 buffer（确保出射程）
_FLEE_ORBIT_STEP: float = 0.7  # flee orbit：每帧绕威胁圆心切向偏转角(rad)→ 绕敌走、不原路返
_FIGHT_AIRBORNE_SCAN: float = 18.0  # fight：找可打的空中目标(飞行/被抬)半径
_WORKER_CHASE_SCAN: float = (
    26.0  # fight 无空中目标：搜此半径内最近敌农民 → 移过去带进抬射程(I41 修 F128)
)
_COHESION_DIST: float = 12.0  # 前线一波聚拢判据:凤凰离质心 <= 此值算"在一起"（用户 2026-07-25）
# ── 限量抬人（2026-07-22 用户：能抬的全抬了只剩 1 个打→抬起来打不死又掉下去，白费）──────
_MAX_LIFTERS_NORMAL: int = 2  # 平时最多同时抬 N 个（留够攻击凤凰把抬起来的打死，再换目标）
_AA_HEAVY_LIFT: int = 4  # 附近可抬对空兵 >= 此数=地对空火力真猛 → 才放宽多抬(抬起对空兵压制保命)。
# 2026-07-22 用户"偶尔抬到3有点问题":门槛 3→4,让平时严格 ≤2、只有对空真多才破例
# ── 归队后"抬地防守"（用户 2026-07-26：80% 凤凰死在归队后主力退却里不抬送掉；凤凰打不到地面,
#    唯一价值=抬地面关键单位集火。归队后不裸交 sharpy 退却,保留此 micro 持续抬敌方地面高价值单位）──
_DEFEND_SCAN: float = 55.0  # 抬地防守:敌方地面单位离我方任一基地此半径内 = 威胁(评审⑤)
_DEFEND_MAX_LIFTERS: int = 4  # 抬地防守:最多同时抬 N 个高价值单位(高坦/坦克/不朽…)
_MIN_DEFEND_SHOOTERS: int = (
    2  # 抬地防守:至少留 N 只不抬的凤凰当集火手(评审③:别 4 抬 1 打→被抬打不死白费)
)
# 保守型(用户 2026-07-26 保存优先,死太多):附近能对空单位 <= 此数才敢 engage/抬(抬人 channel 不能动会
# 挨打),否则退守养能量、绝不硬抬送。兼顾"能安全抬就抬 + 尽量保存",别扎进带 AA 的敌军里。
_DEFEND_ENGAGE_AA_MAX: int = 2

# ---------------------------------------------------------------------------
# lift 优先级表（从 vendor/sharpy/sharpy/combat/protoss/micro_phoenixes.py 抄来）
# 10=最高优先级立刻吊，负数=不吊
# ---------------------------------------------------------------------------

_LIFT_PRIORITY: dict[UnitTypeId, int] = {
    # Terran
    UnitTypeId.SIEGETANK: 4,
    UnitTypeId.SIEGETANKSIEGED: 9,  # 驻守坦克比行进坦克高很多
    UnitTypeId.MULE: 6,
    UnitTypeId.SCV: 4,
    UnitTypeId.WIDOWMINEBURROWED: 10,
    UnitTypeId.WIDOWMINE: 8,
    UnitTypeId.GHOST: 10,
    UnitTypeId.REAPER: 4,
    UnitTypeId.MARAUDER: 4,
    UnitTypeId.MARINE: 3,
    UnitTypeId.CYCLONE: 6,
    UnitTypeId.HELLION: 2,
    UnitTypeId.HELLIONTANK: 1,
    UnitTypeId.THOR: -1,
    # Zerg
    UnitTypeId.QUEEN: 3,
    UnitTypeId.DRONE: 4,
    UnitTypeId.HYDRALISK: 7,
    UnitTypeId.BANELING: 6,
    UnitTypeId.LURKERMP: 9,
    UnitTypeId.LURKERMPBURROWED: 9,
    UnitTypeId.INFESTOR: 10,
    UnitTypeId.INFESTEDTERRAN: 1,
    UnitTypeId.ROACH: 0,
    UnitTypeId.LARVA: -1,
    UnitTypeId.EGG: -1,
    UnitTypeId.LOCUSTMP: -1,
    UnitTypeId.BROODLING: -1,
    UnitTypeId.ULTRALISK: -1,
    # Protoss
    UnitTypeId.SENTRY: 8,
    UnitTypeId.PROBE: 4,
    UnitTypeId.HIGHTEMPLAR: 10,
    UnitTypeId.DARKTEMPLAR: 9,
    UnitTypeId.ADEPT: 4,
    UnitTypeId.ZEALOT: 4,
    UnitTypeId.STALKER: 2,
    UnitTypeId.IMMORTAL: 3,
    UnitTypeId.ARCHON: -1,
    UnitTypeId.COLOSSUS: -1,
}

# 农民类型（gate 通过后的 fallback lift 目标）
_WORKER_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE}
)


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------


class PhoenixSquadMicro:
    """凤凰骚扰纯逻辑 helper（整队 fight-or-flee 状态机）。

    不继承 sharpy，单测可直接构造。所有 SC2 API 访问 duck-typing，异常由本类兜底。

    state:
        _bailing: set[int] — 正在撤退回家的凤凰 tag（回血滞回）
    """

    def __init__(
        self,
        bail_hp_ratio: float = BAIL_HP,
        recover_hp_ratio: float = RECOVER_HP,
        bail_shield_ratio: float = BAIL_SHIELD,
    ) -> None:
        self._bail_hp = float(bail_hp_ratio)
        # D61：护盾当 buffer——护盾掉到 _bail_shield 就退回回盾(拿会回的护盾换战果，别耗到掉血)。
        self._bail_shield = float(bail_shield_ratio)
        # recover_hp_ratio 重新解释为**护盾回复阈值**（神族血不回、只护盾回，见下 _should_bail）。
        self._recover_shield = float(recover_hp_ratio)
        self._bailing: set[int] = set()
        self._last_posture: str = "?"  # 上次整队 posture（approach/fight/flee），供 act trace 读
        self._last_n_lifters: int = 0  # 上次 tick 实际抬人的凤凰数，供 act trace 读
        self._last_n_lifted: int = (
            0  # 上次 tick 被抬集合大小（跨 posture 集火验证，供 act trace 读）
        )

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    def solve_squad(
        self,
        phoenixes: Any,
        harass_anchor: Any,
        approach_wp: Any,
        ai: Any,
        safe_points: Any = None,
    ) -> dict[int, tuple[str, Any]]:
        """为整队凤凰计算本 tick 动作。

        参数（caller = PhoenixSquadAct 提供）：
          harass_anchor : 矿后锚点 Point2（rule 4，caller 从 zone 几何算，兜底 enemy_main）
          approach_wp   : 当前接近路径 waypoint Point2（rule 4，caller 用 plan_avoid_path
                          算的避主基/矿后侧切路径的当前点；兜底 harass_anchor）
          safe_points   : 各矿矿后悬崖安全口袋列表（D71/terrain_harass，地面够不到）。flee 时
                          优先退到最近的、不挨打的安全口袋（心法 D81/D67：敌来退安全区,不原地耗）。

        返回 {unit_tag: (action_type, target)}
          action_type in {"move", "attack", "lift"}
            "move"   → unit.move(target)               （撤退 / 接近 / orbit）
            "attack" → unit.attack(target)             （fight 贴身 move-shot，target=Unit 或 Point2）
            "lift"   → unit(GRAVITONBEAM, target)       （抬对空兵/农民）
        """
        result: dict[int, tuple[str, Any]] = {}

        try:
            squad_center: Any = phoenixes.center
        except Exception:
            squad_center = harass_anchor

        # 整队 posture（一次算，全队共用）：approach / fight / flee
        posture, flee_wp = self._squad_posture(
            phoenixes, squad_center, harass_anchor, ai, safe_points
        )
        self._last_posture = posture

        # 只在 fight 时**发起**新 lift（限量最多 2 只抬）。但"被抬集合"每帧**无条件**计算
        # （D59 修 F98）：一旦有单位被抬起（GRAVITONBEAM buff 未结束），无论整队 posture 是
        # fight/flee/approach，其余凤凰都必须留下集火 A 死它，绝不因 posture 翻转就飞走
        # （旧 bug：lifted_targets 只在 fight 算 → posture 一翻集合置空 → 其他凤凰跟着走）。
        lift_assign = self._assign_lifts(phoenixes, ai) if posture == "fight" else {}
        self._last_n_lifters = len(lift_assign)  # 供 trace
        lifted_targets = self._lifted_targets(lift_assign, ai)
        self._last_n_lifted = len(
            lifted_targets
        )  # 供 trace（被抬集合大小；非空则应 atk>0 无论 posture）

        # D82：bail/回盾退到**最近安全悬崖口袋**(不回家)；整队用同一个 bail_wp = 在一起(D53/D57)。
        bail_wp = self._nearest_safe_point(squad_center, safe_points, ai)

        for unit in phoenixes:
            with contextlib.suppress(Exception):
                result[unit.tag] = self._solve_unit(
                    unit,
                    posture,
                    squad_center,
                    harass_anchor,
                    approach_wp,
                    flee_wp,
                    phoenixes,
                    ai,
                    lift_assign,
                    lifted_targets,
                    bail_wp,
                )
        return result

    def solve_lift_defend(
        self,
        phoenixes: Any,
        ai: Any,
    ) -> dict[int, tuple[str, Any]]:
        """归队后"抬地防守"micro（用户 2026-07-26）：凤凰不裸交 sharpy 退却发呆送掉，持续主动**抬敌方
        地面高价值单位**（高坦/坦克/不朽/哨兵… 按 _LIFT_PRIORITY）集火 + **拦截敌方空军**（凤凰是唯一
        机动 AA）。修 80% 凤凰死在归队后主力退却里不抬（真机 telemetry：归队后损失暴涨）。

        保存实力仍第一（经独立评审补齐 4 洞）：
        - 血/盾危 → 撤**己方侧**安全点回盾（评审④：不能飞去敌方矿后口袋）。
        - 敌空军 → **attack**（自动开火，评审②：别 move 挨打不还手）。
        - 地面高价值 → 抬（留够集火手，评审③）+ 集火打死被抬的。
        - 靠近地面威胁前先判**打不打得过附近 AA**（评审②：打不过就退，别扎进扑家 AA 军送）。
        - 没能量/没威胁/打不过 → 退己方侧养能量。
        返回 {tag: (action, target)}。
        """
        result: dict[int, tuple[str, Any]] = {}
        try:
            squad_center = phoenixes.center
        except Exception:
            squad_center = ai.start_location
        threats = self._defend_threats(ai)
        home_wp = self._own_regen_point(ai)  # 评审④:己方侧养能量/回盾点(离敌主基最远的己方基地)
        # 保守型(用户 2026-07-26 保存优先):附近 AA 轻才 engage/抬,重就全队退守养能量、绝不硬抬送。
        safe = self._defend_safe_to_engage(squad_center, ai)
        lift_assign = self._assign_defend_lifts(phoenixes, ai, threats) if safe else {}
        lifted_targets = self._lifted_targets(lift_assign, ai)
        for unit in phoenixes:
            with contextlib.suppress(Exception):
                result[unit.tag] = self._solve_defend_unit(
                    unit, lift_assign, lifted_targets, threats, home_wp, safe, ai
                )
        return result

    def _defend_safe_to_engage(self, squad_center: Any, ai: Any) -> bool:
        """保守 engage 门（用户 2026-07-26 保存优先，死太多）：squad 附近 _SQUAD_AA_SCAN 内能对空单位
        <= _DEFEND_ENGAGE_AA_MAX 才敢 engage/抬（抬人 channel 不能动会挨打）；AA 重 → 退守养能量、别送。"""
        try:
            n_aa = len(
                ai.enemy_units.filter(
                    lambda e: (
                        e.distance_to(squad_center) <= _SQUAD_AA_SCAN
                        and e.can_attack_air
                        and not e.is_flying
                        and not e.is_structure
                        and e.type_id not in _WORKER_TYPES
                        and not e.has_buff(BuffId.GRAVITONBEAM)
                    )
                )
            )
        except Exception:
            return False
        return n_aa <= _DEFEND_ENGAGE_AA_MAX

    def _own_regen_point(self, ai: Any) -> Any:
        """己方侧养能量/回盾点（评审④）：离敌方主基最远的己方基地（最安全那个），兜底 start_location。"""
        with contextlib.suppress(Exception):
            ths = ai.townhalls
            em = ai.enemy_start_locations[0]
            if ths:
                best = max(ths, key=lambda t: t.distance_to(em))
                return best.position if hasattr(best, "position") else best
        with contextlib.suppress(Exception):
            return ai.start_location
        return None

    def _defend_threats(self, ai: Any) -> Any:
        """归队后要抬的敌方地面单位：可抬（非飞行/非建筑/非农民/有正 lift 优先级、未被抬）、离我方
        **任一基地** _DEFEND_SCAN 内的（评审⑤：离任一 townhall 近，与 recall 同口径，不用质心漏边缘基地）。
        返回 Units（空/无威胁 → None）。"""
        anchors: list[Any] = []
        with contextlib.suppress(Exception):
            ths = ai.townhalls
            if ths:
                anchors = [t.position if hasattr(t, "position") else t for t in ths]
        if not anchors:
            with contextlib.suppress(Exception):
                anchors = [ai.start_location]
        if not anchors:
            return None
        try:
            th = ai.enemy_units.filter(
                lambda e: (
                    not e.is_flying
                    and not e.is_structure
                    and e.type_id not in _WORKER_TYPES
                    and _LIFT_PRIORITY.get(e.type_id, -1) >= 0
                    and not e.has_buff(BuffId.GRAVITONBEAM)
                    and any(e.distance_to(a) <= _DEFEND_SCAN for a in anchors)
                )
            )
            return th if th else None
        except Exception:
            return None

    def _defend_max_lifters(self, phoenixes: Any) -> int:
        """抬地防守抬手数（评审③：留够集火手，别 4 抬 1 打→被抬打不死白费）：min(_DEFEND_MAX_LIFTERS,
        凤凰数 - _MIN_DEFEND_SHOOTERS)，但**至少 1**（孤凤凰/少量凤凰也能抬，只有 ≥4 只才受"留 2 集火手"约束）。"""
        try:
            n = len(list(phoenixes))
        except Exception:
            return 1
        return max(1, min(_DEFEND_MAX_LIFTERS, n - _MIN_DEFEND_SHOOTERS))

    def _assign_defend_lifts(self, phoenixes: Any, ai: Any, threats: Any) -> dict[int, Any]:
        """抬地防守分配 lift：最多 _defend_max_lifters 只带能量凤凰（留够集火手），各抬其 lift 范围内
        **最高 _LIFT_PRIORITY** 的地面单位（去重目标）。确定性按 tag 排序。"""
        assign: dict[int, Any] = {}
        if not threats:
            return assign
        max_lifters = self._defend_max_lifters(phoenixes)
        if max_lifters <= 0:
            return assign
        lifted_tags: set[Any] = set()
        try:
            units = sorted(phoenixes, key=lambda p: p.tag)
        except Exception:
            units = list(phoenixes)
        for unit in units:
            if len(assign) >= max_lifters:
                break
            try:
                if float(unit.energy) < LIFT_ENERGY:
                    continue
            except Exception:
                continue
            # 显式循环选 lift 范围内、未被抬/未分配的最高 _LIFT_PRIORITY 地面单位(避 lambda 闭包 loop 变量)
            best = None
            best_score = -1e18
            with contextlib.suppress(Exception):
                for e in threats:
                    if e.tag in lifted_tags or e.has_buff(BuffId.GRAVITONBEAM):
                        continue
                    d = unit.distance_to(e)
                    if d > LIFT_SCAN_RANGE:
                        continue
                    score = _LIFT_PRIORITY.get(e.type_id, -1) + (1 - d / LIFT_SCAN_RANGE)
                    if score > best_score:
                        best_score = score
                        best = e
            if best is None:
                continue
            assign[unit.tag] = best
            lifted_tags.add(best.tag)
        return assign

    def _solve_defend_unit(
        self,
        unit: Any,
        lift_assign: dict[int, Any],
        lifted_targets: list[Any],
        threats: Any,
        home_wp: Any,
        safe: bool,
        ai: Any,
    ) -> tuple[str, Any]:
        """抬地防守单只分派（保守型，保存优先）：个体保命 > 抬 > 集火被抬 > 打敌空军 >
        (仅 AA 轻/safe 时)靠近地面威胁抬 > 退己方侧养能量。AA 重(not safe)一律退守，绝不硬抬送。"""
        home = home_wp if home_wp is not None else ai.start_location
        # 1. 血/盾危 → 退己方侧养能量/回盾(评审④:不飞去敌方口袋)
        if self._should_bail(unit):
            return ("move", home)
        # 2. 分到 lift → 抬(lift_assign 已只在 safe 时非空)
        my_lift = lift_assign.get(unit.tag)
        if my_lift is not None:
            return ("lift", my_lift)
        # 3. 有被抬单位 → 集火 A 死
        if lifted_targets:
            with contextlib.suppress(Exception):
                return ("attack", min(lifted_targets, key=lambda t: unit.distance_to(t)))
            return ("attack", lifted_targets[0])
        # 4. 敌空军(飞行/被抬) → **attack**(自动开火,凤凰是唯一 AA,别 move 挨打不还手)。air-to-air 是凤凰
        #    本职,不受保守地面 gate 限制;个体挨不住由 _should_bail 兜底退。
        air = self._fight_airborne_target(unit, ai)
        if air is not None:
            return ("attack", air)
        # 5. **仅 AA 轻(safe)** 且有能量有地面威胁 → 靠近抬(保守:AA 重不 engage,见 _defend_safe_to_engage)
        if safe and threats:
            with contextlib.suppress(Exception):
                if float(unit.energy) >= LIFT_ENERGY:
                    return ("move", threats.closest_to(unit).position)
        # 6. 没能量/没威胁/AA重 → 退己方侧养能量(保存优先,别扎进带 AA 的敌军送死)
        return ("move", home)

    def _nearest_safe_point(self, squad_center: Any, safe_points: Any, ai: Any) -> Any:
        """整队回盾/bail 目标:最近的、不挨打的安全口袋(D82,不回家);无口袋才兜底回家。"""
        with contextlib.suppress(Exception):
            if safe_points:
                cand = [p for p in safe_points if not self._point_aa_threatened(p, ai)]
                if cand:
                    return min(cand, key=lambda p: squad_center.distance_to(p))
        with contextlib.suppress(Exception):
            return ai.start_location  # 无安全口袋兜底才回家
        return squad_center

    def _lifted_targets(self, lift_assign: dict[int, Any], ai: Any) -> list[Any]:
        """集火目标:本 tick 被分配抬的 + 已被 GRAVITONBEAM 抬起(上 tick 抬的还没掉下来)的敌方单位,去重。"""
        out: list[Any] = []
        seen: set[Any] = set()
        for t in lift_assign.values():
            if t is None:
                continue
            tag = getattr(t, "tag", id(t))
            if tag not in seen:
                out.append(t)
                seen.add(tag)
        with contextlib.suppress(Exception):
            for e in ai.enemy_units.filter(
                lambda u: u.has_buff(BuffId.GRAVITONBEAM) and not u.is_structure
            ):
                if e.tag not in seen:
                    out.append(e)
                    seen.add(e.tag)
        return out

    def _main_body_center(self, phoenixes: Any, fallback: Any) -> Any:
        """**主群(多数)中心**——用于 D57「少数迁就多数」:主群继续推进,落单/新出的追这个点。

        绝不用全体质心(phoenixes.center):落单的一只会把全体质心往后拽,主群若追全体质心就被
        拽回去等落单者=多数迁就少数(F96 真机 bug)。改取**最大 cohesive 簇**中心:以"邻居最多
        (_COHESION_DIST 内其他凤凰数最多)"那只为核,取其 _COHESION_DIST 内所有凤凰的质心——
        落单者当不了核、也不进簇,故主群中心不被落单者拖后。tag 做确定性 tiebreak(避免帧间抖)。
        """
        try:
            pts = list(phoenixes)
            n = len(pts)
            if n == 0:
                return fallback
            if n == 1:
                return pts[0].position
            core = max(
                pts,
                key=lambda p: (
                    sum(1 for q in pts if p.distance_to(q) <= _COHESION_DIST),
                    p.tag,
                ),
            )
            body = [q for q in pts if core.distance_to(q) <= _COHESION_DIST]
            sx = sum(float(q.position.x) for q in body) / len(body)
            sy = sum(float(q.position.y) for q in body) / len(body)
            return Point2((sx, sy))
        except Exception:
            return fallback

    def _assign_lifts(self, phoenixes: Any, ai: Any) -> dict[int, Any]:
        """限量分配 lift：最多 _max_lifters 只凤凰抬人，去重目标（不同凤凰不抬同一个）。

        平时上限 2（留够攻击凤凰把抬起来的打死，再换目标）；附近可抬对空兵多（火力猛）时
        才放宽到多抬（抬起对空兵压制保命）。确定性按 tag 排序挑抬手。
        """
        assign: dict[int, Any] = {}
        max_lifters = self._max_lifters(phoenixes, ai)
        if max_lifters <= 0:
            return assign
        lifted_target_tags: set[Any] = set()
        try:
            units = sorted(phoenixes, key=lambda p: p.tag)
        except Exception:
            units = list(phoenixes)
        for unit in units:
            if len(assign) >= max_lifters:
                break
            try:
                if float(unit.energy) < LIFT_ENERGY:
                    continue
            except Exception:
                continue
            target = self._try_lift(unit, phoenixes, ai)
            if target is None:
                continue
            try:
                ttag = target.tag
            except Exception:
                ttag = id(target)
            if ttag in lifted_target_tags:
                continue
            assign[unit.tag] = target
            lifted_target_tags.add(ttag)
        return assign

    def _max_lifters(self, phoenixes: Any, ai: Any) -> int:
        """本 tick 允许同时抬几个：平时 _MAX_LIFTERS_NORMAL(2)；附近可抬对空兵 >= _AA_HEAVY_LIFT
        (地对空火力猛)时放宽到 min(对空兵数, 能lift凤凰数)(抬起对空兵压制保命,至少 _AA_HEAVY_LIFT)。"""
        try:
            squad_center = phoenixes.center
            n_aa = len(
                ai.enemy_units.filter(
                    lambda e: (
                        e.distance_to(squad_center) <= _SQUAD_AA_SCAN
                        and e.can_attack_air
                        and not e.is_flying
                        and not e.is_structure
                        and e.type_id not in _WORKER_TYPES
                        and not e.has_buff(BuffId.GRAVITONBEAM)
                    )
                )
            )
        except Exception:
            n_aa = 0
        if n_aa >= _AA_HEAVY_LIFT:
            try:
                n_lift = len(phoenixes.filter(lambda p: p.is_ready and p.energy >= LIFT_ENERGY))
            except Exception:
                n_lift = _AA_HEAVY_LIFT
            return max(min(n_aa, n_lift), _AA_HEAVY_LIFT)
        return _MAX_LIFTERS_NORMAL

    # ------------------------------------------------------------------
    # 整队 posture 决策
    # ------------------------------------------------------------------

    def _squad_posture(
        self,
        phoenixes: Any,
        squad_center: Any,
        harass_anchor: Any,
        ai: Any,
        safe_points: Any = None,
    ) -> tuple[str, Any]:
        """算整队 posture + （flee 时）共享 orbit 撤退点。

        **全程判"该不该跑"（用户 2026-07-22：骚扰状态全程都要判能不能打/该不该跑，不是到点才判）**：
        每帧先看 squad 附近对空压制打不过没——打不过就一律 flee 绕敌撤，**无论在接近途中还是已到
        矿后区**（接近途中被军队拦截也立刻走，不用飞到跟前干瞪眼）。can_fight 无对空时恒 True，
        所以没敌情的正常接近不会误判成 flee。
        - 打不过（附近对空压制）→ "flee"（绕敌撤，保存实力）
        - 打得过 + 已到矿后区 → "fight"
        - 打得过 + 没到矿后区 → "approach"（继续矿后侧切接近）
        """
        if harass_anchor is None:
            return "approach", None
        if not self._squad_can_fight(phoenixes, squad_center, ai):
            return "flee", self._flee_waypoint(
                squad_center, harass_anchor, phoenixes, ai, safe_points
            )
        try:
            in_zone = float(squad_center.distance_to(harass_anchor)) <= _ARRIVE_DIST
        except Exception:
            in_zone = False
        return ("fight", None) if in_zone else ("approach", None)

    def _squad_can_fight(self, phoenixes: Any, squad_center: Any, ai: Any) -> bool:
        """整队能不能打/该不该硬闯（保存实力核心 gate，D89/D90 用游戏数值定阈值——修 F123）。

        凤凰**只能对空**,清对空只有两条路:抬(对可抬的)/护盾扛(对抬不掉的静态)。两类分开判:
        - **可抬对空**(女王/枪兵/刺蛇等**非建筑**地面对空,能 GRAVITONBEAM 抬起):
          N 只**带能量**凤凰可**同时**抬起 N 个 → **带能量凤凰数 ≥ 可抬对空数** 时能一次抬光→
          零对空火力→**零损失**,还有富余凤凰杀农民。故门 = `可抬对空数 < 凤凰总数`(有富余,女王比
          凤凰少,用户 2026-07-26) `且 带能量凤凰数 ≥ 可抬对空数`(抬得动全部)。带能量不足=抬不动=
          会挨打→撤回去回能量("除非没能量")。不再一味躲女王被磨死(F123 真机 9→0)。
        - **静态防空**(孢子/光子炮/导弹塔等 is_structure,**抬不掉**):**少量(≤_STATIC_RAID_MAX)
          可硬闯突袭**——护盾扛+没盾凤凰早撤轮转,冲进去秒几农民就走(护盾脱战会回),全身而退靠
          _should_bail 早撤保证(D90)。太多(>预算)焦点火力秒穿护盾撤不出→不闯,避开换矿(F108)。
        对空=能打空军的敌方非农民、未被抬单位。
        """
        try:
            ready = phoenixes.filter(lambda p: p.is_ready)
            n_ph = len(ready)
            n_energy = len(ready.filter(lambda p: float(p.energy) >= LIFT_ENERGY))
        except Exception:
            n_ph = 0
            n_energy = 0
        try:
            aa = ai.enemy_units.filter(
                lambda e: (
                    e.distance_to(squad_center) <= _SQUAD_AA_SCAN
                    and e.can_attack_air
                    and not e.has_buff(BuffId.GRAVITONBEAM)
                    and e.type_id not in _WORKER_TYPES
                )
            )
            n_static = len(aa.filter(lambda e: e.is_structure))
            n_liftable = len(aa) - n_static
        except Exception:
            n_static = 0
            n_liftable = 0
        # 静态防空(抬不掉):超预算=硬闯必损失→不去;≤预算=可护盾硬闯(靠 _should_bail 早撤全身而退)
        if n_static > _STATIC_RAID_MAX:
            return False
        # 可抬对空:凤凰有富余(数 > 它)且带能量抬得动全部 → 打+抬清场;否则撤(含没能量抬不动)
        if n_liftable == 0:
            return True
        return n_liftable < n_ph and n_energy >= n_liftable

    def _flee_waypoint(
        self,
        squad_center: Any,
        harass_anchor: Any,
        phoenixes: Any,
        ai: Any,
        safe_points: Any = None,
    ) -> Any:
        """flee 目标（心法 D81/D67 + D64 腾挪）：敌方来兵时退到**安全悬崖口袋**(地面够不到)拉扯。

        优先级:①退到最近的、不挨打的**安全口袋**(safe_points,terrain_harass 各矿矿后悬崖口袋)——
        心法'敌来退安全区',在各矿口袋之间腾挪;②无 safe_points 但 harass_anchor 是别处且安全 →
        穿梭过去(D64);③都不行 → orbit 逃离当前威胁(先拉开距离,D66)。
        """
        # ① 退到最近的、不挨打的安全口袋(心法:敌来退安全区/在各矿口袋腾挪)
        with contextlib.suppress(Exception):
            if safe_points:
                cand = [
                    p
                    for p in safe_points
                    if not self._point_aa_threatened(p, ai)
                    and squad_center.distance_to(p) > 3.0  # 不是脚下
                ]
                if cand:
                    return min(cand, key=lambda p: squad_center.distance_to(p))
        # ② 无口袋:穿梭到另一安全矿后(D64)
        with contextlib.suppress(Exception):
            if (
                harass_anchor is not None
                and squad_center.distance_to(harass_anchor) > _ARRIVE_DIST
                and not self._point_aa_threatened(harass_anchor, ai)
            ):
                return harass_anchor
        # ③ orbit 逃离当前威胁(先拉距离)
        return self._flee_orbit_waypoint(squad_center, phoenixes, ai)

    def _point_aa_threatened(self, pt: Any, ai: Any) -> bool:
        """pt 的 _SQUAD_AA_SCAN 内有敌方对空威胁 → 不安全(穿梭目标筛选用)。"""
        try:
            return any(
                e.distance_to(pt) <= _SQUAD_AA_SCAN
                and e.can_attack_air
                and not e.has_buff(BuffId.GRAVITONBEAM)
                and e.type_id not in _WORKER_TYPES
                for e in ai.enemy_units
            )
        except Exception:
            return False

    def _flee_orbit_waypoint(self, squad_center: Any, phoenixes: Any, ai: Any) -> Any:
        """全队共享 orbit 撤退点（rule 3）：以对空威胁为圆心，绕到安全半径外的**另一角**。

        每帧从当前 squad→威胁 的连线方向偏转 _FLEE_ORBIT_STEP → 目标点始终在圆周切向前方
        → 全队一起绕敌群走（不原路折返、不来回拉扯），同时寻找下一个下手角度。安全半径 =
        max(当前距, 威胁 air_range) + margin → 保证飞出对空射程（保存实力）。拿不到威胁 →
        兜底回家（个体 bail 也会兜住）。
        """
        try:
            aa = ai.enemy_units.filter(
                lambda e: (
                    e.can_attack_air
                    and not e.has_buff(BuffId.GRAVITONBEAM)
                    and e.type_id not in _WORKER_TYPES
                    and e.distance_to(squad_center) <= _FLEE_SCAN
                )
            )
        except Exception:
            aa = None
        if not aa:
            try:
                return ai.start_location
            except Exception:
                return squad_center
        try:
            center = aa.center
        except Exception:
            return ai.start_location
        dx = float(squad_center.x) - float(center.x)
        dy = float(squad_center.y) - float(center.y)
        r = math.hypot(dx, dy) or 1.0
        try:
            rng = max((float(e.air_range) for e in aa), default=0.0)
        except Exception:
            rng = 0.0
        safe = max(r, rng if rng > 0 else _FLEE_SAFE_RADIUS) + _FLEE_MARGIN
        base_ang = math.atan2(dy, dx)
        ang = base_ang + _FLEE_ORBIT_STEP
        return Point2((center.x + math.cos(ang) * safe, center.y + math.sin(ang) * safe))

    # ------------------------------------------------------------------
    # 单只分派（按整队 posture）
    # ------------------------------------------------------------------

    def _solve_unit(
        self,
        unit: Any,
        posture: str,
        squad_center: Any,
        harass_anchor: Any,
        approach_wp: Any,
        flee_wp: Any,
        phoenixes: Any,
        ai: Any,
        lift_assign: dict[int, Any],
        lifted_targets: list[Any],
        bail_wp: Any = None,
    ) -> tuple[str, Any]:
        """单只凤凰动作 = 个体保命 > 集火被抬 > 整队 posture 分派。"""
        # 1. 个体血/护盾危 → 退**最近安全悬崖口袋回盾**(D82,不回家;整队同一个 bail_wp=在一起)。
        #    保存实力永远最高优先(图谱 D42)。bail_wp 无(无安全口袋)才兜底回家。
        if self._should_bail(unit):
            return ("move", bail_wp if bail_wp is not None else ai.start_location)

        # 2. 抬手继续抬（fight 时被限量分配到 lift 的凤凰，别打断自己的 lift）
        my_lift = lift_assign.get(unit.tag)
        if my_lift is not None:
            return ("lift", my_lift)

        # 3. **有单位被抬起来 → 集火 A 死它，绝不走开**（D59 修 F98，落地 D55）。
        #    跨 posture：只要被抬集合非空，非抬手凤凰一律去 A 最近的被抬单位——优先级**高于
        #    approach/flee**、仅低于个体 bail。绝不因 posture 翻成 flee/approach 就飞走抛下被抬的。
        #    （这是"凤凰平时只 move 不 A"的唯一例外。）
        if lifted_targets:
            with contextlib.suppress(Exception):
                return ("attack", min(lifted_targets, key=lambda t: unit.distance_to(t)))
            return ("attack", lifted_targets[0])

        # 4. 接近阶段 → **少数迁就多数**（D57/D58 修 F96）：主群(多数)继续沿 approach_wp 推进，
        #    落单/新出的少数去追**主群中心**（不是全体质心，否则又把主群拽回）。绝不让主群回头等落单者。
        if posture == "approach":
            body_center = self._main_body_center(phoenixes, squad_center)
            with contextlib.suppress(Exception):
                if unit.distance_to(body_center) > _COHESION_DIST:
                    return ("move", body_center)  # 落单/新出 → 追主群（少数迁就多数）
            # 主群成员 → 继续推进，绝不回头迁就落单者
            return ("move", approach_wp if approach_wp is not None else harass_anchor)

        # 5. 撤退阶段 → 全队一起 orbit 绕敌撤（rule 2 flee-together + rule 3 绕敌）
        if posture == "flee":
            return ("move", flee_wp if flee_wp is not None else ai.start_location)

        # 6. fight（没被分配抬、也没被抬单位可集火）：有敌空军(飞行/被抬) → move 过去自动开火。
        airborne = self._fight_airborne_target(unit, ai)
        if airborne is not None:
            with contextlib.suppress(Exception):
                return ("move", airborne.position)
        # **要么抬要么跑（D61 修 F105）**：抬不成、无空中目标时，附近有对空威胁(枪兵) → 一律 flee
        #   退出射程回盾，**绝不杵在锚点被白打**；无对空威胁才 move 到矿后锚点(继续找农民/占位)。
        if self._aa_near(squad_center, ai):
            return ("move", self._flee_orbit_waypoint(squad_center, phoenixes, ai))
        # 无对空威胁但没空中目标 → **主动移向最近敌农民**把它带进抬射程(I41 修 F128:别杵静态锚点
        # 干等——到矿后农民常被 pull 出 LIFT_SCAN_RANGE,凤凰快追上去、下一 tick Gate B 抬它杀掉)。
        worker_pos = self._nearest_worker_pos(unit, ai)
        return ("move", worker_pos if worker_pos is not None else harass_anchor)

    def _nearest_worker_pos(self, unit: Any, ai: Any) -> Any:
        """fight 无空中目标时找最近敌农民位置(在 _WORKER_CHASE_SCAN 内)→ 移过去带进抬射程。

        农民移速慢、凤凰移速 5.95 快追得上;带进 LIFT_SCAN_RANGE 后下一 tick Gate B 抬它杀掉。
        没有(农民被杀光/藏起)→ None,调用方回退矿后锚点。
        """
        try:
            workers = ai.enemy_units.filter(
                lambda e: e.type_id in _WORKER_TYPES and e.distance_to(unit) <= _WORKER_CHASE_SCAN
            )
            if workers:
                return workers.closest_to(unit).position
        except Exception:
            pass
        return None

    def _aa_near(self, squad_center: Any, ai: Any) -> bool:
        """squad 附近 _SQUAD_AA_SCAN 内有敌方对空威胁(能打空非农民、非被抬)——判"该不该跑"用。"""
        try:
            return any(
                e.distance_to(squad_center) <= _SQUAD_AA_SCAN
                and e.can_attack_air
                and not e.has_buff(BuffId.GRAVITONBEAM)
                and e.type_id not in _WORKER_TYPES
                for e in ai.enemy_units
            )
        except Exception:
            return False

    def _fight_airborne_target(self, unit: Any, ai: Any) -> Any:
        """fight 阶段可直接开火的目标：附近**飞行**或**被 GRAVITONBEAM 抬起**的敌方单位。

        凤凰武器只打空 → 抬起来的农民/对空 + 敌方空军才能打。取最近的。
        """
        try:
            airborne = ai.enemy_units.filter(
                lambda e: (
                    (getattr(e, "is_flying", False) or e.has_buff(BuffId.GRAVITONBEAM))
                    and not e.is_structure
                    and e.distance_to(unit) <= _FIGHT_AIRBORNE_SCAN
                )
            )
            if airborne:
                return airborne.closest_to(unit)
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # lift 判断（沿用，两条独立 gate）
    # ------------------------------------------------------------------

    def _try_lift(self, unit: Any, phoenixes: Any, ai: Any) -> Any:
        """判断是否应该 lift，若是返回目标 Unit，否则返回 None。

        Gate A — lift 对空兵：威胁对空兵数 <= 能lift凤凰数 × LIFT_GATE_RATIO(0.5)
            确保抬起来后剩余凤凰能打死（边界值算够，鼓励主动抬，rule 5 先清对空）。
        Gate B — lift 农民：周围能lift凤凰数 >= MIN_NEARBY_FOR_LIFT_WORKER(2)
            确保抬起来时有足够 dps 补刀杀农民（Gate A fail 时才走）。
        """
        try:
            if float(unit.energy) < LIFT_ENERGY:
                return None
        except Exception:
            return None

        try:
            nearby_phoenixes = phoenixes.filter(
                lambda p: (
                    p.distance_to(unit) <= LIFT_SCAN_RANGE
                    and p.is_ready
                    and p.energy >= LIFT_ENERGY
                )
            )
            n_lift = len(nearby_phoenixes)
        except Exception:
            return None

        if n_lift == 0:
            return None

        try:
            threat_air = ai.enemy_units.filter(
                lambda e: (
                    e.distance_to(unit) <= ANTI_AIR_SCAN_RANGE
                    and e.can_attack_air
                    and not e.has_buff(BuffId.GRAVITONBEAM)
                    and not e.is_flying
                    and not e.is_structure
                )
            )
            n_threat = len(threat_air)
        except Exception:
            n_threat = 0
            threat_air = None

        # === Gate A: lift 对空兵（D89 放宽到 1:1，与 _squad_can_fight 同口径）===
        # 抬是**序贯清场**：抬起最高优先级那个(女王)→ 附近凤凰集火打死 → 再抬下一个。所以门只需
        # "有优势"(能 lift 凤凰数 >= 威胁对空数)即抬,不必一次抬完所有(旧 ×0.5 要 2 倍凤凰才抬,
        # 面对女王常年不达标 → 从不抬 → 被磨死 F123)。can_fight 已在上游把"打不打得起"拦住。
        if threat_air and n_threat <= n_lift:
            try:
                return max(
                    threat_air,
                    key=lambda e: (
                        _LIFT_PRIORITY.get(e.type_id, -1)
                        + (1 - e.distance_to(unit) / LIFT_SCAN_RANGE)
                    ),
                )
            except Exception:
                pass

        # === Gate B: lift 农民 ===
        if n_lift < MIN_NEARBY_FOR_LIFT_WORKER:
            return None

        try:
            workers = ai.enemy_units.filter(
                lambda e: e.type_id in _WORKER_TYPES and e.distance_to(unit) <= LIFT_SCAN_RANGE
            )
            if workers:
                return workers.closest_to(unit)
        except Exception:
            pass

        return None

    # ------------------------------------------------------------------
    # 撤退 / 血量
    # ------------------------------------------------------------------

    def _should_bail(self, unit: Any) -> bool:
        """血危 → 回家保命，**recover 看护盾回没回**（不看总血）。

        **神族血不回、只护盾脱战自动回（2026-07-22 trace 抓的卡死 bug 修）**：原来 recover 用
        总血比(血+护盾)/满值 ≥ 0.6。但凤凰冲进去掉了**血**(血永久不回)后，就算护盾回满，
        (残血+满护盾)/满值 也可能永远够不到 0.6 → 该凤凰**永远"撤退中"、永远卡在家不再出击**
        (trace 实测:打一波丢几只后整局躲家里 danchor 80-114 不动)。改成 recover 只看**护盾比**
        ≥ 阈值(护盾才是脱战会回的 buffer)——护盾回满就有 buffer、可再战，血低是永久的、不该卡死。
        """
        if unit.tag in self._bailing:
            if self._shield_ratio(unit) >= self._recover_shield:
                self._bailing.discard(unit.tag)
                return False
            return True
        # D61：护盾掉到阈值就退回回盾(护盾是会回的 buffer，拿它换战果)；或血危兜底(护盾没了血也掉)
        if self._shield_ratio(unit) < self._bail_shield or self._hp_ratio(unit) < self._bail_hp:
            self._bailing.add(unit.tag)
            return True
        return False

    def _hp_ratio(self, unit: Any) -> float:
        """(HP + 护盾) / 满值；取不到时按满血处理（不误撤）。bail 触发用（血护盾都算=真危急）。"""
        try:
            mx = float(unit.health_max) + float(unit.shield_max)
            if mx <= 0:
                return 1.0
            return (float(unit.health) + float(unit.shield)) / mx
        except Exception:
            return 1.0

    def _shield_ratio(self, unit: Any) -> float:
        """护盾 / 护盾满值；取不到时按满盾处理（不卡在撤退态）。recover 用（护盾才会脱战回复）。"""
        try:
            smx = float(unit.shield_max)
            if smx <= 0:
                return 1.0
            return float(unit.shield) / smx
        except Exception:
            return 1.0
