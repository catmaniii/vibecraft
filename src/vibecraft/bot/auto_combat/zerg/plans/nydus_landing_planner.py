"""坑道虫落点规划器（NydusLandingPlanner）——2026-07-12 P1 重构。

把原 `_BuildNydusCanalAtEnemy` 里揉在一起的"落点选择 / 窗口检测 / 兜底"抽成**纯挑点模块**
（副作用薄：不移 OL、不下 BUILD_NYDUSWORM，只算候选 + 选点 + 管 blacklist/lock/canal_lost）。
真正的 `network(BUILD_NYDUSWORM, pos)` 由薄 act `_BuildNydusCanalAtEnemy` 调本模块拿点后下。

设计真理源：`docs/plans/2026-07-12-nydus-landing-multiwave-design.md`。核心不变量：

- **下 canal 的门 = ② 落点有视野 ∧ ④ 敌方主力不在落点区**（局部威胁 ≤ 阈值）。
  主力在家即使有视野也别强下（14s 钻出必被秒）；主力不在哪怕佯攻没到位也下。
- **落点按打击价值排**：矿后屠农民优先 → 矿线 → OL 位置兜底。撤退方向**不进**排序
  （有 canal 怎么都能撤，落点只看打击价值 + 当前窗口）。
- **"矿后"锚点几何自算**（`center.towards(minerals.center, 9)`，sharpy 同公式），**不用**
  `zone.behind_mineral_positions`——它对敌方 zone 构造期恒空（`ai.mineral_field` 需视野）。
  矿脉质心来自 `ai.expansion_locations_dict`（几何已知，开局就有，不依赖视野）。
- **命中即锁坐标快照**（非活 OL 实时 position），之后幂等重发同一坐标；仍**每帧 is_visible 复查**
  （踩过 170 次对不可见死点空放）。
- **canal 被拆** → `notify_canal_lost` 拉黑该点 + 清 lock 重选（声东击西换点）。
- **wave_intent**（玩家 `attack_mode_override`）：`all_in`(COMMIT) 容忍落点区少量残敌硬下 + 不查
  per-tile 局部威胁；`probe`/`None`(PROBE) 严格等 ④。**②（有视野）任何模式都不放宽**。
"""

from __future__ import annotations

import contextlib
import logging
import math
from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from vibecraft.bot.unit_kind import is_worker as _is_worker

logger = logging.getLogger(__name__)

# ── 几何锚点参数（sharpy Zone 同公式，见 vendor/.../general/zone.py:120-146）──
_BEHIND_MINERAL_OFFSET: float = 9.0  # center 朝矿脉方向外扩 = 矿后死角
_MINERAL_LINE_PULLBACK: float = 4.0  # 矿后再朝 center 拉回 = 矿线（农民处）
_ENEMY_MAIN_MATCH_D: float = 6.0  # expansion_locations_dict key 与主基 center 的匹配容差

# ── 门 ④：敌方主力"在不在落点区"──
_WINDOW_RADIUS: float = 16.0  # "主力在落点区"搜索半径
_WINDOW_MAX_NEARBY: int = 2  # 半径内非农民敌军 ≤ 此 → 主力不在（PROBE）
_WINDOW_MAX_NEARBY_COMMIT: int = 6  # COMMIT 容忍更多残敌硬下

# ── per-tile 局部威胁（仅 PROBE 查；COMMIT 不查）──
_THREAT_AVOID_RADIUS: float = 6.0
_THREAT_MAX_NEARBY: int = 1

# ── ④ 门时间兜底（2026-07-12 用户"只要落地率"）──
_ARMY_GATE_FALLBACK_S: float = 25.0  # drop 就绪 + ④「主力不在」阻塞超此秒数 → 绕过 ④ 硬落最空格

# ── 被拆点拉黑 / OL 兜底 ──
_BLACKLIST_RADIUS: float = 3.0  # 拉黑半径（避免残骸未散时选回死亡点）
# 拉黑时效:过了就允许重用该点。永久拉黑会把"全场唯一验证过可落的点"永远废掉(2026-07-26 真局)。
_BLACKLIST_TTL_S: float = 60.0
_OLVIS_TILES_PER_OL: int = 6  # 每只 OL 贡献几个"视野内够得着"的候选格(原来只取最近 1 个)
_OL_NEAR_ENEMY_D: float = 30.0  # OL 算"靠近敌方"的距离

