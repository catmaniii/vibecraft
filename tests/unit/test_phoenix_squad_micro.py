"""PhoenixSquadMicro 纯逻辑单测（2026-07-20 整队 fight-or-flee 状态机重写）。

PhoenixSquadMicro 不继承 sharpy，不需要 fake_sharpy fixture。
sc2 在 vendor 中可用，直接 import。用 SimpleNamespace + filter wrapper 构造 mock 数据。

覆盖：
- 个体 bail 血量滞回（护盾脱战回复，无传送无修——见图谱 D43）；
- 整队 posture：approach（没到矿后区）/ fight（对空少）/ flee（对空多，绕敌撤）；
- fight：能 lift 抬（对空优先/农民次）、不能 lift 贴身 attack（不 kite 保距，rule 2）；
- flee：全队 orbit 到安全半径外（rule 3 绕敌不原路返，保存实力）；
- _try_lift 两条独立 gate（沿用，逻辑不变）。
"""

from __future__ import annotations

import math
import sys
from types import ModuleType, SimpleNamespace

import pytest
import sc2.ids.buff_id as _real_buff_id_mod
import sc2.ids.unit_typeid as _real_unit_typeid_mod
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

# ---------------------------------------------------------------------------
# fake sharpy + 强制真 sc2
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fake_sharpy_if_needed():
    """micro 本身不 import sharpy，但确保 conftest 不会因缺少 sharpy 而跳测试。

    关键（2026-05-31 修测试污染）：phoenix_squad_micro 在 import 时用真 UnitTypeId
    建模块级 frozenset（_WORKER_TYPES / _LIFT_PRIORITY）。别的测试会把 fake sc2.ids.*
    塞进 sys.modules 且泄漏，导致本测试重 import 时拿到 fake UnitTypeId → frozenset 用
    fake PROBE → 跟测试里真 UnitTypeId.PROBE 不匹配。这里强制还原真 sc2.ids.* + pop 模块。
    """
    created = []
    for name in ("sharpy", "sharpy.plans", "sharpy.plans.acts"):
        if name not in sys.modules:
            m = ModuleType(name)
            sys.modules[name] = m
            created.append(name)
    acts = sys.modules["sharpy.plans.acts"]
    if not hasattr(acts, "ActBase"):
        acts.ActBase = type("ActBase", (), {})

    saved_sc2 = {}
    for name, real_mod in (
        ("sc2.ids.unit_typeid", _real_unit_typeid_mod),
        ("sc2.ids.buff_id", _real_buff_id_mod),
    ):
        saved_sc2[name] = sys.modules.get(name)
        sys.modules[name] = real_mod
    sys.modules.pop("vibecraft.bot.auto_combat.protoss.phoenix_squad_micro", None)

    yield

    sys.modules.pop("vibecraft.bot.auto_combat.protoss.phoenix_squad_micro", None)
    for name in created:
        sys.modules.pop(name, None)
    for name, prev in saved_sc2.items():
        if prev is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = prev


# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------


class _FakeUnits(list):
    """list 子类，支持 .filter() / .closest_to() / .center / .flying / .closer_than()。"""

    def filter(self, fn):
        return _FakeUnits([u for u in self if fn(u)])

    def closest_to(self, target):
        if not self:
            return None
        ox = target.x if hasattr(target, "x") else target.position.x
        oy = target.y if hasattr(target, "y") else target.position.y
        return min(self, key=lambda u: math.hypot(u.position.x - ox, u.position.y - oy))

    def closer_than(self, dist, ref):
        ox = ref.position.x if hasattr(ref, "position") else ref.x
        oy = ref.position.y if hasattr(ref, "position") else ref.y
        return _FakeUnits(
            [u for u in self if math.hypot(u.position.x - ox, u.position.y - oy) < dist]
        )

    @property
    def center(self):
        if not self:
            return Point2((0.0, 0.0))
        avg_x = sum(u.position.x for u in self) / len(self)
        avg_y = sum(u.position.y for u in self) / len(self)
        return Point2((avg_x, avg_y))

    @property
    def flying(self):
        return _FakeUnits([u for u in self if getattr(u, "is_flying", False)])

    def __bool__(self):
        return len(self) > 0


def _make_phoenix(
    tag: int = 1,
    pos=(50.0, 50.0),
    health: float = 100.0,
    health_max: float = 100.0,
    shield: float = 60.0,
    shield_max: float = 60.0,
    energy: float = 100.0,
    air_range: float = 5.0,
    is_ready: bool = True,
):
    p = Point2((float(pos[0]), float(pos[1])))

    def _dist(other):
        ox = other.x if hasattr(other, "x") else other.position.x
        oy = other.y if hasattr(other, "y") else other.position.y
        return math.hypot(p.x - ox, p.y - oy)

    return SimpleNamespace(
        tag=tag,
        position=p,
        health=health,
        health_max=health_max,
        shield=shield,
        shield_max=shield_max,
        energy=energy,
        air_range=air_range,
        is_ready=is_ready,
        is_flying=False,
        is_structure=False,
        distance_to=_dist,
    )


