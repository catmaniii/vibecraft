"""前线野水晶支援：派 1 农民兼做"探路 + 保命 + 隐蔽地点修 BE + 野生产建筑"。

设计目标
========
- proxy 点尽可能离敌方主基地近（折跃 timing 短），但不被敌方视野看到
- 候选点：敌方主基地外环 + 中线偏敌方侧
- **anchor 走廊安全评分**：选偏离敌军必经路最远的 anchor（不 random，复用人族
  proxy_rax 验证有效的评分）
- **落点由地形规划器离线算好**（`placement_planner.plan_building_cluster`）——
  农民只去**执行**缓存落点，不现场 find_placement 试探（#590 / #543 根因）

神族关键差异：先水晶，生产建筑在能量场内（2026-07-07）
=====================================================
神族偷家 = **先建 1 Pylon，再建 N 个生产建筑（Gateway/Stargate），它们必须在
Pylon 能量场半径 6.5 内**。规划分两步：

1. **规划 Pylon 落点（upfront）**：`plan_building_cluster(anchor, PYLON, 1,
   footprint=2)`。Pylon 不需要能量、`can_place` 直接可查 → 开局就能规划好。
2. **规划 N 个生产建筑（Pylon ready 后）**：`plan_building_cluster(pylon_pos,
   GATEWAY/STARGATE, N, power_source=pylon_pos, power_radius=6.5, footprint=3)`。
   **关键 timing**：神族生产建筑 `can_place` 需要**真实能量**——Pylon 没建好前
   `can_place(GATEWAY)` 恒 False。所以生产建筑落点必须**等 Pylon ready（真能量
   到位）后**才规划，一次锁定缓存（同 `_assign_chain_followup_spots` 约定）。

probe **只执行缓存落点**：先建 Pylon（缓存点），Pylon ready 后依次建 N 个生产
建筑（缓存点）。删掉主路径的现场 find_placement（仅规划失败时兜底）。

完成判定（OR）
==============
A. Pylon ever ready + 已见 N 个前线生产建筑（含在建）
B. 超时 150s
C. worker 死亡 ≥ 4 次（不再砸农民）
D. 主力已出门（combat_intent_override == "attack"）

pylon-only 模式（dt_rush，production_count=0）：Pylon ready / destroyed 即完成。
"""

from __future__ import annotations

import contextlib
import logging
import math
import random
from typing import Any, cast

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

from vibecraft.bot.placement_planner import plan_building_cluster

logger = logging.getLogger(__name__)

# proxy 周围检测半径（forward 建筑必须在 proxy 这个范围内）
_PROXY_R: float = 30.0
# 自家所有 Nexus 这个距离内的建筑视为"家里"，不算 forward
_MIN_HOME_DIST: float = 25.0
# 距离敌方主基地的硬下限
_MIN_DIST_TO_ENEMY: float = 30.0
# 距离敌方主基地的硬上限
_MAX_DIST_TO_ENEMY: float = 60.0
# 距离敌方 natural(二基地)的硬下限
_MIN_DIST_TO_ENEMY_NATURAL: float = 22.0
# 任务超时（秒，游戏内）
_TASK_TIMEOUT_S: float = 150.0
# 选点后卡这么久 PYLON 还是 none（worker 没把水晶修起来）→ 换点 + 换 worker
_REPICK_NO_PROGRESS_S: float = 25.0
# 生产建筑建造无进展这么久 → 从规划落点降级到 find_placement 兜底
_PROD_PATIENCE_S: float = 40.0
# 选点时对分数最高的前 N 个候选逐个查寻路可达性 / 取 top-N anchor 规划
_REACH_CHECK_TOP_N: int = 8
# worker 死亡次数上限（再多就放弃）
_MAX_WORKER_DEATHS: int = 4
# 建造失败换 anchor 重规划的最大次数（防死循环）
_MAX_REPLAN: int = 3
# HP+shield 撤退/复出阈值
_RETREAT_RATIO: float = 0.5
_REENGAGE_RATIO: float = 0.9

# Pylon footprint 2×2（half=1）；生产建筑 3×3（half=1，footprint 参数语义见 planner）
_PYLON_FOOTPRINT: int = 2
_PROD_FOOTPRINT: int = 3
# Pylon 能量场半径（LotV）
_POWER_RADIUS: float = 6.5