# ── 地形栅格常量（与 scripts/nydus_terrain_probe.py 同源，真机验证过 2026-07-12）──
#    高地 = terrain_height 同高度连片；边缘 = 高地格里挨着"低一截悬崖"的那些。
_SCAN_R: int = 18  # 敌方主基周围扫描半径(格)
_H_TOL: int = 6  # |高度-基准| <= 此 → 算同一高地
_CLIFF_DROP: int = 12  # 邻格高度比基准低这么多 → 悬崖下方 → 本格是高地边缘
_EDGE_NBR: int = 2  # 边缘判定:查周围这么多格内有没有悬崖下方
_OL_SIGHT: float = 11.0  # OL 视野半径（F7 探针实测）
# 边缘落点顺悬崖外推。2026-07-12 真机诊断:push=10 → OL 离边缘 ~10-12 卡视野极限外沿、落不了;
# push=7 落点稳定可见但 OL 太贴边、主力在家时被逼得给不上视野。**取 9**:OL 离边缘 9 格 —— 落点仍
# 在视野 11 内(9<11 可见 ✓),又在女王防空射程(~7)外偷看不挨打;配合 scout flee 半径降到 7(只躲进
# 射程的近敌),OL 能从射程外给防守边缘供视野、force-land 才做得成。见推理图谱 F13/D1/D8。
_OL_PUSH: float = 9.0
_SECTOR_COUNT: int = 5  # D3：静态边缘按角度分几个扇区（4-6），每扇区一只 OL 分散冗余

# ── D102：小高台驻守点（2026-07-26 用户在可视化图上圈出并指定）──────────────────
#    敌方主基外围常有一小块与主基**不相连**的高台（探针实测:破晓黎明西侧 ~3x3、高度 223、
#    离最近可落格 10.8 格 < 视野 11，见推理图谱 F158）。OL 停这种高台正上方比停开阔低地好:
#    高地遮蔽低地视野（F9 的反面）→ 更难被发现，且够得着落点。找不到才退回 D1 的外推 9 格。
_PERCH_SCAN_R: int = 30  # 找小高台的扫描半径（比 _SCAN_R 大：高台常在主基外围更远处）
_PERCH_MAX_AREA: int = 60  # 连通块面积 ≤ 此才算"小"高台（更大的是主基高地本身/二矿台地）
_PERCH_MIN_AREA: int = 2  # 太小（单格毛刺）不算，站上去没意义
_PERCH_SIGHT_MARGIN: float = 1.0  # 判"够得着落点"留的余量（sight-1，避免卡视野极限）


# ══════════════════════════════════════════════════════════════════════════
# 地形几何（模块级，与探针 nydus_terrain_probe 同源；纯静态栅格，不需视野）
# ══════════════════════════════════════════════════════════════════════════
def enemy_plateau_edges(ai: Any, center: Point2) -> tuple[list[Point2], int]:
    """扫敌方主基高地 + 高地边缘可放格（F11，纯静态地形，不需视野）。

    返回 `(edge_placeable, base_h)`：
      - edge_placeable：高地边缘（挨着 ≥_CLIFF_DROP 悬崖）且 in_placement_grid 的格 → 坑道落点候选。
      - base_h：敌方主基地形高度基准。
    地形接口取不到（mock / 未接入）→ 返回 `([], 0)`，上层回退旧锚点。
    算法与 `scripts/nydus_terrain_probe.py::_scan` 一字不差同源。
    """
    try:
        base_h = int(ai.get_terrain_height(center))
    except Exception:
        return [], 0
    heights: dict[tuple[float, float], int] = {}
    area: list[Point2] = []
    cx, cy = round(center.x), round(center.y)
    for dx in range(-_SCAN_R, _SCAN_R + 1):
        for dy in range(-_SCAN_R, _SCAN_R + 1):
            p = Point2((cx + dx, cy + dy))
            if p.distance_to(center) > _SCAN_R:
                continue
            try:
                h = int(ai.get_terrain_height(p))
            except Exception:
                continue
            area.append(p)
            heights[(p.x, p.y)] = h
    plateau = [p for p in area if abs(heights[(p.x, p.y)] - base_h) <= _H_TOL]

    def _is_edge(p: Point2) -> bool:
        for ox in range(-_EDGE_NBR, _EDGE_NBR + 1):
            for oy in range(-_EDGE_NBR, _EDGE_NBR + 1):
                nh = heights.get((p.x + ox, p.y + oy))
                if nh is None:
                    try:
                        nh = int(ai.get_terrain_height(Point2((p.x + ox, p.y + oy))))
                    except Exception:
                        continue
                if nh < base_h - _CLIFF_DROP:
                    return True
        return False

    edge_placeable: list[Point2] = []
    for p in plateau:
        if not _is_edge(p):
            continue
        try:
            if ai.in_placement_grid(p):
                edge_placeable.append(p)
        except Exception:
            continue
    return edge_placeable, base_h


