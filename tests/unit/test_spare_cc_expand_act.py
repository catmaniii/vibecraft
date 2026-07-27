"""SpareCcExpandAct 单测（#560）：空闲 spare CC 飞去开矿 + 无 spare 时 no-op。

核心不变量：
  - 无 spare CC（不造额外 CC 的 build）→ **完全 no-op**，不往 bypass 队列塞任何命令（零回归）。
  - 有 idle spare CC（远离矿）+ 有未占扩张点 → 锁定落点 + 经 bypass 发 LIFT（绕 prevent_double 坑）。
  - 在矿区的 CC（主基）不被当 spare（不会被起飞）。

ActBase 子类用 __new__ 绕 __init__，手塞字段；ai 用 SimpleNamespace + AsyncMock。
"""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2


def _live_ability_id():
    """运行时解析 AbilityId —— **必须**和被测 act `from sc2.ids.ability_id import
    AbilityId` 拿到的是**同一个 live 模块对象**。

    根因（order-isolation flake）：本文件若在模块顶部 `from ... import AbilityId`，
    绑定的是 *collection 期* 那个 enum 类。全量跑时，前序测试（test_sharpy_adapter /
    test_game_config_my_race / test_sharpy_vibecraft_hooks 等）会 `del sys.modules['sc2.*']`
    注入 fake 后让真实模块**全新重导入** → 生成**新的 AbilityId enum 类**。spare act 在
    全量序列下重导入拿到新 enum，而 mock 返回的是旧 enum 成员 → `新 in [旧]` 按 identity
    恒 False → act 不发 LIFT → `_vibecraft_bypass_actions` 不创建 → AttributeError。
    在调用点现取 `sys.modules` 里当前 live 的那个 AbilityId（与 act 同源）即可免疫。
    """
    from sc2.ids.ability_id import AbilityId

    return AbilityId


@pytest.fixture(autouse=True)
def _fake_unit_command():
    """真 UnitCommand 会校验 unit 是真 Unit；测试用 SimpleNamespace 当 unit，
    故注入 fake UnitCommand（记录 ability/unit/target）让 _bypass 能塞进队列。"""
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
    """bc... 顶层 import sharpy.plans.acts.ActBase；注入 fake 让 import 过。"""
    import sys
    from types import ModuleType

    created = []
    for name in ("sharpy", "sharpy.plans", "sharpy.plans.acts"):
        if name not in sys.modules:
            sys.modules[name] = ModuleType(name)
            created.append(name)
    acts = sys.modules["sharpy.plans.acts"]
    if not hasattr(acts, "ActBase"):
        acts.ActBase = type("ActBase", (), {})  # type: ignore[attr-defined]
    yield
    sys.modules.pop("vibecraft.bot.auto_combat.terran.spare_cc_expand_act", None)
    for name in created:
        sys.modules.pop(name, None)


def _act():
    from vibecraft.bot.auto_combat.terran.spare_cc_expand_act import SpareCcExpandAct

    a = SpareCcExpandAct.__new__(SpareCcExpandAct)
    a._target_by_tag = {}
    a._lift_time_by_tag = {}
    a._traced_tags = set()
    a._lifted_ok_traced = set()
    a._gave_up = set()
    a._diag_traced = set()
    a._landing_traced = set()
    return a


class _Units:
    """可调用过滤 + closer_than 的 fake 集合。"""

    def __init__(self, items):
        self._items = list(items)

    def __call__(self, _type):
        return self  # of_type 忽略，测试里只放对应类型

    @property
    def ready(self):
        return _Units([u for u in self._items if getattr(u, "is_ready", True)])

    def closer_than(self, dist, pos):
        p = pos if isinstance(pos, Point2) else pos.position
        return _Units([u for u in self._items if u.position.distance_to(p) < dist])

    @property
    def exists(self):
        return bool(self._items)

    def __iter__(self):
        return iter(self._items)


def _cc(tag, pos, idle=True, ready=True, type_id=UnitTypeId.COMMANDCENTER):
    p = Point2(pos)
    return SimpleNamespace(
        tag=tag,
        position=p,
        is_idle=idle,
        is_ready=ready,
        type_id=type_id,
        orders=[],
        distance_to=lambda o, _p=p: _p.distance_to(o if isinstance(o, Point2) else o.position),
    )


