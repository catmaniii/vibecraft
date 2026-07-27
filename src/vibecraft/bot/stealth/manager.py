"""StealthCellManager（WP1 骨架 + WP2 建造链 + WP4 本地产线 + WP5 受击释放）。

职责：
  - 接收 stealth_mine directive payload → 分配 cell_id → 创建 StealthCell(PENDING)
  - 维护 cells dict[int, StealthCell]
  - 提供 stealth_townhall_tags / stealth_worker_tags 属性（所有 cell 并集）
  - on_tick(bot, facade, now) 每帧驱动状态机

WP2 实现（PENDING → BUILDING → MINING）：
  - PENDING: 通过 facade 认领一个 Probe(LLM_CONTROLLED) + 下 Nexus 建造令 → BUILDING
  - BUILDING: 检测 cell.point 附近是否出现 ready NEXUS → 回填 nexus_tag，
              builder 转本地农民（加入 worker_tags），注册 FENCE → MINING
  - MINING: 本地产线（WP4）+ 受击/摧毁检测（WP5）

WP5 实现（MINING → RELEASED / DESTROYED）：
  - 受击检测：stealth Nexus 附近有敌方非农民单位 + on_attack=flee → RELEASED
    → 三件事：① 解除 FENCE（remove_cell 后 stealth_townhall_tags 自动缩小 →
      register_stealth_townhalls 推送更新集合）；② 农民还 role（release_unit_role × 每个
      worker）；③ cell 出局（remove_cell）。
    → 之后 bot DistributeWorkers zone.is_enemys 自动驱赶农民撤到安全矿区，不手写逃散。
  - Nexus 摧毁检测：nexus_tag 不在 bot.structures → DESTROYED（同样三件事）
  - on_attack=hold 时不触发受击释放（硬守场景）

建造复用方案（选 B：直接 facade）：
  manager 直接通过 facade.resolve_selector 取 Probe → facade.set_unit_role(LLM_CONTROLLED)
  → facade.order_probe_build → 记 builder_tag。理由：
    1. 避免 manager→director 循环依赖（Option A 需要 director.submit_directive）；
    2. manager 本身要跟踪 builder_tag 做 BUILDING→MINING 转换，直接控制更清晰；
    3. order_probe_build + LLM_CONTROLLED = 与 director proxy build 路径等价的核心操作。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from vibecraft.bot.facade import UnitRole
from vibecraft.bot.stealth.cell import StealthCell, StealthState

if TYPE_CHECKING:
    from vibecraft.directives.models import StealthMinePayload

logger = logging.getLogger(__name__)

# NEXUS settle 检测半径（tile）。
# 玩家指定锚点到实际建造落点误差通常 < 4 格；设 8 格留余量。
_NEXUS_SETTLE_RADIUS: float = 8.0

# 新孵化农民认领半径（tile）。
# 偷矿 Nexus 出生的农民在 Nexus 附近 idle，设 8 格可覆盖出生点范围。
_PROBE_CLAIM_RADIUS: float = 8.0

# WP5：受击检测半径（tile）。
# 偷矿基地被敌方非农民单位进入此范围时触发 stealth 撤销。
# 设 12 格能在敌人真正到达采矿范围前预警，留出 DistributeWorkers 撤离时间。
_ATTACK_DETECT_RADIUS: float = 12.0

# WP4b：气矿操作半径（tile）。
# Assimilator / vespene geyser 离 Nexus / expansion 中心通常 < 8 格，复用同一半径。
# 偷气搜索半径（tile）：基地的**两个** geyser 通常离 Nexus 中心 ~9-11 格，8.0 只够到 1 个
# → 只建 1 个 assimilator = 最多 3 气，cell 卡在 16 矿+3 气=19 到不了 16+6=22（真机定位）。
# 12.0 能罩住同基地两个 geyser，又够不到邻近基地（expansion 间距 >20 格），不会误抓。
_GAS_RADIUS: float = 12.0

# 死亡判定 grace（游戏秒）：农民连续从 bot.units 消失超过此值才真判死。采气农民钻进
# assimilator 暂时消失约 1-2 秒；新孵化农民出生那帧也短暂不在 cache。4 秒留足余量，
# 既不误删采气农民/新生农民，真死亡也只晚 4 秒清理（可接受）。
_DEAD_GRACE_S: float = 4.0

# 偷气 builder gate 超时（游戏秒）：派农民去建 assimilator 后，若 N 秒内没建成（建造令被
# cache-miss 丢弃 / 被拒），释放 gate 重派。assim 一旦真开建，geyser 被占、find_stealth_geysers
# 自动排除 → 不会重复建；故超时只在"建造令没生效"时触发重试。6 秒够分辨"建造令丢了"。
_GAS_BUILD_TIMEOUT_S: float = 6.0

# 偷气开闸最小矿工数（2026-06-13 矿优先+跟随主经济）：
# 矿工数须达到 min(_GAS_MIN_WORKERS, mineral_ideal × 75%) 才允许开气。
# 12 = 半个矿（2 矿石 × 2 工人 × 3 = 12 ideal 的 75%）；确保矿口有足够人力再分一批去采气。
_GAS_MIN_WORKERS: int = 12


def _main_economy_has_gas(bot: Any) -> bool:
    """主经济当前有在采气：已有 ready 气矿建筑（assimilator / refinery / extractor）→ True。

    判断依据：bot.gas_buildings.ready 非空 = bot 进入采气阶段。保守策略：查不到时返回 True
    （不误阻塞偷矿），与"主经济不采气时偷矿也不开气"的方向一致——宁多开一点也不死锁。

    两条路径：
    1. Test hook：bot._main_economy_has_gas() → bool（单测注入，精确控制）。
    2. 生产路径：bool(bot.gas_buildings.ready)（python-sc2 / ares-sc2 标准属性；
       ASSIMILATOR / REFINERY / EXTRACTOR 统一通过 bot.gas_buildings 暴露）。

    玩家级优先采矿信号将来从这里接入（如 workers=stop 指令或专用 gas_hold 开关）。
    """
    if bot is None:
        return True  # 无 bot（边缘场景）→ 保守不阻塞
    hook = getattr(bot, "_main_economy_has_gas", None)
    if hook is not None:
        return bool(hook())
    try:
        return bool(bot.gas_buildings.ready)
    except Exception as exc:
        logger.debug("_main_economy_has_gas fail: %s", exc)
        return True  # 查不到时保守不阻塞


def _gas_gate_open(
    cell: StealthCell,
    bot: Any,
    mineral_ideal: int,
    mineral_workers: int,
) -> bool:
    """偷气开闸判定（矿优先 + 跟随主经济，2026-06-13）。

    规则（建 assimilator + 派气工都受此门）：
      1. 矿工数 ≥ min(_GAS_MIN_WORKERS, mineral_ideal × 75%)  ← 矿口优先填
      2. 主经济当前有 ready 气矿建筑                          ← 跟随 bot 策略，不抢先开气

    例外：矿位已饱和（mineral_workers ≥ mineral_ideal）→ 无条件开闸——矿没地方派了，
    剩余农民应去采气而非空转。

    玩家级优先采矿信号将来从这里接入（如 workers=stop 或专用 gas_hold 指令）。
    """
    # 例外：矿已饱和 → 无条件开（矿没地方派）
    if mineral_workers >= mineral_ideal:
        return True
    # 矿口还有空位：先看矿工数阈值
    threshold = min(_GAS_MIN_WORKERS, int(mineral_ideal * 0.75))
    if mineral_workers < threshold:
        return False
    # 矿工数达阈值：看主经济是否已在采气（跟随 bot 策略，不抢先开气）
    return _main_economy_has_gas(bot)


def _is_tag_alive(bot: Any, tag: int) -> bool:
    """检查 unit tag 是否仍存活。

    两条路径：
    1. Test hook：若 bot 带有 ``_is_unit_alive(tag) -> bool`` 方法，直接调用。
    2. 生产路径：``tag in bot.units.tags``（sc2 Units 集合的 tags 属性）。
    """
    hook = getattr(bot, "_is_unit_alive", None)
    if hook is not None:
        return hook(tag)
    try:
        return tag in bot.units.tags
    except Exception:
        return True  # 无法判断时保守假设存活，避免误删


def _find_unclaimed_probes_near(
    bot: Any,
    point: tuple[float, float],
    radius: float,
    exclude_tags: set[int],
) -> list[int]:
    """在 bot.workers 里找距 point < radius、不在 exclude_tags、且 **idle** 的 Probe tag 列表。

    两条路径：
    1. Test hook：若 bot 带有 ``_find_nearby_probes(point, radius, exclude_tags) -> list[int]``
       方法，直接调用（单测 mock 用）。
    2. 生产路径：遍历 bot.workers，距离过滤 + 排除 exclude_tags + **只取 idle**。

    **只取 idle（2026-06-10 长局自验定位）**：偷矿 Nexus 新孵化的农民是 idle（没下采矿令
    前），而 bot 自己在矿区采矿的工人是 gathering（非 idle）。若不过滤 idle，当 snapped
    expansion 撞上 bot 自己的分矿时，会把 bot 正在采矿的工人也认领进来（实测一个矿被堆 37
    工人、主矿被抽到 4）。只认领 idle → 只收自己孵化的，不偷 bot 的工人。
    """
    if bot is None:
        return []
    hook = getattr(bot, "_find_nearby_probes", None)
    if hook is not None:
        return hook(point, radius, exclude_tags)
    try:
        from sc2.position import Point2

        p2 = Point2(point)
        result = []
        for w in bot.workers:
            if w.tag in exclude_tags:
                continue
            if not getattr(w, "is_idle", False):
                continue  # 只认领 idle（新孵化），不偷 bot 正在采矿的工人
            if w.distance_to(p2) < radius:
                result.append(int(w.tag))
        return result
    except Exception as exc:
        logger.debug("_find_unclaimed_probes_near fail: %s", exc)
    return []


def _find_ready_nexus_near(bot: Any, point: tuple[float, float], radius: float) -> int | None:
    """在 bot.structures 里查找已 settle（ready）的 NEXUS，距 point < radius。

    返回 tag（int）；未找到或异常返回 None。

    两条路径：
    1. Test-friendly hook：若 bot 带有 ``_find_nearby_nexus(point, radius) -> int | None``
       方法，直接调用（单测 mock 用，不依赖 sc2 类型）。
    2. 生产路径：用真实 sc2 UnitTypeId.NEXUS + bot.structures(...).ready 遍历。
    """
    if bot is None:
        return None
    # 单测 hook：mock bot 可提供此方法，绕开 sc2 导入
    hook = getattr(bot, "_find_nearby_nexus", None)
    if hook is not None:
        return hook(point, radius)
    # 生产路径
    try:
        from sc2.ids.unit_typeid import UnitTypeId
        from sc2.position import Point2

        p2 = Point2(point)
        for s in bot.structures(UnitTypeId.NEXUS).ready:
            if s.distance_to(p2) < radius:
                return int(s.tag)
    except Exception as exc:
        logger.debug("_find_ready_nexus_near fail: %s", exc)
    return None


def _any_nexus_near(bot: Any, point: tuple[float, float], radius: float) -> bool:
    """point 半径内是否有**任意状态**（建造中 or ready）的己方 NEXUS。

    用于 BUILDING：一旦 builder 把 Nexus 放下（开始 warp-in），就有一个 pending Nexus
    在这——此时**绝不能再发建造令**（否则 idle 的 builder 会去建第二个）。

    两条路径：test hook `_any_nexus_near(point, radius)` / 生产 bot.structures(NEXUS)。
    """
    if bot is None:
        return False
    hook = getattr(bot, "_any_nexus_near", None)
    if hook is not None:
        return hook(point, radius)
    try:
        from sc2.ids.unit_typeid import UnitTypeId
        from sc2.position import Point2

        p2 = Point2(point)
        for s in bot.structures(UnitTypeId.NEXUS):
            if s.distance_to(p2) < radius:
                return True
    except Exception as exc:
        logger.debug("_any_nexus_near fail: %s", exc)
    return False


def _townhall_assigned(bot: Any, nexus_tag: int) -> int:
    """诊断用：偷矿 Nexus 上 bot 视角的采矿农民数（assigned_harvesters）。

    > cell 自产农民数 = 有主矿农民倒灌进来（FENCE 漏）。
    test hook `_townhall_assigned(tag)` / 生产 structures.find_by_tag(tag).assigned_harvesters。
    """
    if bot is None:
        return -1
    hook = getattr(bot, "_townhall_assigned", None)
    if hook is not None:
        return hook(nexus_tag)
    try:
        s = bot.structures.find_by_tag(nexus_tag)
        return int(s.assigned_harvesters) if s is not None else -1
    except Exception:
        return -1


def _townhall_mineral_ideal(bot: Any, nexus_tag: int) -> int:
    """偷矿 Nexus 的采矿 ideal_harvesters（= 矿点数×2，矿采空自动降到 0）。

    采矿农民封顶用它，矿枯竭时 cap 自动刷新（跟 SC2 ideal 机制走）。
    test hook `_townhall_ideal(tag)` / 生产 structures.find_by_tag(tag).ideal_harvesters。
    返回 -1 表示查不到（调用方退回 cell.worker_target 作 fallback）。
    """
    if bot is None:
        return -1
    hook = getattr(bot, "_townhall_ideal", None)
    if hook is not None:
        return hook(nexus_tag)
    try:
        s = bot.structures.find_by_tag(nexus_tag)
        return int(s.ideal_harvesters) if s is not None else -1
    except Exception:
        return -1


def _enemy_near(bot: Any, point: tuple[float, float], radius: float) -> bool:
    """检查 point 附近 radius 内是否有敌方非农民单位。

    排除农民类型（PROBE / SCV / DRONE）：对方农民路过偷矿点不触发撤销，
    只有真正的攻击单位才算。

    两条路径：
    1. Test hook：若 bot 带有 ``_enemy_near(point, radius) -> bool`` 方法，直接调用。
    2. 生产路径：遍历 bot.enemy_units，排除 PROBE/SCV/DRONE，检查距离 < radius。
    """
    if bot is None:
        return False
    hook = getattr(bot, "_enemy_near", None)
    if hook is not None:
        return hook(point, radius)
    try:
        from sc2.ids.unit_typeid import UnitTypeId
        from sc2.position import Point2

        _worker_types = {UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE}
        p2 = Point2(point)
        for u in bot.enemy_units:
            if u.type_id in _worker_types:
                continue
            if u.distance_to(p2) < radius:
                return True
    except Exception as exc:
        logger.debug("_enemy_near fail: %s", exc)
    return False


def _is_structure_alive(bot: Any, tag: int) -> bool:
    """检查建筑 tag 是否仍存活（在 bot.structures 中）。

    两条路径：
    1. Test hook：若 bot 带有 ``_is_structure_alive(tag) -> bool`` 方法，直接调用。
    2. 生产路径：``tag in bot.structures.tags``。
    """
    if bot is None:
        return True
    hook = getattr(bot, "_is_structure_alive", None)
    if hook is not None:
        return hook(tag)
    try:
        return tag in bot.structures.tags
    except Exception:
        return True  # 无法判断时保守假设存活，避免误删


class StealthCellManager:
    """偷矿 cell 生命周期管理器。

    Director 在 __init__ 里创建一个实例，并在 on_tick 每帧驱动。
    每个偷矿 cell 独立跑自己的状态机。
    """

    def __init__(self) -> None:
        self.cells: dict[int, StealthCell] = {}
        self._next_cell_id: int = 1  # 从 1 开始自增
        self._chrono_reserved_registered: set[int] = set()  # 上次注册的星空预留集合（防每帧调）
        self._stealth_workers_registered: set[int] = set()  # 上次注册的偷矿农民集合（防每帧调）
        self._townhalls_registered: set[int] = set()  # 上次注册的 FENCE 集（Nexus+气矿，防每帧调）
        # 上次注册的"在建偷矿基地数"。初始 0 而非 -1：SNS 字段默认就是 0，
        # 首帧注册一次 0 纯属多余,还污染"parse error 零副作用"类断言
        # （test_director::TestParseErrorIsNoop,2026-06-12 踩坑）。
        self._pending_registered: int = 0
        # cell 被 release/destroy 时 append event dict，由 director on_tick 后 drain
        # dict 字段: cell_id / reason / location / state
        self.pending_release_events: list[dict] = []

    # ------------------------------------------------------------------
    # 创建 / 删除
    # ------------------------------------------------------------------

    def create_cell(self, payload: StealthMinePayload) -> int:
        """为一条 stealth_mine directive 分配 cell_id，创建 PENDING 状态 cell，返回 cell_id。

        注意：payload.cell_id 是 pydantic BaseModel（immutable 且 extra=forbid），
        本方法不尝试回写 payload，调用方从返回值拿 cell_id。
        """
        cell_id = self._next_cell_id
        self._next_cell_id += 1

        cell = StealthCell(
            cell_id=cell_id,
            point=payload.point,
            state=StealthState.PENDING,
            worker_target=payload.worker_target,
            with_gas=payload.with_gas,
            on_attack=payload.on_attack,
        )
        self.cells[cell_id] = cell
        logger.info(
            "STEALTHTRACE cell_created cell_id=%d point=(%.1f,%.1f) worker_target=%d",
            cell_id,
            payload.point[0],
            payload.point[1],
            payload.worker_target,
        )
        return cell_id

    def remove_cell(self, cell_id: int) -> None:
        """从 cells 删除 cell（RELEASED / DESTROYED 时调用）。"""
        if cell_id in self.cells:
            self.cells.pop(cell_id)
            logger.info("STEALTHTRACE cell_removed cell_id=%d", cell_id)

    # ------------------------------------------------------------------
    # 属性：所有 cell 并集
    # ------------------------------------------------------------------

    @property
    def stealth_townhall_tags(self) -> set[int]:
        """所有 cell 的「偷矿建筑」tag 并集 —— **含 Nexus + assimilator（气矿）**。

        2026-06-12 修倒灌：原来只含 Nexus tag，导致偷矿 assimilator 没被 FENCE 排除 →
        进了 DistributeWorkers 的 work_queue（有空采气位）→ 主矿农民被派去偷矿基地采气
        （真机 381 次倒灌，诊断 24/26 是 ASSIMILATOR）。并入 cell.gas_tags 同时修两处：

          - DistributeWorkers FENCE patch（防倒灌：Nexus 矿口 + assimilator 气位都不进 work_queue）
          - Expand.execute vendor hook（stealth 基地不计入自然扩张账；assim tag 不匹配 zone
            townhall，对 Expand 无副作用）
          - ActUnit.is_done/builders（主矿满采账目排除偷矿 Nexus + 气矿，否则偷矿 6 个气位被
            算进主矿 ideal_harvesters → 主矿 is_done 永不满足 → 过量造农民 → 多的又去倒灌）
        """
        result: set[int] = set()
        for cell in self.cells.values():
            if cell.nexus_tag is not None:
                result.add(cell.nexus_tag)
            result |= (
                cell.gas_tags
            )  # 偷矿 assimilator 也排除（防主矿农民来采气 + is_done 账目分离）
        return result

    @property
    def stealth_worker_tags(self) -> set[int]:
        """所有 cell 的 worker_tags 并集。

        喂给账目分离（主矿 ideal 计算排除这些农民，WP4 实现）。
        """
        result: set[int] = set()
        for cell in self.cells.values():
            result.update(cell.worker_tags)
        return result

    @property
    def stealth_pending_base_count(self) -> int:
        """在建/待建的偷矿 cell 数（PENDING/BUILDING；Nexus 还没 ready，不在 our_zones_with_minerals）。

        2026-06-12 用户：玩家下了偷矿令但偷矿基地还在建（农民在路上 / Nexus 没好）时，bot 也把
        它当一片基地算进基地数 → 延后自己开对应分矿（不开冗余分矿）。MINING（Nexus ready）的
        cell 已被 Expand 的 our_zones_with_minerals 计入，这里只数还没 ready 的，避免重复计数。
        """
        return sum(
            1
            for c in self.cells.values()
            if c.state in (StealthState.PENDING, StealthState.BUILDING)
        )

    # ------------------------------------------------------------------
    # 出生即认领（赶在 DistributeWorkers 抢人前）
    # ------------------------------------------------------------------

    def adopt_newborn(self, unit_tag: int, position: tuple[float, float], facade: Any) -> bool:
        """农民出生回调：若生在某 MINING cell 的 Nexus 旁且该 cell 未满 → 当场认领。

        2026-06-10 长局自验定位的核心修复：偷矿 Nexus 训练的农民出生时是普通 role，
        bot 全局 DistributeWorkers 会**抢先**把它派去主矿采矿（走掉），偷矿 cell 的
        每帧 _tick_mining 认领来不及。必须在 on_unit_created（出生那一刻、早于任何 plan）
        立即标 Reserved + 认领 + 下本地采矿令，DistributeWorkers 再跑就跳过它（Reserved）。

        只认领"生在 stealth Nexus 旁"的农民（主矿产的农民生在主矿旁，不在任何 cell 半径内，
        不会被误收）。已满 target 的 cell 不再收。返回是否认领了。
        """
        px, py = float(position[0]), float(position[1])
        for cell in self.cells.values():
            if cell.state != StealthState.MINING or cell.nexus_tag is None:
                continue
            if len(cell.worker_tags) >= cell.live_total_target:
                continue  # 已满总额（采矿 + 采气），不再收
            if unit_tag in cell.worker_tags:
                return True  # 已属本 cell
            dx, dy = px - cell.point[0], py - cell.point[1]
            if (dx * dx + dy * dy) ** 0.5 < _PROBE_CLAIM_RADIUS:
                facade.set_unit_role(unit_tag, UnitRole.LLM_CONTROLLED)
                cell.worker_tags.add(unit_tag)
                facade.order_worker_gather(unit_tag, cell.point)
                logger.info(
                    "STEALTHTRACE newborn_adopted cell_id=%d tag=%d total=%d",
                    cell.cell_id,
                    unit_tag,
                    len(cell.worker_tags),
                )
                return True
        return False

    # ------------------------------------------------------------------
    # 每 tick 驱动
    # ------------------------------------------------------------------

    def on_tick(self, bot: Any, facade: Any, now: float) -> None:
        """每帧驱动所有 cell 状态机。

        WP2: PENDING → claim Probe + order_probe_build → BUILDING
        WP2: BUILDING → 检测 Nexus settle → 回填 nexus_tag → MINING
        WP4: MINING → 补农民（< worker_target 时 train_probe_at + role=LLM_CONTROLLED）
        WP5: MINING → 检测敌近 → RELEASED（解除 FENCE + 还 role + cell 出局）
        WP5: MINING → 检测 Nexus 被摧毁 → DESTROYED（残余农民释放）
        """
        for cell in list(self.cells.values()):
            try:
                self._tick_cell(cell, bot, facade, now)
            except Exception as exc:
                logger.warning("STEALTHTRACE tick_cell_error cell_id=%d err=%s", cell.cell_id, exc)

        # 星空加速预留：成长期（农民 < live_total_target）的 MINING cell，其 Nexus 能量预留给
        # 自我加速，bot ChronoUnit 不拿它当能量源。满采的不预留 → 能量释放回公共池。
        reserved = {
            cell.nexus_tag
            for cell in self.cells.values()
            if (
                cell.state == StealthState.MINING
                and cell.nexus_tag is not None
                and len(cell.worker_tags) < cell.live_total_target
            )
        }
        # 只在集合变化时调（无偷矿 cell 时恒空 → 不调 → 非偷矿局零 facade 调用）
        if reserved != self._chrono_reserved_registered:
            facade.set_stealth_chrono_reserved(reserved)
            self._chrono_reserved_registered = reserved

        # 注册偷矿农民集合到 SNS（ScoutWorker 等排除它，比 _llm_controlled_tags 稳）。
        _workers = self.stealth_worker_tags
        if _workers != self._stealth_workers_registered:
            facade.register_stealth_workers(_workers)
            self._stealth_workers_registered = set(_workers)

        # 注册 FENCE 集到 SNS（Nexus + 气矿 assimilator tag）。**每 tick 重注册**（2026-06-12 修
        # 倒灌）—— 偷矿 assimilator 是 Nexus 建好之后才陆续建的，settle 那次注册时 gas_tags 还空，
        # 不每帧跟着 gas_tags 增长刷新 → 气矿进不了 FENCE 集 → 主矿农民被派去偷矿基地采气倒灌。
        _th = self.stealth_townhall_tags
        if _th != self._townhalls_registered:
            facade.register_stealth_townhalls(_th)
            self._townhalls_registered = set(_th)

        # 注册"在建偷矿基地数"到 SNS → Expand 把它算进基地数（玩家下了偷矿令 → 延后自己开分矿）。
        _pending = self.stealth_pending_base_count
        if _pending != self._pending_registered:
            facade.register_stealth_pending(_pending)
            self._pending_registered = _pending

    def _release_cell(
        self,
        cell: StealthCell,
        facade: Any,
        reason: str,
        new_state: StealthState,
    ) -> None:
        """撤销 stealth 地位三件事（解除 FENCE + 农民还 role + cell 出局）。

        顺序（先 remove_cell，后 register）：
          1. cell.state = new_state（标记状态）
          2. 农民还 role：facade.release_unit_role(tag) × 每个 worker_tag
             stealth 农民是 manager 直接认领的（只设了 role，没有 directive 卡），
             release_unit_role 即完全交还，无需额外撤销 directive。
          3. remove_cell(cell_id) → self.cells 不再含此 cell
          4. register_stealth_townhalls(self.stealth_townhall_tags) →
             property 在 remove_cell 后已不含该 Nexus → 传出更新（缩小）集合 → 解除 FENCE
          5. log STEALTHTRACE

        之后 bot DistributeWorkers.generate_worker_queue 不再排除该 Nexus →
        zone.is_enemys / needs_evacuation 自动驱赶农民撤到安全矿区，无需手写 move。
        （详见设计 §8 "为什么不用手写逃散"）
        """
        cell.state = new_state
        # 步骤 2：农民还 role（无 directive 卡，release_unit_role 即完全交还）
        for tag in cell.worker_tags:
            facade.release_unit_role(tag)
        # 步骤 3：先 remove_cell，确保 stealth_townhall_tags property 不再含该 Nexus
        self.remove_cell(cell.cell_id)
        # 步骤 4：解除 FENCE，推送已缩小的集合
        facade.register_stealth_townhalls(self.stealth_townhall_tags)
        logger.info(
            "STEALTHTRACE cell_released cell_id=%d nexus_tag=%s worker_count=%d reason=%s state=%s",
            cell.cell_id,
            cell.nexus_tag,
            len(cell.worker_tags),
            reason,
            new_state.value,
        )
        # 通知 director 推 event 给 PWA（director on_tick 后 drain）
        self.pending_release_events.append(
            {
                "cell_id": cell.cell_id,
                "reason": reason,
                "location": [cell.point[0], cell.point[1]],
                "state": new_state.value,
            }
        )

    def _tick_cell(self, cell: StealthCell, bot: Any, facade: Any, now: float) -> None:
        if cell.state == StealthState.PENDING:
            self._tick_pending(cell, facade)
        elif cell.state == StealthState.BUILDING:
            self._tick_building(cell, bot, facade)
        elif cell.state == StealthState.MINING:
            self._tick_mining(cell, bot, facade, now)

    def _tick_pending(self, cell: StealthCell, facade: Any) -> None:
        """PENDING → BUILDING：认领 Probe，下 Nexus 建造令。

        复用代理建造执行路径：
          facade.set_unit_role(probe, LLM_CONTROLLED)   → Probe 不被 DistributeWorkers 拉走
          facade.order_probe_build(probe, "Nexus", pt)  → SC2 下建造令，Probe 自动寻路

        没有可用 Probe 时保持 PENDING，下一帧重试。

        落点吸附（2026-06-10 真机自验定位）：偷矿 Nexus 是采矿基地，玩家点的原始
        坐标常落在无矿/不可建处 → 建造被 SC2 拒（orders_after=[]，Nexus 永远建不成）。
        先把 cell.point 吸附到最近 expansion location（有矿 + 可放 Nexus），再建。
        吸附后 cell.point 即真实 Nexus 落点，下游 settle 检测 / 本地采矿都用它，一致。
        """
        if cell.builder_tag is not None:
            # 已认领 builder，修正状态（防止异常情况下 PENDING cell 带 builder_tag）
            cell.state = StealthState.BUILDING
            return

        # 落点吸附到最近 expansion（只在认领前做一次；幂等）
        if not cell.point_snapped:
            snapped = facade.nearest_expansion(cell.point)
            if snapped is not None:
                if tuple(snapped) != tuple(cell.point):
                    logger.info(
                        "STEALTHTRACE point_snapped cell_id=%d from=(%.1f,%.1f) to=(%.1f,%.1f)",
                        cell.cell_id,
                        cell.point[0],
                        cell.point[1],
                        snapped[0],
                        snapped[1],
                    )
                cell.point = (float(snapped[0]), float(snapped[1]))
            cell.point_snapped = True

        # 从 facade 选一个可用的 Probe
        tags = facade.resolve_selector(unit_type="Probe")
        if not tags:
            logger.debug("STEALTHTRACE no_probe_available cell_id=%d", cell.cell_id)
            return

        probe_tag = tags[0]
        # 认领：设为 LLM_CONTROLLED（= sharpy Reserved），防止 DistributeWorkers 途中拉走
        facade.set_unit_role(probe_tag, UnitRole.LLM_CONTROLLED)
        # 下建造令：cache_key=cell_id 让 find_placement 落点稳定（远程建造每帧重发不抖）
        facade.order_probe_build(probe_tag, "Nexus", cell.point, cache_key=cell.cell_id)

        cell.builder_tag = probe_tag
        cell.state = StealthState.BUILDING
        logger.info(
            "STEALTHTRACE building_started cell_id=%d builder=%d point=(%.1f,%.1f)",
            cell.cell_id,
            probe_tag,
            cell.point[0],
            cell.point[1],
        )

    def _tick_building(self, cell: StealthCell, bot: Any, facade: Any) -> None:
        """BUILDING → MINING：检测 Nexus settle，回填 nexus_tag，注册 FENCE。

        重发建造令的规则（2026-06-11 真机两轮定位后定稿）：
        **每帧重发建造令把 builder 推过去（压过 sharpy 抢人 + 落点缓存失效重找），
        但一旦附近有任意 Nexus 在建（_any_nexus_near）就停止重发**——这样：
        - 远程建造可靠（每帧重发，builder 走半张地图/被打断都能续上，不会卡死）；
        - 第一个 Nexus 放下（warp-in，pending）那一刻起停发 → 只建一个（不再抖出 2-3 个）。
        builder 死/丢且还没 Nexus → 回 PENDING 重新认领续建。
        """
        # 每帧确保 builder 保持 Reserved（走半张地图去建的途中不被 DistributeWorkers 抢回
        # 主矿 → 不会丢 builder 再从主矿认领第 2 个，主矿只出 1 个 founding builder）。
        if cell.builder_tag is not None:
            facade.ensure_units_reserved({cell.builder_tag})

        nexus_tag = _find_ready_nexus_near(bot, cell.point, _NEXUS_SETTLE_RADIUS)
        if nexus_tag is None:
            # 已有 Nexus 在建（pending warp-in）→ 停止重发，只等它 ready（防建第二个）
            if _any_nexus_near(bot, cell.point, _NEXUS_SETTLE_RADIUS):
                return
            # 没有 Nexus 在建：builder 死/丢 → 回 PENDING 重新认领续建
            if cell.builder_tag is None or not _is_tag_alive(bot, cell.builder_tag):
                cell.builder_tag = None
                cell.state = StealthState.PENDING
                return
            # 每帧重发把 builder 推过去（落点 cache_key 稳定；缓存失效时会重找合法位）
            facade.order_probe_build(cell.builder_tag, "Nexus", cell.point, cache_key=cell.cell_id)
            return  # 保持 BUILDING

        # Nexus 已 settle
        cell.nexus_tag = nexus_tag

        # Builder 农民转为本地农民
        # role 已是 LLM_CONTROLLED（PENDING 阶段设置），保持不变（= sharpy Reserved）
        # 加入 worker_tags：由此该农民属于此 cell，WP4 产线计数 + WP5 受击释放均通过此集合管理
        if cell.builder_tag is not None:
            cell.worker_tags.add(cell.builder_tag)

        # 注册 FENCE：stealth_townhall_tags 已含刚回填的 nexus_tag（property 实时计算）
        facade.register_stealth_townhalls(self.stealth_townhall_tags)

        cell.state = StealthState.MINING
        logger.info(
            "STEALTHTRACE mining_started cell_id=%d nexus_tag=%d builder=%d worker_count=%d",
            cell.cell_id,
            nexus_tag,
            cell.builder_tag if cell.builder_tag is not None else -1,
            len(cell.worker_tags),
        )

    def _tick_mining(self, cell: StealthCell, bot: Any, facade: Any, now: float) -> None:
        """MINING 态每帧：受击/摧毁检测（WP5）+ 清理死亡农民 + 认领新孵化农民 + 补农民。

        WP5 受击/摧毁检测（优先，被攻击时直接撤销 stealth 地位，不再执行产线）：
          - Nexus 被摧毁（nexus_tag 不在 bot.structures）→ DESTROYED + _release_cell
          - on_attack=flee + 敌方非农民单位进入 _ATTACK_DETECT_RADIUS → RELEASED + _release_cell
          撤销后 bot DistributeWorkers zone.is_enemys 自动接管逃散，不手写 move（见设计 §8）。
          on_attack=hold 时跳过受击检测（玩家明确硬守）。

        产线步骤（仅在未撤销时执行）：
        1. 清理死亡农民（worker_tags pruning；同步清 gas_worker_tags）。
        2. 认领 Nexus 附近未认领新孵化 Probe：set_unit_role(LLM_CONTROLLED) + 入 worker_tags
           + order_worker_gather(就地采矿)。
        2.5. 气矿（with_gas=True，WP4b）：建 assimilator + 采气饱和（见 _tick_gas）。
        3. 补农民：alive < worker_target 且 nexus 存在 → train_probe_at(nexus_tag)。

        注意：
        - FENCE（Reserved role）让 DistributeWorkers 不自动派这些农民采矿，必须显式下令。
        - gas_worker_tags 是 worker_tags 子集，worker_target 是采矿+采气共用总额。
        """
        # --- WP5：受击/Nexus 摧毁检测（优先于产线，被攻击就别再 train）---

        # 检测 Nexus 是否被摧毁
        if cell.nexus_tag is not None and not _is_structure_alive(bot, cell.nexus_tag):
            self._release_cell(cell, facade, reason="destroyed", new_state=StealthState.DESTROYED)
            return

        # 检测敌方非农民单位进入检测半径（on_attack=hold 时不触发）
        if cell.on_attack == "flee" and _enemy_near(bot, cell.point, _ATTACK_DETECT_RADIUS):
            self._release_cell(cell, facade, reason="under_attack", new_state=StealthState.RELEASED)
            return

        # --- 倒灌告警（不变量守卫）：偷矿 Nexus 上 bot 视角采矿农民数若 > cell 自产数，
        # 说明有主矿农民倒灌进来（FENCE 漏）。正常静默，违反才告警（真机自验证实无倒灌：
        # nexus_assigned 始终 ≤ cell_workers）。---
        if cell.nexus_tag is not None:
            _assigned = _townhall_assigned(bot, cell.nexus_tag)
            if _assigned > len(cell.worker_tags):
                logger.warning(
                    "STEALTHTRACE DRAIN_ALARM cell_id=%d nexus_assigned=%d > cell_workers=%d "
                    "（主矿农民倒灌进偷矿基地，FENCE 漏！）",
                    cell.cell_id,
                    _assigned,
                    len(cell.worker_tags),
                )
            # 外流告警：偷矿 Nexus 实际采矿农民 + 采气农民 远少于 cell 农民数 → 农民被拉走了
            _accounted = _assigned + len(cell.gas_worker_tags)
            if _assigned >= 0 and _accounted < len(cell.worker_tags) - 3:
                logger.warning(
                    "STEALTHTRACE OUTFLOW_ALARM cell_id=%d nexus_assigned=%d gas=%d "
                    "cell_workers=%d（偷矿农民被拉走，不在本基地采矿/采气！）",
                    cell.cell_id,
                    _assigned,
                    len(cell.gas_worker_tags),
                    len(cell.worker_tags),
                )

        # --- 防外流：每帧确保本 cell 全部农民 Reserved（防被 DistributeWorkers 拉回主矿）---
        if cell.worker_tags:
            facade.ensure_units_reserved(cell.worker_tags)

        # --- 步骤 1：清理死亡农民（grace-period，2026-06-11）---
        # 采气农民会周期性"钻进"assimilator 暂时从 bot.units 消失（SC2 机制），1 帧就删会
        # 把采气农民当死亡误删 → gas_worker_tags 永远清零、采气补不满、矿口反被超采（真机
        # 峰值卡 16 矿超采+0 气=19，到不了 16+6=22）。改为：连续消失 _DEAD_GRACE_S 游戏秒才
        # 真判死；tag 重现（钻出来/cache 回来）立即清计时。也顺带兜住新孵化农民出生那帧的
        # 短暂 cache-miss。
        _missing = cell.worker_missing_since
        confirmed_dead: set[int] = set()
        for tag in list(cell.worker_tags):
            if _is_tag_alive(bot, tag):
                _missing.pop(tag, None)  # 又出现了 → 清计时
            else:
                first = _missing.get(tag)
                if first is None:
                    _missing[tag] = now  # 第一帧消失，开始计时
                elif now - first >= _DEAD_GRACE_S:
                    confirmed_dead.add(tag)
        if confirmed_dead:
            cell.worker_tags -= confirmed_dead
            cell.gas_worker_tags -= confirmed_dead  # 同步清采气子集（WP4b）
            for _t in confirmed_dead:
                _missing.pop(_t, None)
            logger.info(
                "STEALTHTRACE worker_died cell_id=%d dead_count=%d remaining=%d",
                cell.cell_id,
                len(confirmed_dead),
                len(cell.worker_tags),
            )
        # 防泄漏：清掉已不在 worker_tags 的计时残留
        if _missing:
            for _t in [t for t in _missing if t not in cell.worker_tags]:
                _missing.pop(_t, None)

        # --- 动态双 cap（2026-06-11 用户）：采矿封顶 = Nexus 实时 ideal_harvesters
        # （矿点数×2，采空自动降）；采气封顶 = assimilator 实时 ideal 之和（3/口，采空变 0）。
        # 总额 = 采矿 + 采气（16+6=22），矿/气枯竭时两个 cap 都跟 SC2 ideal 自动刷新。---
        mineral_target = _townhall_mineral_ideal(bot, cell.nexus_tag) if cell.nexus_tag else -1
        if mineral_target < 0:
            mineral_target = cell.worker_target  # 查不到 → 退回 payload 值（默认 16）
        gas_buildings = (
            facade.find_stealth_gas_buildings(cell.point, _GAS_RADIUS) if cell.with_gas else []
        )
        gas_target = sum(ideal for _, _, ideal in gas_buildings)
        total_target = mineral_target + gas_target
        cell.live_total_target = total_target  # 供 adopt_newborn（on_unit_created）封顶用

        # --- 步骤 2：认领附近未认领的新孵化农民（封顶到 total_target，防超额堆矿）---
        if cell.nexus_tag is not None:
            slots_left = total_target - len(cell.worker_tags)
            if slots_left > 0:
                all_stealth_tags = self.stealth_worker_tags  # 含本 cell 已有农民
                unclaimed = _find_unclaimed_probes_near(
                    bot, cell.point, _PROBE_CLAIM_RADIUS, all_stealth_tags
                )
                for probe_tag in unclaimed[:slots_left]:
                    facade.set_unit_role(probe_tag, UnitRole.LLM_CONTROLLED)
                    cell.worker_tags.add(probe_tag)
                    facade.order_worker_gather(probe_tag, cell.point)
                    logger.info(
                        "STEALTHTRACE worker_claimed cell_id=%d tag=%d total=%d",
                        cell.cell_id,
                        probe_tag,
                        len(cell.worker_tags),
                    )

        # --- 步骤 2.5：气矿（WP4b）矿优先+跟随主经济门控（2026-06-13）---
        # 原逻辑：settle 后很快派农民建 assimilator + 派气工，矿工还少时气优先。
        # 新逻辑：先填矿口（矿工 ≥ threshold），且主经济自己也已在采气，才允许偷矿开气。
        # 门控范围：建 assimilator + 派气工都受此闸；矿位饱和时无条件开（矿没地方派）。
        if cell.with_gas:
            _mw = len(cell.worker_tags) - len(cell.gas_worker_tags)  # 当前纯采矿农民数
            if _gas_gate_open(cell, bot, mineral_target, _mw):
                if not cell.gas_gate_opened:
                    cell.gas_gate_opened = True
                    logger.info(
                        "STEALTHTRACE gas_gate_open cell_id=%d mineral_workers=%d mineral_ideal=%d",
                        cell.cell_id,
                        _mw,
                        mineral_target,
                    )
                self._tick_gas(cell, bot, facade, gas_buildings, now)
            else:
                _thr = min(_GAS_MIN_WORKERS, int(mineral_target * 0.75))
                _reason = "few_mineral_workers" if _mw < _thr else "main_economy_no_gas"
                logger.debug(
                    "STEALTHTRACE gas_gate_hold cell_id=%d reason=%s "
                    "mineral_workers=%d threshold=%d mineral_ideal=%d",
                    cell.cell_id,
                    _reason,
                    _mw,
                    _thr,
                    mineral_target,
                )

        # --- 步骤 3：补农民到 total_target（采矿 + 采气总额）---
        alive_count = len(cell.worker_tags)
        if alive_count < total_target and cell.nexus_tag is not None:
            trained = facade.train_probe_at(cell.nexus_tag)
            if trained:
                logger.info(
                    "STEALTHTRACE train_initiated cell_id=%d nexus=%d alive=%d "
                    "mineral_target=%d gas_target=%d",
                    cell.cell_id,
                    cell.nexus_tag,
                    alive_count,
                    mineral_target,
                    gas_target,
                )

        # --- 步骤 4：星空加速（2026-06-11 用户）—— 放在 train 之后：偷矿基地**成长期**
        # 用自己能量给自己加速产农民（此时 Nexus 正在产农民，chrono 才有意义）。满采
        # （农民 ≥ total_target）后停止 → 能量留给 bot 公共 chrono 池（家里科技/建筑用）。---
        if cell.nexus_tag is not None and len(cell.worker_tags) < total_target:
            facade.cast_chrono_on_nexus(cell.nexus_tag)

    def _tick_gas(
        self,
        cell: StealthCell,
        bot: Any,
        facade: Any,
        gas_buildings: list[tuple[int, int, int]],
        now: float,
    ) -> None:
        """WP4b 偷气：建 assimilator + 把采气农民派满到 gas_target（采气封顶 = assimilator
        实时 ideal 之和，3/口，采空变 0 → cap 自动刷新）。

        步骤 A：建 assimilator —— **一次只建一个**（gas_builder_tag 跟踪 in-flight build）。
               in-flight 时不再派工，避免 assim 在路上（gas_buildings 还查不到）时每帧又派
               一个农民去建同一个 → 农民被反复抽走 / 路上阵亡 → cell 长不起来（真机定位）。
        步骤 B：对每个 deficit（ideal > assigned）的 ready assimilator，从 worker_tags 里
               挑未在 gas_worker_tags 中的农民派采气令，加入 gas_worker_tags（防矿/气抖动）。

        gas_buildings 由 _tick_mining 预查好传入（(tag, assigned, ideal) 列表），避免重复查询。
        """
        ready_count = len(gas_buildings)

        # 步骤 A：建 assimilator（一次一个，gas_builder_tag 门控）
        # gate 释放条件（2026-06-12 修 231 次 churn）：**只**在 (a) assim 建好 或 (b) 超时
        # 没建成 时释放。**不再**因 builder 单帧 cache-miss（采气农民钻进 assim 暂时不在
        # bot.units）就释放 —— 那会每帧重派、231 次 churn + 大量建造令 cache-miss 丢弃。
        if cell.gas_builder_tag is not None:
            if ready_count > cell.gas_ready_baseline:
                # 新 assim 已建好 → 释放 builder + 让它回去采矿（否则建完 idle 站着，浪费 1 农民）
                if cell.gas_builder_tag not in cell.gas_worker_tags:
                    facade.order_worker_gather(cell.gas_builder_tag, cell.point)
                cell.gas_builder_tag = None
            elif now - cell.gas_builder_since >= _GAS_BUILD_TIMEOUT_S:
                # 超时没建成（建造令被 cache-miss 丢 / 被拒）→ 释放重派。assim 真开建后 geyser
                # 被占、find_stealth_geysers 自动排除 → 不会重复建；故超时只在"没生效"时重试。
                cell.gas_builder_tag = None
        # 没有 in-flight build 且还要更多气矿 → 派一个**确认在 cache 的非采气**农民去建
        # （旧 next(iter(worker_tags)) 可能选到正钻在 assim 里的采气农民 → order 时 cache-miss
        #  丢建造令；这里筛掉采气农民 + 要求 _is_tag_alive，保证建造令真发得出去）。
        if cell.gas_builder_tag is None and ready_count < 2:
            geysers = facade.find_stealth_geysers(cell.point, _GAS_RADIUS)
            if geysers:
                candidates = [
                    w
                    for w in cell.worker_tags
                    if w not in cell.gas_worker_tags and _is_tag_alive(bot, w)
                ]
                if candidates:
                    builder_tag = candidates[0]
                    geyser_tag = geysers[0][0]
                    facade.order_probe_build_gas(builder_tag, geyser_tag)
                    cell.gas_builder_tag = builder_tag
                    cell.gas_builder_since = now
                    cell.gas_ready_baseline = ready_count
                    logger.info(
                        "STEALTHTRACE gas_build_started cell_id=%d builder=%d geyser=%d",
                        cell.cell_id,
                        builder_tag,
                        geyser_tag,
                    )

        # 步骤 B：采气饱和（**count-capped**，2026-06-12 修 gas_worker_tags 膨胀到 22）。
        # 旧逻辑按 deficit=ideal-assigned 派工：assim 被 FENCE 排除后没了 DistributeWorkers 帮填，
        # 若 assigned 读数滞后/采气农民周期性钻进 assim，deficit 一直 >0 → 每帧把不同农民塞进
        # gas_worker_tags、永不收敛 → 全部 22 个被标成采气、矿口反被挤超采。改成**按总气位
        # gas_cap 封顶**补员，并每帧把登记的采气农民重新"焊"在气上（防它们漂回矿口）。
        for gas_tag, _assigned, _ideal in gas_buildings:
            cell.gas_tags.add(gas_tag)
        cell.gas_worker_tags &= cell.worker_tags  # 清掉已死/不属本 cell 的残留
        gas_cap = sum(ideal for _, _, ideal in gas_buildings)
        if gas_cap <= 0 or not gas_buildings:
            return
        # 超额（气矿采空 ideal 降）→ 多的踢回采矿
        if len(cell.gas_worker_tags) > gas_cap:
            excess = len(cell.gas_worker_tags) - gas_cap
            for worker_tag in list(cell.gas_worker_tags)[:excess]:
                cell.gas_worker_tags.discard(worker_tag)
                facade.order_worker_gather(worker_tag, cell.point)

        # 待派农民 = 漂走的（重焊，已登记但实际在采矿/idle）+ 未登记的（补到 gas_cap）。
        _drifted = [t for t in cell.gas_worker_tags if facade.gas_worker_drifted(t, cell.gas_tags)]
        _need = max(0, gas_cap - len(cell.gas_worker_tags))
        _fresh = [w for w in cell.worker_tags if w not in cell.gas_worker_tags][:_need]
        _to_assign = _drifted + _fresh
        if not _to_assign:
            return
        # **按每个 assim 的缺口建 slot 队列**（2026-06-12 修 6 个堆一个气矿）：缺口大的 assim 先填，
        # 自然均分到两个气矿各 3。旧逻辑 round-robin 用本帧 index → 总偏向第一个 assim（真机
        # 12 vs 3）→ 超 3 槽的农民挤不进、漂回矿口。用引擎 assigned 算缺口，总数仍受 gas_cap 约束。
        slots: list[int] = []
        for gas_tag, assigned, ideal in sorted(
            gas_buildings, key=lambda g: g[2] - g[1], reverse=True
        ):
            slots.extend([gas_tag] * max(0, ideal - assigned))
        if not slots:  # 引擎都报满但仍有漂走的 → 兜底轮流
            slots = [g[0] for g in gas_buildings]
        for i, worker_tag in enumerate(_to_assign):
            facade.order_worker_gather_gas(worker_tag, slots[i % len(slots)])
            cell.gas_worker_tags.add(worker_tag)
        logger.info(
            "STEALTHTRACE gas_assign cell_id=%d drifted=%d fresh=%d cap=%d slots=%d",
            cell.cell_id,
            len(_drifted),
            len(_fresh),
            gas_cap,
            len(slots),
        )