def off_cliff_dir(ai: Any, tile: Point2, base_h: int) -> tuple[float, float] | None:
    """从高地边缘格 tile 指向【悬崖外低地】的单位方向（顺 terrain_height 下降，J6）。

    扫 tile 周围低一截（悬崖下）的格，取它们方向的平均 = 往低地那侧；没有低格返回 None。
    与 `scripts/nydus_terrain_probe.py::_off_cliff_dir` 同源。
    """
    vx = vy = 0.0
    n = 0
    for ox in range(-4, 5):
        for oy in range(-4, 5):
            if ox == 0 and oy == 0:
                continue
            q = Point2((tile.x + ox, tile.y + oy))
            try:
                h = int(ai.get_terrain_height(q))
            except Exception:
                continue
            if h < base_h - _CLIFF_DROP:
                vx += ox
                vy += oy
                n += 1
    if n == 0:
        return None
    norm = math.hypot(vx, vy) or 1.0
    return (vx / norm, vy / norm)


def fits_3x3(ai: Any, tile: Point2) -> bool:
    """静态判「坑道虫 3x3 footprint 放得下」:自身 + 周围 8 格都在 placement grid 内。

    单格 `in_placement_grid` 合法**不等于**放得下——坑道虫 footprint 是 3x3，最外围那圈贴崖格
    周围缺格 → `can_place` 必 False（推理图谱 I56）。本判据纯静态、不需视野，可开局就把废点筛掉；
    真机校验对 `can_place` **漏判 0、只误报**（F153），当筛子安全。
    """
    for ox in (-1, 0, 1):
        for oy in (-1, 0, 1):
            try:
                if not ai.in_placement_grid(Point2((tile.x + ox, tile.y + oy))):
                    return False
            except Exception:
                return False
    return True


def _high_cells_near(ai: Any, center: Point2, radius: int) -> dict[tuple[int, int], int]:
    """center 周围 radius 内、与主基同高（±_H_TOL）的格 → {(x,y): 高度}。纯静态。"""
    try:
        base_h = int(ai.get_terrain_height(center))
    except Exception:
        return {}
    out: dict[tuple[int, int], int] = {}
    cx, cy = round(center.x), round(center.y)
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            p = Point2((cx + dx, cy + dy))
            if p.distance_to(center) > radius:
                continue
            try:
                h = int(ai.get_terrain_height(p))
            except Exception:
                continue
            if abs(h - base_h) <= _H_TOL:
                out[(cx + dx, cy + dy)] = h
    return out