# 建筑 type → 对应 BUILD AbilityId（识别 worker 是否在 ordering 该建筑）
_BUILD_ABILITIES: dict[UnitTypeId, AbilityId] = {
    UnitTypeId.PYLON: AbilityId.PROTOSSBUILD_PYLON,
    UnitTypeId.GATEWAY: AbilityId.PROTOSSBUILD_GATEWAY,
    UnitTypeId.STARGATE: AbilityId.PROTOSSBUILD_STARGATE,
}


class ForwardSupportPylonGateway(ActBase):  # type: ignore[misc]
    """前线野水晶支援：1 农民到敌方家门口隐蔽点修建，保命优先，必要时牺牲。

    - 默认（4bg）：修 1 BE + 1 野 BG（planner 规划落点）。
    - build_gateway=False（dt_rush 速隐刀）：只修 1 BE，不修野生产建筑。
    - production_type=STARGATE, production_count=2（野 2VS 变体）：修 1 BE + 2 野 VS。

    落点全部由 `placement_planner.plan_building_cluster` 离线规划，probe 只执行缓存
    落点（先 Pylon，Pylon ready 后依次建生产建筑，全部约束在能量场内）。
    """

    def __init__(
        self,
        build_gateway: bool = True,
        production_type: UnitTypeId | None = None,
        production_count: int | None = None,
    ) -> None:
        super().__init__()
        # 生产建筑类型 + 数量（None → 由 build_gateway 推默认：True=1 BG，False=纯水晶）
        self.build_gateway = build_gateway
        self.production_type: UnitTypeId = production_type or UnitTypeId.GATEWAY
        if production_count is None:
            production_count = 1 if build_gateway else 0
        self.production_count: int = production_count

        self.proxy_worker_tag: int | None = None
        self.proxy_location: Point2 | None = None  # anchor
        self.hide_location: Point2 | None = None  # retreat 时躲的位置
        self._completed: bool = False
        self.retreating: bool = False

        # ---- planner 缓存落点（一次锁定，绝不每帧重选，#543）----
        self._planning_done: bool = False  # 防每帧重跑 anchor+pylon 规划
        self._pylon_spot: Point2 | None = None  # 规划好的 Pylon 落点
        self._prod_spots: list[Point2] | None = None  # 规划好的生产建筑落点（pylon ready 后算）
        self._prod_fallback: bool = False  # 生产建筑规划失败 → find_placement 兜底
        self._locked_spot: Point2 | None = None  # 当前正在建的那栋（本 idx）锁定落点
        self._locked_idx: int | None = None
        self._locked_since: float | None = None

        # ---- pylon / 生产建筑 世界态跟踪 ----
        self._pylon_tag: int | None = None
        self._pylon_ever_ready: bool = False
        self._prod_hwm: int = 0  # 已见前线生产建筑数的高水位（含在建；destroy 也算过）
        self._prod_settled_logged: set[int] = set()

        # ---- worker 死亡 / 任务计时 ----
        self._worker_death_count: int = 0
        self._start_time: float | None = None
        self._proxy_set_time: float | None = None

        # ---- 换 anchor 重规划记账 ----
        self._failed_anchors: list[Point2] = []
        self._replan_count: int = 0

    async def start(self, knowledge: Any) -> None:
        await super().start(knowledge)
        self.proxy_location = None
        self.hide_location = None
        self._proxy_set_time = None

    # ==================================================================
    # anchor 选点 + Pylon 规划（upfront）
    # ==================================================================

    async def _pick_proxy_and_plan(self) -> tuple[Point2, Point2] | None:
        """选 anchor（走廊安全评分，不 random）并规划 1 个 Pylon 落点。

        返回 (anchor, pylon_spot) 或 None（全失败）。Pylon 不需要能量，`can_place`
        直接可查 → 开局即可规划。生产建筑落点延后到 Pylon ready 后再规划（需能量）。
        """
        candidates = self._generate_candidates()
        if not candidates:
            return None
        scored = [(p, self._score_pos(p)) for p in candidates]
        scored = [(p, s) for p, s in scored if s > 0]
        if not scored:
            logger.info("ForwardSupport planner: 0 valid candidates after hard filter")
            return None

        # 走廊安全评分（off_attack_axis + off_main_path 越大越安全），降序
        safety = [(p, self._off_attack_axis(p) + self._off_main_path(p)) for p, _ in scored]
        safety.sort(key=lambda x: x[1], reverse=True)
        if self._failed_anchors:
            safety = [
                (p, s)
                for p, s in safety
                if all(p.distance_to(f) > 10.0 for f in self._failed_anchors)
            ]
        top_anchors = [p for p, _ in safety[:_REACH_CHECK_TOP_N]]

        # 全局 flood_fill 一次（所有 anchor 复用）
        reachable_set: Any = None
        try:
            reachable_set = self.ai.game_info.pathing_grid.flood_fill(
                self.ai.start_location.rounded, lambda v: v != 0
            )
        except Exception:
            reachable_set = None

        for anchor in top_anchors:
            try:
                if await self.ai._client.query_pathing(self.ai.start_location, anchor) is None:
                    continue
            except Exception:
                pass
            try:
                spots = await plan_building_cluster(
                    self.ai,
                    anchor,
                    UnitTypeId.PYLON,
                    1,
                    scv_origin=self.ai.start_location,
                    reachable=reachable_set,
                    footprint=_PYLON_FOOTPRINT,
                )
            except Exception as exc:
                logger.debug(
                    "ForwardSupport planner anchor (%.1f,%.1f) pylon fail: %s",
                    anchor.x,
                    anchor.y,
                    exc,
                )
                continue
            if spots:
                return (anchor, spots[0])
        return None

    async def _plan_production(self, pylon_pos: Point2) -> None:
        """Pylon ready 后规划 N 个生产建筑落点（约束在能量场内），一次锁定缓存。

        必须在 Pylon **ready** 后调用：神族生产建筑 `can_place` 需要真实能量，
        Pylon 建好前查恒 False。规划失败 → `_prod_fallback=True` 走 find_placement。
        """
        reachable_set: Any = None
        try:
            reachable_set = self.ai.game_info.pathing_grid.flood_fill(
                self.ai.start_location.rounded, lambda v: v != 0
            )
        except Exception:
            reachable_set = None
        spots: list[Point2] | None = None
        try:
            spots = await plan_building_cluster(
                self.ai,
                pylon_pos,
                self.production_type,
                self.production_count,
                scv_origin=self.ai.start_location,
                reachable=reachable_set,
                power_source=pylon_pos,
                power_radius=_POWER_RADIUS,
                footprint=_PROD_FOOTPRINT,
            )
        except Exception as exc:
            logger.warning("ForwardSupport _plan_production error: %s", exc)
            spots = None
        if spots:
            self._prod_spots = spots
            logger.info(
                "ForwardSupport planned %d %s in power field of pylon (%.1f,%.1f): %s",
                self.production_count,
                self.production_type.name,
                pylon_pos.x,
                pylon_pos.y,
                [(round(s.x, 1), round(s.y, 1)) for s in spots],
            )
        else:
            self._prod_fallback = True
            logger.warning(
                "ForwardSupport plan %d %s FAILED near pylon (%.1f,%.1f) → find_placement fallback",
                self.production_count,
                self.production_type.name,
                pylon_pos.x,
                pylon_pos.y,
            )

    # ==================================================================
    # 候选生成 + 评分（复用验证有效的隐蔽选址；单测直接调这些）
    # ==================================================================

    async def _pick_proxy_location(self) -> Point2 | None:
        """从候选点里选最好的、且 worker 走得到的（reachable top 3 随机）。

        注：execute() 已改用 _pick_proxy_and_plan()（planner）；此方法保留供
        单测 / 外部引用，语义不变（选一个安全可达的隐蔽 anchor）。
        """
        candidates = self._generate_candidates()
        if not candidates:
            return self._sharpy_fallback_proxy()
        scored = [(p, self._score_pos(p)) for p in candidates]
        scored = [(p, s) for p, s in scored if s > 0]
        if not scored:
            logger.info("ForwardSupport: 0 valid candidates after hard filter → sharpy fallback")
            return self._sharpy_fallback_proxy()
        scored.sort(key=lambda x: x[1], reverse=True)
        reachable: list[tuple[Point2, float]] = []
        for p, s in scored[:_REACH_CHECK_TOP_N]:
            if await self._is_reachable(p):
                reachable.append((p, s))
            if len(reachable) >= 3:
                break
        if not reachable:
            logger.info("ForwardSupport: top candidates all unreachable → sharpy fallback")
            return self._sharpy_fallback_proxy()
        chosen, score = random.choice(reachable)
        logger.info(
            "ForwardSupport picked proxy=%s (score=%.1f, %d reachable / %d valid)",
            chosen,
            score,
            len(reachable),
            len(scored),
        )
        return chosen

    async def _is_reachable(self, pos: Point2) -> bool:
        try:
            result = await self.ai._client.query_pathing(self.ai.start_location, pos)
        except Exception:
            return True
        return result is not None

    def _sharpy_fallback_proxy(self) -> Point2 | None:
        try:
            return self.ai.game_info.map_center.towards(self.ai.enemy_start_locations[0], 25)
        except Exception:
            return None

    def _generate_candidates(self) -> list[Point2]:
        """候选 proxy 点：中线偏敌方侧 + 敌方主基地外环。"""
        candidates: list[Point2] = []
        try:
            enemy = self.ai.enemy_start_locations[0]
            own = self.ai.start_location
        except (IndexError, AttributeError):
            return []
        dx, dy = enemy.x - own.x, enemy.y - own.y
        path_len = math.hypot(dx, dy)
        if path_len > 1e-3:
            ux, uy = dx / path_len, dy / path_len
            vx, vy = -uy, ux
            for t in (0.60, 0.67, 0.75):
                base_x = own.x + dx * t
                base_y = own.y + dy * t
                for offset in (0.0, 15.0, -15.0):
                    candidates.append(Point2((base_x + vx * offset, base_y + vy * offset)))
        for dist in (30, 40, 50, 55):
            for angle_deg in range(0, 360, 60):
                angle_rad = math.radians(angle_deg)
                candidates.append(
                    Point2(
                        (
                            enemy.x + dist * math.cos(angle_rad),
                            enemy.y + dist * math.sin(angle_rad),
                        )
                    )
                )
        return candidates

    def _score_pos(self, pos: Point2) -> float:
        """评分 proxy 候选点。负分 = 硬约束违反（必排除）。"""
        try:
            enemy = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return -1.0
        if not self._in_map_bounds(pos):
            return -1.0
        dist = pos.distance_to(enemy)
        if dist < _MIN_DIST_TO_ENEMY:
            return -1.0
        if dist > _MAX_DIST_TO_ENEMY:
            return -1.0
        nat = self._enemy_natural()
        if nat is not None and pos.distance_to(nat) < _MIN_DIST_TO_ENEMY_NATURAL:
            return -1.0
        if self._in_enemy_vision(pos):
            return -1.0
        try:
            if not self.ai.in_placement_grid(pos):
                return -1.0
        except Exception:
            return -1.0

        s: float = 100.0
        s -= (dist - _MIN_DIST_TO_ENEMY) * 2.0
        with contextlib.suppress(Exception):
            s += min(self._off_main_path(pos), 20)
        with contextlib.suppress(Exception):
            s += min(self._off_attack_axis(pos) * 1.5, 30)
        with contextlib.suppress(Exception):
            edge_d = self._edge_distance(pos)
            s += max(0.0, 50.0 - edge_d * 2.0)
        with contextlib.suppress(Exception):
            enemy_height = self.ai.get_terrain_height(enemy)
            pos_height = self.ai.get_terrain_height(pos)
            if pos_height < enemy_height:
                s += 8
        return s

    def _edge_distance(self, pos: Point2) -> float:
        try:
            area = self.ai.game_info.playable_area
            return float(
                min(
                    pos.x - area.x,
                    area.x + area.width - pos.x,
                    pos.y - area.y,
                    area.y + area.height - pos.y,
                )
            )
        except Exception:
            return 100.0

    def _in_map_bounds(self, pos: Point2) -> bool:
        try:
            area = self.ai.game_info.playable_area
            return bool(
                area.x <= pos.x <= area.x + area.width and area.y <= pos.y <= area.y + area.height
            )
        except Exception:
            return True

    async def _safe_find_placement(self, unit_type: UnitTypeId, near: Point2) -> Point2 | None:
        try:
            return cast(
                "Point2 | None",
                await self.ai.find_placement(unit_type, near, max_distance=20),
            )
        except Exception as exc:
            logger.warning(
                "ForwardSupport find_placement fail for %s near (%.1f,%.1f): %s",
                unit_type.name,
                near.x,
                near.y,
                exc,
            )
            return None

    def _off_attack_axis(self, pos: Point2) -> float:
        try:
            own = self.ai.start_location
            enemy = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return 0.0
        dx, dy = enemy.x - own.x, enemy.y - own.y
        line_len_sq = dx * dx + dy * dy
        if line_len_sq < 1e-6:
            return 0.0
        px, py = pos.x - own.x, pos.y - own.y
        t = max(0.0, min(1.0, (px * dx + py * dy) / line_len_sq))
        proj_x = own.x + t * dx
        proj_y = own.y + t * dy
        return math.hypot(pos.x - proj_x, pos.y - proj_y)

    def _in_enemy_vision(self, pos: Point2) -> bool:
        try:
            for e in self.ai.enemy_units | self.ai.enemy_structures:
                sight = getattr(e, "sight_range", 8.0)
                if pos.distance_to(e) < sight + 2.0:
                    return True
        except Exception:
            pass
        return False

    def _enemy_natural(self) -> Point2 | None:
        try:
            enemy_main = self.ai.enemy_start_locations[0]
            expansions = list(self.ai.expansion_locations_list)
            if not expansions:
                return None
            sorted_exp = sorted(expansions, key=lambda p: p.distance_to(enemy_main))
            for p in sorted_exp:
                if p.distance_to(enemy_main) > 1.0:
                    return p
            return None
        except Exception:
            return None

    def _off_main_path(self, pos: Point2) -> float:
        enemy = self.ai.enemy_start_locations[0]
        center = self.ai.game_info.map_center
        dx, dy = center.x - enemy.x, center.y - enemy.y
        line_len_sq = dx * dx + dy * dy
        if line_len_sq < 1e-6:
            return 0.0
        px, py = pos.x - enemy.x, pos.y - enemy.y
        t = max(0.0, min(1.0, (px * dx + py * dy) / line_len_sq))
        proj_x = enemy.x + t * dx
        proj_y = enemy.y + t * dy
        return math.hypot(pos.x - proj_x, pos.y - proj_y)

    # ==================================================================
    # forward 建筑识别 + 世界态跟踪
    # ==================================================================

    def _is_forward_building(self, struct: Any) -> bool:
        """这栋建筑是不是 forward proxy 的（不是家里的）。"""
        if self.proxy_location is None:
            return False
        if struct.distance_to(self.proxy_location) > _PROXY_R:
            return False
        return all(struct.distance_to(nx) >= _MIN_HOME_DIST for nx in self.ai.townhalls)

    def _forward_of_type(self, types: set[UnitTypeId]) -> list[Any]:
        try:
            candidates = self.ai.structures.of_type(types)
        except Exception:
            return []
        return [s for s in candidates if self._is_forward_building(s)]

    def _forward_pylon(self) -> Any:
        """tag 锁定的前线 Pylon 结构（None = 还没出现 / 被拆）。"""
        if self._pylon_tag is not None:
            s = self.ai.structures.find_by_tag(self._pylon_tag)
            if s is not None:
                return s
            # tag 实体没了；曾 ready 过算 destroyed（不重建），否则清 tag 重找
            if not self._pylon_ever_ready:
                self._pylon_tag = None
        fwd = self._forward_of_type({UnitTypeId.PYLON})
        if fwd:
            self._pylon_tag = fwd[0].tag
            return fwd[0]
        return None

    def _pylon_ready(self) -> bool:
        """前线 Pylon 是否 ready（真能量到位）。latch _pylon_ever_ready。"""
        s = self._forward_pylon()
        if s is not None and getattr(s, "is_ready", False):
            self._pylon_ever_ready = True
            return True
        return False

    def _pylon_present(self) -> bool:
        """前线 Pylon 是否已存在（在建或建好）。"""
        return self._forward_pylon() is not None

    def _prod_types(self) -> set[UnitTypeId]:
        types = {self.production_type}
        # GATEWAY 折跃后 tag 不变但 type → WARPGATE，遍历要带上
        if self.production_type == UnitTypeId.GATEWAY:
            types.add(UnitTypeId.WARPGATE)
        return types

    def _forward_prod_count(self) -> int:
        """已见前线生产建筑数（含在建）。刷新高水位 + 落 settle 世界态日志。

        这是**世界真实终态**信号（读 SC2 里真存在的结构 + forward 过滤），不是
        "我发了建造命令"的中间 trace —— 自验据此断言（salvage 纪律）。
        """
        fwd = self._forward_of_type(self._prod_types())
        count = len(fwd)
        if count > self._prod_hwm:
            self._prod_hwm = count
        for s in fwd:
            if s.tag not in self._prod_settled_logged:
                self._prod_settled_logged.add(s.tag)
                try:
                    d_home = (
                        min(s.distance_to(nx) for nx in self.ai.townhalls)
                        if self.ai.townhalls
                        else -1.0
                    )
                except Exception:
                    d_home = -1.0
                logger.info(
                    "FORWARDPROXY prod_settled type=%s tag=%d pos=(%.1f,%.1f) d_home=%.1f",
                    s.type_id.name,
                    s.tag,
                    s.position.x,
                    s.position.y,
                    d_home,
                )
        return count

    # ==================================================================
    # worker 管理
    # ==================================================================

    def _get_proxy_worker(self) -> Any:
        if self.proxy_worker_tag is None:
            return None
        try:
            return self.cache.by_tag(self.proxy_worker_tag)
        except Exception:
            return None

    def _assign_new_worker(self) -> Any:
        try:
            if not self.ai.workers:
                return None
            w = self.ai.workers.closest_to(self.ai.start_location)
        except Exception:
            return None
        self.proxy_worker_tag = w.tag
        logger.info(
            "ForwardSupport assigned worker tag=%d (death_count=%d)",
            w.tag,
            self._worker_death_count,
        )
        try:
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, w)
        except Exception:
            pass
        return w

    def _reassert_reserved(self, worker: Any) -> None:
        try:
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, worker)
        except Exception:
            pass

    def _release_worker(self) -> None:
        if self.proxy_worker_tag is None:
            return
        try:
            from sharpy.managers.core.roles import UnitTask

            w = self.cache.by_tag(self.proxy_worker_tag)
            if w is not None:
                self.knowledge.roles.clear_task(w)
                self.knowledge.roles.set_task(UnitTask.Idle, w)
        except Exception:
            pass
        self.proxy_worker_tag = None

    # ==================================================================
    # 完成判定
    # ==================================================================

    def _is_done(self) -> bool:
        """完成条件，任一满足返回 True。"""
        pylon_ok = self._pylon_ready() or self._pylon_ever_ready
        prod_count = self._forward_prod_count()  # 刷新 hwm

        if self.production_count == 0:
            # 纯水晶模式（dt_rush）：Pylon ready → 完成；曾 ready 后被拆也算完
            if pylon_ok:
                logger.info("ForwardSupport done (A': pylon-only, PYLON ready/ever)")
                return True
        else:
            # A/B: Pylon 曾 ready + 已见 N 个前线生产建筑（含在建，含曾建成被拆）
            if pylon_ok and self._prod_hwm >= self.production_count:
                logger.info(
                    "ForwardSupport done (A: PYLON ready + %d/%d %s placed)",
                    self._prod_hwm,
                    self.production_count,
                    self.production_type.name,
                )
                return True
            _ = prod_count  # 触发日志/hwm 已在上面

        if self._start_time is not None:
            elapsed = self.ai.time - self._start_time
            if elapsed > _TASK_TIMEOUT_S:
                logger.info("ForwardSupport done (C: timeout %.0fs)", elapsed)
                return True
        if self._worker_death_count >= _MAX_WORKER_DEATHS:
            logger.info("ForwardSupport done (D: %d worker deaths)", self._worker_death_count)
            return True
        try:
            intent = getattr(self.ai.knowledge.vibecraft, "combat_intent_override", None)
            if intent == "attack":
                logger.info("ForwardSupport done (E: main army attacking)")
                return True
        except Exception:
            pass
        return False

    # ==================================================================
    # 主 execute
    # ==================================================================

    async def execute(self) -> bool:
        if self._completed:
            return True

        if self._start_time is None:
            self._start_time = self.ai.time

        # —— 1. 一次性规划 anchor + Pylon 落点 ——
        if not self._planning_done:
            result = await self._pick_proxy_and_plan()
            self._planning_done = True
            if result is not None:
                anchor, pylon_spot = result
                self.proxy_location = anchor
                self._pylon_spot = pylon_spot
                logger.info(
                    "ForwardSupport planner: anchor=(%.1f,%.1f) pylon_spot=(%.1f,%.1f) prod=%dx%s",
                    anchor.x,
                    anchor.y,
                    pylon_spot.x,
                    pylon_spot.y,
                    self.production_count,
                    self.production_type.name,
                )
            else:
                # 兜底：sharpy fallback anchor + 现场 find_placement
                self.proxy_location = self._sharpy_fallback_proxy()
                self._prod_fallback = True
                logger.warning("ForwardSupport planner: all anchors failed → fallback")
            if self.proxy_location is None:
                self._planning_done = False  # 下帧再试（极端兜底）
                return False
            self.hide_location = self.proxy_location.towards(self.ai.start_location, 8)
            self._proxy_set_time = self.ai.time

        # —— 2. 完成判定 ——
        if self._is_done():
            self._release_worker()
            self._completed = True
            return True

        try:
            # —— 3. worker 管理 ——
            worker = self._get_proxy_worker()
            if worker is None and self.proxy_worker_tag is not None:
                self._worker_death_count += 1
                self.proxy_worker_tag = None
                if self._worker_death_count >= _MAX_WORKER_DEATHS:
                    return False
            if worker is None:
                worker = self._assign_new_worker()
            if worker is None:
                return False
            self._reassert_reserved(worker)

            # 保命评估
            hp_max = worker.shield_max + worker.health_max
            hp_now = worker.shield + worker.health
            ratio = hp_now / hp_max if hp_max > 0 else 1.0
            if not self.retreating and ratio < _RETREAT_RATIO:
                self.retreating = True
            elif self.retreating and ratio > _REENGAGE_RATIO:
                self.retreating = False
            if self.retreating and self.hide_location is not None:
                if worker.distance_to(self.hide_location) > 4:
                    worker.move(self.hide_location)
                return False

            await self._step_build(worker)
        except Exception as exc:
            logger.warning("ForwardSupport execute failed: %s", exc)
        return False

    async def _step_build(self, worker: Any) -> None:
        """probe 只执行缓存落点：先 Pylon，Pylon ready 后依次建 N 个生产建筑。"""
        pylon_present = self._pylon_present()

        # 无进展 re-pick：卡 _REPICK_NO_PROGRESS_S 秒 Pylon 还没出现 → 换 anchor 重规划
        if (
            not pylon_present
            and self._proxy_set_time is not None
            and self.ai.time - self._proxy_set_time > _REPICK_NO_PROGRESS_S
        ):
            self._replan(reason="no pylon progress")
            return

        # —— 阶段 A：还没 Pylon → 建 Pylon（缓存落点）——
        if not pylon_present:
            spot = self._pylon_spot
            if spot is None and self.proxy_location is not None:
                # fallback：现场找 Pylon 落点（planner 全失败时）
                spot = await self._safe_find_placement(UnitTypeId.PYLON, self.proxy_location)
                self._pylon_spot = spot
            if spot is None:
                self._anchor_worker(worker, self.proxy_location)
                return
            if self._worker_building(worker, UnitTypeId.PYLON):
                return  # 正在建，别打断
            if self.ai.can_afford(UnitTypeId.PYLON):
                logger.info("ForwardSupport build PYLON at (%.1f,%.1f)", spot.x, spot.y)
                worker.build(UnitTypeId.PYLON, spot)
                return
            self._anchor_worker(worker, spot)
            return

        # —— 纯水晶模式：Pylon 已在，等它 ready（_is_done 会收尾）——
        if self.production_count == 0:
            self._anchor_worker(worker, self._anchor_position())
            return

        # —— 阶段 B：等 Pylon ready（生产建筑需要能量才能 can_place / 建造）——
        if not self._pylon_ready():
            self._anchor_worker(worker, self._anchor_position())
            return

        # Pylon ready → 规划生产建筑落点（一次锁定，能量已到位 can_place 才准）
        if self._prod_spots is None and not self._prod_fallback:
            pylon = self._forward_pylon()
            if pylon is not None:
                await self._plan_production(pylon.position)

        # —— 阶段 C：依次建 N 个生产建筑 ——
        count = self._forward_prod_count()
        if count >= self.production_count:
            self._anchor_worker(worker, self._anchor_position())
            return
        if self._worker_building(worker, self.production_type):
            return  # 正在建当前这栋，别打断

        next_idx = count  # 顺序建：已见 count 栋 → 建第 count 栋
        spot = await self._prod_spot(next_idx)
        if spot is None:
            self._anchor_worker(worker, self._anchor_position())
            return
        # 生产建筑建造无进展 → 从规划落点降级到 find_placement
        if (
            self._locked_since is not None
            and self.ai.time - self._locked_since > _PROD_PATIENCE_S
            and not self._prod_fallback
        ):
            logger.warning(
                "ForwardSupport %s build no progress %.0fs → find_placement fallback",
                self.production_type.name,
                self.ai.time - self._locked_since,
            )
            self._prod_fallback = True
            self._prod_spots = None
            self._locked_spot = None
            self._locked_idx = None
            self._locked_since = None
            return
        if self.ai.can_afford(self.production_type):
            worker.build(self.production_type, spot)
            return
        self._anchor_worker(worker, spot)

    async def _prod_spot(self, idx: int) -> Point2 | None:
        """第 idx 个生产建筑的落点（一次锁定，绝不每帧重选）。

        规划模式：直接取 _prod_spots[idx]。fallback：find_placement near pylon，
        缓存进 _locked_spot（同 idx 复用，切 idx 才重算）。
        """
        if self._locked_idx == idx and self._locked_spot is not None:
            return self._locked_spot
        # 新 idx → 计算并锁定
        spot: Point2 | None = None
        if not self._prod_fallback and self._prod_spots is not None and idx < len(self._prod_spots):
            spot = self._prod_spots[idx]
        else:
            base = self._anchor_position() or self.proxy_location
            if base is not None:
                near = base.towards(self.ai.start_location, 3)
                spot = await self._safe_find_placement(self.production_type, near)
        self._locked_spot = spot
        self._locked_idx = idx
        self._locked_since = self.ai.time
        return spot

    def _replan(self, reason: str) -> None:
        """排除当前 anchor + 换一个重规划（限次），或落 find_placement 兜底。"""
        if (
            self._replan_count < _MAX_REPLAN
            and self.proxy_location is not None
            and self._pylon_spot is not None  # 只在走了 planner 时换 anchor
        ):
            self._failed_anchors.append(self.proxy_location)
            self._replan_count += 1
            logger.warning(
                "ForwardSupport anchor (%.1f,%.1f) %s → 排除 + 换 anchor 重规划（第 %d 次）",
                self.proxy_location.x,
                self.proxy_location.y,
                reason,
                self._replan_count,
            )
        else:
            logger.info("ForwardSupport %s → 重置重试", reason)
        self._release_worker()
        self.proxy_location = None
        self.hide_location = None
        self._proxy_set_time = None
        self._planning_done = False
        self._pylon_spot = None
        self._prod_spots = None
        self._locked_spot = None
        self._locked_idx = None
        self._locked_since = None
        self._pylon_tag = None

    # ==================================================================
    # worker 锚定 / 辅助
    # ==================================================================

    def _anchor_position(self) -> Point2 | None:
        """worker 应锚定的位置：有 Pylon 贴 Pylon，否则用 Pylon 落点 / anchor。"""
        pylon = self._forward_pylon()
        if pylon is not None:
            return cast("Point2", pylon.position)
        return self._pylon_spot or self.proxy_location

    def _worker_building(self, worker: Any, unit_type: UnitTypeId) -> bool:
        """worker.orders 是否含 unit_type 的 BUILD ability。"""
        want = _BUILD_ABILITIES.get(unit_type)
        if want is None:
            return False
        for order in getattr(worker, "orders", None) or []:
            ability = getattr(order, "ability", None)
            if ability is not None and getattr(ability, "id", None) == want:
                return True
        return False

    def _worker_busy_with_forward_build(self, worker: Any) -> bool:
        """worker 正在 build 任一前线建筑（Pylon / 生产建筑）→ 别打断。"""
        wanted = {
            _BUILD_ABILITIES[UnitTypeId.PYLON],
            _BUILD_ABILITIES.get(self.production_type),
        }
        for order in getattr(worker, "orders", None) or []:
            ability = getattr(order, "ability", None)
            if ability is not None and getattr(ability, "id", None) in wanted:
                return True
        return False

    def _anchor_worker(self, worker: Any, anchor: Point2 | None) -> None:
        """worker 没在 build 且偏离 anchor 就拉回（不依赖 is_idle，防 auto-mining 回家）。"""
        if anchor is None:
            return
        if self._worker_busy_with_forward_build(worker):
            return
        if worker.distance_to(anchor) <= 4:
            return
        worker.move(anchor)
