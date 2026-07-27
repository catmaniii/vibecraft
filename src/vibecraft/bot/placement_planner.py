"""placement_planner.py - rigid cluster template building placement planner.

Finds N building spots near a target anchor using a fixed shape template,
verifying:
  1. All spots are in the placement grid (sync fast check)
  2. No overlapping footprints
  3. Optional power source (Pylon energy field) constraint
  4. After placing all buildings, SCV can still reach each building entrance
     (MUST-FIX 3: connectivity re-check with footprints blocked)
  5. can_place batch check (MUST-FIX 2: true buildability)
  6. query_pathings batch check (MUST-FIX 1: SCV pathable to each spot)
"""

from __future__ import annotations

import contextlib
import logging
import math
from collections import deque
from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

if TYPE_CHECKING:
    from sc2.bot_ai import BotAI
    from sc2.pixel_map import PixelMap

logger = logging.getLogger(__name__)

# 建筑1 螺旋搜索里，最多试几个可建种子（每个种子做一遍贴簇摆放 + 硬复核）
_SEED_LIMIT = 5
# 可达性排序时，最多对多少个可建候选查 query_pathing（控制网络往返数）
_SEED_RANK_POOL = 12
# 簇内跨屏障剔除：seed→贴簇落点的实际路径 > 直线×ratio + slack ⇒ 隔屏障，弃
_INTRA_DETOUR_RATIO = 2.2
_INTRA_DETOUR_SLACK = 6.0

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ordered_shifts(radius: int) -> list[tuple[float, float]]:
    """Enumerate integer offsets in [-radius, radius]^2 by Chebyshev distance."""
    result: list[tuple[float, float]] = []
    for r in range(radius + 1):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if max(abs(dx), abs(dy)) == r:
                    result.append((float(dx), float(dy)))
    return result


def _footprint_cells(center: Point2, half: int) -> list[tuple[int, int]]:
    """Integer grid cells occupied by a building footprint (2*half+1 square)."""
    cx = math.floor(center.x)
    cy = math.floor(center.y)
    return [(cx + dx, cy + dy) for dx in range(-half, half + 1) for dy in range(-half, half + 1)]


def _adjacent_cells(center: Point2, half: int) -> list[tuple[int, int]]:
    """Outer ring cells adjacent to the footprint (Chebyshev distance == half+1)."""
    cx = math.floor(center.x)
    cy = math.floor(center.y)
    outer = half + 1
    result = []
    for dx in range(-outer, outer + 1):
        for dy in range(-outer, outer + 1):
            if abs(dx) == outer or abs(dy) == outer:
                result.append((cx + dx, cy + dy))
    return result


def _nearest_pathable(
    grid: PixelMap, seed: tuple[int, int], max_r: int = 24
) -> tuple[int, int] | None:
    """Spiral out from seed to find the nearest pathable (v != 0) cell.

    Needed because the main-base center tile (start_location) is marked
    NON-pathable in the static pathing_grid (the townhall footprint), so
    flooding directly from it returns an empty set.
    """
    sx, sy = seed
    for r in range(max_r + 1):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if max(abs(dx), abs(dy)) != r:
                    continue
                x, y = sx + dx, sy + dy
                if 0 <= x < grid.width and 0 <= y < grid.height:
                    try:
                        if grid[x, y] != 0:
                            return (x, y)
                    except (AssertionError, IndexError):
                        continue
    return None


def _bfs_excluding(
    grid: PixelMap,
    start: tuple[int, int],
    excluded: set[tuple[int, int]],
) -> set[tuple[int, int]]:
    """8-connected BFS reachability, treating excluded cells as walls.

    Fallback when pathing_grid.copy().__setitem__ is unavailable.
    """
    width, height = grid.width, grid.height
    visited: set[tuple[int, int]] = set()
    queue: deque[tuple[int, int]] = deque([start])

    while queue:
        x, y = queue.popleft()
        if not (0 <= x < width and 0 <= y < height):
            continue
        if (x, y) in visited:
            continue
        if (x, y) in excluded:
            continue
        try:
            if grid[x, y] == 0:
                continue
        except (AssertionError, IndexError):
            continue
        visited.add((x, y))
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                if ddx == 0 and ddy == 0:
                    continue
                nxt = (x + ddx, y + ddy)
                if nxt not in visited:
                    queue.append(nxt)

    return visited


