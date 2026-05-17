"""验证 SharpyFacade 3 个方法写到 knowledge.vibecraft namespace 的逻辑。

_SharpyFacade 是 make_protoss_bot_class 函数内的嵌套类，不可直接 import。
测试策略：
  1. 向 sys.modules 注入 fake sharpy（与 test_sharpy_adapter.py 同模式）
  2. import protoss.bot，调 make_protoss_bot_class 拿 bot_class
  3. 实例化 bot，手工注入 knowledge.vibecraft namespace
  4. asyncio.run(bot.on_start()) —— super().on_start() 是 FakeKnowledgeBot.on_start（no-op）
  5. 从 bot.facade 拿真实 _SharpyFacade 实例，对 3 个方法断言
"""

from __future__ import annotations

import asyncio
import queue
import sys
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

_PROTOSS_BOT_MOD = "vibecraft.bot.auto_combat.protoss.bot"


# ---------------------------------------------------------------------------
# fake sharpy 注入
# ---------------------------------------------------------------------------


def _inject_fake_sharpy() -> None:
    """注入最小 fake sharpy / sc2 模块，让 protoss/bot.py 顶层 import 通过。"""
    import enum

    class FakeUnitTask(enum.IntEnum):
        Idle = 0
        Reserved = 8

    for mod_name in [
        "sharpy",
        "sharpy.knowledges",
        "sharpy.knowledges.knowledge_bot",
        "sharpy.managers",
        "sharpy.managers.core",
        "sharpy.managers.core.roles",
        "sharpy.managers.core.roles.unit_task",
        "sharpy.managers.extensions",
        "sharpy.managers.extensions.build_order_manager",
        "sharpy.plans",
        "sharpy.plans.if_else",
        "sharpy.plans.acts",
        "sharpy.plans.acts.act_unit_once",
        "sharpy.plans.require",
        "sharpy.plans.require.supply",
        "sharpy.plans.require.custom_requirement",
        "sharpy.plans.require.require_base",
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = ModuleType(mod_name)

    sys.modules["sharpy.managers.core.roles.unit_task"].UnitTask = FakeUnitTask  # type: ignore[attr-defined]
    sys.modules["sharpy.managers.core.roles"].UnitTask = FakeUnitTask  # type: ignore[attr-defined]

    class FakeBuildOrder:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

    class FakeIfElse:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

    class FakeActUnitOnce:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

    class FakeRequireBase:
        pass

    class FakeRequireSupply:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

    class FakeCustomRequirement:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

    sys.modules["sharpy.plans"].BuildOrder = FakeBuildOrder  # type: ignore[attr-defined]
    sys.modules["sharpy.plans"].IfElse = FakeIfElse  # type: ignore[attr-defined]
    sys.modules["sharpy.plans.if_else"].IfElse = FakeIfElse  # type: ignore[attr-defined]
    sys.modules["sharpy.plans.acts.act_unit_once"].ActUnitOnce = FakeActUnitOnce  # type: ignore[attr-defined]
    sys.modules["sharpy.plans.require.require_base"].RequireBase = FakeRequireBase  # type: ignore[attr-defined]
    sys.modules["sharpy.plans.require.supply"].RequireSupply = FakeRequireSupply  # type: ignore[attr-defined]
    sys.modules["sharpy.plans.require.custom_requirement"].CustomRequirement = FakeCustomRequirement  # type: ignore[attr-defined]

    class FakeKnowledge:
        def __init__(self) -> None:
            self.roles = MagicMock()
            self.unit_cache = MagicMock()

        def pre_start(self, *a: Any, **kw: Any) -> None:
            pass

        async def start(self) -> None:
            pass

        async def update(self, iteration: int) -> None:
            pass

        async def post_update(self) -> None:
            pass

        async def on_unit_destroyed(self, unit_tag: int) -> None:
            pass

        async def on_end(self, result: Any) -> None:
            pass

        def print(self, *a: Any, **kw: Any) -> None:
            pass

    class FakeKnowledgeBot:
        """sharpy KnowledgeBot 极简 stub。"""

        def __init__(self, name: str = "fake") -> None:
            self.name = name
            self.knowledge = FakeKnowledge()

        def create_plan(self) -> Any:
            return None

        async def on_start(self) -> None:
            pass

        async def on_step(self, iteration: int) -> None:
            pass

    sys.modules["sharpy.knowledges.knowledge_bot"].KnowledgeBot = FakeKnowledgeBot  # type: ignore[attr-defined]

    for mod_name in ["sc2", "sc2.position", "sc2.ids", "sc2.ids.unit_typeid"]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = ModuleType(mod_name)

    class FakePoint2:
        def __init__(self, pt: Any) -> None:
            self._pt = pt

    sys.modules["sc2.position"].Point2 = FakePoint2  # type: ignore[attr-defined]

    class FakeUnitTypeId:
        NEXUS = "NEXUS"

    sys.modules["sc2.ids.unit_typeid"].UnitTypeId = FakeUnitTypeId  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# fixture：每个 test 注入 fake sharpy 并清理 protoss.bot 模块缓存
# ---------------------------------------------------------------------------


_SHARPY_PREFIXES = (
    "sharpy",
    "vibecraft.bot.auto_combat",
    "vibecraft.bot.sharpy_adapter",
)


def _clean_sharpy_mods() -> None:
    for key in list(sys.modules):
        if any(key == p or key.startswith(p + ".") for p in _SHARPY_PREFIXES):
            del sys.modules[key]


@pytest.fixture(autouse=True)
def _fake_sharpy_env():
    _clean_sharpy_mods()
    _inject_fake_sharpy()
    yield
    _clean_sharpy_mods()


# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------


def _make_facade_for_test() -> tuple[Any, Any]:
    """返回 (facade_instance, knowledge.vibecraft namespace)。

    facade 是真正的 _SharpyFacade（不是 FakeFacade）。
    不调 asyncio.run()：直接手工初始化 bot 状态 + 构造 facade，
    避免 ProactorEventLoop unclosed 触发 filterwarnings=error。
    """
    import importlib

    mod = importlib.import_module(_PROTOSS_BOT_MOD)

    def _noop_director_factory(facade: Any) -> Any:
        return None

    def _noop_run_cmd(cmd: Any, echo: Any) -> None:
        pass

    bot_class = mod.make_protoss_bot_class(
        director_factory=_noop_director_factory,
        strategy_library=None,
        status_callback=None,
        down_q=queue.Queue(),
        echo_callback=None,
        snapshot_callback=None,
        event_callback=None,
        minimap_callback=None,
        run_command_with_echo_fn=_noop_run_cmd,
    )
    bot = bot_class()

    # 手工完成 on_start 的关键步骤，不跑 asyncio event loop：
    # 1. 初始化 knowledge.vibecraft namespace（Step 3b(1) 新增的逻辑）
    bot.knowledge.vibecraft = SimpleNamespace(
        attack_target_override=None,
        combat_intent_override=None,
    )
    # 2. 构造 _SharpyFacade：on_start 里 `self.facade = _SharpyFacade(self)`
    #    _SharpyFacade 类型在工厂闭包里；利用 bot_class.__init_subclass__ 找不到，
    #    但 bot_class.on_start 是真实方法，inside 有 _SharpyFacade(self) 调用。
    #    最简单：直接跑 on_start，但用 @pytest.mark.asyncio 或同步 runner。
    #    因为 FakeKnowledgeBot.on_start 是 async no-op，用 asyncio.get_event_loop()
    #    的已有 loop 或者用 asyncio.run_coroutine_threadsafe 都有副作用。
    #
    #    真正最简：构造一个临时 loop，运行完立即 cancel 并 close，
    #    然后显式触发一次 GC 收集，让 __del__ 在 loop 关闭后调用（避免 __del__ in open loop）。
    import gc

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(bot.on_start())
        # 关闭所有 async generators，防止 ResourceWarning
        loop.run_until_complete(loop.shutdown_asyncgens())
    finally:
        loop.close()
    # 强制 GC，确保 loop.__del__ 在 close 之后调用（不触发 ResourceWarning）
    del loop
    gc.collect()

    vibe_ns = bot.knowledge.vibecraft
    return bot.facade, vibe_ns


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_set_attack_target_override_writes_knowledge():
    """set_attack_target_override 把 point 写到 knowledge.vibecraft.attack_target_override。"""
    facade, vibe_ns = _make_facade_for_test()

    facade.set_attack_target_override((10.0, 20.0))
    assert vibe_ns.attack_target_override == (10.0, 20.0)

    facade.set_attack_target_override(None)
    assert vibe_ns.attack_target_override is None


def test_set_combat_intent_override_writes_knowledge():
    """set_combat_intent_override 把 intent 写到 knowledge.vibecraft.combat_intent_override。"""
    facade, vibe_ns = _make_facade_for_test()

    facade.set_combat_intent_override("attack")
    assert vibe_ns.combat_intent_override == "attack"

    facade.set_combat_intent_override(None)
    assert vibe_ns.combat_intent_override is None


def test_set_engagement_stance_delegates_to_combat_intent():
    """stance → combat_intent_override：defend/hold/retreat 同名，free → None。"""
    facade, vibe_ns = _make_facade_for_test()

    facade.set_engagement_stance("defend")
    assert vibe_ns.combat_intent_override == "defend"

    facade.set_engagement_stance("hold")
    assert vibe_ns.combat_intent_override == "hold"

    facade.set_engagement_stance("retreat")
    assert vibe_ns.combat_intent_override == "retreat"

    facade.set_engagement_stance("free")
    assert vibe_ns.combat_intent_override is None
