"""SpareQueenAct 单测（2026-07-26 用户：坑道链卡住时多余女王去铺菌毯 + 前线防守）。

覆盖：卡住判定（网络就绪够久 ∧ 敌方无虫）/ 链正常时不介入 / 不抢留家注卵女王与坑道队女王 /
脚下有菌毯就种瘤 / 到最外分矿 clear_task 交回 sharpy 防守。

不拉起 SC2：mock ai/knowledge/cache，同 `test_feint_squad_act.py` 范式。
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2


@pytest.fixture(autouse=True)
def _sc2_enum_same_source() -> None:
    import sc2.ids.unit_typeid as _m

    globals()["UnitTypeId"] = _m.UnitTypeId
    yield


@pytest.fixture(autouse=True)
def _fake_unit_command():
    """真 UnitCommand 断言 unit 必须是 sc2.Unit，mock 单位过不去 —— 同 test_nydus_raid.py，
    在 act 模块 import 之前把它换成简单记录器（act 模块在 `_act()` 里才 import，赶得上）。"""
    mod = sys.modules.get("sc2.unit_command")
    created = False
    if mod is None:
        mod = ModuleType("sc2.unit_command")
        sys.modules["sc2.unit_command"] = mod
        created = True
    orig = getattr(mod, "UnitCommand", None)

    def _fake(ability, unit, target=None, queue=False):
        return SimpleNamespace(ability=ability, unit=unit, target=target, queue=queue)

    mod.UnitCommand = _fake  # type: ignore[attr-defined]
    yield
    if created:
        sys.modules.pop("sc2.unit_command", None)
    elif orig is not None:
        mod.UnitCommand = orig  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _fake_sharpy():
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
    sys.modules.pop("vibecraft.bot.auto_combat.zerg.plans.spare_queen_act", None)
    for name in created:
        sys.modules.pop(name, None)


class _Units:
    def __init__(self, items):
        self._items = list(items)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)

    def __bool__(self):
        return bool(self._items)

    @property
    def exists(self):
        return bool(self._items)

    @property
    def ready(self):
        return _Units(list(self._items))

    @property
    def amount(self):
        return len(self._items)


def _queen(tag, pos=(0.0, 0.0), energy=100.0):
    p = Point2(pos)
    return SimpleNamespace(
        tag=tag,
        position=p,
        type_id=UnitTypeId.QUEEN,
        energy=energy,
        distance_to=lambda o, _p=p: _p.distance_to(o if isinstance(o, Point2) else o.position),
        move=MagicMock(),
    )


def _hall(tag, pos):
    p = Point2(pos)
    return SimpleNamespace(
        tag=tag,
        position=p,
        distance_to=lambda o, _p=p: _p.distance_to(o if isinstance(o, Point2) else o.position),
    )


def _act(**overrides):
    from vibecraft.bot.auto_combat.zerg.plans.spare_queen_act import SpareQueenAct

    kwargs = {"stall_after_s": 100.0, "keep_home_queens": 1}
    kwargs.update(overrides)
    a = SpareQueenAct.__new__(SpareQueenAct)
    SpareQueenAct.__init__(a, **kwargs)
    return a


def _wire(
    a,
    *,
    queens=None,
    halls=None,
    networks=("ready",),
    canals=(),
    time=500.0,
    creep=True,
    raid_tags=None,
    network_ready_since=0.0,
):
    a.knowledge = SimpleNamespace(roles=MagicMock(), vibecraft=SimpleNamespace())
    halls = halls or [_hall(90, (0.0, 0.0)), _hall(91, (40.0, 40.0))]
    type_map = {
        UnitTypeId.QUEEN: queens or [],
        UnitTypeId.HATCHERY: halls,
        UnitTypeId.LAIR: [],
        UnitTypeId.HIVE: [],
        UnitTypeId.NYDUSNETWORK: list(networks),
        UnitTypeId.NYDUSCANAL: list(canals),
        UnitTypeId.CREEPTUMOR: [],
        UnitTypeId.CREEPTUMORBURROWED: [],
        UnitTypeId.CREEPTUMORQUEEN: [],
    }
    cache = MagicMock()
    cache.own.side_effect = lambda t: _Units(type_map.get(t, []))
    a.cache = cache
    a.ai = SimpleNamespace(
        time=time,
        _llm_controlled_tags=set(),
        _vibecraft_nydus_raid_tags=set(raid_tags or set()),
        _vibecraft_bypass_actions=[],
        enemy_start_locations=[Point2((100.0, 100.0))],
        start_location=Point2((0.0, 0.0)),
        townhalls=_Units(halls),
        has_creep=lambda p, _c=creep: _c,
        in_placement_grid=lambda p: True,
    )
    a._network_ready_since = network_ready_since
    return a


# ══════════════════════════════════════════════════════════════════════════
# 触发条件：坑道链确实卡住了才动手
# ══════════════════════════════════════════════════════════════════════════
def test_does_nothing_while_nydus_chain_healthy():
    """敌方那边已有坑道虫 = 链没卡 → 完全不介入，女王该注卵注卵。"""
    a = _act()
    q = _queen(5, (0.0, 0.0))
    _wire(a, queens=[_queen(1), q], canals=("canal",))
    a._tick()
    assert a._state == {}
    q.move.assert_not_called()


def test_does_nothing_before_stall_timeout():
    """网络刚 ready、还没到卡住阈值 → 不介入。"""
    a = _act(stall_after_s=100.0)
    q = _queen(5)
    _wire(a, queens=[_queen(1), q], time=50.0, network_ready_since=0.0)
    a._tick()
    assert a._state == {}


# ══════════════════════════════════════════════════════════════════════════
# 不抢别人的女王
# ══════════════════════════════════════════════════════════════════════════
def test_keeps_inject_queens_home_and_skips_raid_owned():
    """留家注卵的（按基地数）+ 坑道队认领的，一律不碰。"""
    a = _act(keep_home_queens=1)
    qs = [_queen(1), _queen(2), _queen(3), _queen(4)]
    # 2 个基地 → keep=max(1,2)=2 → tag 1,2 留家；3 被坑道队认领 → 只剩 4 可用
    _wire(a, queens=qs, raid_tags={3})
    spare = a._spare_queens()
    assert [q.tag for q in spare] == [4]


# ══════════════════════════════════════════════════════════════════════════
# 行为：铺菌毯 → 到前线交还 sharpy
# ══════════════════════════════════════════════════════════════════════════
def test_spare_queen_plants_tumor_when_on_creep():
    a = _act()
    q = _queen(9, (0.0, 0.0))
    _wire(a, queens=[_queen(1), _queen(2), q], creep=True)
    a._tick()
    assert a.ai._vibecraft_bypass_actions, "脚下有菌毯 + 能量够 → 应种菌毯瘤"


def test_spare_queen_walks_to_frontier_when_no_creep():
    """没菌毯可种 → 朝最外分矿（离敌方主基最近的自家基地）走。"""
    a = _act()
    q = _queen(9, (0.0, 0.0))
    _wire(a, queens=[_queen(1), _queen(2), q], creep=False)
    a._tick()
    assert a._state[9] == "CREEP"
    q.move.assert_called()
    assert q.move.call_args[0][0] == Point2((40.0, 40.0))  # 离敌方(100,100)最近那个基地


def test_spare_queen_released_to_sharpy_at_frontier():
    """到最外分矿 → clear_task 交回 sharpy 当防守兵，本 act 不再管它。"""
    a = _act()
    q = _queen(9, (40.0, 40.0))  # 已在前线
    _wire(a, queens=[_queen(1), _queen(2), q], creep=False)
    a._tick()
    assert a._state[9] == "DEFEND"
    a.knowledge.roles.clear_task.assert_called()
    # DEFEND 之后不再被招募回来
    assert [x.tag for x in a._spare_queens()] == []
