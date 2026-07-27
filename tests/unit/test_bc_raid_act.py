"""GroupHarassAct 单状态机测试（#587 重写）。

旧实现已推倒重写为单状态机（STAGE/DIVE/HEAL per-BC）。
旧测试引用的 _joined_tags/_group_posture/_posture_since/_healing_tags/
_approach_wps/_approach_arrived/_rally_since/_raid_move_point/_approach_wp
等均已删除，对应旧用例全部删除。

保留：叶子函数（_harass_geom/_p1_threat_flee/_p1_aa_cheap_kill/_pick_group_zone/
       patrol/_nearby_worker_center）单测不引用旧字段。
新增：单状态机核心行为（fallback stage / commit gate / HEAL / DIVE-no-target /
       recruit / 孤立触发 / 剪切 / 空群 / near-micro）。
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

# ── fake-sharpy fixture ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fake_sharpy():
    """bc_raid_act 顶层 import sharpy.plans.acts.ActBase。注入 fake 让 import 过。"""
    created = []
    for name in (
        "sharpy",
        "sharpy.plans",
        "sharpy.plans.acts",
        "sharpy.managers",
        "sharpy.managers.core",
        "sharpy.managers.core.roles",
    ):
        if name not in sys.modules:
            sys.modules[name] = ModuleType(name)
            created.append(name)
    acts = sys.modules["sharpy.plans.acts"]
    if not hasattr(acts, "ActBase"):
        acts.ActBase = type("ActBase", (), {})  # type: ignore[attr-defined]
    roles = sys.modules["sharpy.managers.core.roles"]
    if not hasattr(roles, "UnitTask"):
        roles.UnitTask = SimpleNamespace(Reserved="Reserved", Idle="Idle")  # type: ignore[attr-defined]
    yield
    sys.modules.pop("vibecraft.bot.auto_combat.terran.bc_raid_act", None)
    for name in created:
        sys.modules.pop(name, None)


# ── helpers ──────────────────────────────────────────────────────────────────


def _act():
    """绕过 __init__ 构造 GroupHarassAct，塞新单状态机字段。"""
    from vibecraft.bot.auto_combat.terran.bc_raid_act import GroupHarassAct

    a = GroupHarassAct.__new__(GroupHarassAct)
    a._jump_floor_ratio = 0.09
    a._jump_safety_s = 6.5
    a._recover_hp_ratio = 0.95
    a._raid_dwell_s = 10.0
    # per-BC 单状态机（#587）
    a._state = {}
    a._state_since = {}
    a._last_hp = {}
    a._healed_stopped = set()
    a._home_anchor = None
    a._sweep_axis_by_tag = {}
    a._zone_center_by_tag = {}
    a._last_flyout_by_tag = {}
    # STAGE 贴边路径缓存
    a._stage_path = {}
    a._stage_idx = {}
    # per-group
    a._group_zone = {}
    a._group_zone_since = {}
    a._group_patrol_rank = {}
    a._group_patrol_since = {}
    a._stage_pt = {}
    a._stage_key = {}
    return a


def _bc(tag=1, pos=(50.0, 50.0), hp=550.0, hp_max=550.0):
    """普通 BC fake（不支持 ability call；move/attack/hold_position 均为 MagicMock）。"""
    p = Point2(pos)
    return SimpleNamespace(
        tag=tag,
        position=p,
        health=hp,
        health_max=hp_max,
        health_percentage=hp / hp_max,
        radius=1.25,
        distance_to=lambda other, _p=p: _p.distance_to(
            other if isinstance(other, Point2) else other.position
        ),
        move=MagicMock(),
        attack=MagicMock(),
        hold_position=MagicMock(),
    )


def _bc_callable(tag=1, pos=(50.0, 50.0), hp=550.0, hp_max=550.0):
    """BC MagicMock（支持 bc(AbilityId.X, target) 的 HEAL jump 断言）。"""
    p = Point2(pos)
    bc = MagicMock()
    bc.tag = tag
    bc.position = p
    bc.health = hp
    bc.health_max = hp_max
    bc.health_percentage = hp / hp_max
    bc.radius = 1.25
    bc.distance_to = lambda other, _p=p: _p.distance_to(
        other if isinstance(other, Point2) else other.position
    )
    return bc


def _worker(pos):
    p = Point2(pos)
    return SimpleNamespace(
        type_id=UnitTypeId.DRONE,  # 农民按真实 type_id 建模(Unit 无 is_worker)
        is_structure=False,
        position=p,
        distance_to=lambda other, _p=p: _p.distance_to(
            other if isinstance(other, Point2) else other.position
        ),
    )


class _FakeMins:
    def __init__(self, patches):
        self._patches = [SimpleNamespace(position=Point2(p)) for p in patches]
        self.amount = len(self._patches)
        cx = sum(p[0] for p in patches) / len(patches)
        cy = sum(p[1] for p in patches) / len(patches)
        self.center = Point2((cx, cy))

    def __iter__(self):
        return iter(self._patches)


def _zone(center, is_enemys=True, patch_dy=(-3.0, 0.0, 3.0)):
    c = Point2(center)
    patches = [(center[0] + 5.0, center[1] + dy) for dy in patch_dy]
    ml = Point2((center[0] + 4.0, center[1]))
    return SimpleNamespace(
        center_location=c,
        mineral_line_center=ml,
        behind_mineral_position_center=Point2((center[0] + 7.0, center[1])),
        mineral_fields=_FakeMins(patches),
        is_enemys=is_enemys,
    )


def _make_group(did="did-001", tags=None, target=None):
    return {"did": did, "tags": set(tags or []), "target": target, "target_count": None}


def _wire(
    a,
    *,
    bcs,
    groups=None,
    enemy_units=None,
    intent=None,
    zones=None,
    enemy_start=(100.0, 100.0),
    own_start=(10.0, 10.0),
    cd_ready=False,
):
    """挂上 act 跑 _tick 需要的最小环境。cd_ready 控制 EFFECT_TACTICALJUMP 是否可用。"""
    if groups is None:
        groups = []
    a.knowledge = SimpleNamespace(
        roles=MagicMock(),
        vibecraft=SimpleNamespace(bc_harass_groups=groups, combat_intent_override=intent),
    )
    cache = MagicMock()

    def _own(ut):
        if ut == UnitTypeId.BATTLECRUISER:
            return SimpleNamespace(ready=list(bcs))
        return []

    cache.own.side_effect = _own
    a.cache = cache
    a.cd_manager = SimpleNamespace(
        is_ready=lambda tag, ability: cd_ready,
        used_ability=lambda *_: None,
    )
    if zones is None:
        zones = [_zone((100.0, 100.0)), _zone((80.0, 90.0)), _zone((60.0, 80.0))]
    a.zone_manager = SimpleNamespace(
        expansion_zones=zones,
        enemy_start_location=Point2(enemy_start),
        enemy_main_zone=zones[0] if zones else None,
        our_zones_with_minerals=[_zone((10.0, 10.0), is_enemys=False)],
    )
    a.ai = SimpleNamespace(
        time=100.0,
        enemy_units=enemy_units or [],
        all_enemy_units=SimpleNamespace(closer_than=lambda *_a, **_k: []),
        state=SimpleNamespace(effects=[]),
        enemy_start_locations=[Point2(enemy_start)],
        start_location=Point2(own_start),
        game_info=SimpleNamespace(
            playable_area=SimpleNamespace(x=0.0, y=0.0, width=200.0, height=200.0)
        ),
    )


def _pa200():
    return SimpleNamespace(x=0.0, y=0.0, width=200.0, height=200.0)


# P1 helpers


def _combat(pos, air=True):
    p = Point2(pos)
    return SimpleNamespace(
        can_attack_air=air,
        position=p,
        distance_to=lambda other, _p=p: _p.distance_to(
            other if isinstance(other, Point2) else other.position
        ),
    )


def _aa_unit(pos, dps=20.0):
    p = Point2(pos)
    return SimpleNamespace(
        can_attack_air=True,
        air_dps=dps,
        type_id=UnitTypeId.MARINE,
        position=p,
        distance_to=lambda other, _p=p: _p.distance_to(
            other if isinstance(other, Point2) else other.position
        ),
    )


def _static_aa(pos, dps=20.0):
    p = Point2(pos)
    return SimpleNamespace(
        can_attack_air=False,
        air_dps=dps,
        type_id=UnitTypeId.MISSILETURRET,
        position=p,
        distance_to=lambda other, _p=p: _p.distance_to(
            other if isinstance(other, Point2) else other.position
        ),
    )


def _spore(pos, hp=300.0, air_dps=15.0):
    p = Point2(pos)
    return SimpleNamespace(
        type_id=UnitTypeId.SPORECRAWLER,
        can_attack_air=False,
        air_dps=air_dps,
        health=hp,
        position=p,
        distance_to=lambda other, _p=p: _p.distance_to(
            other if isinstance(other, Point2) else other.position
        ),
    )


def _bc_fit(pos, ground_dps=10.0, hp=550.0):
    p = Point2(pos)
    return SimpleNamespace(
        tag=id(p),
        position=p,
        health=hp,
        health_max=hp,
        health_percentage=1.0,
        ground_dps=ground_dps,
        distance_to=lambda other, _p=p: _p.distance_to(
            other if isinstance(other, Point2) else other.position
        ),
        move=MagicMock(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. 纯几何 helper（保留不变）
# ══════════════════════════════════════════════════════════════════════════════


def test_enemy_zone_by_rank_orders_by_distance_to_enemy_start():
    a = _act()
    zones = [_zone((60.0, 80.0)), _zone((100.0, 100.0)), _zone((80.0, 90.0))]
    a.zone_manager = SimpleNamespace(
        expansion_zones=zones, enemy_start_location=Point2((100.0, 100.0))
    )
    a.ai = SimpleNamespace(enemy_start_locations=[Point2((100.0, 100.0))])
    assert a._enemy_zone_by_rank(0).center_location == Point2((100.0, 100.0))
    assert a._enemy_zone_by_rank(1).center_location == Point2((80.0, 90.0))
    assert a._enemy_zone_by_rank(2).center_location == Point2((60.0, 80.0))


def test_harass_geom_anchor_is_behind_mineral_farther_from_base():
    a = _act()
    z = _zone((100.0, 100.0))
    anchor, axis, zc = a._harass_geom(z)
    assert anchor.distance_to(z.center_location) > z.mineral_line_center.distance_to(
        z.center_location
    )
    assert zc == z.center_location
    assert abs(axis.x) < 1e-6 and abs(abs(axis.y) - 1.0) < 1e-6
    assert anchor.distance_to(z.mineral_line_center) < 13.0


def test_harass_geom_wide_minerals_workers_reachable_from_behind():
    a = _act()
    z = _zone((50.0, 50.0), patch_dy=(-4.0, 0.0, 4.0))
    anchor, _, _ = a._harass_geom(z)
    assert anchor.distance_to(z.mineral_line_center) < 13.0


def test_nearby_worker_center_averages_visible_workers():
    a = _act()
    a.ai = SimpleNamespace(
        enemy_units=[_worker((100.0, 100.0)), _worker((104.0, 100.0)), _worker((200.0, 200.0))]
    )
    bc = _bc(tag=1, pos=(101.0, 100.0))
    anchor = Point2((102.0, 100.0))
    assert a._nearby_worker_center(bc, anchor) == Point2((102.0, 100.0))


def test_nearby_worker_center_none_when_far():
    a = _act()
    a.ai = SimpleNamespace(enemy_units=[_worker((200.0, 200.0))])
    anchor = Point2((100.0, 100.0))
    assert a._nearby_worker_center(_bc(tag=1, pos=(100.0, 100.0)), anchor) is None


# ══════════════════════════════════════════════════════════════════════════════
# 2. _tick gating：无 group / BC 不在 group 时不碰
# ══════════════════════════════════════════════════════════════════════════════


def test_tick_no_groups_does_nothing():
    a = _act()
    bc = _bc(tag=1)
    _wire(a, bcs=[bc], groups=[])
    a._tick()
    bc.move.assert_not_called()


def test_tick_bc_not_in_any_group_is_untouched():
    """BC tag 不在任何 group 的 tags 里 → 完全不碰。"""
    a = _act()
    nat = _zone((80.0, 90.0))
    zones = [_zone((100.0, 100.0)), nat, _zone((60.0, 80.0))]
    carded = _bc(tag=1, pos=(0.0, 0.0))
    free = _bc(tag=2, pos=(5.0, 5.0))
    group = _make_group(did="g1", tags=[1], target="natural")
    _wire(
        a,
        bcs=[carded, free],
        groups=[group],
        enemy_units=[_worker((nat.mineral_line_center.x, nat.mineral_line_center.y))],
        zones=zones,
    )
    a._tick()
    free.move.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 3. per-BC / per-group 状态剪切（pruning）
# ══════════════════════════════════════════════════════════════════════════════


def test_tick_prunes_stale_per_bc_state():
    """BC dead/released 后 per-tag 状态（_state/_state_since/_last_hp/等）被剪。"""
    a = _act()
    a._state = {99: "DIVE"}
    a._state_since = {99: 50.0}
    a._last_hp = {99: 500.0}
    a._sweep_axis_by_tag = {99: Point2((0.0, 1.0))}
    a._zone_center_by_tag = {99: Point2((100.0, 100.0))}
    bc = _bc(tag=1, pos=(0.0, 0.0))
    group = _make_group(did="g1", tags=[1], target=None)
    _wire(a, bcs=[bc], groups=[group], enemy_units=[_worker((100.0, 100.0))])
    a._tick()
    assert 99 not in a._state
    assert 99 not in a._state_since
    assert 99 not in a._last_hp
    assert 99 not in a._sweep_axis_by_tag
    assert 99 not in a._zone_center_by_tag


def test_tick_prunes_stale_per_group_state():
    """group 消失后 per-group 状态（_stage_pt/_stage_key/_group_zone 等）被剪。"""
    a = _act()
    a._stage_pt = {"dead-did": Point2((50.0, 50.0))}
    a._stage_key = {"dead-did": "ml:50,50"}
    a._group_zone = {"dead-did": 0}
    bc = _bc(tag=1)
    group = _make_group(did="live-did", tags=[1], target=None)
    _wire(a, bcs=[bc], groups=[group], enemy_units=[_worker((100.0, 100.0))])
    a._tick()
    assert "dead-did" not in a._stage_pt
    assert "dead-did" not in a._stage_key
    assert "dead-did" not in a._group_zone


# ══════════════════════════════════════════════════════════════════════════════
# 4. 兜底集结点（REQ-1）：矿线未知时绝不杵家
# ══════════════════════════════════════════════════════════════════════════════


def test_stage_fallback_returns_nonnil_when_mine_unknown():
    """_stage_for_group：harass_ml=None（矿线未知）→ 返回非 None 兜底集结点（enemy_start 外推）。"""
    a = _act()
    a.ai = SimpleNamespace(
        start_location=Point2((10.0, 10.0)),
        enemy_start_locations=[Point2((100.0, 100.0))],
        game_info=SimpleNamespace(
            playable_area=SimpleNamespace(x=0.0, y=0.0, width=200.0, height=200.0)
        ),
    )
    stage = a._stage_for_group("g1", harass_ml=None, zone_center=None, enemy_main_c=None)
    assert stage is not None
    from vibecraft.bot.auto_combat.terran.bc_raid_act import _FALLBACK_STAGE_OUT

    # 兜底 = enemy_start 朝 own_start 外推 FALLBACK_STAGE_OUT
    assert stage.distance_to(Point2((100.0, 100.0))) == pytest.approx(_FALLBACK_STAGE_OUT, abs=0.5)


def test_fallback_stage_bc_moves_not_idle():
    """矿线未侦察（zone 无 mineral_line_center）→ tick 后 BC 被 move（不原地杵家）。"""
    a = _act()
    zone_no_ml = SimpleNamespace(center_location=Point2((100.0, 100.0)))  # 无 mineral_line_center
    bc = _bc(tag=1, pos=(50.0, 50.0))
    group = _make_group(did="g1", tags=[1], target=None)
    _wire(a, bcs=[bc], groups=[group], enemy_units=[], zones=[zone_no_ml])
    a._tick()
    bc.move.assert_called()
    call_pos = bc.move.call_args[0][0]
    assert call_pos != Point2((0.0, 0.0))


# ══════════════════════════════════════════════════════════════════════════════
# 5. commit gate：第一艘及时 DIVE + 前排不被拉偏（REQ-2 / REQ-3）
# ══════════════════════════════════════════════════════════════════════════════


def test_first_bc_dives_when_at_stage_no_divers():
    """单 BC STAGE + 在 stage(<RALLY_RADIUS=5) + 无 DIVE 前排 → commit_min=1 → 转 DIVE。

    stage ≈ harass_stage_point(ml=(84,90), th=(80,90)) = (92,90)（_HARASS_STAGE_OUT=8）。
    """
    a = _act()
    nat = _zone((80.0, 90.0))
    zones = [_zone((100.0, 100.0)), nat, _zone((60.0, 80.0))]
    bc = _bc(tag=1, pos=(92.0, 90.0))  # 在 stage(92,90) 附近，dist≈0
    a._state = {1: "STAGE"}
    a._state_since = {1: 90.0}
    group = _make_group(did="g1", tags=[1], target="natural")
    _wire(
        a,
        bcs=[bc],
        groups=[group],
        enemy_units=[_worker((nat.mineral_line_center.x, nat.mineral_line_center.y))],
        zones=zones,
    )
    a._tick()
    assert a._state.get(1) == "DIVE", "到 stage 的首艘（无 DIVE 前排）应立即 commit DIVE"


def test_frontliner_dive_not_displaced_by_group_commit():
    """DIVE BC 全程不被 commit 逻辑触碰；STAGE BC 离 stage 远时不转 DIVE（REQ-3）。"""
    a = _act()
    nat = _zone((80.0, 90.0))
    zones = [_zone((100.0, 100.0)), nat, _zone((60.0, 80.0))]
    bc_a = _bc(tag=1, pos=(84.0, 90.0))  # DIVE（矿线附近）
    bc_b = _bc(tag=2, pos=(10.0, 10.0))  # STAGE（远离 stage≈(92,90)）
    a._state = {1: "DIVE", 2: "STAGE"}
    a._state_since = {1: 90.0, 2: 90.0}
    group = _make_group(did="g1", tags=[1, 2], target="natural")
    _wire(
        a,
        bcs=[bc_a, bc_b],
        groups=[group],
        enemy_units=[_worker((nat.mineral_line_center.x, nat.mineral_line_center.y))],
        zones=zones,
    )
    a._tick()
    assert a._state.get(1) == "DIVE", "前排 DIVE BC 不应被群 commit 改掉（REQ-3）"
    assert a._state.get(2) == "STAGE", "离 stage 的 STAGE BC 不满足 ready，不应转 DIVE"


def test_second_bc_waits_when_diver_present():
    """有 DIVE 前排时 commit_min=2；只有 1 个 STAGE-ready → 不 commit（REQ-3b）。"""
    a = _act()
    nat = _zone((80.0, 90.0))
    zones = [_zone((100.0, 100.0)), nat, _zone((60.0, 80.0))]
    bc_a = _bc(tag=1, pos=(84.0, 90.0))  # DIVE（前排）
    bc_b = _bc(tag=2, pos=(92.0, 90.0))  # STAGE + 在 stage 附近 → ready=1
    a._state = {1: "DIVE", 2: "STAGE"}
    a._state_since = {1: 90.0, 2: 100.0}  # B 刚到，elapsed=0 < GATHER_WINDOW_S(4) → 未超时
    group = _make_group(did="g1", tags=[1, 2], target="natural")
    _wire(
        a,
        bcs=[bc_a, bc_b],
        groups=[group],
        enemy_units=[_worker((nat.mineral_line_center.x, nat.mineral_line_center.y))],
        zones=zones,
    )
    a._tick()
    # commit_min=2（有 DIVE 前排），ready=[bc_b]=1 < 2，未超时 → B 仍 STAGE
    assert a._state.get(2) == "STAGE", "有 DIVE 前排时 commit_min=2，单 ready BC 不转 DIVE"
    assert a._state.get(1) == "DIVE", "前排 A 仍 DIVE"


# ══════════════════════════════════════════════════════════════════════════════
# 6. HEAL 转换（REQ-4）
# ══════════════════════════════════════════════════════════════════════════════


def test_heal_triggered_by_low_hp():
    """DIVE BC hp ≤ jump_hp_threshold(floor=9%) → 状态转 HEAL（纯自身触发）。"""
    a = _act()
    nat = _zone((80.0, 90.0))
    zones = [_zone((100.0, 100.0)), nat, _zone((60.0, 80.0))]
    bc = _bc(tag=1, pos=(84.0, 90.0), hp=10.0, hp_max=550.0)  # 1.8% << floor(9%)
    a._state = {1: "DIVE"}
    a._state_since = {1: 90.0}
    group = _make_group(did="g1", tags=[1], target="natural")
    _wire(
        a,
        bcs=[bc],
        groups=[group],
        enemy_units=[_worker((nat.mineral_line_center.x, nat.mineral_line_center.y))],
        zones=zones,
    )
    a._tick()
    assert a._state.get(1) == "HEAL", "低血 DIVE BC 应转 HEAL"


def test_heal_jump_when_cd_ready():
    """HEAL BC 远离家 + CD 好 → 调 bc(EFFECT_TACTICALJUMP, home)。"""
    a = _act()
    nat = _zone((80.0, 90.0))
    zones = [_zone((100.0, 100.0)), nat, _zone((60.0, 80.0))]
    # home ≈ (14,10)（_get_home_anchor from our_zones_with_minerals）；BC 在(84,90) >> HOME_STOP_RADIUS(6)
    bc = _bc_callable(tag=1, pos=(84.0, 90.0), hp=50.0, hp_max=550.0)
    a._state = {1: "HEAL"}
    a._state_since = {1: 90.0}
    a._last_hp = {1: 50.0}
    group = _make_group(did="g1", tags=[1], target="natural")
    _wire(a, bcs=[bc], groups=[group], enemy_units=[], zones=zones, cd_ready=True)
    a._tick()
    bc.assert_called()
    args = bc.call_args[0]
    assert args[0] == AbilityId.EFFECT_TACTICALJUMP, "HEAL+CD好 应调 EFFECT_TACTICALJUMP"


def test_heal_move_to_stage_when_cd_not_ready():
    """HEAL BC 远离家 + CD 没好 → move 到 stage（绝不 move home 穿中路）。"""
    a = _act()
    zones = [_zone((100.0, 100.0)), _zone((80.0, 90.0)), _zone((60.0, 80.0))]
    bc = _bc(tag=1, pos=(84.0, 90.0), hp=50.0, hp_max=550.0)
    a._state = {1: "HEAL"}
    a._state_since = {1: 90.0}
    a._last_hp = {1: 50.0}
    group = _make_group(did="g1", tags=[1], target=None)
    _wire(a, bcs=[bc], groups=[group], enemy_units=[], zones=zones, cd_ready=False)
    a._tick()
    bc.move.assert_called()
    tgt = bc.move.call_args[0][0]
    home = Point2((14.0, 10.0))  # home_anchor ≈ (14,10)
    assert tgt.distance_to(home) > 5.0, (
        f"CD 没好时不应 move home({home})，应退到 stage；实际 tgt={tgt}"
    )


def test_heal_hold_position_at_home_once():
    """HEAL BC 到家(<HOME_STOP_RADIUS=6) → hold_position 调一次；再 tick 不重发。"""
    a = _act()
    zones = [_zone((100.0, 100.0)), _zone((80.0, 90.0)), _zone((60.0, 80.0))]
    # home ≈ (14,10)；BC 在 (14,10) 正好在家
    bc = _bc(tag=1, pos=(14.0, 10.0), hp=100.0, hp_max=550.0)
    a._state = {1: "HEAL"}
    a._state_since = {1: 90.0}
    a._last_hp = {1: 100.0}
    group = _make_group(did="g1", tags=[1], target=None)
    _wire(a, bcs=[bc], groups=[group], enemy_units=[], zones=zones, cd_ready=True)
    a._tick()
    assert bc.hold_position.call_count == 1, "到家第一 tick 应调 hold_position 恰好一次"
    a._tick()
    assert bc.hold_position.call_count == 1, (
        "第二 tick 不应再次 hold_position（_healed_stopped 生效）"
    )


def test_heal_recovers_to_stage():
    """HEAL BC hp≥recover_hp_ratio(0.95) → 状态回 STAGE。"""
    a = _act()
    zones = [_zone((100.0, 100.0)), _zone((80.0, 90.0)), _zone((60.0, 80.0))]
    bc = _bc(tag=1, pos=(14.0, 10.0), hp=530.0, hp_max=550.0)  # 530/550≈96.4%≥95%
    a._state = {1: "HEAL"}
    a._state_since = {1: 90.0}
    a._last_hp = {1: 530.0}
    group = _make_group(did="g1", tags=[1], target=None)
    _wire(a, bcs=[bc], groups=[group], enemy_units=[], zones=zones)
    a._tick()
    assert a._state.get(1) == "STAGE", "hp≥recover_hp_ratio 应转回 STAGE"


# ══════════════════════════════════════════════════════════════════════════════
# 7. DIVE 无目标不回家（REQ-5 / 必修2）
# ══════════════════════════════════════════════════════════════════════════════


def test_dive_no_target_moves_to_stage_not_home():
    """DIVE BC + zone 无 mineral_line_center（patrol fallback，_harass_geom=None）→ move 到 stage，不回家。"""
    a = _act()
    zone_no_ml = SimpleNamespace(center_location=Point2((100.0, 100.0)))
    bc = _bc(tag=1, pos=(50.0, 50.0))
    a._state = {1: "DIVE"}
    a._state_since = {1: 90.0}
    group = _make_group(did="g1", tags=[1], target=None)
    _wire(a, bcs=[bc], groups=[group], enemy_units=[], zones=[zone_no_ml])
    a._tick()
    bc.move.assert_called()
    tgt = bc.move.call_args[0][0]
    home = Point2((14.0, 10.0))
    assert tgt.distance_to(home) > 5.0, f"DIVE 无目标时不应回家({home})，应去 stage；实际={tgt}"


# ══════════════════════════════════════════════════════════════════════════════
# 8. recruit-state + reserve（REQ-6）
# ══════════════════════════════════════════════════════════════════════════════


def test_new_bc_gets_state_and_reserve():
    """新 tag 进 group.tags → tick 后有 _state + roles.set_task 被调（_reserve 独占）。"""
    a = _act()
    zones = [_zone((100.0, 100.0)), _zone((80.0, 90.0)), _zone((60.0, 80.0))]
    bc = _bc(tag=7, pos=(0.0, 0.0))
    group = _make_group(did="g1", tags=[7], target=None)
    _wire(a, bcs=[bc], groups=[group], enemy_units=[], zones=zones)
    assert 7 not in a._state
    a._tick()
    assert 7 in a._state, "新 BC 应在 tick 后获得 _state"
    a.knowledge.roles.set_task.assert_called()  # _reserve 调了 set_task


# ══════════════════════════════════════════════════════════════════════════════
# 9. HEAL 纯自身触发（REQ-7 / Goal6）：不影响满血同组 BC
# ══════════════════════════════════════════════════════════════════════════════


def test_heal_only_triggers_on_low_hp_bc_not_healthy_bc():
    """一个 BC 低血 → HEAL；同组满血 BC 状态不受影响（纯自身，绝不整队 HEAL）。"""
    a = _act()
    nat = _zone((80.0, 90.0))
    zones = [_zone((100.0, 100.0)), nat, _zone((60.0, 80.0))]
    bc_low = _bc(tag=1, pos=(84.0, 90.0), hp=10.0, hp_max=550.0)  # 1.8% → HEAL
    bc_full = _bc(tag=2, pos=(82.0, 90.0), hp=550.0, hp_max=550.0)  # 100% → 不变
    a._state = {1: "DIVE", 2: "DIVE"}
    a._state_since = {1: 90.0, 2: 90.0}
    group = _make_group(did="g1", tags=[1, 2], target="natural")
    _wire(
        a,
        bcs=[bc_low, bc_full],
        groups=[group],
        enemy_units=[_worker((nat.mineral_line_center.x, nat.mineral_line_center.y))],
        zones=zones,
    )
    a._tick()
    assert a._state.get(1) == "HEAL", "低血 BC 应转 HEAL"
    assert a._state.get(2) == "DIVE", "满血 BC 不应被拉入 HEAL（Goal6：纯自身触发）"


# ══════════════════════════════════════════════════════════════════════════════
# 10. pruning：BC 死亡（REQ-8）
# ══════════════════════════════════════════════════════════════════════════════


def test_dead_bc_state_pruned():
    """BC 不在 all_bcs（已死） → _state/_state_since/_last_hp 被清。"""
    a = _act()
    a._state = {99: "DIVE", 1: "STAGE"}
    a._state_since = {99: 50.0, 1: 90.0}
    a._last_hp = {99: 200.0, 1: 550.0}
    bc = _bc(tag=1, pos=(0.0, 0.0))
    # group.tags 含 99，但 all_bcs 里只有 1（99 已死）
    group = _make_group(did="g1", tags=[1, 99], target=None)
    _wire(a, bcs=[bc], groups=[group], enemy_units=[])
    a._tick()
    assert 99 not in a._state, "已死 BC 的 state 应被剪"
    assert 99 not in a._state_since
    assert 99 not in a._last_hp
    assert 1 in a._state, "活着 BC 的 state 应保留"


# ══════════════════════════════════════════════════════════════════════════════
# 11. 空群不崩（REQ-9）
# ══════════════════════════════════════════════════════════════════════════════


def test_empty_group_no_crash():
    """group.tags 为空（或 tags 全死）→ _tick 不崩溃。"""
    a = _act()
    group = _make_group(did="g1", tags=[], target=None)
    _wire(a, bcs=[], groups=[group], enemy_units=[])
    a._tick()  # 不应抛异常


# ══════════════════════════════════════════════════════════════════════════════
# 12. DIVE near-micro：到位调 attack（REQ-10）
# ══════════════════════════════════════════════════════════════════════════════


def test_dive_near_micro_calls_attack_when_no_threat():
    """DIVE BC 到位（dist(behind)<ENGAGE_RADIUS=7）+ 无对空威胁 → bc.attack(aim) 被调。

    nat 矿：center=(80,90), ml=(84,90) → behind=(84.5,90)。
    BC 在(84,90)，dist≈0.5 < 7 → 进 near-micro；只有工人（不算威胁） → mode=attack。
    """
    a = _act()
    nat = _zone((80.0, 90.0))
    zones = [_zone((100.0, 100.0)), nat, _zone((60.0, 80.0))]
    bc = _bc(tag=1, pos=(84.0, 90.0), hp=550.0)
    a._state = {1: "DIVE"}
    a._state_since = {1: 90.0}
    worker = _worker((86.0, 90.0))
    group = _make_group(did="g1", tags=[1], target="natural")
    _wire(a, bcs=[bc], groups=[group], enemy_units=[worker], zones=zones)
    a._tick()
    assert bc.attack.called, "near-micro 无对空威胁 → 应调 bc.attack(aim)"


# ══════════════════════════════════════════════════════════════════════════════
# 13. _pick_group_zone（保留不变）
# ══════════════════════════════════════════════════════════════════════════════


def test_pick_group_zone_fixed_target_returns_correct_rank():
    """target="natural" → rank=1，不跑 picker。"""
    a = _act()
    zones = [_zone((100.0, 100.0)), _zone((80.0, 90.0)), _zone((60.0, 80.0))]
    a.zone_manager = SimpleNamespace(
        expansion_zones=zones, enemy_start_location=Point2((100.0, 100.0))
    )
    a.ai = SimpleNamespace(enemy_units=[], enemy_start_locations=[Point2((100.0, 100.0))])
    group = _make_group(did="g1", tags=[1], target="natural")
    assert a._pick_group_zone(group, "g1", now=100.0, alive=[]) == 1


def test_pick_group_zone_auto_picks_zone_with_workers():
    """target=None → auto picker 选有农民的矿区（score > 0）。"""
    a = _act()
    main = _zone((100.0, 100.0))
    nat = _zone((80.0, 90.0))
    third = _zone((60.0, 80.0))
    zones = [main, nat, third]
    a.zone_manager = SimpleNamespace(
        expansion_zones=zones, enemy_start_location=Point2((100.0, 100.0))
    )
    a.ai = SimpleNamespace(
        enemy_units=[_worker((nat.mineral_line_center.x, nat.mineral_line_center.y))],
        enemy_start_locations=[Point2((100.0, 100.0))],
    )
    group = _make_group(did="g1", tags=[1], target=None)
    assert a._pick_group_zone(group, "g1", now=100.0, alive=[]) == 1


def test_pick_group_zone_zone_switch_requires_hysteresis():
    """当前矿 score 与目标相同，未达 1.3x 领先且停留 <8s → 不切换。"""
    a = _act()
    main = _zone((100.0, 100.0))
    nat = _zone((80.0, 90.0))
    third = _zone((60.0, 80.0))
    zones = [main, nat, third]
    a.zone_manager = SimpleNamespace(
        expansion_zones=zones, enemy_start_location=Point2((100.0, 100.0))
    )
    a.ai = SimpleNamespace(
        enemy_units=[
            _worker((main.mineral_line_center.x, main.mineral_line_center.y)),
            _worker((main.mineral_line_center.x, main.mineral_line_center.y + 1)),
            _worker((nat.mineral_line_center.x, nat.mineral_line_center.y)),
            _worker((nat.mineral_line_center.x, nat.mineral_line_center.y + 1)),
        ],
        enemy_start_locations=[Point2((100.0, 100.0))],
    )
    a._group_zone["g1"] = 0
    a._group_zone_since["g1"] = 96.0  # now=100, 4s < 8s
    group = _make_group(did="g1", tags=[1], target=None)
    assert a._pick_group_zone(group, "g1", now=100.0, alive=[]) == 0


def test_patrol_fallback_locks_target_until_arrival_then_rotates():
    """BC 贴边途中锁死目标不轮换；抵达且驻留满才轮换（#580 修，2026-07-02）。"""
    a = _act()
    zones = [_zone((100.0, 100.0)), _zone((80.0, 90.0)), _zone((60.0, 80.0))]
    a.zone_manager = SimpleNamespace(
        expansion_zones=zones, enemy_start_location=Point2((100.0, 100.0))
    )
    a.ai = SimpleNamespace(enemy_units=[], enemy_start_locations=[Point2((100.0, 100.0))])
    group = _make_group(did="g1", tags=[1], target=None)
    far = _bc(tag=1, pos=(10.0, 10.0))  # 离 rank0 矿远
    near = _bc(tag=1, pos=(100.0, 100.0))  # 在 rank0 矿 airspace 内
    assert a._pick_group_zone(group, "g1", 100.0, alive=[far]) == 0
    assert a._pick_group_zone(group, "g1", 200.0, alive=[far]) == 0  # 锁死
    assert a._pick_group_zone(group, "g1", 999.0, alive=[far]) == 0  # 仍锁死
    assert a._pick_group_zone(group, "g1", 1000.0, alive=[near]) == 0  # 抵达但驻留不够
    assert a._pick_group_zone(group, "g1", 1010.0, alive=[near]) == 1  # 驻留满 → 轮换


