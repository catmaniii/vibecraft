"""harass_workers player claim 单测。

覆盖：
1. harass_act 模块级纯函数：player_should_bail + player_harass_micro
2. director._publish_worker_harass_tags：正确汇总 tags 并写入 vibecraft namespace
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# Fixture：fake sharpy（harass_act 顶层 import ActBase）
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fake_sharpy_for_harass():
    created = []
    for name in ("sharpy", "sharpy.plans", "sharpy.plans.acts"):
        if name not in sys.modules:
            sys.modules[name] = ModuleType(name)
            created.append(name)
    acts = sys.modules["sharpy.plans.acts"]
    if not hasattr(acts, "ActBase"):
        acts.ActBase = type("ActBase", (), {})  # type: ignore[attr-defined]
    yield
    sys.modules.pop("vibecraft.bot.auto_combat.harass_act", None)
    for name in created:
        sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unit(
    tag: int = 1,
    health: float = 100,
    health_max: float = 100,
    shield: float = 0,
    shield_max: float = 0,
    is_flying: bool = False,
    weapon_cooldown: float = 0.0,
) -> SimpleNamespace:
    pos = SimpleNamespace(x=10.0, y=10.0)
    pos.towards = lambda target, dist: ("towards", target, dist)
    return SimpleNamespace(
        tag=tag,
        health=health,
        health_max=health_max,
        shield=shield,
        shield_max=shield_max,
        is_flying=is_flying,
        weapon_cooldown=weapon_cooldown,
        position=pos,
        target_in_range=lambda w: False,
        attack=lambda t: None,
        move=lambda t: None,
        distance_to=lambda t: 30.0,  # default: far from enemy main
        is_ready=True,
    )


# ---------------------------------------------------------------------------
# player_should_bail
# ---------------------------------------------------------------------------


def test_player_should_bail_critical_hp():
    from vibecraft.bot.auto_combat.harass_act import player_should_bail

    bailing: set[int] = set()
    u = _unit(tag=5, health=50, health_max=100)  # hp=0.5 < bail=0.6
    assert player_should_bail(u, bailing) is True
    assert 5 in bailing


def test_player_should_bail_healthy_unit_does_not_bail():
    from vibecraft.bot.auto_combat.harass_act import player_should_bail

    bailing: set[int] = set()
    u = _unit(tag=5, health=90, health_max=100)  # hp=0.9 > bail=0.6
    assert player_should_bail(u, bailing) is False
    assert 5 not in bailing


def test_player_should_bail_hysteresis_until_recover():
    from vibecraft.bot.auto_combat.harass_act import player_should_bail

    bailing: set[int] = set()
    u_low = _unit(tag=7, health=50, health_max=100)  # 0.5 < 0.6 → bail
    assert player_should_bail(u_low, bailing) is True

    u_mid = _unit(tag=7, health=70, health_max=100)  # 0.7 < recover=0.99 → still bail
    assert player_should_bail(u_mid, bailing) is True

    u_full = _unit(tag=7, health=100, health_max=100)  # 1.0 >= recover=0.99 → clear
    assert player_should_bail(u_full, bailing) is False
    assert 7 not in bailing


def test_player_should_bail_already_bailing_cleared_at_full():
    from vibecraft.bot.auto_combat.harass_act import player_should_bail

    bailing = {99}
    u = _unit(tag=99, health=100, health_max=100)
    assert player_should_bail(u, bailing) is False
    assert 99 not in bailing


# ---------------------------------------------------------------------------
# player_harass_micro — approach phase (far from enemy main)
# ---------------------------------------------------------------------------


def test_player_harass_micro_approaches_enemy_main_when_far():
    """离矿区远 → move 向敌方主基地。"""
    from vibecraft.bot.auto_combat.harass_act import player_harass_micro

    calls: list = []
    enemy_main = SimpleNamespace(x=100.0, y=100.0)

    def close_or_far(t: object) -> float:
        if t is enemy_main:
            return 30.0  # 30 > _ARRIVE_DIST=22 → still far
        return 5.0

    u = _unit(tag=1)
    u.distance_to = close_or_far
    u.move = lambda t: calls.append(("move", t))

    player_harass_micro(u, None, enemy_main, [], set(), SimpleNamespace(x=0.0, y=0.0))
    assert calls == [("move", enemy_main)]


# ---------------------------------------------------------------------------
# player_harass_micro — bail phase
# ---------------------------------------------------------------------------


def test_player_harass_micro_retreats_to_start_when_bailing():
    """血量低 → 撤回 start_location。"""
    from vibecraft.bot.auto_combat.harass_act import player_harass_micro

    calls: list = []
    start = SimpleNamespace(x=0.0, y=0.0)
    enemy_main = SimpleNamespace(x=100.0, y=100.0)

    u = _unit(tag=3, health=50, health_max=100)  # bail_hp=0.6 → will bail
    u.move = lambda t: calls.append(("move", t))

    bailing: set[int] = set()
    player_harass_micro(u, None, enemy_main, [], bailing, start)
    assert ("move", start) in calls
    assert 3 in bailing


# ---------------------------------------------------------------------------
# player_harass_micro — hit-and-run at mineral line
# ---------------------------------------------------------------------------


def test_player_harass_micro_attacks_worker_in_range():
    """已到矿区 + 武器好 + 射程内有农民 → attack 那个农民。"""
    from vibecraft.bot.auto_combat.harass_act import player_harass_micro

    calls: list = []
    enemy_main = SimpleNamespace(x=5.0, y=5.0)

    u = _unit(tag=10, health=100, health_max=100)
    u.distance_to = lambda t: 5.0  # 5 < _ARRIVE_DIST=22 → at mineral line
    worker = SimpleNamespace(
        tag=20,
        can_attack_ground=True,
        can_attack_air=False,
        position=SimpleNamespace(x=5.0, y=5.0),
        distance_to=lambda _: 3.0,
    )
    u.target_in_range = lambda w: w is worker
    u.attack = lambda t: calls.append(("attack", t))
    u.move = lambda t: calls.append(("move", t))

    # workers object needs closest_to
    workers = SimpleNamespace()
    workers.__iter__ = lambda s: iter([worker])
    workers.__bool__ = lambda s: True
    workers.closest_to = lambda u: worker

    player_harass_micro(u, workers, enemy_main, [], set(), SimpleNamespace(x=0.0, y=0.0))
    assert ("attack", worker) in calls


# ---------------------------------------------------------------------------
# director._publish_worker_harass_tags
# ---------------------------------------------------------------------------


def _make_director_stub_with_vib() -> SimpleNamespace:
    """构造最小 director stub：只有 _publish_worker_harass_tags 需要的字段。"""
    from vibecraft.bot.director import Director
    from vibecraft.directives.models import Directive, UnitClaimPayload
    from vibecraft.directives.scope import Selector
    from vibecraft.directives.task import Action, Task, Verb

    d = object.__new__(Director)
    vib = SimpleNamespace(worker_harass_tags=set(), bc_harass_groups=[])
    bot_stub = SimpleNamespace(knowledge=SimpleNamespace(vibecraft=vib))
    d._bot = bot_stub
    d.standing_orders = []
    d._standing_order_tags = {}
    d._worker_harass_bailing = set()

    def _make_claim(tags: set[int]) -> Directive:
        action = Action(verb=Verb.HARASS_WORKERS, target=None)
        task = Task(primary_action=action)
        selector = Selector(unit_type="Reaper")
        payload = UnitClaimPayload(selector=selector, task=task, persistent=True)
        directive = Directive(payload=payload, issued_at=0.0)
        d.standing_orders.append(directive)
        d._standing_order_tags[directive.id] = set(tags)
        return directive

    return d, vib, _make_claim


def test_publish_worker_harass_tags_empty_when_no_claims():
    d, vib, _ = _make_director_stub_with_vib()
    d._publish_worker_harass_tags()
    assert vib.worker_harass_tags == set()


def test_publish_worker_harass_tags_collects_all_tags():
    d, vib, make_claim = _make_director_stub_with_vib()
    make_claim({101, 102})
    make_claim({103})
    d._publish_worker_harass_tags()
    assert vib.worker_harass_tags == {101, 102, 103}


def test_publish_worker_harass_tags_ignores_other_verbs():
    """group_harass standing order 的 tags 不该混入 worker_harass_tags。"""
    from vibecraft.directives.models import Directive, UnitClaimPayload
    from vibecraft.directives.scope import Selector
    from vibecraft.directives.task import Action, Task, Verb

    d, vib, make_claim = _make_director_stub_with_vib()
    # add a group_harass order (different verb) separately
    action_gh = Action(verb=Verb.GROUP_HARASS, target=None)
    task_gh = Task(primary_action=action_gh)
    payload_gh = UnitClaimPayload(
        selector=Selector(unit_type="BATTLECRUISER"), task=task_gh, persistent=True
    )
    dir_gh = Directive(payload=payload_gh, issued_at=0.0)
    d.standing_orders.append(dir_gh)
    d._standing_order_tags[dir_gh.id] = {999}

    make_claim({101})
    d._publish_worker_harass_tags()
    assert vib.worker_harass_tags == {101}
    assert 999 not in vib.worker_harass_tags


def test_publish_clears_when_all_claims_gone():
    d, vib, make_claim = _make_director_stub_with_vib()
    make_claim({101})
    d._publish_worker_harass_tags()
    assert vib.worker_harass_tags == {101}

    # remove the standing order (simulates claim revoked)
    d.standing_orders.clear()
    d._standing_order_tags.clear()
    d._publish_worker_harass_tags()
    assert vib.worker_harass_tags == set()


# ---------------------------------------------------------------------------
# 回归：harass_workers claim 的 target=None（"没指明矿区 → auto 轮换"）不能崩命令路径
# （2026-07-05 真局自验暴露：_brief_directive / _inject_camera_point 裸解引用 target →
#  提交前 AttributeError → claim 从没提交 → 死神杵着不动）
# ---------------------------------------------------------------------------


def _harass_claim_target_none():
    from vibecraft.directives.models import Directive, UnitClaimPayload
    from vibecraft.directives.scope import Selector
    from vibecraft.directives.task import Action, Task, Verb

    action = Action(verb=Verb.HARASS_WORKERS, target=None)  # 关键：target 省略
    task = Task(primary_action=action)
    payload = UnitClaimPayload(selector=Selector(unit_type="Reaper"), task=task, persistent=True)
    return Directive(payload=payload, issued_at=0.0)


def test_brief_directive_harass_workers_target_none_no_crash():
    """_brief_directive 遇到 target=None 的 harass_workers claim 不得抛 AttributeError。"""
    from vibecraft.bot.director import Director

    d = object.__new__(Director)
    out = d._brief_directive(_harass_claim_target_none())
    assert "harass_workers" in out
    assert "auto" in out  # target=None → "auto"


def test_inject_camera_point_harass_workers_target_none_no_crash():
    """_inject_camera_point 遇到 target=None 的 harass_workers claim 不得抛 AttributeError。"""
    from vibecraft.bot.director import Director

    d = object.__new__(Director)
    # 不抛异常即通过（target=None 时 _patch_target 直接 return）
    d._inject_camera_point([_harass_claim_target_none()], (5.0, 5.0))


# ---------------------------------------------------------------------------
# 修复(根因 F80/F81)：骚扰参考点用「离本单位最近的敌方农民位置」，不是死主基地。
#   飞龙(射程 3)骚扰二矿时，原来恒传 enemy_start_locations[0] → far 判定恒 True →
#   一直往主基飞、够不着二矿农民。修后 ref_point = 最近农民位置 → 到二矿 hit-and-run。
# ---------------------------------------------------------------------------


class _FakeUnits:
    """最小 Units 替身：filter / closest_to / by_tag / 迭代 / 真值。"""

    def __init__(self, items: list) -> None:
        self._items = list(items)

    def __iter__(self):
        return iter(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def filter(self, fn):
        return _FakeUnits([u for u in self._items if fn(u)])

    def closest_to(self, _u):
        return self._items[0] if self._items else None

    def by_tag(self, tag):
        for it in self._items:
            if getattr(it, "tag", None) == tag:
                return it
        return None


def _make_wharass_bot(*, has_worker: bool) -> tuple[object, object, object]:
    """构造 director + bot stub：飞龙在二矿(离主基远)，视野内(可选)有二矿农民。"""
    from sc2.ids.unit_typeid import UnitTypeId

    from vibecraft.bot.director import Director

    enemy_main = SimpleNamespace(x=100.0, y=100.0)  # 敌方主基地(远)
    worker_pos = SimpleNamespace(x=40.0, y=40.0)  # 二矿农民(离主基 ~85)
    workers = []
    if has_worker:
        workers.append(
            SimpleNamespace(
                tag=20,
                position=worker_pos,
                type_id=UnitTypeId.DRONE,
                is_structure=False,
            )
        )
    muta = SimpleNamespace(tag=1, is_ready=True, distance_to=lambda _t: 999.0)
    vib = SimpleNamespace(worker_harass_tags={1})
    bot = SimpleNamespace(
        knowledge=SimpleNamespace(vibecraft=vib),
        enemy_units=_FakeUnits(workers),
        enemy_structures=_FakeUnits([]),
        units=_FakeUnits([muta]),
        enemy_start_locations=[enemy_main],
        start_location=SimpleNamespace(x=0.0, y=0.0),
    )
    d = object.__new__(Director)
    d._bot = bot
    d._worker_harass_bailing = set()
    return d, enemy_main, worker_pos


def _patch_micro_capture(monkeypatch) -> dict:
    """把 player_harass_micro 换成捕获 enemy_main 参数的 stub,返回 captured dict。

    注:必须先 `import ... as H` 再对**模块对象**打补丁(不能用字符串路径)——autouse
    fixture 每个用例后 pop harass_act 出 sys.modules 但没清父包 vibecraft.bot.auto_combat
    的 .harass_act 属性(残留旧模块对象),monkeypatch 字符串路径会打到那个残留旧对象,而
    director 运行时 `from ... import` 触发**重新导入**拿到的是新对象 → 补丁打空。先显式
    import 一次让 sys.modules/父包属性都指向同一新对象,再对该对象打补丁,两边就一致了。
    """
    import vibecraft.bot.auto_combat.harass_act as H

    captured: dict = {}

    def _fake_micro(unit, workers, enemy_main, threats, bailing, start_location, *a, **k):
        captured["ref"] = enemy_main

    monkeypatch.setattr(H, "player_harass_micro", _fake_micro)
    return captured


def test_wharass_micro_ref_point_is_nearest_worker_not_main(monkeypatch):
    """有可见二矿农民 → player_harass_micro 收到的 enemy_main = 农民位置,不是主基地。"""
    captured = _patch_micro_capture(monkeypatch)
    d, enemy_main, worker_pos = _make_wharass_bot(has_worker=True)
    d._execute_worker_harass_micro()
    assert captured["ref"] is worker_pos  # 扑向二矿农民,不是死主基地
    assert captured["ref"] is not enemy_main


def test_wharass_micro_ref_point_falls_back_to_main_when_no_worker(monkeypatch):
    """无可见农民 → fallback 回主基地(保留原「没视野直奔主基找」行为)。"""
    captured = _patch_micro_capture(monkeypatch)
    d, enemy_main, _worker_pos = _make_wharass_bot(has_worker=False)
    d._execute_worker_harass_micro()
    assert captured["ref"] is enemy_main