def _make_enemy(
    tag: int = 100,
    pos=(60.0, 50.0),
    type_id=None,
    can_attack_air: bool = False,
    is_flying: bool = False,
    is_structure: bool = False,
    air_range: float = 6.0,
    buff_ids=None,
):
    p = Point2((float(pos[0]), float(pos[1])))

    def _dist(other):
        ox = other.x if hasattr(other, "x") else other.position.x
        oy = other.y if hasattr(other, "y") else other.position.y
        return math.hypot(p.x - ox, p.y - oy)

    def _has_buff(buff):
        return buff in (buff_ids or [])

    return SimpleNamespace(
        tag=tag,
        position=p,
        type_id=type_id or UnitTypeId.MARINE,
        can_attack_air=can_attack_air,
        is_flying=is_flying,
        is_structure=is_structure,
        air_range=air_range,
        has_buff=_has_buff,
        distance_to=_dist,
    )


def _make_ai(
    start_location=(10.0, 10.0),
    enemy_main_pos=(150.0, 150.0),
    enemy_units=None,
    time: float = 120.0,
):
    return SimpleNamespace(
        start_location=Point2(start_location),
        enemy_start_locations=[Point2(enemy_main_pos)],
        enemy_units=enemy_units or _FakeUnits(),
        time=time,
    )


def _micro(bail_hp_ratio=0.3, recover_hp_ratio=0.6):
    from vibecraft.bot.auto_combat.protoss.phoenix_squad_micro import PhoenixSquadMicro

    return PhoenixSquadMicro(bail_hp_ratio=bail_hp_ratio, recover_hp_ratio=recover_hp_ratio)


# ---------------------------------------------------------------------------
# _should_bail / 血量滞回（护盾脱战回复，无传送无修）
# ---------------------------------------------------------------------------


def test_should_bail_recover_by_shield():
    """bail 触发看总血危急;recover 看**护盾**回没回(神族血不回、只护盾回,别卡死在撤退态)。"""
    m = _micro()  # _recover_shield=0.6
    # 血护盾都低(20+0)/160=0.125 < 0.3 → bail
    p = _make_phoenix(tag=1, health=20, health_max=100, shield=0, shield_max=60)
    assert m._should_bail(p) is True
    assert 1 in m._bailing
    # 护盾没回够(18/60=0.3 < 0.6)→ 仍 bail(即便血回了些)
    p2 = _make_phoenix(tag=1, health=60, health_max=100, shield=18, shield_max=60)
    assert m._should_bail(p2) is True
    # 关键(修的 bug):护盾回够(48/60=0.8 >= 0.6)→ 解除撤退,**即便血还低**(30 血、总血比只 0.49,
    # 旧的总血 recover 会永远卡死在家)。血不回是永久的,不该拿它当 recover 门槛。
    p3 = _make_phoenix(tag=1, health=30, health_max=100, shield=48, shield_max=60)
    assert m._should_bail(p3) is False
    assert 1 not in m._bailing


# ---------------------------------------------------------------------------
# 整队 posture：approach / fight / flee
# ---------------------------------------------------------------------------


def test_posture_approach_far_moves_to_approach_wp():
    """squad 离矿后锚点远（> _ARRIVE_DIST）→ posture=approach → 全队走 approach_wp。"""
    m = _micro()
    p1 = _make_phoenix(tag=1, pos=(50.0, 50.0), energy=30.0)
    p2 = _make_phoenix(tag=2, pos=(52.0, 50.0), energy=30.0)
    phoenixes = _FakeUnits([p1, p2])
    harass_anchor = Point2((150.0, 150.0))
    approach_wp = Point2((100.0, 100.0))  # caller 算的矿后侧切路径当前点
    ai = _make_ai(enemy_main_pos=(150.0, 150.0))

    actions = m.solve_squad(phoenixes, harass_anchor, approach_wp, ai)
    for tag in (1, 2):
        act, tgt = actions[tag]
        assert act == "move"
        assert tgt is approach_wp  # 走接近路径，不直奔锚点


def test_posture_fight_lifts_worker():
    """squad 到矿后区 + 无对空 + 有农民 → posture=fight → 能 lift 凤凰抬农民。"""
    m = _micro()
    # 4 只在矿后区（距 anchor 5 < 22），energy 足
    phoenixes = _FakeUnits(
        [_make_phoenix(tag=i, pos=(150.0, 145.0), energy=100.0) for i in range(1, 5)]
    )
    harass_anchor = Point2((150.0, 150.0))
    worker = _make_enemy(
        tag=200, pos=(151.0, 145.0), type_id=UnitTypeId.PROBE, can_attack_air=False
    )
    ai = _make_ai(enemy_units=_FakeUnits([worker]))

    actions = m.solve_squad(phoenixes, harass_anchor, harass_anchor, ai)
    # 至少一只 lift 农民
    lifts = [t for a, t in actions.values() if a == "lift"]
    assert worker in lifts