# ══════════════════════════════════════════════════════════════════════════════
# 14. P1 威胁规避（纯函数 KEPT；集成测试更新用 _state）
# ══════════════════════════════════════════════════════════════════════════════


def test_p1_threat_flee_when_high_dps():
    a = _act()
    a.ai = SimpleNamespace(
        enemy_units=[_aa_unit((55.0, 50.0), dps=20.0), _aa_unit((58.0, 50.0), dps=20.0)]
    )
    bc = _bc(tag=1, pos=(50.0, 50.0))
    flee_pt = a._p1_threat_flee(bc)
    assert flee_pt is not None
    assert flee_pt.x < bc.position.x  # 远离右方威胁


def test_p1_no_threat_returns_none():
    a = _act()
    a.ai = SimpleNamespace(enemy_units=[_worker((55.0, 50.0))])
    assert a._p1_threat_flee(_bc(tag=1, pos=(50.0, 50.0))) is None


def test_p1_low_dps_threat_returns_none():
    a = _act()
    a.ai = SimpleNamespace(enemy_units=[_aa_unit((52.0, 50.0), dps=5.0)])
    assert a._p1_threat_flee(_bc(tag=1, pos=(50.0, 50.0))) is None


def test_p1_static_aa_triggers_flee():
    a = _act()
    a.ai = SimpleNamespace(enemy_units=[_static_aa((52.0, 50.0), dps=25.0)])
    assert a._p1_threat_flee(_bc(tag=1, pos=(50.0, 50.0))) is not None


