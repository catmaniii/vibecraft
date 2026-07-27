"""_SharpyFacadeBase.release_unit_role 单测 + facade Protocol 一致性 audit。

2026-06-07 玩家报根因:取消"集中"指令卡后,被 claim 的虚空仍不听全军进攻。
追到 _SharpyFacadeBase(真实游戏 facade)**漏实现 release_unit_role**——而 Sc2Facade
是 Protocol(运行时不强制),且单测一直用 FakeFacade(有此方法)→ 单测绿、真局炸。

后果链:revoke_directive → _release_standing_order_units → `hasattr(facade,
"release_unit_role")` 在真实游戏为 False → role 永不释放 → tag 留在
_llm_controlled_tags → _refresh_llm_controlled_roles 每帧 re-Reserve → 永久锁死。

这里:
1. audit —— _SharpyFacadeBase 必须实现 Sc2Facade Protocol 的全部公开方法(防再漏)。
2. 行为 —— release_unit_role 无条件把 tag 从 _llm_controlled_tags 移除(核心修复点)。
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace


def test_sharpy_facade_implements_all_protocol_methods() -> None:
    """_SharpyFacadeBase 必须实现 Sc2Facade Protocol 的全部公开方法。

    Protocol 不强制实现 → 漏方法不报错 → 单测用 FakeFacade 测不出,真局崩。
    这条 audit 把"FakeFacade 有但真实 facade 没有"的偏差挡在单测里。
    """
    from vibecraft.bot.auto_combat.common_bot import _make_sharpy_facade_base_class
    from vibecraft.bot.facade import Sc2Facade

    impl = _make_sharpy_facade_base_class()
    protocol_methods = {
        name
        for name, _ in inspect.getmembers(Sc2Facade, inspect.isfunction)
        if not name.startswith("_")
    }
    assert protocol_methods, "未能从 Sc2Facade Protocol 提取到方法(测试本身坏了)"
    missing = sorted(m for m in protocol_methods if not hasattr(impl, m))
    assert not missing, f"_SharpyFacadeBase 未实现 Sc2Facade Protocol 方法: {missing}"


def test_release_unit_role_removes_from_llm_controlled_tags() -> None:
    """核心修复:release 必须把 tag 从 _llm_controlled_tags 移除,否则每帧 re-Reserve 锁死。

    单位不在 cache(by_tag→None)→ 只走 discard 分支(不依赖 sc2/sharpy 的 set_task)。
    """
    from vibecraft.bot.auto_combat.common_bot import _make_sharpy_facade_base_class

    cls = _make_sharpy_facade_base_class()

    class _Cache:
        def by_tag(self, _tag: int) -> None:
            return None  # 单位已不在 cache → 只验 discard

    bot = SimpleNamespace(
        _llm_controlled_tags={101, 202},
        knowledge=SimpleNamespace(unit_cache=_Cache()),
    )
    facade = cls(bot)
    facade.release_unit_role(101)
    assert 101 not in bot._llm_controlled_tags  # 停止每帧 re-Reserve(核心)
    assert 202 in bot._llm_controlled_tags  # 只移除指定的,不误伤别的


def test_release_unit_role_resets_task_when_unit_alive() -> None:
    """单位还活着 → 把 sharpy task 从 Reserved 还原(set_task 被调),让 PlanZoneAttack 接管。"""
    from unittest.mock import MagicMock

    from vibecraft.bot.auto_combat.common_bot import _make_sharpy_facade_base_class

    cls = _make_sharpy_facade_base_class()
    unit = SimpleNamespace(tag=101)

    class _Cache:
        def by_tag(self, tag: int):
            return unit if tag == 101 else None

    roles = MagicMock()
    bot = SimpleNamespace(
        _llm_controlled_tags={101},
        knowledge=SimpleNamespace(unit_cache=_Cache(), roles=roles),
    )
    facade = cls(bot)
    facade.release_unit_role(101)
    assert 101 not in bot._llm_controlled_tags
    assert roles.set_task.called  # 还原 sharpy task → free 给 PlanZoneAttack


def test_fake_facade_register_stealth_townhalls_records_tags() -> None:
    """FakeFacade.register_stealth_townhalls 整体覆盖写入 stealth_townhall_tags 属性。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    assert f.stealth_townhall_tags == set()
    f.register_stealth_townhalls({10, 20, 30})
    assert f.stealth_townhall_tags == {10, 20, 30}
    # 再次调用整体覆盖（旧值消失）
    f.register_stealth_townhalls({5})
    assert f.stealth_townhall_tags == {5}
    # calls 记录
    assert any(c.method == "register_stealth_townhalls" for c in f.calls)