def _wire(a, *, ccs, minerals, expansions, flying=None, lift_available=True):
    a.ai = SimpleNamespace(
        time=100.0,
        townhalls=_Units(ccs),
        mineral_field=_Units(minerals),
        structures=lambda _t: _Units(flying or []),
        expansion_locations_list=[Point2(p) for p in expansions],
        get_available_abilities=AsyncMock(
            return_value=[_live_ability_id().LIFT_COMMANDCENTER] if lift_available else []
        ),
    )
    # townhalls(type) 应能再 closer_than（占用判定）。_Units.__call__ 返回 self，OK。


# --- 无 spare CC → no-op（零回归核心契约）---------------------------------------


def test_noop_when_no_spare_cc():
    """所有 CC 都在矿区（主基）→ 不是 spare → 不往 bypass 塞命令（对现有 build 零影响）。"""
    a = _act()
    main = _cc(1, (50, 50))
    _wire(
        a, ccs=[main], minerals=[SimpleNamespace(position=Point2((52, 50)))], expansions=[(90, 90)]
    )
    asyncio.run(a._tick())
    assert not getattr(a.ai, "_vibecraft_bypass_actions", [])  # 没塞任何命令


def test_noop_when_no_free_expansion():
    """有 spare CC 但没有空闲扩张点 → 不起飞（不锁 target、不发 LIFT）。"""
    a = _act()
    spare = _cc(2, (50, 50))
    # 没有 mineral 靠近 spare（是 spare）；但 expansions 都被 townhall 占（无 free）
    occupied_exp = (50, 50)  # 主基自己占着
    _wire(a, ccs=[spare], minerals=[], expansions=[occupied_exp])
    asyncio.run(a._tick())
    assert not getattr(a.ai, "_vibecraft_bypass_actions", [])


# --- 有 spare CC + free expansion → 锁定 + 发 LIFT（经 bypass）--------------------


def test_spare_cc_locks_target_and_lifts_via_bypass():
    """idle spare CC（远离矿）+ free expansion → 锁定落点 + 经 bypass 发 LIFT_COMMANDCENTER。"""
    a = _act()
    spare = _cc(2, (50, 50))
    far_exp = (90, 90)  # 远离任何 townhall + 有矿
    mins_at_exp = SimpleNamespace(position=Point2((92, 90)))
    _wire(a, ccs=[spare], minerals=[mins_at_exp], expansions=[far_exp])
    asyncio.run(a._tick())
    # 落点锁定
    assert 2 in a._target_by_tag
    assert a._target_by_tag[2] == Point2(far_exp)
    # 经 bypass 发了 LIFT（不是直接 cc(LIFT) —— 绕 prevent_double 坑）
    bypass = a.ai._vibecraft_bypass_actions
    assert len(bypass) == 1
    cmd = bypass[0]
    assert cmd.ability == _live_ability_id().LIFT_COMMANDCENTER
    assert cmd.unit.tag == 2


def test_cc_in_mineral_line_not_treated_as_spare():
    """矿区里的 CC（主基，周围有矿）不被当 spare → 不起飞。"""
    a = _act()
    main = _cc(1, (50, 50))
    near_min = SimpleNamespace(position=Point2((53, 50)))  # 主基矿线（<10）
    _wire(a, ccs=[main], minerals=[near_min], expansions=[(90, 90)])
    asyncio.run(a._tick())
    assert not getattr(a.ai, "_vibecraft_bypass_actions", [])


def test_lift_not_available_no_command():
    """LIFT 不可用（CC 在产兵等）→ 锁 target 但不发 LIFT（等可用再发）。"""
    a = _act()
    spare = _cc(2, (50, 50))
    mins_at_exp = SimpleNamespace(position=Point2((92, 90)))
    _wire(a, ccs=[spare], minerals=[mins_at_exp], expansions=[(90, 90)], lift_available=False)
    asyncio.run(a._tick())
    assert 2 in a._target_by_tag  # 锁了 target
    assert not getattr(a.ai, "_vibecraft_bypass_actions", [])  # 但没发 LIFT（不可用）