def test_p1_in_tick_bc_flees_not_toward_workers():
    """DIVE BC + 高 DPS 威胁 → move 到规避点（远离威胁），不扑农民。"""
    a = _act()
    nat = _zone((80.0, 90.0))
    zones = [_zone((100.0, 100.0)), nat, _zone((60.0, 80.0))]
    bc1 = _bc(tag=1, pos=(80.0, 90.0), hp=550.0)
    bc2 = _bc(tag=2, pos=(82.0, 90.0), hp=550.0)
    a._state = {1: "DIVE", 2: "DIVE"}
    a._state_since = {1: 90.0, 2: 90.0}
    aa = _aa_unit((84.0, 90.0), dps=30.0)  # 4 格内，DPS=30 > floor(20)
    worker = _worker((nat.mineral_line_center.x, nat.mineral_line_center.y))
    group = _make_group(did="g1", tags=[1, 2], target="natural")
    _wire(a, bcs=[bc1, bc2], groups=[group], enemy_units=[aa, worker], zones=zones)
    a._tick()
    bc1.move.assert_called()
    call_args = bc1.move.call_args[0][0]
    assert call_args.x < bc1.position.x, f"应逃离右方威胁(x=84)，flee 点 x 应<80，实际={call_args}"


def test_p1_cheap_kill_isolated_spore_viable_returns_building():
    """cheap-kill 成立：孤立孢子 + 群火够 → 返回 spore。"""
    a = _act()
    spore = _spore((55.0, 50.0), hp=300.0, air_dps=15.0)
    a.ai = SimpleNamespace(enemy_units=[spore])
    bc1 = _bc_fit((50.0, 50.0), ground_dps=10.0)
    bc2 = _bc_fit((52.0, 50.0), ground_dps=10.0)
    assert a._p1_aa_cheap_kill([bc1, bc2], [bc1, bc2]) is spore


