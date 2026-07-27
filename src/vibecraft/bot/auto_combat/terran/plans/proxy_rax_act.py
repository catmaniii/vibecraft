"""4 rax proxy：3 SCV 并行走到地图隐蔽中段，各建 1 个兵营。

选点策略：中轴偏敌方（~55%）贴地图边（隐蔽）。
出发时机：等家里第一个兵营开始造，才拉 3 SCV 出去（太早拖慢开局采矿）。
SCV 赶路：走一个贴边航点再直奔 proxy，避免穿越地图中央被发现。
SCV 到达：在 proxy 锚点 reserve（每帧重申）+ hold_position 站桩，等钱够 150 即建
  —— 不被采矿 manager / 全军暴退拉回家反复拉扯（用户反馈）。
建完去向：默认释放（Idle role）→ PlanZoneAttack 接管跟枪兵前压。
地堡封锁（blockade_enabled=False，默认关）：建完走到敌方斜坡底修地堡封口。

落点一次锁定规则（CLAUDE.md 强规则）：
  每个 SCV 的 barracks_placed 第一次由 placement_planner 预规划落点决定后缓存，之后每帧
  发同一个位置，绝不重选（否则 SCV 追漂移目标永远落不下）。
  patience 超时才重置重试（建造命令被 sharpy 拦截 / 选点地形坏的容错）。

完成判定（OR）：
  A. ≥ 3 个 proxy 兵营（含在建）出现在地图上
  B. 超时 240s
  C. SCV 累计死亡 ≥ 4
  D. 已出门（combat_intent_override == "attack"）
"""

from __future__ import annotations

import contextlib
import math
import random
from typing import Any

from loguru import logger
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

from vibecraft.bot.placement_planner import plan_building_cluster

_TARGET_RAX: int = 3
_MAX_WORKER_DEATHS: int = 4
_TASK_TIMEOUT_S: float = 240.0
_BUILD_PATIENCE_S: float = 75.0
_WP_TIMEOUT_S: float = 25.0
_TRAVEL_GIVEUP_S: float = 90.0
_RETREAT_RATIO: float = 0.5
_REENGAGE_RATIO: float = 0.9
_MIN_DIST_TO_ENEMY_NATURAL: float = 22.0
_MIN_HOME_DIST: float = 25.0
# 距敌方主基地的硬下限（太近必被发现）
_MIN_DIST_TO_ENEMY: float = 30.0
# 距敌方主基地的硬上限（太远 marine 赶路慢，rush timing 差）
_MAX_DIST_TO_ENEMY: float = 65.0
# 对分数最高的前 N 个候选逐个查寻路可达性
_REACH_CHECK_TOP_N: int = 8
# 建造失败换 anchor 重规划的最大次数（防死循环；用完仍不行落松散兜底）
_MAX_REPLAN: int = 3
# 3 个兵营落点相对于 proxy 中心的偏移（各 8 格距离，确保 4×3 兵营不重叠）
_SLOT_OFFSETS: list[tuple[float, float]] = [(0.0, 0.0), (8.0, 0.0), (0.0, 7.0)]


class _SCV:
    """单个 proxy SCV 的赶路 + 建造状态。"""

    def __init__(self, tag: int, now: float, slot: int) -> None:
        self.tag = tag
        self.slot = slot  # 0/1/2，对应 _SLOT_OFFSETS 里的落点
        self.assigned_at = now
        self.route_idx = 0
        self.wp_since = now
        self.reached_proxy = False
        self.retreating = False
        self.holding = False  # 已在锚点 hold_position 站桩（防重复发 hold 命令）
        # 建造状态（一次锁定，不重选）
        self.barracks_placed: Point2 | None = None
        self.barracks_issued_at: float | None = None
        self.barracks_done = False
        self.released = False
        # 完成判定 tag 锁定：紧凑簇（间距 4）时避免相邻 slot 的兵营互相串味
        # （旧 5.0 半径检测 > 4.0 间距，一个建好会误判邻居也建好 → 提前释放）
        self.barracks_tag: int | None = None
        # 地堡封锁状态（blockade_enabled=True 时用）
        self.blockade_placed: Point2 | None = None
        self.blockade_issued_at: float | None = None
        self.blockade_done = False