async def _check_connectivity(
    ai: BotAI,
    candidates: list[Point2],
    footprint: int,
    scv_origin: Point2,
) -> bool:
    """MUST-FIX 3: after blocking all footprints, each building must have
    at least one adjacent cell reachable by SCV from scv_origin.
    """
    half = footprint // 2
    excluded: set[tuple[int, int]] = set()
    for c in candidates:
        excluded.update(_footprint_cells(c, half))

    # Seed the flood from a PATHABLE cell near scv_origin. The main-base center
    # (start_location) is non-pathable in the static grid (townhall footprint),
    # so flooding from it directly returns an empty set (bug: everything fails).
    grid = ai.game_info.pathing_grid
    seed = (math.floor(scv_origin.x), math.floor(scv_origin.y))
    try:
        if grid[seed] == 0:
            seed = _nearest_pathable(grid, seed) or seed
    except (AssertionError, IndexError):
        alt = _nearest_pathable(grid, seed)
        if alt is not None:
            seed = alt

    # Primary path: pathing_grid.copy() + __setitem__ on the copy
    reachable_set: set[tuple[int, int]] | None = None
    try:
        grid_copy = ai.game_info.pathing_grid.copy()
        for x, y in excluded:
            # Out-of-bounds or value error; skip silently
            with contextlib.suppress(AssertionError, ValueError, IndexError):
                grid_copy[(x, y)] = 0
        raw: set[Point2] = grid_copy.flood_fill(Point2(seed), lambda v: v != 0)
        reachable_set = {(int(p.x), int(p.y)) for p in raw}
    except Exception as exc:
        logger.debug("_check_connectivity: copy+flood_fill failed (%s), using BFS fallback", exc)

    if reachable_set is None:
        # Fallback: BFS on original grid treating excluded cells as walls
        reachable_set = _bfs_excluding(ai.game_info.pathing_grid, seed, excluded)

    # Each building must have at least one adjacent reachable cell
    for c in candidates:
        adj = _adjacent_cells(c, half)
        if not any(cell in reachable_set for cell in adj):
            return False

    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def _batch_can_place(ai: BotAI, building: UnitTypeId, cands: list[Point2]) -> list[bool]:
    """引擎批量可建查询（can_place = can_place_single 的批量形式，同一引擎真源）。"""
    if not cands:
        return []
    try:
        return await ai.can_place(building, cands)
    except Exception as exc:
        logger.warning("plan_building_cluster: can_place error: %s", exc)
        return [False] * len(cands)


async def _place_adjacent(
    ai: BotAI,
    building: UnitTypeId,
    placed: list[Point2],
    min_dist: float,
    power_source: Point2 | None,
    power_radius: float,
) -> Point2 | None:
    """给已放的簇找**紧挨着**的下一个可建落点（贴簇滑动，就近优先）。

    候选 = 每个已放建筑的 8 个方向（cardinal 先=并排优先）× 由 spacing 向外滑动
    0..max_slide 格。过滤重叠 + power，按"到最近已放建筑的距离"升序（越贴越优先），
    引擎批量 can_place，取第一个能放的 → 始终贴着簇，不散开；窄口袋里能滑进去。
    """
    spacing = min_dist  # footprint+1，并排留 1 宽缝
    max_slide = 5
    # cardinal 在前（并排优先），diagonal 兜底
    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
    cands: list[Point2] = []
    seen: set[tuple[int, int]] = set()
    for p in placed:
        for dx, dy in dirs:
            for s in range(max_slide + 1):
                cx = p.x + dx * (spacing + s)
                cy = p.y + dy * (spacing + s)
                key = (round(cx * 2), round(cy * 2))
                if key in seen:
                    continue
                seen.add(key)
                c = Point2((cx, cy))
                # 不与已放建筑重叠（含 margin）
                if any(c.distance_to(q) < min_dist - 0.01 for q in placed):
                    continue
                # power 约束（神族）
                if power_source is not None and c.distance_to(power_source) > power_radius:
                    continue
                cands.append(c)

    # 就近优先：到最近已放建筑距离升序 → 贴簇最紧的排前面
    cands.sort(key=lambda c: min(c.distance_to(q) for q in placed))

    placeable = await _batch_can_place(ai, building, cands)
    for c, ok in zip(cands, placeable, strict=False):
        if ok:
            return c
    return None