def test_posture_fight_lifts_anti_air_priority():
    """fight + 1 对空兵 + 1 农民（对空少可打）→ 优先 lift 对空兵。"""
    m = _micro()
    phoenixes = _FakeUnits(
        [_make_phoenix(tag=i, pos=(150.0, 145.0), energy=100.0) for i in range(1, 5)]
    )
    harass_anchor = Point2((150.0, 150.0))
    anti_air = _make_enemy(
        tag=201, pos=(150.5, 145.0), type_id=UnitTypeId.MARINE, can_attack_air=True
    )
    worker = _make_enemy(tag=202, pos=(151.0, 145.0), type_id=UnitTypeId.PROBE)
    ai = _make_ai(enemy_units=_FakeUnits([anti_air, worker]))

    actions = m.solve_squad(phoenixes, harass_anchor, harass_anchor, ai)
    lifts = [t for a, t in actions.values() if a == "lift"]
    assert anti_air in lifts  # 对空兵优先


def test_posture_flee_when_overwhelmed():
    """squad 到矿后区 + 可抬对空数 > 凤凰数(D89 真被压垮)→ posture=flee → 全队 orbit 撤，飞出射程。"""
    from vibecraft.bot.auto_combat.protoss.phoenix_squad_micro import _FLEE_MARGIN

    m = _micro()
    phoenixes = _FakeUnits(
        [_make_phoenix(tag=i, pos=(150.0, 145.0), energy=60.0) for i in range(1, 5)]
    )
    harass_anchor = Point2((150.0, 150.0))
    # 6 可抬对空兵（6 > 4 凤凰 → 抬不完、打不过 → 撤；D89 后 4:4 反而算有优势能打）
    aa = [
        _make_enemy(
            tag=200 + i,
            pos=(150.0, 146.0),
            type_id=UnitTypeId.MARINE,
            can_attack_air=True,
            air_range=5.0,
        )
        for i in range(6)
    ]
    ai = _make_ai(enemy_units=_FakeUnits(aa))

    actions = m.solve_squad(phoenixes, harass_anchor, harass_anchor, ai)
    # 全队 move（orbit 撤退），且都走同一个点（集群），且该点飞出对空射程
    targets = [t for a, t in actions.values() if a == "move"]
    assert len(targets) == 4
    t0 = targets[0]
    for t in targets[1:]:
        assert abs(t.x - t0.x) < 1e-6 and abs(t.y - t0.y) < 1e-6  # 同一 orbit 点
    aa_center = Point2((150.0, 146.0))
    # orbit 点距对空中心 >= 射程(5) + margin（飞出射程 = 保存实力）
    assert t0.distance_to(aa_center) >= 5.0 + _FLEE_MARGIN - 0.5


def test_bail_overrides_fight_posture():
    """fight posture 下，血危凤凰仍回家（个体保命 > 整队 posture，图谱 D42）。"""
    m = _micro()
    # 1 只血危 + 3 只正常，在矿后区，无对空 → posture=fight
    hurt = _make_phoenix(
        tag=9, pos=(150.0, 145.0), health=5, health_max=100, shield=0, shield_max=60, energy=100.0
    )
    healthy = [_make_phoenix(tag=i, pos=(150.0, 145.0), energy=100.0) for i in range(1, 4)]
    phoenixes = _FakeUnits([hurt, *healthy])
    harass_anchor = Point2((150.0, 150.0))
    worker = _make_enemy(tag=200, pos=(151.0, 145.0), type_id=UnitTypeId.PROBE)
    ai = _make_ai(enemy_units=_FakeUnits([worker]))

    actions = m.solve_squad(phoenixes, harass_anchor, harass_anchor, ai)
    act, tgt = actions[9]
    assert act == "move"
    assert tgt is ai.start_location  # 血危回家，不参与 fight


def test_fight_no_lift_attacks_airborne():
    """fight + 凤凰能量不足不能 lift + 有被抬起/飞行的空中目标 → attack 它（贴身 move-shot）。"""
    from sc2.ids.buff_id import BuffId

    m = _micro()
    # 能量不足（30 < 50）不能 lift
    p = _make_phoenix(tag=1, pos=(150.0, 145.0), energy=30.0)
    phoenixes = _FakeUnits([p, _make_phoenix(tag=2, pos=(150.0, 145.0), energy=30.0)])
    harass_anchor = Point2((150.0, 150.0))
    # 一个被 GRAVITONBEAM 抬起的农民（空中，可打）；无对空 → fight
    lifted = _make_enemy(
        tag=300,
        pos=(150.5, 145.0),
        type_id=UnitTypeId.PROBE,
        can_attack_air=False,
        buff_ids=[BuffId.GRAVITONBEAM],
    )
    ai = _make_ai(enemy_units=_FakeUnits([lifted]))

    actions = m.solve_squad(phoenixes, harass_anchor, harass_anchor, ai)
    act, tgt = actions[1]
    assert act == "attack"
    assert tgt is lifted