def test_p1_cheap_kill_not_viable_army_backup_returns_none():
    """Marine 紧贴孢子（非孤立）→ cheap-kill 不成立，返回 None。"""
    a = _act()
    spore = _spore((55.0, 50.0), hp=300.0, air_dps=15.0)
    marine_p = Point2((58.0, 50.0))
    marine = SimpleNamespace(
        type_id=UnitTypeId.MARINE,
        can_attack_air=True,
        air_dps=10.0,
        position=marine_p,
        distance_to=lambda other, _p=marine_p: _p.distance_to(
            other if isinstance(other, Point2) else other.position
        ),
    )
    a.ai = SimpleNamespace(enemy_units=[spore, marine])
    bc = _bc_fit((50.0, 50.0), ground_dps=10.0)
    assert a._p1_aa_cheap_kill([bc], [bc]) is None


def test_p1_cheap_kill_not_viable_insufficient_dps_returns_none():
    """群火力不足（杀太慢，承受伤害超预算）→ 返回 None。"""
    a = _act()
    spore = _spore((55.0, 50.0), hp=2000.0, air_dps=100.0)
    a.ai = SimpleNamespace(enemy_units=[spore])
    bc = _bc_fit((50.0, 50.0), ground_dps=5.0)
    assert a._p1_aa_cheap_kill([bc], [bc]) is None


