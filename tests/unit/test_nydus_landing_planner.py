"""NydusLandingPlanner 单测（2026-07-12 P1 落点重构）。

覆盖：矿后/矿线锚点几何自算（不依赖视野）/ 门 ②有视野 ∧ ④主力不在 / 命中锁坐标快照幂等复用
（#543）/ 视野丢失清 lock 重选 / canal 被拆拉黑换点 / COMMIT 放宽 ④与 per-tile 威胁但不放宽②视野 /
OL 位置兜底。不拉起 SC2：mock ai（is_visible/can_place_single/enemy_units/expansion_locations_dict）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from vibecraft.bot.auto_combat.zerg.plans.nydus_landing_planner import (
    NydusLandingPlanner,
    enemy_plateau_edges,
    off_cliff_dir,
    overlord_float_points,
)


# ══════════════════════════════════════════════════════════════════════════
# mock 工具
# ══════════════════════════════════════════════════════════════════════════
class _Minerals:
    def __init__(self, center: Point2) -> None:
        self._c = center

    @property
    def center(self) -> Point2:
        return self._c

    def __bool__(self) -> bool:
        return True


def _enemy(pos, worker: bool = False):
    p = Point2(pos)
    return SimpleNamespace(
        # 用真实 type_id 建模农民/军队：python-sc2 的 Unit 没有 is_worker(2026-07-27 踩坑)
        type_id=UnitTypeId.DRONE if worker else UnitTypeId.ZERGLING,
        is_structure=False,
        position=p,
        distance_to=lambda o, _p=p: _p.distance_to(o if isinstance(o, Point2) else o.position),
    )


def _ol(pos):
    p = Point2(pos)
    return SimpleNamespace(
        position=p,
        distance_to=lambda o, _p=p: _p.distance_to(o if isinstance(o, Point2) else o.position),
    )


def _mk_ai(
    *,
    enemy_start=(100.0, 100.0),
    minerals_center=(110.0, 100.0),
    visible=None,
    placeable=None,
    enemy_units=None,
):
    ai = SimpleNamespace()
    ai.enemy_start_locations = [Point2(enemy_start)]
    ai.start_location = Point2((0.0, 0.0))
    ai.game_info = SimpleNamespace(map_center=Point2((50.0, 50.0)))
    if minerals_center is not None:
        ai.expansion_locations_dict = {Point2(enemy_start): _Minerals(Point2(minerals_center))}
    else:
        ai.expansion_locations_dict = {}
    ai.is_visible = visible if visible is not None else (lambda p: True)

    _plc = placeable if placeable is not None else (lambda p: True)

    async def _cps(_t, p):
        return _plc(p)

    ai.can_place_single = _cps
    ai.enemy_units = enemy_units if enemy_units is not None else []
    return ai


def _pick(planner, ai, *, scout_units=None, wave_all_in=False):
    return asyncio.run(
        planner.pick_available_now(ai, None, scout_units=scout_units or [], wave_all_in=wave_all_in)
    )


# ══════════════════════════════════════════════════════════════════════════
# 几何锚点（不依赖视野）
# ══════════════════════════════════════════════════════════════════════════
def test_behind_mineral_and_line_geometry():
    p = NydusLandingPlanner()
    ai = _mk_ai(enemy_start=(100.0, 100.0), minerals_center=(110.0, 100.0))
    center = Point2((100.0, 100.0))
    bm = p._behind_mineral(ai, center)
    ml = p._mineral_line(ai, center)
    # center.towards(mineral(110,100), 9) = (109,100); 再 towards(center,4) = (105,100)
    assert bm is not None and abs(bm.x - 109.0) < 1e-6 and abs(bm.y - 100.0) < 1e-6
    assert ml is not None and abs(ml.x - 105.0) < 1e-6 and abs(ml.y - 100.0) < 1e-6


def test_mineral_centroid_from_expansion_dict_not_vision():
    # 矿脉质心来自 expansion_locations_dict（几何已知），即使全程 is_visible=False 也能算锚点
    p = NydusLandingPlanner()
    ai = _mk_ai(minerals_center=(110.0, 100.0))
    assert p._mineral_centroid(ai, Point2((100.0, 100.0))) == Point2((110.0, 100.0))


# ══════════════════════════════════════════════════════════════════════════
# 门 ④：主力不在落点区
# ══════════════════════════════════════════════════════════════════════════
def test_pick_none_when_main_army_present():
    p = NydusLandingPlanner()
    # 3 个非农民敌军贴矿线(anchor≈105,100) → PROBE 阈值 2，超了 → 不下
    army = [_enemy((105.0, 100.0)), _enemy((106.0, 100.0)), _enemy((104.0, 100.0))]
    ai = _mk_ai(enemy_units=army)
    assert _pick(p, ai) is None


def test_pick_lands_when_army_away_and_visible_placeable():
    p = NydusLandingPlanner()
    ai = _mk_ai(enemy_units=[])  # 主力不在
    pos = _pick(p, ai)
    assert pos is not None
    # 首选矿后死角 (109,100)
    assert abs(pos.x - 109.0) < 1e-6 and abs(pos.y - 100.0) < 1e-6


def test_workers_dont_count_as_main_army():
    p = NydusLandingPlanner()
    workers = [_enemy((105.0, 100.0), worker=True) for _ in range(5)]
    ai = _mk_ai(enemy_units=workers)
    assert _pick(p, ai) is not None  # 农民不算主力 → 照下


def test_gate_time_fallback_lands_when_army_camps_home():
    """④ 时间兜底(2026-07-12 用户"只要落地率"):主力赖家永不满足 ④，阻塞超阈值 → 绕过 ④ 硬落
    局部最空格。军队近 anchor(挡④)但远落点(局部空)。"""
    from vibecraft.bot.auto_combat.zerg.plans.nydus_landing_planner import _ARMY_GATE_FALLBACK_S

    p = NydusLandingPlanner()
    # 3 主力在 (98/99/100,100)：近 mineral_line anchor(105,100) < 16 → ④ 恒 False；
    # 远 behind_mineral 落点(109,100) > 6 → 该落点局部无威胁。
    army = [_enemy((98.0, 100.0)), _enemy((99.0, 100.0)), _enemy((100.0, 100.0))]
    ai = _mk_ai(enemy_units=army)
    ai.time = 100.0
    assert _pick(p, ai) is None  # 刚开始阻塞 → 还不兜底
    ai.time = 100.0 + _ARMY_GATE_FALLBACK_S + 1.0  # 等够阈值
    pos = _pick(p, ai)
    assert pos is not None  # ④ 兜底触发 → 硬落局部最空格(109,100)
    assert abs(pos.x - 109.0) < 1e-6


def test_gate_fallback_timer_resets_when_army_leaves():
    """主力中途离开 → 计时器清零，恢复"优先好窗口"(不再残留兜底状态)。"""
    p = NydusLandingPlanner()
    army = [_enemy((98.0, 100.0)), _enemy((99.0, 100.0)), _enemy((100.0, 100.0))]
    ai = _mk_ai(enemy_units=army)
    ai.time = 100.0
    _pick(p, ai)  # 开始阻塞计时
    assert p._army_gate_block_since == 100.0
    ai.enemy_units = []  # 主力走了
    ai.time = 105.0
    _pick(p, ai)
    assert p._army_gate_block_since is None  # 清零


# ══════════════════════════════════════════════════════════════════════════
# 门 ②：视野（硬门，任何模式不放宽）
# ══════════════════════════════════════════════════════════════════════════
def test_pick_skips_invisible_tiles():
    p = NydusLandingPlanner()
    ai = _mk_ai(visible=lambda pos: False)
    assert _pick(p, ai) is None


def test_pick_skips_non_placeable_tiles():
    p = NydusLandingPlanner()
    ai = _mk_ai(placeable=lambda pos: False)
    assert _pick(p, ai) is None


# ══════════════════════════════════════════════════════════════════════════
# 命中锁坐标快照 + 幂等复用（#543）
# ══════════════════════════════════════════════════════════════════════════
def test_locked_pos_reused_when_still_visible():
    p = NydusLandingPlanner()
    ai = _mk_ai()
    first = _pick(p, ai)
    assert first is not None and p._locked_pos == first
    # 改变可放性也不重选（已锁 + 仍可见 → 幂等复用）
    ai.can_place_single = None  # 不该被调用；若调用会 TypeError

    async def _boom(_t, _p):
        raise AssertionError("locked 复用不应再查 can_place")

    ai.can_place_single = _boom
    assert _pick(p, ai) == first


def test_locked_pos_dropped_and_reselected_when_vision_lost():
    p = NydusLandingPlanner()
    ai = _mk_ai()
    first = _pick(p, ai)
    assert first is not None
    # 锁定点丢视野、其余可见 → 清 lock 重选一个不同点
    lost = first

    ai.is_visible = lambda pos: pos.distance_to(lost) > 0.5
    second = _pick(p, ai)
    assert second is not None and second != lost


# ══════════════════════════════════════════════════════════════════════════
# canal 被拆 → 拉黑换点
# ══════════════════════════════════════════════════════════════════════════
def test_notify_canal_lost_blacklists_and_picks_different_point():
    p = NydusLandingPlanner()
    ai = _mk_ai()
    first = _pick(p, ai)
    assert first is not None
    p.notify_canal_lost()
    assert p._locked_pos is None
    second = _pick(p, ai)
    assert second is not None
    # 换点：新点不在旧点拉黑圈内
    assert second.distance_to(first) >= 3.0


# ══════════════════════════════════════════════════════════════════════════
# wave_intent：COMMIT 放宽 ④ 与 per-tile 威胁，但不放宽 ② 视野
# ══════════════════════════════════════════════════════════════════════════
def test_commit_relaxes_main_army_gate():
    p_probe = NydusLandingPlanner()
    p_commit = NydusLandingPlanner()
    # 4 个敌军贴矿线：PROBE 阈值 2 超 → None；COMMIT 阈值 6 未超 → 下
    army = [_enemy((105.0 + i * 0.3, 100.0)) for i in range(4)]
    ai = _mk_ai(enemy_units=army)
    assert _pick(p_probe, ai, wave_all_in=False) is None
    assert _pick(p_commit, ai, wave_all_in=True) is not None


def test_commit_ignores_local_threat_but_not_visibility():
    # 只有矿后死角(109,100)可放；2 敌军贴它(局部威胁>1)但全在 window 内不超 COMMIT 阈值
    p_probe = NydusLandingPlanner()
    p_commit = NydusLandingPlanner()
    army = [_enemy((108.0, 100.0)), _enemy((108.5, 100.0))]

    def placeable(pos):
        return pos.distance_to(Point2((109.0, 100.0))) < 0.5

    ai = _mk_ai(enemy_units=army, placeable=placeable)
    # PROBE：window 2≤2 过，但 (109,100) 局部威胁 2>1 → skip，其余不可放 → None
    assert _pick(p_probe, ai, wave_all_in=False) is None
    # COMMIT：不查局部威胁 → 落 (109,100)
    got = _pick(p_commit, ai, wave_all_in=True)
    assert got is not None and abs(got.x - 109.0) < 1e-6
    # COMMIT 仍不放宽视野：不可见则不落
    p_commit2 = NydusLandingPlanner()
    ai2 = _mk_ai(enemy_units=army, placeable=placeable, visible=lambda pos: False)
    assert _pick(p_commit2, ai2, wave_all_in=True) is None


# ══════════════════════════════════════════════════════════════════════════
# OL 位置兜底（无矿脉数据时仍能靠活 OL 落）
# ══════════════════════════════════════════════════════════════════════════
def test_ol_fallback_used_when_no_mineral_data():
    p = NydusLandingPlanner()
    # 无 expansion 矿脉数据 → 矿后锚点算不出；但有活 OL 贴敌方 → 兜底可落
    ai = _mk_ai(minerals_center=None)
    scout = [_ol((108.0, 100.0))]  # 距敌方主基(100,100) < 30
    got = _pick(p, ai, scout_units=scout)
    assert got is not None


# ══════════════════════════════════════════════════════════════════════════
# 地形几何：高地/边缘检测 / off_cliff 方向 / 漂浮点（D1/D3，纯静态，不需视野）
# ══════════════════════════════════════════════════════════════════════════
def _mk_terrain_ai(
    *,
    enemy_start=(100.0, 100.0),
    plateau_r=8.0,
    base_h=200,
    low_h=180,
    minerals_center=(112.0, 100.0),
    visible=None,
    placeable=None,
    grid=None,
    enemy_units=None,
):
    """在 _mk_ai 基础上加 get_terrain_height / in_placement_grid：
    以 enemy_start 为心、半径 plateau_r 内是高地(base_h)，外是低地(low_h，差 ≥12 = 悬崖)。"""
    ai = _mk_ai(
        enemy_start=enemy_start,
        minerals_center=minerals_center,
        visible=visible,
        placeable=placeable,
        enemy_units=enemy_units,
    )
    ec = Point2(enemy_start)
    ai.get_terrain_height = lambda p, _ec=ec: base_h if _ec.distance_to(p) <= plateau_r else low_h
    ai.in_placement_grid = grid if grid is not None else (lambda p: True)
    return ai


def test_enemy_plateau_edges_detects_placeable_edge():
    ai = _mk_terrain_ai()
    edges, base_h = enemy_plateau_edges(ai, Point2((100.0, 100.0)))
    assert base_h == 200
    assert edges  # 高地边缘可放格非空
    ec = Point2((100.0, 100.0))
    # 边缘格都在高地上(dist<=plateau_r，允许 0.5 格取整余量)、且贴悬崖
    for p in edges:
        assert p.distance_to(ec) <= 8.5
    # 朝矿(+x)那侧有边缘格
    assert any(p.x > 100 for p in edges)


def test_enemy_plateau_edges_empty_without_terrain_api():
    # mock ai 无 get_terrain_height → 优雅返回 ([],0)（旧锚点路径兜底，不炸）
    ai = _mk_ai()
    assert enemy_plateau_edges(ai, Point2((100.0, 100.0))) == ([], 0)


def test_off_cliff_dir_points_downhill_to_low_ground():
    ai = _mk_terrain_ai()
    # +x 侧边缘格，悬崖外低地在 +x → 方向 x 分量为正、y 近 0
    d = off_cliff_dir(ai, Point2((108.0, 100.0)), 200)
    assert d is not None and d[0] > 0.5 and abs(d[1]) < 0.5


def test_off_cliff_dir_none_when_no_cliff_nearby():
    ai = _mk_terrain_ai(plateau_r=25.0)  # 全高地、无悬崖
    assert off_cliff_dir(ai, Point2((100.0, 100.0)), 200) is None


def test_overlord_float_points_land_on_low_ground_and_spread():
    ec = Point2((100.0, 100.0))
    ai = _mk_terrain_ai()
    floats = overlord_float_points(ai, ec, sectors=5, push=10.0)
    assert floats
    for fp in floats:
        # 漂浮点在悬崖外低地(terrain < base_h - 12)、被外推到比高地边缘更远
        assert ai.get_terrain_height(fp) < 200 - 12
        assert fp.distance_to(ec) > 8.0
    # D3：环形悬崖 → 多扇区分散（至少 3 个不同漂浮点做冗余）
    assert len(floats) >= 3


# ══════════════════════════════════════════════════════════════════════════
# D2：动态扫高地边缘可放格，挑离矿最近（有地形时替代旧锚点环）
# ══════════════════════════════════════════════════════════════════════════
def test_pick_edge_tile_nearest_mineral_when_terrain_available():
    ai = _mk_terrain_ai(minerals_center=(112.0, 100.0), enemy_units=[])
    p = NydusLandingPlanner()
    pos = _pick(p, ai)
    assert pos is not None
    # 挑朝矿(+x)那侧边缘格（离矿最近）；是高地边缘格、非旧矿后锚点(109,100)那种低地几何点。
    # 2026-07-12：落点 snap 到格心(X.5/Y.5)后距离多 ~0.5，阈值 8.5→9.0。
    assert pos.x > 100
    # 落点已 snap 到格心
    assert pos.x % 1 == 0.5 and pos.y % 1 == 0.5
    assert pos.distance_to(Point2((100.0, 100.0))) <= 9.0


def test_edge_scan_still_gated_by_army_and_visibility():
    # 主力压在落点区 → 门 ④ 不过（有地形也不下）
    army = [_enemy((105.0, 100.0)), _enemy((106.0, 100.0)), _enemy((104.0, 100.0))]
    ai = _mk_terrain_ai(enemy_units=army)
    assert _pick(NydusLandingPlanner(), ai) is None
    # 落点全不可见 → 门 ② 不过
    ai2 = _mk_terrain_ai(enemy_units=[], visible=lambda p: False)
    assert _pick(NydusLandingPlanner(), ai2) is None


def test_static_edge_cache_reused_across_picks():
    # 静态边缘一次算好缓存复用（#543，不每帧重扫地形）
    ai = _mk_terrain_ai(enemy_units=[])
    p = NydusLandingPlanner()
    _pick(p, ai)
    assert p._static_edge_cache is not None
    cached = p._static_edge_cache
    # 第二次 pick 复用同一 list 对象（未重算）
    p._locked_pos = None  # 清 lock 强制重走候选，验缓存仍复用
    _pick(p, ai)
    assert p._static_edge_cache is cached


# ══════════════════════════════════════════════════════════════════════════
# D100/D102（2026-07-26）：3x3 可放筛 + 小高台驻守点 + 站位总入口
# ══════════════════════════════════════════════════════════════════════════
def test_fits_3x3_rejects_tile_with_unbuildable_neighbor():
    """单格可放不等于放得下：坑道虫 footprint 3x3，邻格缺一个就该判 False（I56）。"""
    from vibecraft.bot.auto_combat.zerg.plans.nydus_landing_planner import fits_3x3

    hole = Point2((11.0, 10.0))
    ai = _mk_terrain_ai(grid=lambda p, _h=hole: p.distance_to(_h) > 0.1)
    assert fits_3x3(ai, Point2((20.0, 20.0))) is True  # 四周都可放
    assert fits_3x3(ai, Point2((10.0, 10.0))) is False  # 右邻是洞


def test_small_plateau_perch_found_and_reaches_spot():
    """与主基不相连的小高台 → 成为驻守点；且它离落点在视野内。"""
    from vibecraft.bot.auto_combat.zerg.plans.nydus_landing_planner import small_plateau_perches

    ec = Point2((100.0, 100.0))
    # 主基高地半径 8；另在 (112,100) 处放一块 2x2 的同高小高台（与主基不相连）
    perch_cells = {(112, 100), (113, 100), (112, 101), (113, 101)}

    def _h(p, _ec=ec):
        if (round(p.x), round(p.y)) in perch_cells:
            return 200
        return 200 if _ec.distance_to(p) <= 8.0 else 180

    ai = _mk_terrain_ai()
    ai.get_terrain_height = _h
    spots = [Point2((107.5, 100.5))]  # 落点在主基高地边缘，小高台离它 ~5 格
    perches = small_plateau_perches(ai, ec, spots)
    assert perches, "应找到那块小高台"
    assert min(perches[0].distance_to(s) for s in spots) <= 10.0  # 够得着落点（视野 11 内）


def test_small_plateau_perch_skipped_when_too_far_from_spots():
    """小高台离落点超出视野 → 站上去也看不见落点，不该当候选。"""
    from vibecraft.bot.auto_combat.zerg.plans.nydus_landing_planner import small_plateau_perches

    ec = Point2((100.0, 100.0))
    perch_cells = {(125, 100), (126, 100)}

    def _h(p, _ec=ec):
        if (round(p.x), round(p.y)) in perch_cells:
            return 200
        return 200 if _ec.distance_to(p) <= 8.0 else 180

    ai = _mk_terrain_ai()
    ai.get_terrain_height = _h
    assert small_plateau_perches(ai, ec, [Point2((107.5, 100.5))]) == []


def test_station_points_put_perch_first_then_float():
    """站位总入口：小高台排在外推低地漂浮点之前（D102 优先级）。"""
    from vibecraft.bot.auto_combat.zerg.plans.nydus_landing_planner import (
        overlord_station_points,
        small_plateau_perches,
    )

    ec = Point2((100.0, 100.0))
    perch_cells = {(112, 100), (113, 100), (112, 101), (113, 101)}

    def _h(p, _ec=ec):
        if (round(p.x), round(p.y)) in perch_cells:
            return 200
        return 200 if _ec.distance_to(p) <= 8.0 else 180

    ai = _mk_terrain_ai()
    ai.get_terrain_height = _h
    stations = overlord_station_points(ai, ec)
    perches = small_plateau_perches(ai, ec, [Point2((107.5, 100.5))])
    assert stations, "至少要有站位候选"
    if perches:
        assert stations[0].distance_to(perches[0]) < 6.0  # 头一个就是高台那侧


# ══════════════════════════════════════════════════════════════════════════
# 2026-07-26 真局:虫被拆后再也落不下去（拉黑永久 + 候选没过 3x3 筛）
# ══════════════════════════════════════════════════════════════════════════
def test_blacklist_expires_after_ttl():
    """拉黑要限时——永久拉黑会把全场唯一验证过可落的点永远废掉。"""
    from vibecraft.bot.auto_combat.zerg.plans.nydus_landing_planner import _BLACKLIST_TTL_S

    p = NydusLandingPlanner()
    p._locked_pos = Point2((100.0, 100.0))
    p.notify_canal_lost(now=10.0)
    assert p._is_blacklisted(Point2((100.0, 100.0)), now=10.0) is True
    assert p._is_blacklisted(Point2((100.0, 100.0)), now=10.0 + _BLACKLIST_TTL_S + 1) is False


def test_pick_reuses_blacklisted_spot_when_nothing_else_passes():
    """一个候选都过不去时,第二轮无视拉黑重用老点(有个虫总比没有强)。"""
    p = NydusLandingPlanner()
    ai = _mk_ai(enemy_units=[])
    pos1 = _pick(p, ai)
    assert pos1 is not None
    p._locked_pos = pos1
    p.notify_canal_lost(now=1.0)  # 拉黑它;此局只有这一片候选
    pos2 = _pick(p, ai)
    assert pos2 is not None, "全被拉黑时应无视拉黑重用老点,而不是永远不下虫"


def test_pick_skips_blacklisted_when_alternative_exists():
    """有别的可落点时,拉黑仍然生效(换点优先)。"""
    p = NydusLandingPlanner()
    ai = _mk_ai(enemy_units=[])
    pos1 = _pick(p, ai)
    assert pos1 is not None
    p._locked_pos = pos1
    p.notify_canal_lost(now=1.0)
    p._static_edge_cache = None
    pos2 = _pick(p, ai)
    assert pos2 is not None
    # 老锚点环里有很多候选 → 应挑到离拉黑点 >= 拉黑半径 的另一个
    from vibecraft.bot.auto_combat.zerg.plans.nydus_landing_planner import _BLACKLIST_RADIUS

    assert pos2.distance_to(pos1) >= _BLACKLIST_RADIUS or pos2 == pos1


def test_edge_candidates_filtered_by_3x3():
    """落点候选必须过 3x3 筛——这正是真局里"唯二有视野的格 place=False"的根因。"""
    ec = Point2((100.0, 100.0))

    # 只有 (104,100) 附近一格四周齐全，其余格右邻是洞 → 只应留下齐全的那些
    def _grid(p, _bad=Point2((108.0, 100.0))):
        return p.distance_to(_bad) > 0.1

    ai = _mk_terrain_ai(grid=_grid, enemy_units=[])
    p = NydusLandingPlanner()
    tiles = p._edge_landing_tiles(ai, ec)
    assert tiles, "不该被筛空"
    from vibecraft.bot.auto_combat.zerg.plans.nydus_landing_planner import fits_3x3

    assert all(fits_3x3(ai, t) for t in tiles), "留下的候选都应能放下 3x3"
