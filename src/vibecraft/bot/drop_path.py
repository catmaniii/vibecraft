# src/vibecraft/bot/drop_path.py
"""空投路径递归细分算法。

设计 §2:plan_drop_path(A, B, bot) 返回 waypoint list (含 A 和 B)。
- A→B 直线穿过"已确定敌方基地"(zone 有 known townhall) → 插入转折点 C
- C = M.position + (P-M).normalized() * (R+push), P 是 M 在 AB 上垂足
- 递归 max_depth=3 兜底,防 loop

"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

R_MINERAL_AVOID: float = 15.0  # zone 影响半径
PUSH_DIST: float = 5.0  # C 额外 buffer
MAX_DEPTH: int = 3  # 最多插入 3 个新点
# 2026-05-24 修:C 点离 playable_area 边界 buffer。
# 原 bug:C 点算到 playable 外(y > y_max),棱镜飞不到永远卡在 playable edge。
_PLAYABLE_CLEARANCE: float = 2.0

# sharpy zone "has known townhall" 检测的 unit type
_ENEMY_TOWNHALL_NAMES: frozenset[str] = frozenset(
    {
        "NEXUS",
        "HATCHERY",
        "LAIR",
        "HIVE",
        "COMMANDCENTER",
        "ORBITALCOMMAND",
        "PLANETARYFORTRESS",
    }
)


def _get_townhall_type_ids() -> set:
    """延迟 import sc2.UnitTypeId(测试 mock 友好)。"""
    try:
        from sc2.ids.unit_typeid import UnitTypeId

        return {getattr(UnitTypeId, n) for n in _ENEMY_TOWNHALL_NAMES if hasattr(UnitTypeId, n)}
    except ImportError:
        return set()


def _zone_has_known_townhall(zone: Any) -> bool:
    """zone.known_enemy_units 含 own townhall(玩家已侦察到对方基地)?"""
    try:
        return bool(zone.known_enemy_units.of_type(_get_townhall_type_ids()).exists)
    except Exception:
        return False


def project_point_onto_segment(M: Any, A: Any, B: Any) -> Any:
    """M 在线段 AB 上的垂足(超出段时 clamp 到端点)。"""
    from sc2.position import Point2

    ax, ay = A.x, A.y
    bx, by = B.x, B.y
    mx, my = M.x, M.y
    abx = bx - ax
    aby = by - ay
    ab_len_sq = abx * abx + aby * aby
    if ab_len_sq < 1e-9:
        return Point2((ax, ay))  # A==B
    amx = mx - ax
    amy = my - ay
    t = (amx * abx + amy * aby) / ab_len_sq
    t = max(0.0, min(1.0, t))  # clamp [0,1]
    return Point2((ax + t * abx, ay + t * aby))


def first_blocking_zone(A: Any, B: Any, bot: Any, R: float = R_MINERAL_AVOID) -> Any | None:
    """找第一个被 AB 段穿过(距 < R)的敌方基地 zone。

    2026-05-24 修:zones[0](敌方主基地)位置开局已知(enemy_start_locations),
    无条件当 blocker;不要求 _zone_has_known_townhall。
    其他扩张点保留侦察过滤 —— 没侦察前不知道对方扩没扩,绕行无意义。
    """
    try:
        zones = bot.knowledge.zone_manager.enemy_expansion_zones
    except AttributeError:
        return None
    if not zones:
        return None
    for idx, z in enumerate(zones):
        # zones[0] = enemy_main:位置开局就 known,无条件 block。
        # 其他 zone:必须侦察到 townhall(否则可能对方根本没扩到这,绕也白绕)。
        if idx > 0 and not _zone_has_known_townhall(z):
            continue
        try:
            M = z.center_location
        except AttributeError:
            continue
        P = project_point_onto_segment(M, A, B)
        d = ((P.x - M.x) ** 2 + (P.y - M.y) ** 2) ** 0.5
        if d < R:
            return z
    return None


def _clamp_to_playable(p: Any, bot: Any) -> Any:
    """把点 clamp 到 playable_area 内,留 _PLAYABLE_CLEARANCE 格 buffer。

    2026-05-24 修:原 plan_drop_path 算 C = M + dir*20 可能落到地图外
    (e.g., y=139 但 playable y_max=134),棱镜飞不到永远卡在 playable edge。
    """
    from sc2.position import Point2

    try:
        pa = bot.game_info.playable_area
        x_min = pa.x + _PLAYABLE_CLEARANCE
        x_max = pa.x + pa.width - _PLAYABLE_CLEARANCE
        y_min = pa.y + _PLAYABLE_CLEARANCE
        y_max = pa.y + pa.height - _PLAYABLE_CLEARANCE
    except (AttributeError, TypeError):
        return p  # 取不到 playable → 不动
    return Point2((max(x_min, min(x_max, p.x)), max(y_min, min(y_max, p.y))))


def plan_drop_path(A: Any, B: Any, bot: Any, depth: int = 0, max_depth: int = MAX_DEPTH) -> list:
    """A→B 路径细分。返回 waypoint list (含 A 和 B)。

    递归:AB 穿过 blocker → 插入 C → 拆 A→C, C→B。
    depth 兜底防 loop。
    """
    from sc2.position import Point2

    if depth >= max_depth:
        return [A, B]
    M_zone = first_blocking_zone(A, B, bot)
    if M_zone is None:
        return [A, B]
    M = M_zone.center_location
    P = project_point_onto_segment(M, A, B)
    # C 方向:P-M 归一化(远离 M)。若 P==M(AB 穿过 M)用垂直 AB 方向。
    dx = P.x - M.x
    dy = P.y - M.y
    norm = (dx * dx + dy * dy) ** 0.5
    if norm < 1e-6:
        # 用 AB 垂直方向
        abx = B.x - A.x
        aby = B.y - A.y
        ab_n = (abx * abx + aby * aby) ** 0.5
        if ab_n < 1e-6:
            return [A, B]
        dx, dy = -aby / ab_n, abx / ab_n
    else:
        dx, dy = dx / norm, dy / norm
    push = R_MINERAL_AVOID + PUSH_DIST
    C = Point2((M.x + dx * push, M.y + dy * push))
    # 2026-05-24 修:C clamp 到 playable_area 内,否则棱镜飞到边界永远卡。
    C = _clamp_to_playable(C, bot)
    left = plan_drop_path(A, C, bot, depth + 1, max_depth)
    right = plan_drop_path(C, B, bot, depth + 1, max_depth)
    return left[:-1] + right  # 去重 C


# ── BC 群体骚扰接近路径（#581，2026-07-03）────────────────────────────────────
# 与空投同思路（垂距避障），但：①避障中心由调用方显式传（只避敌方主基地）；
# ②终点走"场外集结点 stage → 矿后点"两段，保证从矿背后/外侧切入（几何构造，不靠运气）；
# ③退化(三点近共线)时选"更贴地图边"那侧，不推向中央（评审 #2）。

_HARASS_STAGE_OUT: float = 8.0  # 矿线中心沿"远离基地"方向外推，得场外集结点
_HARASS_AVOID_R: float = 13.0  # 主基地视野避障半径（UNVERIFIED 调参：主基地视野 ~9-11 + buffer）
_HARASS_PUSH: float = 5.0  # 避障拐点额外 buffer


def _map_center(playable_area: Any) -> Any:
    from sc2.position import Point2

    return Point2(
        (
            playable_area.x + playable_area.width / 2.0,
            playable_area.y + playable_area.height / 2.0,
        )
    )


def _clamp_point_to_area(p: Any, playable_area: Any, clearance: float = _PLAYABLE_CLEARANCE) -> Any:
    """把点 clamp 进 playable_area（直接吃 playable_area，不依赖 bot）。"""
    from sc2.position import Point2

    try:
        x_min = playable_area.x + clearance
        x_max = playable_area.x + playable_area.width - clearance
        y_min = playable_area.y + clearance
        y_max = playable_area.y + playable_area.height - clearance
    except (AttributeError, TypeError):
        return p
    return Point2((max(x_min, min(x_max, p.x)), max(y_min, min(y_max, p.y))))


def plan_avoid_path(
    start: Any,
    end: Any,
    avoid_centers: list,
    playable_area: Any,
    r_avoid: float = _HARASS_AVOID_R,
    push: float = _HARASS_PUSH,
    max_depth: int = MAX_DEPTH,
    depth: int = 0,
) -> list:
    """start→end 直线；若 avoid_centers 里某中心距线段 < r_avoid → 垂直推拐点 C 绕过，递归细分。

    退化（线段穿过中心，垂足≈中心）：用线段垂直方向，从 ±perp 两侧候选里选**离地图中心更远**
    （更贴地图边）那个，避免把路径推向中央（评审 #2）。C clamp 进 playable。max_depth 防 loop。
    """
    from sc2.position import Point2

    if depth >= max_depth or not avoid_centers:
        return [start, end]

    # 第一个挡路中心
    blocker = None
    for M in avoid_centers:
        P = project_point_onto_segment(M, start, end)
        d = ((P.x - M.x) ** 2 + (P.y - M.y) ** 2) ** 0.5
        if d < r_avoid:
            blocker = (M, P)
            break
    if blocker is None:
        return [start, end]

    M, P = blocker
    reach = r_avoid + push
    dx = P.x - M.x
    dy = P.y - M.y
    norm = (dx * dx + dy * dy) ** 0.5
    if norm < 1e-6:
        # 退化：线段穿过 M → 垂直 AB 方向，选更贴边（离地图中心更远）那侧
        abx = end.x - start.x
        aby = end.y - start.y
        ab_n = (abx * abx + aby * aby) ** 0.5
        if ab_n < 1e-6:
            return [start, end]
        perp_x, perp_y = -aby / ab_n, abx / ab_n
        c_plus = Point2((M.x + perp_x * reach, M.y + perp_y * reach))
        c_minus = Point2((M.x - perp_x * reach, M.y - perp_y * reach))
        mc = _map_center(playable_area)
        d_plus = (c_plus.x - mc.x) ** 2 + (c_plus.y - mc.y) ** 2
        d_minus = (c_minus.x - mc.x) ** 2 + (c_minus.y - mc.y) ** 2
        C = c_plus if d_plus >= d_minus else c_minus
    else:
        C = Point2((M.x + dx / norm * reach, M.y + dy / norm * reach))
    C = _clamp_point_to_area(C, playable_area)

    left = plan_avoid_path(
        start, C, avoid_centers, playable_area, r_avoid, push, max_depth, depth + 1
    )
    right = plan_avoid_path(
        C, end, avoid_centers, playable_area, r_avoid, push, max_depth, depth + 1
    )
    return left[:-1] + right  # 去重 C


def harass_stage_point(
    mineral_center: Any,
    townhall: Any,
    playable_area: Any = None,
    stage_out: float = _HARASS_STAGE_OUT,
) -> Any:
    """场外集结点 stage = 矿线中心沿"远离 townhall"方向外推 stage_out（矿背基地一侧开阔地）。

    `plan_harass_approach` 与 GroupHarassAct 的「入队判定」共用此点，确保同源不漂。
    playable_area 给了就 clamp（进图内）；不给就不 clamp（入队距离判定 ~5 格无所谓边界）。
    """
    from sc2.position import Point2

    dx = mineral_center.x - townhall.x
    dy = mineral_center.y - townhall.y
    n = (dx * dx + dy * dy) ** 0.5
    if n < 1e-6:
        stage = mineral_center
    else:
        stage = Point2(
            (mineral_center.x + dx / n * stage_out, mineral_center.y + dy / n * stage_out)
        )
    if playable_area is not None:
        stage = _clamp_point_to_area(stage, playable_area)
    return stage


def plan_harass_approach(
    start: Any,
    mineral_center: Any,
    townhall: Any,
    behind_point: Any,
    enemy_main_center: Any,
    playable_area: Any,
    stage_out: float = _HARASS_STAGE_OUT,
    r_avoid: float = _HARASS_AVOID_R,
    push: float = _HARASS_PUSH,
) -> list:
    """② 骚扰接近：`start → (避主基地) → 场外集结点 stage → 矿后点 behind`。

    stage = mineral_center 沿"远离 townhall"方向外推 stage_out（恒在矿线背基地一侧的开阔地）
    → 保证末段 stage→behind 从矿**背后/外侧**切入（不从基地头顶压过）。
    start→stage 走 plan_avoid_path 绕开 enemy_main（贴其视野边缘）。
    返回 [start, …避障拐点…, stage, behind_point]（一次锁定，调用方缓存幂等重发）。
    """
    stage = harass_stage_point(mineral_center, townhall, playable_area, stage_out)

    avoid = [enemy_main_center] if enemy_main_center is not None else []
    path = plan_avoid_path(start, stage, avoid, playable_area, r_avoid, push)
    # 末段：stage → 矿后点（stage≈behind 时不重复）
    if stage.distance_to(behind_point) > 1.0:
        path.append(behind_point)
    return path


def plan_edge_path(
    start: Any,
    target: Any,
    playable_area: Any,
    enemy_start: Any,
) -> list:
    """贴地图矩形边界接近路径（BC 骚扰用，贴边=晚被发现）。

    沿 playable_area 矩形周长走，取离 enemy_start 更远的那条弧（最隐蔽）。
    返回 [start, 边界投影点?, 中间角点…, 目标边界点?, target]。
    BC 逐点飞，全程贴外围，避开敌方中央视野。

    注意：此函数本身无状态；调用方负责按 (bc.tag, target_key) 一次锁定缓存，
    中途幂等重发同一串（CLAUDE.md 强规则）。
    """
    from sc2.position import Point2

    try:
        x0 = float(playable_area.x)
        y0 = float(playable_area.y)
        W = float(playable_area.width)
        H = float(playable_area.height)
    except (AttributeError, TypeError):
        return [start, target]

    x1, y1 = x0 + W, y0 + H
    perim = 2.0 * (W + H)

    # 4 corners in CW order with their perimeter-parameter t:
    # BL(t=0) → BR(t=W) → TR(t=W+H) → TL(t=2W+H) → back to BL(t=perim)
    _corners_with_t = [
        (0.0, Point2((x0, y0))),
        (W, Point2((x1, y0))),
        (W + H, Point2((x1, y1))),
        (2 * W + H, Point2((x0, y1))),
    ]

    def to_t(p: Any) -> float:
        """Project p onto nearest perimeter edge, return CW parameter t ∈ [0, perim)."""
        px = max(x0, min(x1, float(p.x)))
        py = max(y0, min(y1, float(p.y)))
        d_b = abs(py - y0)
        d_r = abs(px - x1)
        d_t = abs(py - y1)
        d_l = abs(px - x0)
        md = min(d_b, d_r, d_t, d_l)
        if md == d_b:
            return px - x0
        if md == d_r:
            return W + (py - y0)
        if md == d_t:
            return W + H + (x1 - px)
        return W + H + W + (y1 - py)

    def t_to_point(t: float) -> Point2:
        t = t % perim
        if t <= W:
            return Point2((x0 + t, y0))
        t -= W
        if t <= H:
            return Point2((x1, y0 + t))
        t -= H
        if t <= W:
            return Point2((x1 - t, y1))
        t -= W
        return Point2((x0, y1 - t))

    def corners_cw_between(t_from: float, t_to: float) -> list:
        """Corner points (in CW order from t_from) strictly between t_from and t_to."""
        result: list[tuple[float, Any]] = []
        for ct, c in _corners_with_t:
            if t_from < t_to:
                if t_from < ct < t_to:
                    result.append((ct, c))
            else:
                # Arc wraps around t=0
                if ct > t_from or ct < t_to:
                    eff = ct if ct > t_from else ct + perim
                    result.append((eff, c))
        result.sort(key=lambda x: x[0])
        return [c for _, c in result]

    t_s = to_t(start)
    t_e = to_t(target)

    # Skip if projections coincide (same perimeter point)
    if abs(t_s - t_e) < 1e-6:
        return [start, target]

    # CW arc: t_s → t_e going clockwise (increasing t mod perim)
    cw_corners = corners_cw_between(t_s, t_e)
    # CCW arc: reverse of CW from t_e → t_s
    ccw_corners = list(reversed(corners_cw_between(t_e, t_s)))

    proj_s = t_to_point(t_s)
    proj_e = t_to_point(t_e)

    def arc_centroid_dist_to_enemy(midpoints: list) -> float:
        all_pts = [proj_s, *midpoints, proj_e]
        cx = sum(float(p.x) for p in all_pts) / len(all_pts)
        cy = sum(float(p.y) for p in all_pts) / len(all_pts)
        ex, ey = float(enemy_start.x), float(enemy_start.y)
        return ((cx - ex) ** 2 + (cy - ey) ** 2) ** 0.5

    chosen = (
        cw_corners
        if arc_centroid_dist_to_enemy(cw_corners) >= arc_centroid_dist_to_enemy(ccw_corners)
        else ccw_corners
    )

    path: list = [start]
    if start.distance_to(proj_s) > 2.0:
        path.append(proj_s)
    path.extend(chosen)
    if proj_e.distance_to(target) > 2.0:
        path.append(proj_e)
    path.append(target)
    return path


# ── 地形感知空军接近选路（D60，2026-07-25；独立设计评审后改 snap 版）──────────────
# 真正落地"空军骚扰只走地面部队去不了的地方"(图谱 D56/D60)。评审(见 docs 复盘)否掉了
# 全局 A*(会重演被否的 plan_edge_path 绕大圈、丢矿后切入角、极性未验证),改**局部 snap**：
# 在已真机验证的几何路径(plan_avoid_path，保矿后切入)上，把每个中间点局部 snap 到最近的
# **地面不可走(悬崖)格**——半径受限，只做局部贴崖、不全局改道，天然不会甩去地图边绕大圈。
# 极性(哪个值=可走)不自己猜:调用方传 is_pathable 回调(= ai.in_pathing_grid，其"True=地面
# 可走"已被 proxy/nydus 等真机功能验证),本模块只问"这格地面军到不了吗"。
# **scope 只避地面拦截,不避对空(AA)**——AA 规避由凤凰 fight/flee 门另管。
_AIR_SNAP_RADIUS: float = (
    6.0  # 向最近悬崖格 snap 的搜索半径(格)；半径受限=不绕大圈(避免重演被否的 edge_path)
)
_AIR_SNAP_SEG: float = 5.0  # base 路径致密化间距；相邻 snap 点靠近 → 段间少穿地面
_AIR_SNAP_BUDGET: float = 1.4  # 单点 snap 局部增长比上限(prev→snap→next ≤ prev→cur→next 的此倍)
_AIR_MAX_DETOUR: float = (
    1.35  # 全局绕路守卫(评审 F2)：snap 后总长 > base × 此值 → 弃 snap 用 base(别慢慢绕到)
)


def _polyline_len(pts: list) -> float:
    """折线总长。"""
    from itertools import pairwise

    if len(pts) < 2:
        return 0.0
    return sum(_seg_len(a, b) for a, b in pairwise(pts))


def _pathable_safe(is_pathable: Any, p: Any) -> bool:
    """is_pathable(p) 兜底：True=地面可走。异常/取不到当"可走"(→ 不往那 snap，保守)。"""
    try:
        return bool(is_pathable(p))
    except Exception:
        return True


def _seg_len(a: Any, b: Any) -> float:
    return ((float(a.x) - float(b.x)) ** 2 + (float(a.y) - float(b.y)) ** 2) ** 0.5


def _densify(path: list, seg: float) -> list:
    """把折线按 seg 间距插点(含原顶点)。相邻点靠近 → snap 后段间少穿地面。"""
    from itertools import pairwise

    from sc2.position import Point2

    if len(path) < 2:
        return list(path)
    out: list = [path[0]]
    for a, b in pairwise(path):
        d = _seg_len(a, b)
        n = int(d // seg)
        for k in range(1, n + 1):
            t = (k * seg) / d
            if t >= 1.0:
                break
            out.append(
                Point2(
                    (
                        float(a.x) + (float(b.x) - float(a.x)) * t,
                        float(a.y) + (float(b.y) - float(a.y)) * t,
                    )
                )
            )
        out.append(b)
    return out


def _snap_to_cliff(
    cur: Any,
    prev: Any,
    nxt: Any,
    is_pathable: Any,
    playable_area: Any,
    radius: float,
    budget: float,
) -> Any:
    """把 cur 局部 snap 到半径内最近的悬崖格(地面不可走)——只在不显著增长局部路径时。

    cur 已在悬崖上 → 原样返回。否则扫半径内的整数格,取"地面不可走"且 prev→cand→nxt 长度
    ≤ prev→cur→nxt × budget 的候选里增长最小的那个。找不到 → 原样返回(不硬 snap)。
    """
    from sc2.position import Point2

    if not _pathable_safe(is_pathable, cur):
        return cur  # 已在悬崖
    base_len = _seg_len(prev, cur) + _seg_len(cur, nxt)
    r = int(radius)
    cx, cy = round(float(cur.x)), round(float(cur.y))
    best = cur
    best_len: float | None = None
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            if dx * dx + dy * dy > radius * radius:
                continue
            cand = Point2((cx + dx, cy + dy))
            if not _in_playable(cand, playable_area):
                continue
            if _pathable_safe(is_pathable, cand):
                continue  # 仍是地面 → 不是我们要的悬崖
            new_len = _seg_len(prev, cand) + _seg_len(cand, nxt)
            if new_len <= base_len * budget and (best_len is None or new_len < best_len):
                best_len = new_len
                best = cand
    return best


def _in_playable(p: Any, playable_area: Any) -> bool:
    try:
        return bool(
            playable_area.x <= float(p.x) <= playable_area.x + playable_area.width
            and playable_area.y <= float(p.y) <= playable_area.y + playable_area.height
        )
    except (AttributeError, TypeError):
        return True


def _dedup_collinear(pts: list) -> list:
    """删掉共线冗余点(保留被 snap 出的拐点),压缩 waypoint 数。"""
    if len(pts) <= 2:
        return list(pts)
    out: list = [pts[0]]
    for i in range(1, len(pts) - 1):
        ax, ay = float(out[-1].x), float(out[-1].y)
        bx, by = float(pts[i].x), float(pts[i].y)
        cx, cy = float(pts[i + 1].x), float(pts[i + 1].y)
        cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(cross) > 1e-6:  # 非共线 → 是拐点,保留
            out.append(pts[i])
    out.append(pts[-1])
    return out


def plan_air_path(
    start: Any,
    end: Any,
    avoid_centers: list,
    is_pathable: Any,
    playable_area: Any,
    r_avoid: float = _HARASS_AVOID_R,
    snap_radius: float = _AIR_SNAP_RADIUS,
    seg: float = _AIR_SNAP_SEG,
    budget: float = _AIR_SNAP_BUDGET,
    max_detour: float = _AIR_MAX_DETOUR,
) -> list:
    """地形感知空军选路(snap 版,图谱 D60)：在几何路径上把中间点贴到最近悬崖格。

    base = plan_avoid_path(保矿后切入的已验证几何路径) → 致密化 → 每个中间点局部 snap 到最近
    地面不可走(悬崖)格(半径受限,不全局改道/不绕大圈) → 去共线冗余。**全局绕路守卫(评审 F2)**:
    snap 后总长 > base × max_detour 就丢弃 snap、回退 base——防重演被否的"慢慢绕大圈到"。
    is_pathable=可走判定回调(用 ai.in_pathing_grid),None → 回退纯几何 plan_avoid_path。
    **scope 只避地面拦截,不避 AA。** 一次算好,调用方按目标 key 缓存幂等重发。
    """
    base = plan_avoid_path(start, end, avoid_centers, playable_area, r_avoid)
    if is_pathable is None or len(base) < 2:
        return base  # 无地形回调 → 纯几何
    # base 即便只有 [start,end]，致密化后也有中间点可 snap（直线段正是最需要贴悬崖处）
    dense = _densify(base, seg)
    if len(dense) < 3:
        return base
    out: list = [dense[0]]
    for i in range(1, len(dense) - 1):
        out.append(
            _snap_to_cliff(
                dense[i], out[-1], dense[i + 1], is_pathable, playable_area, snap_radius, budget
            )
        )
    out.append(dense[-1])
    snapped = _dedup_collinear(out)
    # 全局绕路守卫(评审 F2)：snap 相对 base 增长过多 = 病态绕路(慢慢到) → 弃 snap 用 base
    if _polyline_len(snapped) > _polyline_len(base) * max_detour:
        return base
    return snapped


def air_path_ground_frac(path: list, is_pathable: Any, samples_per_seg: int = 4) -> float:
    """路径落在**地面可走**格上的采样比例(真机 trace 验证用;越低=越贴悬崖走、越好)。

    每段等距采样 samples_per_seg 点,查 is_pathable。取不到回调 → -1。**注(评审 F9):这是代理
    指标,会被"贴无用远崖"刷低,必须配路径长度/直线比 + 真机存活/杀农民一起看,别单独当门。**
    """
    if is_pathable is None or not path or len(path) < 2:
        return -1.0
    from itertools import pairwise

    from sc2.position import Point2

    total = 0
    on_ground = 0
    for a, b in pairwise(path):
        for k in range(samples_per_seg):
            t = (k + 0.5) / samples_per_seg
            p = Point2(
                (
                    float(a.x) + (float(b.x) - float(a.x)) * t,
                    float(a.y) + (float(b.y) - float(a.y)) * t,
                )
            )
            total += 1
            if _pathable_safe(is_pathable, p):
                on_ground += 1
    return on_ground / total if total else -1.0