def test_lift_cap_normal_max_two():
    """限量抬人(2026-07-22):5 只满能量 + 5 农民,平时最多 2 只抬、其余 3 只攻击(不再全抬)。"""
    m = _micro()
    phoenixes = _FakeUnits(
        [_make_phoenix(tag=i, pos=(150.0 + i, 145.0), energy=100.0) for i in range(5)]
    )
    harass_anchor = Point2((150.0, 150.0))
    # 5 农民各自贴一只凤凰(不同位置→不同最近农民→抬手挑到不同目标)
    workers = _FakeUnits(
        [
            _make_enemy(tag=200 + i, pos=(150.0 + i, 146.0), type_id=UnitTypeId.PROBE)
            for i in range(5)
        ]
    )
    ai = _make_ai(enemy_units=workers)

    actions = m.solve_squad(phoenixes, harass_anchor, harass_anchor, ai)
    n_lift = sum(1 for a, _ in actions.values() if a == "lift")
    n_atk = sum(1 for a, _ in actions.values() if a == "attack")
    assert n_lift == 2  # 平时上限 2
    assert n_atk == 3  # 其余全攻击(把抬起来的打死)


def test_max_lifters_scales_with_heavy_aa():
    """地对空火力猛(可抬对空兵 >= _AA_HEAVY_LIFT)时放宽抬人上限(抬起对空兵压制保命)。"""
    from vibecraft.bot.auto_combat.protoss.phoenix_squad_micro import (
        _AA_HEAVY_LIFT,
        _MAX_LIFTERS_NORMAL,
    )

    m = _micro()
    big_squad = _FakeUnits(
        [_make_phoenix(tag=i, pos=(150.0, 145.0), energy=100.0) for i in range(10)]
    )
    # 无对空 → 上限 = 平时值
    ai_no_aa = _make_ai(enemy_units=_FakeUnits())
    assert m._max_lifters(big_squad, ai_no_aa) == _MAX_LIFTERS_NORMAL
    # 3 对空兵(>= _AA_HEAVY_LIFT)→ 放宽到 min(3, 10)=3
    aa = [
        _make_enemy(tag=300 + i, pos=(150.0, 146.0), type_id=UnitTypeId.MARINE, can_attack_air=True)
        for i in range(_AA_HEAVY_LIFT)
    ]
    ai_heavy = _make_ai(enemy_units=_FakeUnits(aa))
    assert m._max_lifters(big_squad, ai_heavy) >= _AA_HEAVY_LIFT


def test_approach_majority_advances_minority_catches_up():
    """少数迁就多数(D57/D58 修 F96):主群(多数)继续推进,落单的少数去追**主群中心**,
    绝不让主群回头往全体质心靠拢等落单者。

    强场景(旧代码在此会把主群拽回):主群 3 只已推进到前方 (100,100),落单 1 只还在后方
    (50,50)。全体质心=(87.5,87.5) 被落单者拖后——旧 `_squad_cohesive` 会判不聚拢 → 全体
    move 向 (87.5,87.5) = **主群从 100 被拽回 87.5**。新逻辑:主群按主群中心(100,100)判定,
    继续推进 approach_wp;落单者去追 (100,100)(向前),整队不后退。
    """
    m = _micro()
    core = [_make_phoenix(tag=i, pos=(100.0, 100.0), energy=30.0) for i in range(1, 4)]
    straggler = _make_phoenix(tag=9, pos=(50.0, 50.0), energy=30.0)  # 落后主群 ~70
    phoenixes = _FakeUnits([*core, straggler])
    harass_anchor = Point2((160.0, 160.0))  # 远 → approach
    approach_wp = Point2((150.0, 150.0))
    ai = _make_ai(enemy_units=_FakeUnits())

    actions = m.solve_squad(phoenixes, harass_anchor, approach_wp, ai)
    # 主群 → 继续推进 approach_wp(绝不被拽回全体质心 87.5)
    a1, t1 = actions[1]
    assert a1 == "move" and t1 is approach_wp
    assert not (abs(t1.x - 87.5) < 1.0 and abs(t1.y - 87.5) < 1.0)  # 铁证:没回拽到全体质心
    # 落单者 → 追主群中心(~100,100)向前,不是 approach_wp、更不是往后
    a9, t9 = actions[9]
    assert a9 == "move" and t9 is not approach_wp
    assert abs(t9.x - 100.0) < 1.0 and abs(t9.y - 100.0) < 1.0  # 主群中心=(100,100),向前追


def test_focus_fire_lifted_persists_when_posture_flee():
    """集火跨 posture(D59 修 F98):即便对空多、整队 posture=flee,只要还有单位被抬(buff 未结束),
    其他凤凰也必须留下集火 A 死被抬的,绝不跟 flee 飞走。"""
    from sc2.ids.buff_id import BuffId

    m = _micro()
    phoenixes = _FakeUnits(
        [_make_phoenix(tag=i, pos=(150.0, 145.0), energy=30.0) for i in range(1, 3)]
    )
    harass_anchor = Point2((150.0, 150.0))
    # 3 个对空兵 → n_aa(3) > n_ph(2)*0.5 → can_fight False → posture=flee
    aa = [
        _make_enemy(tag=10 + i, pos=(150.0, 146.0), type_id=UnitTypeId.MARINE, can_attack_air=True)
        for i in range(3)
    ]
    # 一个已被抬起的农民(GRAVITONBEAM buff 未结束)
    lifted = _make_enemy(
        tag=200, pos=(151.0, 145.0), type_id=UnitTypeId.PROBE, buff_ids=[BuffId.GRAVITONBEAM]
    )
    ai = _make_ai(enemy_units=_FakeUnits([*aa, lifted]))

    # 先确认 posture 确实是 flee(否则测不到"跨 posture")
    assert m._squad_can_fight(phoenixes, phoenixes.center, ai) is False

    actions = m.solve_squad(phoenixes, harass_anchor, harass_anchor, ai)
    # 尽管 flee,两只都去 A 被抬的农民,没有一只 move 飞走
    assert all(a == "attack" and t is lifted for a, t in actions.values())