def test_p1_cheap_kill_in_tick_bc_moves_to_building_not_flee():
    """DIVE BC + 孤立孢子 cheap_kill → BC move 朝建筑，不逃跑。"""
    a = _act()
    nat = _zone((80.0, 90.0))
    zones = [_zone((100.0, 100.0)), nat, _zone((60.0, 80.0))]
    bc1 = _bc(tag=1, pos=(80.0, 90.0), hp=550.0)
    bc2 = _bc(tag=2, pos=(82.0, 90.0), hp=550.0)
    bc1.ground_dps = 10.0  # type: ignore[attr-defined]
    bc2.ground_dps = 10.0  # type: ignore[attr-defined]
    a._state = {1: "DIVE", 2: "DIVE"}
    a._state_since = {1: 90.0, 2: 90.0}
    spore_p = Point2((83.0, 90.0))
    spore = SimpleNamespace(
        type_id=UnitTypeId.SPORECRAWLER,
        can_attack_air=False,
        air_dps=15.0,
        health=150.0,
        position=spore_p,
        distance_to=lambda other, _p=spore_p: _p.distance_to(
            other if isinstance(other, Point2) else other.position
        ),
    )
    group = _make_group(did="g1", tags=[1, 2], target="natural")
    _wire(
        a,
        bcs=[bc1, bc2],
        groups=[group],
        enemy_units=[spore, _worker((nat.mineral_line_center.x, nat.mineral_line_center.y))],
        zones=zones,
    )
    a._tick()
    bc1.move.assert_called()
    call_args = bc1.move.call_args[0][0]
    assert call_args.x >= bc1.position.x, f"cheap_kill 应令 BC 朝建筑移动(x≥80)，实际={call_args}"