async def plan_building_cluster(
    ai: BotAI,
    anchor: Point2,
    building: UnitTypeId,
    count: int,
    offset_variants: list[list[tuple[float, float]]] | None = None,  # 兼容旧签名，已忽略
    scv_origin: Point2 | None = None,
    reachable: set[Point2] | None = None,
    power_source: Point2 | None = None,
    power_radius: float = 6.5,
    footprint: int = 3,
    anchor_search_radius: int = 3,
) -> list[Point2] | None:
    """在 anchor 附近增量摆放 count 个**紧挨成簇**的建筑落点。

    算法（灵活增量摆放 + 保持挨着，取代旧的固定模板整块平移）：
      1. 建筑1：从 anchor 螺旋向外找**最近的可建点**（引擎 can_place），作为簇的种子。
         试前几个种子（容错：第一个种子后续复核不过就换下一个）。
      2. 建筑 k(2..count)：`_place_adjacent` 找**紧挨已放簇**的最近可建点，滑动到能放为止
         → 始终贴着簇，窄口袋容错高。
      3. 三条硬验证（全照旧）：
         - 每个候选 `can_place`（引擎真源）——步骤 1/2 内联。
         - **MUST-FIX 3** 建后连通复核：N 个 footprint 全置 0，从 scv_origin 重 flood_fill，
           每栋楼相邻格 ≥1 个 ∈ R'（不互相封路）。
         - **MUST-FIX 1** 每个 final 落点 `query_pathing(scv_origin, spot)` 引擎确认走得到。

    接口不变：返回 count 个确切落点，或放不下时 None。offset_variants 参数保留仅为
    兼容旧调用，现已忽略（不再用固定模板）。

    Args:
        ai: SC2 BotAI 实例（真机或 mock）。
        anchor: 目标区域中心。
        building: 建筑类型（如 UnitTypeId.BARRACKS）。
        count: 需要的建筑数。
        scv_origin: SCV 起点，用于连通复核 + query_pathing。None 则跳过这两项。
        reachable: 预计算 flood_fill 可达集（可复用；本算法内不直接用于过滤，
            连通复核每次自行重 flood）。
        power_source: 神族 Pylon 中心（能量场约束）。
        power_radius: 能量场半径（默认 6.5）。
        footprint: 建筑 footprint 尺寸（默认 3 = 3×3）。
        anchor_search_radius: 建筑1 种子螺旋搜索半径（格）。

    Returns:
        count 个 Point2 落点，或放不下返回 None。
    """
    # footprint+3=6.0：留 3 宽缝，每个建筑独立进场空间——间距 4(1宽缝)真局 3 SCV 并行建
    # 身体挤窄缝互堵(~30%成功)；6 提升到 ~2/3。仍比旧 spread(8) 紧凑。
    # 残留 ~1/3 是并行建造方差(建造执行问题，非选点)，待后续 staggered/sequential build 攻。
    min_dist = float(footprint + 3)  # 6.0，并排间距（独立进场，不互堵）

    # --- 建筑1 种子：anchor 螺旋向外的可建点（引擎批量 can_place，一趟）---
    seed_shifts = _ordered_shifts(anchor_search_radius + 2)  # 稍宽一点找种子
    seed_cands = [Point2((anchor.x + dx, anchor.y + dy)) for dx, dy in seed_shifts]
    if power_source is not None:
        seed_cands = [c for c in seed_cands if c.distance_to(power_source) <= power_radius]
    seed_place = await _batch_can_place(ai, building, seed_cands)
    placeable_seeds = [c for c, ok in zip(seed_cands, seed_place, strict=False) if ok]
    if not placeable_seeds:
        logger.warning(
            "plan_building_cluster FAILED anchor=(%.1f,%.1f) 无可建种子", anchor.x, anchor.y
        )
        return None

    # **可达性排序种子（关键）**：anchor 可能落在地形屏障的"远侧"——引擎 can_place 说能放、
    # query_pathing 也返回一条（绕远的）路，但 SCV 实际走不进去建（真局实测卡在 ~20 格外来回晃）。
    # 屏障近侧的点 SCV 一路直达、path 短；远侧的点要绕、path 长。故按 query_pathing(起点→种子)
    # 的**实际路径距离升序**挑种子 → 优先近侧可直达的点当 seed，簇就落在 SCV 够得到的一侧。
    if scv_origin is not None and len(placeable_seeds) > 1:
        scored: list[tuple[float, Point2]] = []
        for c in placeable_seeds[:_SEED_RANK_POOL]:
            try:
                pd = await ai._client.query_pathing(scv_origin, c)
            except Exception:
                pd = None
            if pd is None:
                continue  # 完全不可达，弃
            scored.append((float(pd), c))
        if scored:
            scored.sort(key=lambda t: t[0])
            seeds = [c for _, c in scored][:_SEED_LIMIT]
        else:
            seeds = placeable_seeds[:_SEED_LIMIT]
    else:
        seeds = placeable_seeds[:_SEED_LIMIT]

    for first in seeds:
        placed: list[Point2] = [first]
        ok = True
        for _k in range(1, count):
            nxt = await _place_adjacent(ai, building, placed, min_dist, power_source, power_radius)
            if nxt is None:
                ok = False
                break
            placed.append(nxt)
        if not ok:
            continue

        # MUST-FIX 3：建后连通复核（互相封路 → 换种子）
        if scv_origin is not None and not await _check_connectivity(
            ai, placed, footprint, scv_origin
        ):
            continue

        # MUST-FIX 1：每个 final 落点引擎寻路确认（资源感知）
        if scv_origin is not None:
            bad = False
            for c in placed:
                try:
                    dist = await ai._client.query_pathing(scv_origin, c)
                except Exception as exc:
                    logger.warning("plan_building_cluster: query_pathing error: %s", exc)
                    dist = None
                if dist is None:
                    bad = True
                    break
            if bad:
                continue

            # 簇内一致性（跨屏障剔除）：seed 已选成可达点；若某个贴簇落点与 seed 隔着
            # 地形屏障，query_pathing(seed→它) 会**绕远**（path ≫ 直线）。这种簇 SCV 建到
            # 一半就卡在屏障这侧够不到对侧那栋（真局实测的根因）。检测到 → 弃这个种子，
            # 换下一个；种子都不行 → 返回 None → 上层换下一个安全 anchor（可能在屏障近侧）。
            seed_pt = placed[0]
            spanning = False
            for c in placed[1:]:
                straight = seed_pt.distance_to(c)
                try:
                    pd = await ai._client.query_pathing(seed_pt, c)
                except Exception:
                    pd = None
                # pd=None（同侧极近有时引擎返 None）不算屏障；只在明显绕远时剔除
                if pd is not None and pd > straight * _INTRA_DETOUR_RATIO + _INTRA_DETOUR_SLACK:
                    spanning = True
                    break
            if spanning:
                continue

        return placed

    logger.warning(
        "plan_building_cluster FAILED anchor=(%.1f,%.1f) seeds=%d 复核全不过",
        anchor.x,
        anchor.y,
        len(seeds),
    )
    return None