def test_fight_others_focus_fire_lifted():
    """有凤凰在抬人 → 其他凤凰集火 A 被抬的单位、不走开(用户 2026-07-25:平时不A的唯一例外)。"""
    m = _micro()
    phoenixes = _FakeUnits(
        [_make_phoenix(tag=i, pos=(150.0, 145.0), energy=100.0) for i in range(1, 5)]
    )
    harass_anchor = Point2((150.0, 150.0))
    worker = _make_enemy(tag=200, pos=(151.0, 145.0), type_id=UnitTypeId.PROBE)
    ai = _make_ai(enemy_units=_FakeUnits([worker]))

    actions = m.solve_squad(phoenixes, harass_anchor, harass_anchor, ai)
    lifts = [t for a, t in actions.values() if a == "lift"]
    attacks = [t for a, t in actions.values() if a == "attack"]
    assert worker in lifts  # 有凤凰抬它
    assert attacks and all(t is worker for t in attacks)  # 其余全 A 它(集火,不走开)


def test_fight_no_lifted_no_air_moves_anchor():
    """fight + 没人抬人 + 没有敌空军 + **无对空威胁** → move 到矿后锚点(找农民),不 A(2026-07-25)。"""
    m = _micro()
    p = _make_phoenix(tag=1, pos=(150.0, 145.0), energy=30.0)
    phoenixes = _FakeUnits([p])
    harass_anchor = Point2((150.0, 150.0))
    ai = _make_ai(enemy_units=_FakeUnits())  # 空(无对空)

    actions = m.solve_squad(phoenixes, harass_anchor, harass_anchor, ai)
    act, tgt = actions[1]
    assert act == "move"
    assert tgt is harass_anchor


def test_fight_cant_lift_but_aa_near_flees():
    """要么抬要么跑(D61 修 F105)：fight 到区但抬不成(能量不足)且旁边有枪兵 → flee 退回回盾，
    绝不杵在锚点被白打。"""
    m = _micro()
    # 4 只能量 30(< LIFT_ENERGY 50，抬不了)；1 个枪兵(可打空)→ n_aa1 <= n_ph4×0.5 → can_fight → fight
    phoenixes = _FakeUnits(
        [_make_phoenix(tag=i, pos=(150.0, 145.0), energy=30.0) for i in range(1, 5)]
    )
    harass_anchor = Point2((150.0, 150.0))
    marine = _make_enemy(
        tag=200, pos=(150.0, 146.0), type_id=UnitTypeId.MARINE, can_attack_air=True
    )
    ai = _make_ai(enemy_units=_FakeUnits([marine]))

    actions = m.solve_squad(phoenixes, harass_anchor, harass_anchor, ai)
    # 抬不成 + 有枪兵 → 全体 flee(move 到 orbit 撤退点，绝不是杵在 harass_anchor)
    for _tag, (act, tgt) in actions.items():
        assert act == "move"
        assert tgt is not harass_anchor


def test_flee_shuttles_to_other_safe_mineback():
    """穿梭腾挪(D64 修 F107)：敌方来兵打不过时(flee)，若另一矿后(harass_anchor)安全且在别处 →
    穿梭过去拉扯，而不是原地 orbit。"""
    m = _micro()
    # squad 在一矿(150,145)，一堆枪兵镇着 → can_fight False → flee
    phoenixes = _FakeUnits(
        [_make_phoenix(tag=i, pos=(150.0, 145.0), energy=60.0) for i in range(1, 4)]
    )
    marines = [
        _make_enemy(tag=10 + i, pos=(150.0, 146.0), type_id=UnitTypeId.MARINE, can_attack_air=True)
        for i in range(4)  # 4 枪兵 > 3×0.5 → 打不过
    ]
    ai = _make_ai(enemy_units=_FakeUnits(marines))
    # harass_anchor = 二矿后(50,50)，远(>ARRIVE)且无枪兵 → 安全
    other_mineback = Point2((50.0, 50.0))

    assert m._squad_can_fight(phoenixes, phoenixes.center, ai) is False
    actions = m.solve_squad(phoenixes, other_mineback, other_mineback, ai)
    # flee 全体穿梭去二矿后(move 到 other_mineback)，不是原地 orbit
    for _tag, (act, tgt) in actions.items():
        assert act == "move"
        assert abs(tgt.x - 50.0) < 1.0 and abs(tgt.y - 50.0) < 1.0