class ProxyBarracksAct(ActBase):  # type: ignore[misc]
    """3 SCV 并行走到隐蔽野点，各建 1 个兵营（proxy 4 rax 的 3 个野营部分）。"""

    def __init__(self, target_rax: int = _TARGET_RAX, blockade_enabled: bool = False) -> None:
        super().__init__()
        self._target_rax = target_rax
        self._blockade_enabled = blockade_enabled
        self._completed = False
        self._start_time: float | None = None
        self._proxy_location: Point2 | None = None
        self._hide_location: Point2 | None = None
        self._route: list[Point2] = []
        self._workers: dict[int, _SCV] = {}
        self._death_count: int = 0
        self._cached_planned_spots: list[Any] | None = (
            None  # plan_building_cluster 的返回值（3 个落点）
        )
        self._planning_done: bool = False  # 防止每帧重跑规划
        self._using_fallback: bool = False  # True = 规划全失败，用宽松 find_placement 兜底
        # 顺序建造门死锁保护：seed(slot0)建不起来（patience 过）时置 True，
        # 释放门让 slot1/slot2 照常建（避免坏 seed 拖垮整簇 → 至少不比不加门差）。
        self._seed_gave_up: bool = False
        # 建造失败重规划（2026-07-07 真局取证）：规划器验过的 anchor，某些贴边/远地形 SCV 实际建不出
        # (seed patience 超时)。此时排除该 anchor、换一个重规划（好 anchor 建得出），而非级联落松散兜底。
        self._failed_anchors: list[Point2] = []
        self._replan_count: int = 0

    async def start(self, knowledge: Any) -> None:
        await super().start(knowledge)
        self._proxy_location = None
        self._hide_location = None
        self._route = []

    # ------------------------------------------------------------------
    # Proxy 选点（复用 4bg forward_proxy 的隐蔽选址算法）
    # ------------------------------------------------------------------

    async def _pick_proxy_location(self) -> Point2 | None:
        """从候选点里选最好的、且 SCV 走得到的（reachable top 3 随机）。

        候选：中线 60-75% + 敌方主基地外环（30-55 距离）。
        评分：距敌主适中、避 natural（≥22）、贴地图边（最高权重）、偏轴。
        Fallback：候选全空/全不可达 → map_center.towards(enemy_start, 25)。

        注：execute() 已改用 _pick_proxy_and_plan()，此方法保留以防外部引用。
        """
        candidates = self._generate_candidates()
        if not candidates:
            return self._sharpy_fallback_proxy()

        scored = [(p, self._score_pos(p)) for p in candidates]
        scored = [(p, s) for p, s in scored if s > 0]
        if not scored:
            logger.warning("ProxyRax: 0 valid candidates after hard filter → sharpy fallback")
            return self._sharpy_fallback_proxy()

        scored.sort(key=lambda x: x[1], reverse=True)
        # 对分数最高的前 N 个查寻路可达，凑够 3 个 reachable 就停
        reachable: list[tuple[Point2, float]] = []
        for p, s in scored[:_REACH_CHECK_TOP_N]:
            if await self._is_reachable(p):
                reachable.append((p, s))
            if len(reachable) >= 3:
                break
        if not reachable:
            logger.warning("ProxyRax: top candidates all unreachable → sharpy fallback")
            return self._sharpy_fallback_proxy()

        chosen, score = random.choice(reachable)
        logger.info(
            f"ProxyRax picked proxy=({chosen.x:.1f},{chosen.y:.1f}) "
            f"(score={score:.1f}, {len(reachable)} reachable / {len(scored)} valid)"
        )
        return chosen

    async def _pick_proxy_and_plan(self) -> tuple | None:
        """选 proxy 锚点并一次性规划 3 个兵营落点。

        返回 (anchor: Point2, spots: list[Point2]) 或 None（全失败）。

        算法：
        1. 生成候选 → 硬过滤（负分排除）
        2. 走廊安全评分（off_attack_axis + off_main_path）降序，取 top N
        3. 全局 flood_fill 一次（所有 anchor 复用）
        4. 逐个 anchor：query_pathing 快查可达 → plan_building_cluster → 第一个成功立即返回
        """
        candidates = self._generate_candidates()
        if not candidates:
            return None

        scored = [(p, self._score_pos(p)) for p in candidates]
        scored = [(p, s) for p, s in scored if s > 0]
        if not scored:
            logger.warning("ProxyRax planner: 0 valid candidates after hard filter")
            return None

        # 走廊安全评分（替换原来的 random.choice）
        safety_scored: list[tuple[Point2, float]] = []
        for p, _s in scored:
            safety = self._off_attack_axis(p) + self._off_main_path(p)
            safety_scored.append((p, safety))
        safety_scored.sort(key=lambda x: x[1], reverse=True)
        # 排除已确认"SCV 建不出"的失败 anchor 区域（真局取证：某些贴边/远地形规划验过但建不成）
        if self._failed_anchors:
            safety_scored = [
                (p, s)
                for p, s in safety_scored
                if all(p.distance_to(f) > 10.0 for f in self._failed_anchors)
            ]
        top_anchors = [p for p, _ in safety_scored[:_REACH_CHECK_TOP_N]]

        # 全局 flood_fill 一次（所有 anchor 复用）
        reachable_set: Any = None
        try:
            reachable_set = self.ai.game_info.pathing_grid.flood_fill(
                self.ai.start_location.rounded, lambda v: v != 0
            )
        except Exception:
            reachable_set = None

        # 逐个检查 anchor 可达性 + 规划落点
        for anchor in top_anchors:
            try:
                result = await self.ai._client.query_pathing(self.ai.start_location, anchor)
                if result is None:
                    continue
            except Exception:
                pass
            try:
                spots = await plan_building_cluster(
                    self.ai,
                    anchor,
                    UnitTypeId.BARRACKS,
                    3,
                    scv_origin=self.ai.start_location,
                    reachable=reachable_set,
                )
            except Exception as exc:
                logger.debug(
                    f"ProxyRax planner anchor ({anchor.x:.1f},{anchor.y:.1f}) failed: {exc}"
                )
                continue
            if spots is not None:
                return (anchor, spots)

        return None

    async def _is_reachable(self, pos: Point2) -> bool:
        """SCV 能否从自家地面走到 pos（寻路连通）。

        query_pathing 返回 None = 不连通。查询失败时不否决（返回 True），
        留给无进展超时兜底。
        """
        try:
            result = await self.ai._client.query_pathing(self.ai.start_location, pos)
        except Exception:
            return True
        return result is not None

    def _sharpy_fallback_proxy(self) -> Point2 | None:
        """兜底：map_center 朝敌方主基地 25 距离（几乎必可达）。"""
        try:
            return self.ai.game_info.map_center.towards(self.ai.enemy_start_locations[0], 25)
        except Exception:
            return None

    def _generate_candidates(self) -> list[Point2]:
        """生成候选 proxy 点：中线候选 + 敌方主基地外环。

        A. 中线候选：自家→敌家直线 60%-75% 位置 × 3 个侧向偏移（贴边走廊）。
        B. 环形候选：敌方主基地外环 30/40/50/55 距离 × 6 方向。
        """
        candidates: list[Point2] = []
        try:
            enemy = self.ai.enemy_start_locations[0]
            own = self.ai.start_location
        except (IndexError, AttributeError):
            return []

        # A. 中线候选（偏敌方一侧，侧向偏移绕开侦查巡逻路线）
        dx, dy = enemy.x - own.x, enemy.y - own.y
        path_len = math.hypot(dx, dy)
        if path_len > 1e-3:
            ux, uy = dx / path_len, dy / path_len
            vx, vy = -uy, ux  # 垂直向量（顺时针 90°）
            for t in (0.60, 0.67, 0.75):
                base_x = own.x + dx * t
                base_y = own.y + dy * t
                for offset in (0.0, 15.0, -15.0):
                    candidates.append(Point2((base_x + vx * offset, base_y + vy * offset)))

        # B. 环形候选（从敌主为圆心，配合 _MIN_DIST_TO_ENEMY=30）
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
        if not self._in_map_bounds(pos):
            return -1.0
        try:
            enemy = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return -1.0

        dist = pos.distance_to(enemy)
        # 硬约束：距敌主基地范围
        if dist < _MIN_DIST_TO_ENEMY:
            return -1.0
        if dist > _MAX_DIST_TO_ENEMY:
            return -1.0
        # 硬约束：距敌方 natural 太近（守家 SCV/侦查必发现）
        nat = self._enemy_natural()
        if nat is not None and pos.distance_to(nat) < _MIN_DIST_TO_ENEMY_NATURAL:
            return -1.0
        # 硬约束：被敌方单位/建筑当前看到
        if self._in_enemy_vision(pos):
            return -1.0
        # 硬约束：不能建建筑
        try:
            if not self.ai.in_placement_grid(pos):
                return -1.0
        except Exception:
            return -1.0

        # 软评分
        s = 100.0
        s -= (dist - _MIN_DIST_TO_ENEMY) * 2.0  # 距离越近越高分

        # 偏离"敌方主基地→地图中心"主路加分（侧翼隐蔽）
        with contextlib.suppress(Exception):
            s += min(self._off_main_path(pos), 20.0)

        # 偏离"自家→敌家"进攻轴线加分（SCV 赶路时更安全，权重 1.5）
        with contextlib.suppress(Exception):
            s += min(self._off_attack_axis(pos) * 1.5, 30.0)

        # 贴地图边加分（最高权重，距边 0 → +50，距边 25+ → 0）
        with contextlib.suppress(Exception):
            edge_d = self._edge_distance(pos)
            s += max(0.0, 50.0 - edge_d * 2.0)

        # 低地小加分（敌方主基地通常在高地）
        with contextlib.suppress(Exception):
            if self.ai.get_terrain_height(pos) < self.ai.get_terrain_height(enemy):
                s += 8.0

        return s

    def _in_map_bounds(self, pos: Point2) -> bool:
        """pos 是否在 playable_area 矩形内（防止 ring 越过地图边界生成负坐标点）。"""
        try:
            area = self.ai.game_info.playable_area
            return bool(
                area.x <= pos.x <= area.x + area.width and area.y <= pos.y <= area.y + area.height
            )
        except Exception:
            return True

    def _in_enemy_vision(self, pos: Point2) -> bool:
        """pos 是否在任何已见敌方单位/建筑的视野半径内。"""
        try:
            for e in self.ai.enemy_units | self.ai.enemy_structures:
                sight = getattr(e, "sight_range", 8.0)
                if pos.distance_to(e) < sight + 2.0:
                    return True
        except Exception:
            pass
        return False

    def _enemy_natural(self) -> Point2 | None:
        """敌方 natural（二基地）位置：离敌方主基地最近的非主基地扩张点。"""
        try:
            enemy_main = self.ai.enemy_start_locations[0]
            exps = [e for e in self.ai.expansion_locations_list if e.distance_to(enemy_main) > 1.0]
            if not exps:
                return None
            return min(exps, key=lambda e: e.distance_to(enemy_main))
        except Exception:
            return None

    def _off_main_path(self, pos: Point2) -> float:
        """pos 到 enemy_main→map_center 直线的垂直距离（越大越偏路）。"""
        try:
            enemy = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return 0.0
        center = self.ai.game_info.map_center
        dx, dy = center.x - enemy.x, center.y - enemy.y
        line_len_sq = dx * dx + dy * dy
        if line_len_sq < 1e-6:
            return 0.0
        px, py = pos.x - enemy.x, pos.y - enemy.y
        t = max(0.0, min(1.0, (px * dx + py * dy) / line_len_sq))
        return math.hypot(pos.x - (enemy.x + t * dx), pos.y - (enemy.y + t * dy))

    def _off_attack_axis(self, pos: Point2) -> float:
        """pos 到 own_main→enemy_main 直线的垂直距离（偏轴越大 SCV 赶路越安全）。"""
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
        return math.hypot(pos.x - (own.x + t * dx), pos.y - (own.y + t * dy))

    def _edge_distance(self, pos: Point2) -> float:
        """pos 到 playable_area 最近一条边的距离（越小越贴边）。"""
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

    # ------------------------------------------------------------------
    # 赶路（贴边航点 → proxy）
    # ------------------------------------------------------------------

    def _compute_edge_route(self) -> list[Point2]:
        if self._proxy_location is None:
            return []
        return [self._edge_waypoint(self._proxy_location)]

    def _edge_waypoint(self, p: Point2) -> Point2:
        try:
            area = self.ai.game_info.playable_area
        except Exception:
            return p
        d_left = p.x - area.x
        d_right = area.x + area.width - p.x
        d_bottom = p.y - area.y
        d_top = area.y + area.height - p.y
        m = min(d_left, d_right, d_bottom, d_top)
        margin = 7.0
        if m == d_left:
            return Point2((area.x + margin, p.y))
        if m == d_right:
            return Point2((area.x + area.width - margin, p.y))
        if m == d_bottom:
            return Point2((p.x, area.y + margin))
        return Point2((p.x, area.y + area.height - margin))

    def _travel_step(self, worker: Any, ws: _SCV) -> None:
        if self._proxy_location is None:
            return
        if ws.route_idx < len(self._route):
            wp = self._route[ws.route_idx]
            if worker.distance_to(wp) < 7.0 or self.ai.time - ws.wp_since > _WP_TIMEOUT_S:
                ws.route_idx += 1
                ws.wp_since = self.ai.time
                return
            worker.move(wp)
            return
        worker.move(self._proxy_location)

    # ------------------------------------------------------------------
    # SCV 管理
    # ------------------------------------------------------------------

    def _alive(self, tag: int) -> Any:
        try:
            return self.cache.by_tag(tag)
        except Exception:
            return None

    def _reap_dead(self) -> None:
        for tag in list(self._workers):
            if self._alive(tag) is None:
                self._death_count += 1
                del self._workers[tag]
                logger.info(f"ProxyRax SCV {tag} lost (total deaths={self._death_count})")

    def _ensure_workers(self) -> None:
        assigned_slots = {ws.slot for ws in self._workers.values()}
        for slot in range(self._target_rax):
            if slot not in assigned_slots and len(self._workers) < self._target_rax:
                self._add_worker(slot)

    def _add_worker(self, slot: int) -> None:
        used = set(self._workers)
        try:
            avail = [w for w in self.ai.workers if w.tag not in used]
            if not avail:
                return
            w = min(avail, key=lambda u: u.distance_to(self.ai.start_location))
        except Exception:
            return
        self._workers[w.tag] = _SCV(w.tag, self.ai.time, slot)
        try:
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, w)
        except Exception:
            pass
        logger.info(f"ProxyRax assigned SCV tag={w.tag} slot={slot} at game_t={self.ai.time:.0f}s")

    def _release_all(self) -> None:
        for tag in list(self._workers):
            w = self._alive(tag)
            if w is None:
                continue
            self._do_release(w)
        self._workers.clear()

    def _do_release(self, worker: Any) -> None:
        try:
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.clear_task(worker)
            self.knowledge.roles.set_task(UnitTask.Idle, worker)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 建造辅助
    # ------------------------------------------------------------------

    def _is_proxy_building(self, struct: Any) -> bool:
        """建筑是否在 proxy 侧（远离所有自家 CC）。"""
        return all(struct.distance_to(th) >= _MIN_HOME_DIST for th in self.ai.townhalls)

    def _barracks_at_slot(self, ws: _SCV) -> Any | None:
        """找当前 SCV 落点处的兵营（含在建），tag 锁定防相邻 slot 串味。

        紧凑簇（planner 间距 4）时，旧的 5.0 半径检测 > 间距 → 一个兵营建好
        会被相邻 slot 也当成"自己的建好了" → 提前释放 SCV，邻居兵营永远不建。
        改法：① 一旦匹配到就锁 tag，之后只认这个 tag；② 未锁 tag 时用紧半径
        (2.0 < 间距 4 的一半) + 排除已被别的 slot 认领的 tag，取最近的那个。
        """
        # 已锁 tag：只认这个（None 表示被拆/丢，交给上层重试逻辑）
        if ws.barracks_tag is not None:
            return self.ai.structures.find_by_tag(ws.barracks_tag)
        if ws.barracks_placed is None:
            return None
        claimed = {w.barracks_tag for w in self._workers.values() if w.barracks_tag is not None}
        best = None
        best_d = 2.5  # 紧半径：< 紧凑间距 4 的一半，杜绝邻居串味
        for s in self.ai.structures(UnitTypeId.BARRACKS):
            if s.tag in claimed:
                continue
            if not self._is_proxy_building(s):
                continue
            d = s.position.distance_to(ws.barracks_placed)
            if d < best_d:
                best_d = d
                best = s
        if best is not None:
            ws.barracks_tag = best.tag  # 锁定，后续只认这个 tag
        return best

    def _slot_barracks_started(self, slot: int) -> bool:
        """slot 的兵营是否已出现在地图上（在建或建好）——顺序建造门用。

        找不到该 slot 的 worker（已建完释放 / 从没派）→ 视为"已开始"，不阻塞后续 slot。
        """
        for ws in self._workers.values():
            if ws.slot != slot:
                continue
            if ws.barracks_done:
                return True
            if ws.barracks_tag is not None:
                return self.ai.structures.find_by_tag(ws.barracks_tag) is not None
            sp = ws.barracks_placed
            if sp is None:
                return False
            for s in self.ai.structures(UnitTypeId.BARRACKS):
                if self._is_proxy_building(s) and s.position.distance_to(sp) < 2.5:
                    return True
            return False
        return True  # 没有该 slot 的 worker → 不阻塞

    def _compute_anchor(self) -> Point2 | None:
        """枪兵集结锚点：3 个规划落点的质心；未规划（fallback 模式）时退回 proxy_location。

        供 `MarineStagingAct` 读取（`knowledge.vibecraft.proxy_anchor`）——枪兵集结点用
        3 兵营质心而非某一个兵营坐标，出发时到任意一个 proxy 兵营距离都不会太远。
        """
        if self._cached_planned_spots and len(self._cached_planned_spots) == 3:
            xs = sum(p.x for p in self._cached_planned_spots)
            ys = sum(p.y for p in self._cached_planned_spots)
            return Point2((xs / 3, ys / 3))
        return self._proxy_location

    def _slot_anchor(self, slot: int) -> Point2 | None:
        # 规划落点模式：用预规划的坐标作为 anchor（hold 等待时也用这里）
        if (
            not self._using_fallback
            and self._cached_planned_spots is not None
            and slot < len(self._cached_planned_spots)
        ):
            return self._cached_planned_spots[slot]
        # 兜底/fallback 模式：原来的偏移逻辑
        if self._proxy_location is None:
            return None
        ox, oy = _SLOT_OFFSETS[slot]
        return Point2((self._proxy_location.x + ox, self._proxy_location.y + oy))

    def _hold_at_anchor(self, worker: Any, ws: _SCV) -> None:
        """SCV 在 proxy 锚点站桩不动（防采矿/暴退把它漂回家）。

        远了 → move 过去；到位（<4）→ `hold_position()` 一次，之后不再发命令 → SCV 不空闲、
        采矿 manager 抢不走（配合每帧 _reassert_reserved），真正"站着不动直到修好兵营"。
        """
        anchor = self._slot_anchor(ws.slot) or self._proxy_location
        if anchor is None:
            return
        if worker.distance_to(anchor) > 4:
            worker.move(anchor)
            ws.holding = False
        elif not ws.holding:
            with contextlib.suppress(Exception):
                worker.hold_position()
            ws.holding = True

    async def _safe_find_placement(self, unit_type: UnitTypeId, near: Point2) -> Point2 | None:
        try:
            return await self.ai.find_placement(unit_type, near, max_distance=15)
        except Exception as exc:
            logger.warning(f"ProxyRax find_placement {unit_type.name}: {exc}")
            return None

    # ------------------------------------------------------------------
    # 完成判定
    # ------------------------------------------------------------------

    def _is_done(self) -> bool:
        # A: ≥ 3 个 proxy 兵营存在（含在建）
        proxy_rax_count = sum(
            1 for s in self.ai.structures(UnitTypeId.BARRACKS) if self._is_proxy_building(s)
        )
        if proxy_rax_count >= self._target_rax:
            logger.info(f"ProxyRax done (A: {proxy_rax_count} proxy barracks)")
            return True
        # B: 超时
        if self._start_time is not None and self.ai.time - self._start_time > _TASK_TIMEOUT_S:
            logger.info(f"ProxyRax done (B: timeout {self.ai.time - self._start_time:.0f}s)")
            return True
        # C: SCV 死太多
        if self._death_count >= _MAX_WORKER_DEATHS:
            logger.info(f"ProxyRax done (C: {self._death_count} SCV deaths)")
            return True
        # D: 主力已出门
        try:
            if getattr(self.ai.knowledge.vibecraft, "combat_intent_override", None) == "attack":
                logger.info("ProxyRax done (D: army attacking)")
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # 主 execute
    # ------------------------------------------------------------------

    async def execute(self) -> bool:
        if self._completed:
            return True

        if self._start_time is None:
            self._start_time = self.ai.time

        # 第一次：选 proxy + 一次性规划落点
        if not self._planning_done:
            result = await self._pick_proxy_and_plan()
            self._planning_done = True
            if result is not None:
                proxy_loc, planned_spots = result
                self._proxy_location = proxy_loc
                self._cached_planned_spots = planned_spots
                logger.info(
                    f"ProxyRax planner: proxy=({proxy_loc.x:.1f},{proxy_loc.y:.1f}) "
                    f"spots={[(round(s.x, 1), round(s.y, 1)) for s in planned_spots]}"
                )
            else:
                # 兜底：sharpy fallback proxy + 宽松 find_placement
                self._proxy_location = self._sharpy_fallback_proxy()
                self._using_fallback = True
                logger.warning(
                    "ProxyRax planner: all anchors failed → fallback loose find_placement"
                )

            if self._proxy_location is None:
                return False  # 极端兜底（几乎不会到这）

            self._hide_location = self._proxy_location.towards(self.ai.start_location, 10)
            self._route = self._compute_edge_route()
            logger.info(
                f"ProxyRax start: proxy=({self._proxy_location.x:.1f},{self._proxy_location.y:.1f}) "
                f"blockade={self._blockade_enabled} fallback={self._using_fallback}"
            )

        self._reap_dead()

        if self._is_done():
            self._release_all()
            self._completed = True
            return True

        # 出发时机门：等家里第一个兵营开始造，再拉 SCV 出去（太早出去拖慢开局采矿；
        # 用户 2026-07-05 反馈"上来就出去三个农民太早"）。一旦已派出（_workers 非空）不再门控。
        if self._workers or self._first_barracks_started():
            self._ensure_workers()

        # 发布 proxy 建造农民 tag → ScoutWorker 排除它们，别把 proxy 农民抓去探路
        # （2026-07-07 玩家实测：proxy 农民离敌近被抓走 → 到不了 proxy 点 + 卡死顺序门）。
        # 同时发布 proxy 锚点 → MarineStagingAct 读它做"枪兵前向集结"（2026-07-09）。
        with contextlib.suppress(Exception):
            vib = getattr(self.knowledge, "vibecraft", None)
            if vib is not None:
                vib.proxy_builder_tags = set(self._workers.keys())
                vib.proxy_anchor = self._compute_anchor()

        for ws in list(self._workers.values()):
            worker = self._alive(ws.tag)
            if worker is None:
                continue
            # 每帧重申 Reserved：防采矿 manager / 全军暴退把在外面站桩的 SCV 抢回家采矿
            # （只在 _add_worker 设一次不够——会被拉回基地又拉出去反复拉扯，用户反馈）。
            self._reassert_reserved(worker)
            await self._step_scv(worker, ws)

        return False

    def _first_barracks_started(self) -> bool:
        """家里第一个兵营是否已开始建造（含在建 / 已完成）。用作 SCV 出发时机门。

        派 SCV 前地图上还没有任何兵营，所以此判定 = 家里那个兵营（base bot 按 build order 造）
        已经动工。查询失败不卡死（返回 True 放行）。
        """
        try:
            if self.ai.structures(UnitTypeId.BARRACKS).exists:
                return True
            return bool(self.ai.already_pending(UnitTypeId.BARRACKS))
        except Exception:
            return True

    def _reassert_reserved(self, worker: Any) -> None:
        """把 SCV 的 role 每帧重申为 Reserved（幂等），采矿 manager 就不会抢它去采矿。"""
        try:
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, worker)
        except Exception:
            pass

    async def _step_scv(self, worker: Any, ws: _SCV) -> None:
        if ws.released:
            return

        # 保命：血量低撤退
        hp_max = worker.shield_max + worker.health_max
        ratio = (worker.shield + worker.health) / hp_max if hp_max > 0 else 1.0
        if not ws.retreating and ratio < _RETREAT_RATIO:
            ws.retreating = True
        elif ws.retreating and ratio > _REENGAGE_RATIO:
            ws.retreating = False
        if ws.retreating:
            if self._hide_location is not None and worker.distance_to(self._hide_location) > 4:
                worker.move(self._hide_location)
            return

        # 赶路到 proxy
        if not ws.reached_proxy:
            near_proxy = (
                self._proxy_location is not None and worker.distance_to(self._proxy_location) < 9.0
            )
            if near_proxy or self.ai.time - ws.assigned_at > _TRAVEL_GIVEUP_S:
                ws.reached_proxy = True
            else:
                self._travel_step(worker, ws)
                return

        # 地堡封锁流（建完兵营后）
        if ws.barracks_done and self._blockade_enabled:
            await self._step_blockade(worker, ws)
            return

        # 建完兵营 → 释放（默认：跟大部队前压）
        if ws.barracks_done:
            self._release_scv(worker, ws)
            return

        # 检查落点处兵营状态
        existing = self._barracks_at_slot(ws)
        if existing is not None:
            if existing.build_progress >= 1.0:
                ws.barracks_done = True
            # 在建或建完 → 不再发命令，等它建好
            return

        # 顺序建造门（部分顺序）：slot1/slot2 等 seed(slot0)的兵营先出现再建（seed 进场路先留空）。
        # 完全顺序(slot0→1→2 依次)试过：修了身体互堵但太慢、slot2 来不及开工，不如部分顺序。
        # 死锁保护（2026-07-07）：seed(slot0)若被拉走/卡住永不建（patience 不触发因没发 build），
        # slot1/2 会等死 → 任务过 40% timeout 仍没 seed 兵营 → 放开门让 slot1/2 照常建。
        stagger_deadline = self._start_time is not None and (
            self.ai.time - self._start_time > _TASK_TIMEOUT_S * 0.4
        )
        if (
            ws.slot > 0
            and not self._seed_gave_up
            and not stagger_deadline
            and not self._slot_barracks_started(0)
        ):
            self._hold_at_anchor(worker, ws)
            return

        # 落点还没兵营 → 等钱或下建造命令

        # 没钱且尚未发出命令 → 站桩等在锚点（reserve + hold，不被拉回家）
        if not self.ai.can_afford(UnitTypeId.BARRACKS) and ws.barracks_placed is None:
            self._hold_at_anchor(worker, ws)
            return

        # 需要确定落点（一次锁定）
        if ws.barracks_placed is None:
            if (
                not self._using_fallback
                and self._cached_planned_spots is not None
                and ws.slot < len(self._cached_planned_spots)
            ):
                # 直接使用规划好的落点（无 find_placement）
                ws.barracks_placed = self._cached_planned_spots[ws.slot]
                ws.barracks_issued_at = self.ai.time
                logger.info(
                    f"ProxyRax SCV {ws.tag} slot={ws.slot} planner locked "
                    f"({ws.barracks_placed.x:.1f},{ws.barracks_placed.y:.1f})"
                )
            else:
                # fallback 或 patience 重试：现场 find_placement（宁松散别卡死）
                anchor = self._slot_anchor(ws.slot)
                if anchor is None:
                    return
                place = await self._safe_find_placement(UnitTypeId.BARRACKS, anchor)
                if place is None:
                    self._hold_at_anchor(worker, ws)
                    return
                ws.barracks_placed = place
                ws.barracks_issued_at = self.ai.time
                logger.info(
                    f"ProxyRax SCV {ws.tag} slot={ws.slot} "
                    f"{'fallback' if self._using_fallback else 'patience-retry'} locked "
                    f"({ws.barracks_placed.x:.1f},{ws.barracks_placed.y:.1f})"
                )

        # Patience 超时 → 建造失败。真局取证：规划器验过的 anchor，某些贴边/远地形 SCV 实际建不出
        # （seed 卡 ~20 格外）。**排除这个 anchor + 换一个重规划**（好 anchor 建得出），而非级联落松散兜底。
        if (
            ws.barracks_issued_at is not None
            and self.ai.time - ws.barracks_issued_at > _BUILD_PATIENCE_S
        ):
            # 优先：排除当前 anchor + 换一个重规划（限 _MAX_REPLAN 次，防死循环）
            if (
                self._replan_count < _MAX_REPLAN
                and self._proxy_location is not None
                and not self._using_fallback
            ):
                self._failed_anchors.append(self._proxy_location)
                self._replan_count += 1
                logger.warning(
                    f"ProxyRax: anchor ({self._proxy_location.x:.0f},{self._proxy_location.y:.0f}) "
                    f"SCV 建不出 → 排除 + 换 anchor 重规划（第 {self._replan_count} 次）"
                )
                # 重置整簇 → 下帧重新 _pick_proxy_location（排除失败 anchor）+ SCV 重赶路建
                self._proxy_location = None
                self._hide_location = None
                self._route = []
                self._cached_planned_spots = None
                self._planning_done = False
                self._seed_gave_up = False
                for w in self._workers.values():
                    w.barracks_placed = None
                    w.barracks_issued_at = None
                    w.barracks_tag = None
                    w.reached_proxy = False
                    w.holding = False
                    w.route_idx = 0
                return
            # 重规划用完 max 次仍不行 → 松散兜底（本 slot 改 find_placement）
            logger.info(f"ProxyRax SCV {ws.tag} build patience expired → fallback find_placement")
            if ws.slot == 0 and not self._seed_gave_up:
                self._seed_gave_up = True
            ws.barracks_placed = None
            ws.barracks_issued_at = None
            ws.barracks_tag = None
            if not self._using_fallback:
                self._using_fallback = True
                logger.warning("ProxyRax: 重规划用完仍超时 → fallback find_placement globally")
            return

        # 发建造命令（同一落点幂等重发，直到建筑出现）。清 holding：要去建了，不再站桩。
        if self.ai.can_afford(UnitTypeId.BARRACKS):
            ws.holding = False
            worker.build(UnitTypeId.BARRACKS, ws.barracks_placed)

    def _release_scv(self, worker: Any, ws: _SCV) -> None:
        if ws.released:
            return
        ws.released = True
        self._do_release(worker)
        logger.info(f"ProxyRax SCV {ws.tag} released → join army")

    # ------------------------------------------------------------------
    # 地堡封锁流（blockade_enabled=True 时）
    # ------------------------------------------------------------------

    async def _step_blockade(self, worker: Any, ws: _SCV) -> None:
        """建完兵营后 SCV 走到敌方斜坡底修地堡封口。"""
        if ws.blockade_done:
            self._release_scv(worker, ws)
            return

        # 确定敌方斜坡底
        ramp_bottom = self._enemy_ramp_bottom()
        if ramp_bottom is None:
            self._release_scv(worker, ws)
            return

        # 走到斜坡底附近
        if worker.distance_to(ramp_bottom) > 10:
            worker.move(ramp_bottom)
            return

        # 到达斜坡底 → 建地堡（一次锁定落点）
        if not self.ai.can_afford(UnitTypeId.BUNKER):
            return

        if ws.blockade_placed is None:
            place = await self._safe_find_placement(UnitTypeId.BUNKER, ramp_bottom)
            if place is None:
                self._release_scv(worker, ws)
                return
            ws.blockade_placed = place
            ws.blockade_issued_at = self.ai.time
            logger.info(
                f"ProxyRax SCV {ws.tag} blockade bunker locked ({place.x:.1f},{place.y:.1f})"
            )

        # Patience 超时 → 放弃封锁，加入主力
        if (
            ws.blockade_issued_at is not None
            and self.ai.time - ws.blockade_issued_at > _BUILD_PATIENCE_S
        ):
            logger.info(f"ProxyRax SCV {ws.tag} blockade patience expired → join army")
            ws.blockade_done = True
            self._release_scv(worker, ws)
            return

        # 检查地堡是否建好
        for s in self.ai.structures(UnitTypeId.BUNKER):
            if s.position.distance_to(ws.blockade_placed) < 4.0:
                if s.build_progress >= 1.0:
                    ws.blockade_done = True
                    self._release_scv(worker, ws)
                return  # 在建，等待

        if self.ai.can_afford(UnitTypeId.BUNKER):
            worker.build(UnitTypeId.BUNKER, ws.blockade_placed)

    def _enemy_ramp_bottom(self) -> Point2 | None:
        try:
            enemy_main: Point2 = self.ai.enemy_start_locations[0]
            ramps = self.ai.game_info.map_ramps
            if not ramps:
                return None
            ramp = min(ramps, key=lambda r: r.top_center.distance_to(enemy_main))
            return ramp.bottom_center
        except Exception:
            return None