def _connected_components(cells: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """4 连通分块（对角不连，避免两块高台被一个斜角"焊"成一块）。"""
    comps: list[set[tuple[int, int]]] = []
    todo = set(cells)
    while todo:
        seed = todo.pop()
        comp = {seed}
        stack = [seed]
        while stack:
            x, y = stack.pop()
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (nx, ny) in todo:
                    todo.discard((nx, ny))
                    comp.add((nx, ny))
                    stack.append((nx, ny))
        comps.append(comp)
    return comps


def small_plateau_perches(ai: Any, center: Point2, spots: list[Point2]) -> list[Point2]:
    """D102：与敌方主基**不相连**的小高台驻守点，按"离落点近"排序。

    做法（纯静态地形，不需视野）：取主基周围与主基同高的格 → 4 连通分块 → 丢掉含主基那块
    （主基高地本身）→ 面积在 [_PERCH_MIN_AREA, _PERCH_MAX_AREA] 的块就是"小高台" → 每块取
    **离最近落点最近**的那格作驻守点，且要求该距离 ≤ 视野-余量（够得着落点才有意义）。
    `spots` 传"真能落坑道虫的格"（已过 `fits_3x3`）。没有合格高台 → 返回 []（上层退回 D1）。
    """
    if not spots:
        return []
    cells = _high_cells_near(ai, center, _PERCH_SCAN_R)
    if not cells:
        return []
    main_key = (round(center.x), round(center.y))
    reach = _OL_SIGHT - _PERCH_SIGHT_MARGIN
    out: list[tuple[float, Point2]] = []
    for comp in _connected_components(set(cells)):
        if main_key in comp or not (_PERCH_MIN_AREA <= len(comp) <= _PERCH_MAX_AREA):
            continue
        best: tuple[float, Point2] | None = None
        for cx, cy in comp:
            p = Point2((cx + 0.5, cy + 0.5))
            d = min(p.distance_to(s) for s in spots)
            if best is None or d < best[0]:
                best = (d, p)
        if best is not None and best[0] <= reach:
            out.append(best)
    out.sort(key=lambda t: t[0])
    return [p for _d, p in out]


def landing_spots_3x3(ai: Any, center: Point2) -> list[Point2]:
    """真能落坑道虫的候选格:高地边缘格 snap 格心(F35) 后再过 `fits_3x3`(I56)。

    全被筛空（地形怪 / 判据太严）→ 退回未过筛的边缘格，宁可多试也别一个候选都没有。
    """
    edge, _h = enemy_plateau_edges(ai, center)
    if not edge:
        return []
    snapped = [Point2((math.floor(p.x) + 0.5, math.floor(p.y) + 0.5)) for p in edge]
    fits = [t for t in snapped if fits_3x3(ai, t)]
    return fits or snapped


def overlord_station_points(ai: Any, center: Point2) -> list[Point2]:
    """OL 驻守点总入口：**小高台优先(D102)，其次高地边缘外推 9 格到低地(D1)**。

    高台点排在前面，`_SendOverlordToEnemy` 按隐蔽度挑时天然先拿到它们。两类都是纯静态几何、
    开局即可算（I7），所以第一只 OL 一出生就能直接飞过去驻守。
    """
    spots = landing_spots_3x3(ai, center)
    perches = small_plateau_perches(ai, center, spots)
    floats = overlord_float_points(ai, center)
    seen: set[tuple[int, int]] = set()
    out: list[Point2] = []
    for p in list(perches) + list(floats):
        key = (round(p.x), round(p.y))
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def overlord_float_points(
    ai: Any,
    center: Point2,
    *,
    sectors: int = _SECTOR_COUNT,
    push: float = _OL_PUSH,
) -> list[Point2]:
    """D1+D3：OL 漂浮点 = 高地边缘可放格分角度扇区、每扇区取最外围格顺悬崖外推到低地。

    纯静态几何（不需视野，J7）：每个扇区取离 center 最远的边缘格（最外围 = 最贴悬崖），
    顺 `off_cliff_dir` 外推 `push`(≈sight-1) 格 → 停在悬崖外低地。多扇区 → 多只 OL 分散驻守
    做冗余（D3）。地形取不到 → 返回 `[]`，上层兜底。
    """
    edge_placeable, base_h = enemy_plateau_edges(ai, center)
    if not edge_placeable:
        return []
    best_by_sector: dict[int, tuple[Point2, float]] = {}
    for p in edge_placeable:
        ang = math.atan2(p.y - center.y, p.x - center.x)
        sec = int((ang + math.pi) / (2 * math.pi) * sectors) % sectors
        d = p.distance_to(center)
        if sec not in best_by_sector or d > best_by_sector[sec][1]:
            best_by_sector[sec] = (p, d)
    floats: list[Point2] = []
    for sec in sorted(best_by_sector):
        tile = best_by_sector[sec][0]
        direction = off_cliff_dir(ai, tile, base_h)
        if direction is None:
            continue
        floats.append(Point2((tile.x + direction[0] * push, tile.y + direction[1] * push)))
    return floats


# ══════════════════════════════════════════════════════════════════════════
# 几何锚点（模块级，planner 与 _SendOverlordToEnemy 共用；全部不依赖视野）
# ══════════════════════════════════════════════════════════════════════════
def enemy_main_center(ai: Any, zone_manager: Any) -> Point2:
    """敌方主基 center（几何已知）。zone_manager 取不到 → 兜底 enemy_start_locations[0]。"""
    with contextlib.suppress(Exception):
        zm = zone_manager
        if zm is not None:
            start = getattr(zm, "enemy_start_location", None)
            if start is None:
                start = ai.enemy_start_locations[0]
            zones = sorted(zm.expansion_zones, key=lambda z: z.center_location.distance_to(start))
            if zones:
                return zones[0].center_location
    return ai.enemy_start_locations[0]


def mineral_centroid(ai: Any, center: Point2) -> Point2 | None:
    """敌方主基矿脉质心（来自 expansion_locations_dict，几何已知，不需视野）。"""
    with contextlib.suppress(Exception):
        d = getattr(ai, "expansion_locations_dict", None)
        if d:
            best = min(d.keys(), key=lambda p: p.distance_to(center))
            if best.distance_to(center) < _ENEMY_MAIN_MATCH_D:
                mfs = d[best]
                if mfs:
                    return mfs.center
    return None


def behind_mineral_anchor(ai: Any, center: Point2) -> Point2 | None:
    """矿后死角（基地背面/高地边缘外围，最隐蔽，钻出即邻农民）。center.towards(矿脉,9)。"""
    mc = mineral_centroid(ai, center)
    if mc is None:
        return None
    return center.towards(mc, _BEHIND_MINERAL_OFFSET)


def mineral_line_anchor(ai: Any, center: Point2) -> Point2 | None:
    """矿线（农民处）。矿后再朝 center 拉回 4 格。"""
    bm = behind_mineral_anchor(ai, center)
    if bm is None:
        return None
    return bm.towards(center, _MINERAL_LINE_PULLBACK)


class NydusLandingPlanner:
    """坑道虫落点：候选生成 + 门控挑点 + lock/blacklist/canal_lost。无副作用。"""

    def __init__(self) -> None:
        self._locked_pos: Point2 | None = None
        # (点, 拉黑时刻);限时失效,见 notify_canal_lost
        self._blacklisted: list[tuple[Point2, float]] = []
        # D2：静态边缘落点候选（高地边缘可放格，按离矿脉质心最近排序）——
        # 纯静态地形 + 矿脉质心开局即知 → 一次算好锁住幂等复用（#543，别每帧重扫 ~1369 格）。
        self._static_edge_cache: list[Point2] | None = None
        # 落地诊断节流（2026-07-12 目标落地率 90%+：真机"有视野却不放"根因排查）。
        self._last_diag: float = -999.0
        # ④ 门时间兜底（2026-07-12 用户"只要落地率"）：drop 就绪但 ④「主力不在」因 VeryHard 主力
        # 赖家永不满足时，阻塞超 `_ARMY_GATE_FALLBACK_S` 秒 → 绕过 ④，按 per-tile 局部威胁挑最空格硬落
        # （落进家里 canal 会被拆，但"落地"了；后面怎么打交给玩家）。主力真离开则清零、恢复优先好窗口。
        self._army_gate_block_since: float | None = None

    # ══════════════════════════════════════════════════════════════════
    # canal 生命周期
    # ══════════════════════════════════════════════════════════════════
    def notify_canal_lost(self, now: float = 0.0) -> None:
        """canal 被拆 → **限时**拉黑当前锁定点 + 清 lock（先换点，过期后允许重用）。

        2026-07-26 真局教训:原来是**永久**拉黑,而且半径 3 格会把隔壁候选一起连坐。那一局里
        (57.5,20.5) 是全场唯一被验证过"看得见 + 放得下"的点,虫被拆后它和隔壁 (59.5,19.5) 一起
        被永久排除 → 之后窗口开了一路也无点可落。**虫死的原因不是点不好,是钻出来没人接应**,
        点是好点、该重用。故改成限时;并且 `pick_available_now` 在"一个候选都过不去"时会忽略
        拉黑重用老点(有个虫总比没有强)。
        """
        if self._locked_pos is not None:
            self._blacklisted.append((self._locked_pos, now))
        self._locked_pos = None

    # ══════════════════════════════════════════════════════════════════
    # 几何锚点（委托模块级函数，与 _SendOverlordToEnemy 共用，不重复实现）
    # ══════════════════════════════════════════════════════════════════
    def _enemy_main_center(self, ai: Any, zone_manager: Any) -> Point2:
        return enemy_main_center(ai, zone_manager)

    def _mineral_centroid(self, ai: Any, center: Point2) -> Point2 | None:
        return mineral_centroid(ai, center)

    def _behind_mineral(self, ai: Any, center: Point2) -> Point2 | None:
        return behind_mineral_anchor(ai, center)

    def _mineral_line(self, ai: Any, center: Point2) -> Point2 | None:
        return mineral_line_anchor(ai, center)

    # ══════════════════════════════════════════════════════════════════
    # 候选列表（D2：动态扫高地边缘可放格，挑离矿最近 → 屠农民；地形不可用回退旧锚点环）
    # ══════════════════════════════════════════════════════════════════
    def _edge_landing_tiles(self, ai: Any, center: Point2) -> list[Point2]:
        """D2：高地边缘可放格候选，按离敌方矿脉质心最近排序（近矿优先屠农民）。

        静态地形 + 矿脉质心开局即知 → 一次算好锁进 `_static_edge_cache` 幂等复用（#543）；
        地形接口不可用（mock / 未接入）→ 返回 `[]`，上层走旧锚点环兜底（不缓存空）。
        """
        if self._static_edge_cache is not None:
            return self._static_edge_cache
        edge_placeable, _base_h = enemy_plateau_edges(ai, center)
        if not edge_placeable:
            return []
        # ★ 2026-07-12 真机诊断:worm 实际落点全是 X.5/Y.5 格心(114.5,114.5...),而扫描出的边缘格
        # 是整数坐标(格角)→ can_place_single 对整数坐标返回 False(格角放不下 footprint)、对格心返回
        # True。故把候选格 snap 到格心,OLvis 主格才可放(否则 vis=True place=False 被滤、白等)。
        edge_placeable = [
            Point2((math.floor(p.x) + 0.5, math.floor(p.y) + 0.5)) for p in edge_placeable
        ]
        # ★ 2026-07-26 真局根因:上面只保证"单格在 placement grid",而坑道虫 footprint 是 3x3,
        # 最外围贴崖那圈单格合法、3x3 悬空 → `can_place` 恒 False(I56/F152:真机 5/5 全这样)。
        # 之前把 `fits_3x3` 只接到 OL 站位推导上、漏了这里 → 真局里唯二有视野的候选全是 place=False,
        # 窗口开着也永远落不下去。这里过筛;全被筛空(地形怪/判据太严)才退回未筛的,宁可多试。
        fits = [t for t in edge_placeable if fits_3x3(ai, t)]
        edge_placeable = fits or edge_placeable
        mc = self._mineral_centroid(ai, center)
        ref = mc if mc is not None else center
        edge_placeable.sort(key=lambda p: p.distance_to(ref))
        self._static_edge_cache = edge_placeable
        return edge_placeable

    def _legacy_anchor_tiles(self, ai: Any, center: Point2) -> list[Point2]:
        """回退候选（地形栅格不可用时）：旧矿后/矿线锚点环 + 主基朝地图中央兜底。"""
        cands: list[Point2] = []
        bm = self._behind_mineral(ai, center)
        ml = self._mineral_line(ai, center)
        for anchor in (bm, ml):
            if anchor is None:
                continue
            for d in (0.0, 2.0, 4.0):
                for ang_deg in range(0, 360, 45):
                    ang = math.radians(ang_deg)
                    cands.append(
                        Point2((anchor.x + d * math.cos(ang), anchor.y + d * math.sin(ang)))
                    )
        if not cands:
            with contextlib.suppress(Exception):
                map_center = ai.game_info.map_center
                cands.extend(center.towards(map_center, dd) for dd in (5, 8, 12, 15, 3))
        return cands

    def _candidate_tiles(self, ai: Any, center: Point2) -> list[Point2]:
        edge = self._edge_landing_tiles(ai, center)
        if edge:
            return edge
        return self._legacy_anchor_tiles(ai, center)

    def _ol_vision_edge_tiles(
        self, ai: Any, center: Point2, scout_units: list[Any]
    ) -> list[Point2]:
        """★ 落地率天花板兜底（2026-07-12 真机诊断后重写）：对每只靠近敌方的活 OL，取离它**最近的
        可放边缘格**——该格离 OL ≤ sight → **必在其视野内(vis=True)** 且本身是 in_placement_grid 边缘格
        (place=True)。OL 飘在哪扇区就落哪扇区，**不依赖"离矿最近"那个可能没视野的格**。这是把落点
        直接耦合到"当前真有视野的地方"，根治"有 OL 却无可落点"（旧 `_ol_fallback_tiles` 用 OL 位置本体
        在悬崖外低地 → 可见但**不可放** → 双门永过不了）。"""
        edges = self._edge_landing_tiles(ai, center)
        if not edges:
            return []
        out: list[Point2] = []
        seen: set[tuple[int, int]] = set()
        for ol in scout_units:
            with contextlib.suppress(Exception):
                if ol.position.distance_to(center) > _OL_NEAR_ENEMY_D:
                    continue
                # 2026-07-26:取该 OL 视野内**所有**够得着的格(按距离近→远),不再只取最近那一个——
                # 最近那个往往正是贴崖放不下的(F152),只取它等于把这只 OL 的贡献浪费掉。
                # 留 1 格余量(sight-1)：几何在视野内才当"必可见"，避免 razor-thin 误判。
                reach = [t for t in edges if ol.position.distance_to(t) <= _OL_SIGHT - 1.0]
                reach.sort(key=lambda t: ol.position.distance_to(t))
                for near in reach[:_OLVIS_TILES_PER_OL]:
                    key = (round(near.x), round(near.y))
                    if key not in seen:
                        seen.add(key)
                        out.append(near)
        return out

    # ══════════════════════════════════════════════════════════════════
    # 门控挑点
    # ══════════════════════════════════════════════════════════════════
    def _main_army_away(self, ai: Any, anchor: Point2, wave_all_in: bool) -> bool:
        """④ 主力不在落点区：落点区半径内非农民敌方机动单位 ≤ 阈值。
        （静态防御是 enemy_structures，不在 enemy_units 里，延续现有语义不计入。）"""
        max_nearby = _WINDOW_MAX_NEARBY_COMMIT if wave_all_in else _WINDOW_MAX_NEARBY
        count = 0
        with contextlib.suppress(Exception):
            for u in ai.enemy_units:
                if _is_worker(u):
                    continue
                if u.distance_to(anchor) <= _WINDOW_RADIUS:
                    count += 1
        return count <= max_nearby

    def _local_threat(self, ai: Any, pos: Point2) -> int:
        n = 0
        with contextlib.suppress(Exception):
            for u in ai.enemy_units:
                if _is_worker(u):
                    continue
                if u.distance_to(pos) <= _THREAT_AVOID_RADIUS:
                    n += 1
        return n

    def _is_blacklisted(self, pos: Point2, now: float) -> bool:
        """限时拉黑:超过 `_BLACKLIST_TTL_S` 的条目自动失效(顺手清掉)。"""
        self._blacklisted = [(p, t) for p, t in self._blacklisted if now - t < _BLACKLIST_TTL_S]
        return any(pos.distance_to(p) < _BLACKLIST_RADIUS for p, _t in self._blacklisted)

    def _should_diag(self, ai: Any) -> bool:
        """落地诊断节流：每 ~4s 允许打一次完整 per-tile 分解（真机根因排查用）。"""
        with contextlib.suppress(Exception):
            now = float(ai.time)
            if now - self._last_diag >= 4.0:
                self._last_diag = now
                return True
        return False

    def _nearest_ol_dist(self, scout_units: list[Any], pos: Point2) -> float:
        d = 999.0
        for ol in scout_units:
            with contextlib.suppress(Exception):
                d = min(d, ol.position.distance_to(pos))
        return d

    async def pick_available_now(
        self,
        ai: Any,
        zone_manager: Any,
        *,
        scout_units: list[Any],
        wave_all_in: bool,
        allow_gate_bypass: bool = True,
        ignore_blacklist: bool = False,
    ) -> Point2 | None:
        """挑一个"第一时间可落"的坐标；无则 None（主力在家/无可见可放点 → 等）。

        门 = ② is_visible ∧ ④ 主力不在落点区。命中即锁坐标快照，之后幂等复用（#543）。
        每 ~4s 打一次 per-tile 判定分解日志（`NYDUSDIAG`）——真机"有视野却不放"根因排查。
        """
        center = self._enemy_main_center(ai, zone_manager)
        diag = self._should_diag(ai)

        # 已锁定点仍可见 → 幂等复用（不漂移）；不可见 → 清 lock 重选（点没死，不拉黑）
        if self._locked_pos is not None:
            if ai.is_visible(self._locked_pos):
                return self._locked_pos
            self._locked_pos = None

        # 门 ④：主力在落点区 → 不下（COMMIT 放宽阈值，PROBE 严格）
        anchor = self._mineral_line(ai, center) or center
        army_away = self._main_army_away(ai, anchor, wave_all_in)
        # ④ 时间兜底：主力赖家永不满足时，阻塞超阈值秒 → 绕过 ④，靠 per-tile 局部威胁挑最空格硬落。
        now = 0.0
        with contextlib.suppress(Exception):
            now = float(ai.time)
        gate_bypass = False
        if army_away:
            self._army_gate_block_since = None
        else:
            if self._army_gate_block_since is None:
                self._army_gate_block_since = now
            elif allow_gate_bypass and now - self._army_gate_block_since >= _ARMY_GATE_FALLBACK_S:
                gate_bypass = True  # 等够了,主力就是不走 → 硬落(局部最空格)
                # allow_gate_bypass=False 时(虫被拆过的重投)绝不硬落:第二个虫落回刚拆掉第一个的
                # 那堆兵里必然再被秒,必须等佯攻真把主力引开的窗口。
        if diag:
            near = 0
            with contextlib.suppress(Exception):
                near = sum(
                    1
                    for u in ai.enemy_units
                    if not _is_worker(u) and u.distance_to(anchor) <= _WINDOW_RADIUS
                )
            ol_ds = [round(self._nearest_ol_dist(scout_units, center), 1)]
            logger.info(
                "NYDUSDIAG gate: army_away=%s nearby=%d anchor=(%.0f,%.0f) OL=%d dOL_center=%s "
                "commit=%s center=(%.0f,%.0f)",
                army_away,
                near,
                anchor.x,
                anchor.y,
                len(scout_units),
                ol_ds,
                wave_all_in,
                center.x,
                center.y,
            )
            if gate_bypass:
                logger.info(
                    "NYDUSDIAG gate ④ 兜底触发: 主力赖家阻塞≥%.0fs → 绕过 ④ 硬落最空格",
                    _ARMY_GATE_FALLBACK_S,
                )
        if not army_away and not gate_bypass:
            return None

        # 扫候选：★ OL 真实视野耦合格【优先】(必 vis+place) → 再矿后/矿线(打击价值高但可能没视野)
        ol_vis = self._ol_vision_edge_tiles(ai, center, scout_units)
        cand = self._candidate_tiles(ai, center)
        tiles = ol_vis + cand

        # 诊断:对前若干候选打完整判定分解(vis/place/bl/threat/最近OL距离),看到底哪步 False。
        if diag:
            src_split = len(ol_vis)
            for i, pos in enumerate(tiles[:12]):
                vis = bool(ai.is_visible(pos))
                place = None
                if vis:  # can_place 无视野必 False，只在有视野时查（省 query）
                    with contextlib.suppress(Exception):
                        place = bool(await ai.can_place_single(UnitTypeId.NYDUSCANAL, pos))
                logger.info(
                    "NYDUSDIAG tile[%d]%s (%.1f,%.1f) vis=%s place=%s bl=%s threat=%d dOL=%.1f",
                    i,
                    "OLvis" if i < src_split else "edge",
                    pos.x,
                    pos.y,
                    vis,
                    place,
                    self._is_blacklisted(pos, now),
                    self._local_threat(ai, pos),
                    self._nearest_ol_dist(scout_units, pos),
                )

        # 两轮:先按拉黑筛;一个都过不去 → 第二轮**无视拉黑**(2026-07-26 真局:全场唯一验证过
        # 可落的点被拉黑后就再没落过,有个虫总比没有强)。
        for ignore_bl in (True,) if ignore_blacklist else (False, True):
            for pos in tiles:
                if not ignore_bl and self._is_blacklisted(pos, now):
                    continue
                if not ai.is_visible(pos):  # ② 硬门，任何模式不放宽
                    continue
                if not await ai.can_place_single(UnitTypeId.NYDUSCANAL, pos):
                    continue
                if not wave_all_in and self._local_threat(ai, pos) > _THREAT_MAX_NEARBY:
                    continue
                self._locked_pos = pos  # 坐标快照（非活 OL 实时 position）
                logger.info(
                    "NydusLanding: worm locked @ (%.1f, %.1f) commit=%s%s",
                    pos.x,
                    pos.y,
                    wave_all_in,
                    " [无视拉黑重用老点]" if ignore_bl else "",
                )
                return pos
        return None