def test_flee_retreats_to_nearest_safe_pocket():
    """心法 D81/D67:打不过时(flee)优先退到最近的、不挨打的**安全悬崖口袋**(safe_points),
    而不是原地 orbit 或穿去矿区。"""
    m = _micro()
    phoenixes = _FakeUnits(
        [_make_phoenix(tag=i, pos=(150.0, 145.0), energy=60.0) for i in range(1, 4)]
    )
    marines = [
        _make_enemy(tag=10 + i, pos=(150.0, 146.0), type_id=UnitTypeId.MARINE, can_attack_air=True)
        for i in range(4)  # 打不过 → flee
    ]
    ai = _make_ai(enemy_units=_FakeUnits(marines))
    # 两个安全口袋:(120,120) 近且安全 / (10,10) 远。应退到近的 (120,120)
    safe = [Point2((120.0, 120.0)), Point2((10.0, 10.0))]

    assert m._squad_can_fight(phoenixes, phoenixes.center, ai) is False
    actions = m.solve_squad(phoenixes, Point2((150.0, 150.0)), Point2((150.0, 150.0)), ai, safe)
    for _tag, (act, tgt) in actions.items():
        assert act == "move"
        assert abs(tgt.x - 120.0) < 1.0 and abs(tgt.y - 120.0) < 1.0  # 退到最近安全口袋


def test_bail_on_low_shield_even_full_health():
    """D61：护盾掉到阈值(<0.2)就退回回盾，即便血满(拿会回的护盾换战果，别耗到掉血)。"""
    m = _micro()  # _bail_shield=0.2
    p = _make_phoenix(tag=1, health=100, health_max=100, shield=10, shield_max=60)  # 护盾 0.167<0.2
    assert m._should_bail(p) is True
    ai = _make_ai(enemy_units=_FakeUnits())
    actions = m.solve_squad(_FakeUnits([p]), Point2((150.0, 150.0)), Point2((150.0, 150.0)), ai)
    act, tgt = actions[1]
    assert act == "move" and tgt is ai.start_location  # 无安全口袋 → 兜底回家回盾


def test_bail_retreats_to_safe_pocket_not_home():
    """D82：血/护盾危时退到**最近安全口袋回盾**(不回家),整队同一个(在一起)。"""
    m = _micro()
    p = _make_phoenix(
        tag=1, pos=(150.0, 150.0), health=100, health_max=100, shield=5, shield_max=60
    )
    ai = _make_ai(enemy_units=_FakeUnits())
    safe = [Point2((140.0, 140.0)), Point2((10.0, 10.0))]  # 近口袋(140,140)
    actions = m.solve_squad(
        _FakeUnits([p]), Point2((150.0, 150.0)), Point2((150.0, 150.0)), ai, safe
    )
    act, tgt = actions[1]
    assert act == "move"
    assert abs(tgt.x - 140.0) < 1.0 and abs(tgt.y - 140.0) < 1.0  # 退最近安全口袋，不回家


# ---------------------------------------------------------------------------
# _squad_can_fight gate 边界
# ---------------------------------------------------------------------------


class TestSquadCanFight:
    def _run(self, n_phoenix_energy, n_aa):
        m = _micro()
        phoenixes = _FakeUnits(
            [
                _make_phoenix(tag=i, pos=(150.0, 145.0), energy=60.0)
                for i in range(1, n_phoenix_energy + 1)
            ]
        )
        aa = [
            _make_enemy(
                tag=200 + i, pos=(150.0, 146.0), type_id=UnitTypeId.MARINE, can_attack_air=True
            )
            for i in range(n_aa)
        ]
        ai = _make_ai(enemy_units=_FakeUnits(aa))
        return m._squad_can_fight(phoenixes, Point2((150.0, 145.0)), ai)

    def test_no_aa_can_fight(self):
        assert self._run(4, 0) is True

    def test_few_aa_can_fight(self):
        # 3 可抬对空 < 4 凤凰 且 4 只带能量 >= 3(抬得动全部)→ 打得过(抬清对空,D89/D90)
        assert self._run(4, 3) is True

    def test_equal_aa_cannot_fight(self):
        # 4 可抬对空 == 4 凤凰(无富余,抬光了没凤凰杀农民)→ 不打(要"女王比凤凰少",D90)
        assert self._run(4, 4) is False

    def test_many_aa_cannot_fight(self):
        # 5 可抬对空 > 4 凤凰(抬不完)→ 打不过（保存实力，撤）
        assert self._run(4, 5) is False

    def test_static_aa_within_budget_can_raid(self):
        # 静态防空 ≤ _STATIC_RAID_MAX(2) → 可护盾硬闯突袭杀农民(D90),不再一律躲。
        m = _micro()
        phoenixes = _FakeUnits(
            [_make_phoenix(tag=i, pos=(150.0, 145.0), energy=60.0) for i in range(1, 9)]
        )
        spores = [
            _make_enemy(
                tag=300 + i,
                pos=(150.0, 146.0),
                type_id=UnitTypeId.SPORECRAWLER,
                can_attack_air=True,
                is_structure=True,
            )
            for i in range(2)
        ]
        ai = _make_ai(enemy_units=_FakeUnits(spores))
        assert m._squad_can_fight(phoenixes, Point2((150.0, 145.0)), ai) is True

    def test_static_aa_over_budget_cannot_fight(self):
        # 静态防空 > 预算(3 座)→ 焦点火力秒穿护盾撤不出 → 不硬闯,换矿(D90/F108)。
        m = _micro()
        phoenixes = _FakeUnits(
            [_make_phoenix(tag=i, pos=(150.0, 145.0), energy=60.0) for i in range(1, 9)]
        )
        spores = [
            _make_enemy(
                tag=300 + i,
                pos=(150.0, 146.0),
                type_id=UnitTypeId.SPORECRAWLER,
                can_attack_air=True,
                is_structure=True,
            )
            for i in range(3)
        ]
        ai = _make_ai(enemy_units=_FakeUnits(spores))
        assert m._squad_can_fight(phoenixes, Point2((150.0, 145.0)), ai) is False

    def test_drained_energy_cannot_fight_liftable_aa(self):
        # D90:没能量抬不动可抬对空(女王/枪兵)→ 会挨打,撤回去回能量再来("除非没能量")。
        # 4 只 energy=20(<50 抬不了)vs 1 可抬对空 → 抬不动 → 不打。
        m = _micro()
        phoenixes = _FakeUnits(
            [_make_phoenix(tag=i, pos=(150.0, 145.0), energy=20.0) for i in range(1, 5)]
        )
        aa = [
            _make_enemy(tag=200, pos=(150.0, 146.0), type_id=UnitTypeId.MARINE, can_attack_air=True)
        ]
        ai = _make_ai(enemy_units=_FakeUnits(aa))
        assert m._squad_can_fight(phoenixes, Point2((150.0, 145.0)), ai) is False