def test_p1_precise_flee_dist_uses_max_air_range_plus_buffer():
    """精确射程：flee_dist = max(air_range) + _P1_FLEE_RANGE_BUFFER(2.0)。"""
    a = _act()
    tpos1, tpos2 = Point2((56.0, 50.0)), Point2((52.0, 50.0))

    def _threat(p, ar):
        return SimpleNamespace(
            can_attack_air=True,
            air_dps=15.0,
            air_range=ar,
            type_id=UnitTypeId.MARINE,
            position=p,
            distance_to=lambda other, _p=p: _p.distance_to(
                other if isinstance(other, Point2) else other.position
            ),
        )

    a.ai = SimpleNamespace(enemy_units=[_threat(tpos1, 7.0), _threat(tpos2, 12.0)])
    bc = _bc(tag=1, pos=(50.0, 50.0))
    flee_pt = a._p1_threat_flee(bc)
    assert flee_pt is not None
    expected = 12.0 + 2.0
    assert abs(flee_pt.distance_to(bc.position) - expected) < 0.5, (
        f"flee_dist 应≈{expected}，实际={flee_pt.distance_to(bc.position):.2f}"
    )


def test_p1_precise_flee_dist_fallback_when_no_air_range():
    """air_range 缺失 → fallback 到 _P1_FLEE_DIST(12.0)。"""
    a = _act()
    tpos = Point2((55.0, 50.0))
    threat = SimpleNamespace(
        can_attack_air=True,
        air_dps=25.0,
        type_id=UnitTypeId.MARINE,
        position=tpos,
        distance_to=lambda other, _p=tpos: _p.distance_to(
            other if isinstance(other, Point2) else other.position
        ),
    )
    a.ai = SimpleNamespace(enemy_units=[threat])
    bc = _bc(tag=1, pos=(50.0, 50.0))
    flee_pt = a._p1_threat_flee(bc)
    assert flee_pt is not None
    assert abs(flee_pt.distance_to(bc.position) - 12.0) < 0.5


# ══════════════════════════════════════════════════════════════════════════════
# 15. backward compat alias（保留不变）
# ══════════════════════════════════════════════════════════════════════════════


def test_bcraidsquadact_alias_is_groupharassact():
    from vibecraft.bot.auto_combat.terran.bc_raid_act import BcRaidSquadAct, GroupHarassAct

    assert BcRaidSquadAct is GroupHarassAct
