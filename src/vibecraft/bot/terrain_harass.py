"""空军骚扰地形基础层（种族中性,所有空军骚扰复用）。

图谱 harass-doctrine 层 D70/D71/D79 + F114 真机探针结论落地:
- **安全集结点(矿后安全口袋)判定基 = 矿后悬崖(terrain_height 突降),不靠矿脉**
  (F114 真机:静态 pathing_grid 不编码矿脉,自然矿脉格 pg=1;可靠屏障是矿后悬崖:
   terrain_height 突降 + pg=0 + 地面 BFS 够不到)。
- 空军(凤凰/女妖/飞龙/BC…)飞哪都行、对它无"可达不可达";要判的是**敌方地面部队够不到 +
  出敌地面射程**的点——那就是矿脉背后越过悬崖那段,空军停那儿拉扯、地面爬不上来(F87/F112)。

纯几何 + 回调,单测友好(合成 terrain_height/pathing 即可测,不起 SC2)。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

# 判定常量(F114 真机:矿后+8 处高度从 ~220 突降到 ~130,落差 ~90;取保守阈值)
_CLIFF_DROP: float = 10.0  # terrain_height 相对矿线基准落这么多 → 越过悬崖 = 地面够不到
_POCKET_MIN_OUT: float = 3.0  # 从矿线中心沿矿后方向至少外推这么远才开始找(避开矿脉本身)
_POCKET_MAX_OUT: float = 16.0  # 最多外推这么远找悬崖(再远就出图/无意义)
_POCKET_STEP: float = 1.0  # 采样步长
_POCKET_PAST_EDGE: float = 1.5  # 找到悬崖边后再往外推一点,确保稳在低地侧(地面上不来)


def _norm_dir(fromp: Any, top: Any) -> tuple[float, float]:
    dx = float(top.x) - float(fromp.x)
    dy = float(top.y) - float(fromp.y)
    n = math.hypot(dx, dy) or 1.0
    return dx / n, dy / n


def _in_area(x: float, y: float, playable_area: Any) -> bool:
    try:
        return bool(
            playable_area.x <= x <= playable_area.x + playable_area.width
            and playable_area.y <= y <= playable_area.y + playable_area.height
        )
    except (AttributeError, TypeError):
        return True


def find_mineback_pocket(
    townhall: Any,
    mineral_center: Any,
    terrain_height: Callable[[Any], float],
    playable_area: Any,
    cliff_drop: float = _CLIFF_DROP,
    max_out: float = _POCKET_MAX_OUT,
    step: float = _POCKET_STEP,
) -> Any:
    """找一个矿的**矿后安全口袋**:矿线中心沿'远离基地'方向外推,第一处 terrain_height 相对
    矿线基准突降 >= cliff_drop 的点(越过悬崖=地面够不到),再往外推一点稳在低地侧。

    返回 Point2,或 None(平坦矿无悬崖 → 调用方回退,如矿后点/矿线锚点,别崩)。
    terrain_height 是回调(真机传 ai.get_terrain_height;单测传合成函数)。
    """
    from sc2.position import Point2

    ux, uy = _norm_dir(townhall, mineral_center)  # 基地→矿 方向 = 矿后方向
    try:
        base_h = float(terrain_height(mineral_center))
    except Exception:
        return None
    d = _POCKET_MIN_OUT
    while d <= max_out:
        x = float(mineral_center.x) + ux * d
        y = float(mineral_center.y) + uy * d
        if not _in_area(x, y, playable_area):
            return None
        try:
            h = float(terrain_height(Point2((x, y))))
        except Exception:
            h = base_h
        if base_h - h >= cliff_drop:  # 越过悬崖边
            px = float(mineral_center.x) + ux * (d + _POCKET_PAST_EDGE)
            py = float(mineral_center.y) + uy * (d + _POCKET_PAST_EDGE)
            if not _in_area(px, py, playable_area):
                px, py = x, y
            return Point2((px, py))
        d += step
    return None  # 无悬崖(平坦矿)→ None,调用方回退


def plateau_radius(
    center: Any,
    terrain_height: Callable[[Any], float],
    playable_area: Any,
    cliff_drop: float = _CLIFF_DROP,
    max_r: float = 24.0,
    step: float = 1.0,
) -> float:
    """从 center 各方向走到 terrain_height 突降(悬崖)的距离 = **高地(plateau)边缘半径**。

    8 方向射线,每条找第一处相对 center 基准高度落 >= cliff_drop 的距离,取**中位数**(robust,
    避免某方向缺口/长廊拉偏)。用作接近避障半径(避开敌方高地边缘=避所有可能建筑视野,F121),
    比拍脑袋固定半径更贴真实地形。取不到 → 兜底 _POCKET_MAX_OUT。
    """
    from sc2.position import Point2

    try:
        base_h = float(terrain_height(center))
    except Exception:
        return max_r
    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
    edges: list[float] = []
    for dx, dy in dirs:
        n = (dx * dx + dy * dy) ** 0.5
        ux, uy = dx / n, dy / n
        d = step
        hit = max_r
        while d <= max_r:
            x = float(center.x) + ux * d
            y = float(center.y) + uy * d
            if not _in_area(x, y, playable_area):
                hit = d
                break
            try:
                if base_h - float(terrain_height(Point2((x, y)))) >= cliff_drop:
                    hit = d
                    break
            except Exception:
                pass
            d += step
        edges.append(hit)
    edges.sort()
    return edges[len(edges) // 2]  # 中位数


def bfs_ground_reachable(
    seeds: list[Any],
    is_pathable: Callable[[tuple[int, int]], bool],
    bounds: tuple[int, int, int, int],
    max_cells: int = 20000,
) -> set[tuple[int, int]]:
    """从 seeds(可走格,整数 (x,y))对地面做 BFS,返回可达可走格集合。

    **seeds 必须是可走格**(F114 实现坑:基地位常是建筑 pg=0,从它 BFS 卡死→全不可达假象;
    真机用矿线中心/工兵位作 seed)。is_pathable((x,y))->bool。bounds=(x0,y0,x1,y1) 限范围。
    """
    from collections import deque

    x0, y0, x1, y1 = bounds
    seen: set[tuple[int, int]] = set()
    dq: deque[tuple[int, int]] = deque()
    for s in seeds:
        c = (round(float(s.x)), round(float(s.y)))
        if c not in seen and is_pathable(c):
            seen.add(c)
            dq.append(c)
    while dq and len(seen) < max_cells:
        x, y = dq.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if (nx, ny) in seen or not (x0 <= nx <= x1 and y0 <= ny <= y1):
                continue
            if is_pathable((nx, ny)):
                seen.add((nx, ny))
                dq.append((nx, ny))
    return seen


def point_ground_reachable(pt: Any, reachable: set[tuple[int, int]], radius: int = 0) -> bool:
    """pt(及其 radius 内格)是否在敌地面可达集里。radius>0 用于'射程内有无可达格'(F2)。"""
    cx, cy = round(float(pt.x)), round(float(pt.y))
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            if dx * dx + dy * dy <= radius * radius and (cx + dx, cy + dy) in reachable:
                return True
    return False


# ── 低地路由（F122 真解，2026-07-26；设计见 docs/plans/2026-07-26-phoenix-lowground-routing-design.md）──
# 凤凰接近/撤退走**低地**(敌方高地台面以外)不穿高地：敌方高地代价栅格 + A*(叠加动态 AA/军队惩罚)。
# 空军能飞任何格,非 pathable(悬崖/缺口)对空军是低代价好格;高地=敌军所在+建筑俯视处=高代价避开。
_NEI8: tuple[tuple[int, int, float], ...] = (
    (1, 0, 1.0),
    (-1, 0, 1.0),
    (0, 1, 1.0),
    (0, -1, 1.0),
    (1, 1, 1.41421356),
    (1, -1, 1.41421356),
    (-1, 1, 1.41421356),
    (-1, -1, 1.41421356),
)


def _bounds(playable_area: Any) -> tuple[int, int, int, int]:
    """playable_area → 整数格边界 (x0,y0,x1,y1)。取不到 → 一个大兜底框。"""
    try:
        x0 = math.floor(playable_area.x)
        y0 = math.floor(playable_area.y)
        x1 = math.ceil(playable_area.x + playable_area.width)
        y1 = math.ceil(playable_area.y + playable_area.height)
        return x0, y0, x1, y1
    except (AttributeError, TypeError):
        return 0, 0, 255, 255


def _clampi(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def _dedup_collinear_cells(pts: list[Any]) -> list[Any]:
    """删共线冗余点(保拐点),压缩 waypoint 数。与 drop_path._dedup_collinear 同逻辑,避跨模块引私有。"""
    if len(pts) <= 2:
        return list(pts)
    out: list[Any] = [pts[0]]
    for i in range(1, len(pts) - 1):
        ax, ay = float(out[-1].x), float(out[-1].y)
        bx, by = float(pts[i].x), float(pts[i].y)
        cx, cy = float(pts[i + 1].x), float(pts[i + 1].y)
        cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(cross) > 1e-6:
            out.append(pts[i])
    out.append(pts[-1])
    return out


def build_enemy_highground_cells(
    terrain_height: Callable[[Any], float],
    playable_area: Any,
    enemy_start: Any,
    my_start: Any,
    cliff_margin: float = 10.0,
) -> frozenset[tuple[int, int]]:
    """敌方高地格集合(**静态,一局一算,调用方缓存 by map**)。

    敌方高地格 = `terrain_height(cell) >= h_enemy - cliff_margin`(和敌基同台面) **且** cell 在**敌方半场**
    (离 enemy_start 比离 my_start 近)。这些格=敌军所在+建筑俯视处,凤凰路线该避(F121/F122)。
    **不用 enemy_ground_reachable 交集**(评审必改①:BFS 淹没整个连通地面、误罚自家高地)——纯几何半场
    门控才真正只针对敌方台面。空军能飞任何格,故只标'代价高'不标不可走。
    terrain_height 是回调(真机 ai.get_terrain_height;单测合成)。
    """
    from sc2.position import Point2

    x0, y0, x1, y1 = _bounds(playable_area)
    try:
        h_enemy = float(terrain_height(enemy_start))
    except Exception:
        return frozenset()
    thresh = h_enemy - cliff_margin
    ex, ey = float(enemy_start.x), float(enemy_start.y)
    mx, my = float(my_start.x), float(my_start.y)
    cells: set[tuple[int, int]] = set()
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            # 敌方半场:离敌 start 比离我 start 近(先判,省下大半 terrain_height 调用)
            if (x - ex) ** 2 + (y - ey) ** 2 >= (x - mx) ** 2 + (y - my) ** 2:
                continue
            try:
                if float(terrain_height(Point2((x, y)))) >= thresh:
                    cells.add((x, y))
            except Exception:
                pass
    return frozenset(cells)


def plan_lowground_path(
    start: Any,
    end: Any,
    high_cells: frozenset[tuple[int, int]],
    playable_area: Any,
    avoid_pts: list[Any] | None = None,
    high_penalty: float = 8.0,
    aa_penalty: float = 6.0,
    aa_r: float = 8.0,
    max_expand: int = 20000,
    max_detour: float = 1.7,
) -> list[Any] | None:
    """低地 A* 选路(F122 真解):在敌高地代价栅格 high_cells + 动态 AA/军队惩罚 avoid_pts 上找
    start→end 最小代价路径。空军全格连通、**恒有解**;超 max_expand 或 路径 > 直线×max_detour →
    返回 **None**(调用方回退 plan_air_path)。返回 waypoint list(含 start,end)或 None。

    cost(进入 cell) = 步长(1 或 √2) × (1 + high_penalty[cell∈high_cells] + aa_penalty[cell 距任一
    avoid_pt < aa_r])。启发式=到 goal 欧氏距(可采纳,A* 最优)。坐标取整与 bfs_ground_reachable 同
    `round(float(...))` 约定(评审风险:off-by-one)。
    """
    import heapq

    from sc2.position import Point2

    x0, y0, x1, y1 = _bounds(playable_area)
    sx = _clampi(round(float(start.x)), x0, x1)
    sy = _clampi(round(float(start.y)), y0, y1)
    gx = _clampi(round(float(end.x)), x0, x1)
    gy = _clampi(round(float(end.y)), y0, y1)
    if (sx, sy) == (gx, gy):
        return [start, end]
    straight = math.hypot(gx - sx, gy - sy) or 1.0
    avoid = [(float(p.x), float(p.y)) for p in (avoid_pts or [])]
    aa_r2 = aa_r * aa_r

    def cell_penalty(cx: int, cy: int) -> float:
        pen = high_penalty if (cx, cy) in high_cells else 0.0
        for ax, ay in avoid:
            if (cx - ax) ** 2 + (cy - ay) ** 2 < aa_r2:
                pen += aa_penalty
                break
        return pen

    openh: list[tuple[float, int, int]] = [(0.0, sx, sy)]
    g: dict[tuple[int, int], float] = {(sx, sy): 0.0}
    came: dict[tuple[int, int], tuple[int, int]] = {}
    expanded = 0
    found = False
    while openh:
        _, cx, cy = heapq.heappop(openh)
        if (cx, cy) == (gx, gy):
            found = True
            break
        expanded += 1
        if expanded > max_expand:
            return None
        cg = g[(cx, cy)]
        for dx, dy, base in _NEI8:
            nx, ny = cx + dx, cy + dy
            if not (x0 <= nx <= x1 and y0 <= ny <= y1):
                continue
            ng = cg + base * (1.0 + cell_penalty(nx, ny))
            if ng < g.get((nx, ny), 1e18):
                g[(nx, ny)] = ng
                came[(nx, ny)] = (cx, cy)
                heapq.heappush(openh, (ng + math.hypot(gx - nx, gy - ny), nx, ny))
    if not found:
        return None
    cells_path: list[tuple[int, int]] = [(gx, gy)]
    cur = (gx, gy)
    while cur in came:
        cur = came[cur]
        cells_path.append(cur)
    cells_path.reverse()
    plen = sum(
        math.hypot(cells_path[i + 1][0] - cells_path[i][0], cells_path[i + 1][1] - cells_path[i][1])
        for i in range(len(cells_path) - 1)
    )
    if plen > straight * max_detour:
        return None  # 绕大圈(F122 教训)→ 回退 snap
    pts: list[Any] = [Point2((c[0], c[1])) for c in cells_path]
    pts[0] = start
    pts[-1] = end
    return _dedup_collinear_cells(pts)


def path_highground_frac(
    path: list[Any],
    high_cells: frozenset[tuple[int, int]],
    samples_per_seg: int = 4,
) -> float:
    """路径落在**敌高地格**上的采样比例(验收指标,评审⑧:直接对应优化目标,比建筑视野 vis 更准)。
    越低=越贴低地走、越好。空路径/单点 → -1。"""
    if not path or len(path) < 2:
        return -1.0
    from itertools import pairwise

    total = 0
    on_high = 0
    for a, b in pairwise(path):
        for k in range(samples_per_seg):
            t = (k + 0.5) / samples_per_seg
            px = round(float(a.x) + (float(b.x) - float(a.x)) * t)
            py = round(float(a.y) + (float(b.y) - float(a.y)) * t)
            total += 1
            if (px, py) in high_cells:
                on_high += 1
    return on_high / total if total else -1.0