# ---------------------------------------------------------------------------
# _try_lift gate 边界（两条独立 gate，逻辑不变，沿用）
# ---------------------------------------------------------------------------


class TestLiftGateAntiAir:
    """Gate A：lift 对空兵 gate — 威胁对空兵 <= 能lift凤凰 * LIFT_GATE_RATIO(0.5)。"""

    def _setup(self, n_phoenix: int, n_anti_air: int, with_worker: bool = True):
        from vibecraft.bot.auto_combat.protoss.phoenix_squad_micro import PhoenixSquadMicro

        m = PhoenixSquadMicro()
        p = _make_phoenix(tag=1, pos=(50.0, 50.0), energy=100.0)
        phoenixes = _FakeUnits(
            [_make_phoenix(tag=i, pos=(50.0, 50.0), energy=60.0) for i in range(1, n_phoenix + 1)]
        )
        anti_air_units = [
            _make_enemy(
                tag=200 + i, pos=(52.0 + i, 50.0), type_id=UnitTypeId.MARINE, can_attack_air=True
            )
            for i in range(n_anti_air)
        ]
        extras = []
        if with_worker:
            extras = [_make_enemy(tag=999, pos=(53.0, 50.0), type_id=UnitTypeId.PROBE)]
        ai = _make_ai(enemy_units=_FakeUnits(anti_air_units + extras))
        return m._try_lift(p, phoenixes, ai)

    def test_lift_anti_air_4_phoenix_0_anti_air(self):
        assert self._setup(4, 0) is not None  # Gate B 抬 worker

    def test_lift_anti_air_4_phoenix_1_anti_air(self):
        result = self._setup(4, 1)
        assert result is not None and result.type_id == UnitTypeId.MARINE

    def test_lift_anti_air_4_phoenix_2_anti_air(self):
        result = self._setup(4, 2)
        assert result is not None and result.type_id == UnitTypeId.MARINE

    def test_lift_anti_air_2_phoenix_1_anti_air(self):
        result = self._setup(2, 1)
        assert result is not None and result.type_id == UnitTypeId.MARINE

    def test_lift_anti_air_4_phoenix_3_anti_air(self):
        # D89:3 对空 <= 4 能lift凤凰 → Gate A 抬对空(序贯清场),不再退到抬农民
        result = self._setup(4, 3)
        assert result is not None and result.type_id == UnitTypeId.MARINE

    def test_lift_anti_air_2_phoenix_3_anti_air_gate_fail(self):
        # 3 对空 > 2 能lift凤凰 → Gate A fail → 退到 Gate B 抬农民(凤凰不够抬清对空)
        result = self._setup(2, 3)
        assert result is not None and result.type_id == UnitTypeId.PROBE