def test_fake_facade_train_probe_at_records_call() -> None:
    """FakeFacade.train_probe_at 记录 nexus_tag 调用列表，默认返回 True。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    assert f.train_probe_calls == []
    result = f.train_probe_at(101)
    assert result is True
    assert f.train_probe_calls == [101]
    # 再次调用，追加
    f.train_probe_at(202)
    assert f.train_probe_calls == [101, 202]
    # calls 记录
    assert any(c.method == "train_probe_at" for c in f.calls)


def test_fake_facade_train_probe_at_result_controllable() -> None:
    """FakeFacade.train_probe_at_result 可被测试覆盖（模拟资源不足 → False）。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f.train_probe_at_result = False
    assert f.train_probe_at(99) is False


def test_resolve_target_point_handles_camera() -> None:
    """camera 目标(已注入 point)必须能解析(2026-06-08 修 standby/move kind=camera unresolvable)。"""
    from types import SimpleNamespace

    from vibecraft.bot.auto_combat.common_bot import _make_sharpy_facade_base_class

    cls = _make_sharpy_facade_base_class()
    facade = cls(SimpleNamespace())
    pt = facade._resolve_target_point({"kind": "camera", "point": [42.0, 84.0], "named_spot": None})
    assert pt is not None
    assert float(pt.x) == 42.0 and float(pt.y) == 84.0
    # 无 point 的 camera → None(不崩)
    assert facade._resolve_target_point({"kind": "camera", "point": None}) is None


# ---------------------------------------------------------------------------
# WP4b：FakeFacade 4 个新方法行为 + Protocol audit（已由顶层 audit 测试覆盖）
# ---------------------------------------------------------------------------


def test_fake_facade_find_stealth_geysers_returns_stub() -> None:
    """FakeFacade.find_stealth_geysers 默认返回 []；注入 stub 后返回 stub。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    assert f.find_stealth_geysers((50.0, 60.0), 8.0) == []
    # 注入 stub
    f.stealth_geysers_stub = [(101, (52.0, 58.0)), (102, (48.0, 62.0))]
    result = f.find_stealth_geysers((50.0, 60.0), 8.0)
    assert result == [(101, (52.0, 58.0)), (102, (48.0, 62.0))]
    # calls 记录
    assert any(c.method == "find_stealth_geysers" for c in f.calls)


def test_fake_facade_order_probe_build_gas_records_call() -> None:
    """FakeFacade.order_probe_build_gas 记录 (probe_tag, geyser_tag)。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f.order_probe_build_gas(11, 201)
    assert (11, 201) in f.gas_build_orders
    assert any(c.method == "order_probe_build_gas" for c in f.calls)


def test_fake_facade_find_stealth_gas_buildings_returns_stub() -> None:
    """FakeFacade.find_stealth_gas_buildings 默认返回 []；注入后返回 stub。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    assert f.find_stealth_gas_buildings((50.0, 60.0), 8.0) == []
    f.stealth_gas_buildings_stub = [(301, 2, 3)]
    result = f.find_stealth_gas_buildings((50.0, 60.0), 8.0)
    assert result == [(301, 2, 3)]
    assert any(c.method == "find_stealth_gas_buildings" for c in f.calls)


def test_fake_facade_order_worker_gather_gas_records_call() -> None:
    """FakeFacade.order_worker_gather_gas 记录 (worker_tag, gas_building_tag)。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f.order_worker_gather_gas(55, 301)
    assert (55, 301) in f.gas_gather_orders
    assert any(c.method == "order_worker_gather_gas" for c in f.calls)