class TestLiftGateWorker:
    """Gate B：lift 农民 gate — 周围能lift凤凰 >= MIN_NEARBY_FOR_LIFT_WORKER(2)。"""

    def _setup_worker_only(self, n_phoenix: int):
        from vibecraft.bot.auto_combat.protoss.phoenix_squad_micro import PhoenixSquadMicro

        m = PhoenixSquadMicro()
        p = _make_phoenix(tag=1, pos=(50.0, 50.0), energy=100.0)
        phoenixes = _FakeUnits(
            [_make_phoenix(tag=i, pos=(50.0, 50.0), energy=60.0) for i in range(1, n_phoenix + 1)]
        )
        worker = _make_enemy(tag=500, pos=(52.0, 50.0), type_id=UnitTypeId.PROBE)
        ai = _make_ai(enemy_units=_FakeUnits([worker]))
        return m._try_lift(p, phoenixes, ai), worker

    def test_lift_workers_2_phoenix_no_anti_air(self):
        result, worker = self._setup_worker_only(2)
        assert result is worker

    def test_lift_workers_1_phoenix_no_anti_air(self):
        result, _ = self._setup_worker_only(1)
        assert result is None


def test_lift_low_energy():
    """凤凰 energy=40 < LIFT_ENERGY=50 → 直接 None（前置检查失败）。"""
    from vibecraft.bot.auto_combat.protoss.phoenix_squad_micro import PhoenixSquadMicro

    m = PhoenixSquadMicro()
    p = _make_phoenix(tag=1, pos=(50.0, 50.0), energy=40.0)
    phoenixes = _FakeUnits([p])
    ai = _make_ai()
    result = m._try_lift(p, phoenixes, ai)
    assert result is None


# ---------------------------------------------------------------------------
# 归队后"抬地防守"（用户 2026-07-26：80% 凤凰死在归队后主力退却里不抬送掉）
# ---------------------------------------------------------------------------


def test_lift_defend_lifts_high_priority_ground():
    """有能量凤凰 + 范围内敌方高价值地面单位(高坦) → 抬它(凤凰打不到地面,抬是唯一价值)。"""
    m = _micro()
    phoenixes = _FakeUnits([_make_phoenix(tag=1, pos=(50.0, 50.0), energy=100.0)])
    ht = _make_enemy(tag=100, pos=(55.0, 50.0), type_id=UnitTypeId.HIGHTEMPLAR)
    ai = _make_ai(start_location=(50.0, 50.0), enemy_units=_FakeUnits([ht]))
    actions = m.solve_lift_defend(phoenixes, ai)
    assert actions[1][0] == "lift"
    assert actions[1][1].tag == 100


def test_lift_defend_prioritizes_higher_value():
    """更近的低价值(追猎 pri 2) vs 更远的高价值(高坦 pri 10) → 抬高坦。"""
    m = _micro()
    phoenixes = _FakeUnits([_make_phoenix(tag=1, pos=(50.0, 50.0), energy=100.0)])
    stalker = _make_enemy(tag=100, pos=(52.0, 50.0), type_id=UnitTypeId.STALKER)
    ht = _make_enemy(tag=101, pos=(56.0, 50.0), type_id=UnitTypeId.HIGHTEMPLAR)
    ai = _make_ai(start_location=(50.0, 50.0), enemy_units=_FakeUnits([stalker, ht]))
    actions = m.solve_lift_defend(phoenixes, ai)
    assert actions[1][0] == "lift"
    assert actions[1][1].tag == 101


def test_lift_defend_no_threat_moves_home():
    """无敌方地面威胁 → 凤凰回家附近养能量(move),不乱跑进敌军送死。"""
    m = _micro()
    phoenixes = _FakeUnits([_make_phoenix(tag=1, pos=(50.0, 50.0), energy=100.0)])
    ai = _make_ai(start_location=(50.0, 50.0), enemy_units=_FakeUnits())
    actions = m.solve_lift_defend(phoenixes, ai)
    assert actions[1][0] == "move"


def test_lift_defend_skips_workers():
    """抬地防守不抬农民(优先级低、无战术价值)——只抬战斗单位。农民 → 无威胁 → move 回家。"""
    m = _micro()
    phoenixes = _FakeUnits([_make_phoenix(tag=1, pos=(50.0, 50.0), energy=100.0)])
    probe = _make_enemy(tag=100, pos=(55.0, 50.0), type_id=UnitTypeId.PROBE)
    ai = _make_ai(start_location=(50.0, 50.0), enemy_units=_FakeUnits([probe]))
    actions = m.solve_lift_defend(phoenixes, ai)
    assert actions[1][0] == "move"  # 农民不抬 → 无威胁 → 回家


def test_lift_defend_conservative_retreats_when_aa_heavy():
    """保守型(用户 2026-07-26 保存优先):附近能对空单位 >2 → 不 engage/抬,退守养能量(别扎进 AA 军送)。"""
    m = _micro()
    phoenixes = _FakeUnits([_make_phoenix(tag=1, pos=(50.0, 50.0), energy=100.0)])
    # 3 个能对空地面单位(追猎)贴近 squad → AA 重(>_DEFEND_ENGAGE_AA_MAX=2)
    aa = [
        _make_enemy(tag=100 + i, pos=(53.0, 50.0), type_id=UnitTypeId.STALKER, can_attack_air=True)
        for i in range(3)
    ]
    ai = _make_ai(start_location=(50.0, 50.0), enemy_units=_FakeUnits(aa))
    actions = m.solve_lift_defend(phoenixes, ai)
    assert actions[1][0] == "move"  # AA 重 → 退守,不抬(保存优先)
