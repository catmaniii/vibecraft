"""sharpy_adapter 单元测试。

**不需要 python-sc2 / sharpy**：全部用 Mock 模拟 KnowledgeBot，验证：
1. `set_build(name)` → M1：bot.active_recipe = name + log（M3 接 IfElse）
2. `make_bot_class(factory)` 返回可实例化的类（KnowledgeBot 子类）
3. `on_start` 构造 facade / director + 注入 snapshot/event callback
4. `on_step` 消费 down_q（command / view_move / leave）
5. `on_start` 无 strategy_library 也不抛（向后兼容）
6. `set_unit_role` 走 sharpy roles.set_task（找不到 unit 时 warn 不崩）
7. move_camera 暂存模式 + drain_pending_actions 串行发出（ADR 0008）
"""

from __future__ import annotations

import asyncio
import queue
import sys
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibecraft.strategy.library import StrategyLibrary
from vibecraft.strategy.models import OpeningBuild

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _make_opening(opening_id: str = "test_build") -> OpeningBuild:
    return OpeningBuild.model_validate(
        {
            "kind": "opening_build",
            "id": opening_id,
            "display_name_zh": "测试",
            "phases": [{"id": "p1", "display": "P1"}],
            "steps": ["13 build Pylon", "14 build Gateway"],
        }
    )


def _make_library(*opening_ids: str) -> StrategyLibrary:
    openings = [_make_opening(oid) for oid in opening_ids]
    return StrategyLibrary(openings=openings, midgames=[], lategames=[])


# ---------------------------------------------------------------------------
# fake sharpy 注入辅助
# ---------------------------------------------------------------------------


def _inject_fake_sharpy() -> tuple[type, type, type, type]:
    """向 sys.modules 注入伪 sharpy / sc2 模块，返回 (FakeKnowledgeBot, FakeUnitTask, FakeBuildOrder, FakeIfElse)。

    调用者必须在测试结束后清理（autouse fixture 负责）。
    """
    # --- sharpy.managers.core.roles.unit_task ---
    import enum

    class FakeUnitTask(enum.IntEnum):
        Idle = 0
        Building = 1
        Gathering = 2
        Scouting = 3
        Moving = 4
        Fighting = 5
        Defending = 6
        Attacking = 7
        Reserved = 8
        Hallucination = 9

    fake_unit_task_mod = ModuleType("sharpy.managers.core.roles.unit_task")
    fake_unit_task_mod.UnitTask = FakeUnitTask  # type: ignore[attr-defined]

    # --- sharpy.managers.core.roles ---
    fake_roles_mod = ModuleType("sharpy.managers.core.roles")
    fake_roles_mod.UnitTask = FakeUnitTask  # type: ignore[attr-defined]

    # --- sharpy.managers.core ---
    fake_managers_core_mod = ModuleType("sharpy.managers.core")

    # --- sharpy.managers.extensions ---
    fake_managers_ext_mod = ModuleType("sharpy.managers.extensions")

    # --- sharpy.plans ---
    class FakeBuildOrder:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args

    class FakeIfElse:
        """最小 IfElse stub：记录 condition / action / action_else，
        execute() 调 condition（传 None 作 ai 替代）选分支。"""

        def __init__(
            self,
            condition: Any,
            action: Any,
            action_else: Any = None,
            **_: Any,
        ) -> None:
            self.condition = condition
            self.action = action
            self.action_else = action_else

        def check(self, ai: Any = None) -> bool:
            # condition 是 callable（lambda）或有 check() 的 RequireBase
            if callable(self.condition):
                return bool(self.condition(ai))
            return bool(self.condition.check())

        async def execute(self, ai: Any = None) -> bool:
            if self.check(ai):
                return True
            if self.action_else is not None and isinstance(self.action_else, FakeIfElse):
                return await self.action_else.execute(ai)
            return True

    fake_plans_mod = ModuleType("sharpy.plans")
    fake_plans_mod.BuildOrder = FakeBuildOrder  # type: ignore[attr-defined]
    fake_plans_mod.IfElse = FakeIfElse  # type: ignore[attr-defined]

    fake_if_else_mod = ModuleType("sharpy.plans.if_else")
    fake_if_else_mod.IfElse = FakeIfElse  # type: ignore[attr-defined]

    # --- sharpy.knowledges ---
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
        """sharpy KnowledgeBot 的极简 stub。"""

        def __init__(self, name: str = "fake") -> None:
            self.name = name
            self.knowledge = FakeKnowledge()
            self.time = 0.0
            self.minerals = 0
            self.vespene = 0
            self.supply_used = 0
            self.supply_cap = 0
            self.townhalls: list[Any] = []
            self.units = MagicMock()
            self.enemy_units: list[Any] = []
            self.client = MagicMock()
            self.client.move_camera = AsyncMock()
            self.state = MagicMock()
            self.last_game_loop = -1
            self.realtime = False
            self.active_recipe = ""

        async def on_start(self) -> None:
            """模拟 KnowledgeBot.on_start（已完成 Manager 初始化）。"""
            pass

        async def on_step(self, iteration: int) -> None:
            """模拟 KnowledgeBot.on_step。"""
            pass

        async def on_unit_destroyed(self, unit_tag: int) -> None:
            await self.knowledge.on_unit_destroyed(unit_tag)

        async def on_end(self, result: Any) -> None:
            await self.knowledge.on_end(result)

    fake_knowledges_mod = ModuleType("sharpy.knowledges")
    fake_knowledges_mod.KnowledgeBot = FakeKnowledgeBot  # type: ignore[attr-defined]
    fake_knowledges_mod.BuildOrder = FakeBuildOrder  # type: ignore[attr-defined]

    fake_kb_mod = ModuleType("sharpy.knowledges.knowledge_bot")
    fake_kb_mod.KnowledgeBot = FakeKnowledgeBot  # type: ignore[attr-defined]

    # --- sharpy top-level ---
    fake_sharpy_mod = ModuleType("sharpy")

    # --- sc2 modules ---
    if "sc2" not in sys.modules:
        fake_sc2 = ModuleType("sc2")
        fake_sc2_position = ModuleType("sc2.position")
        fake_sc2_position.Point2 = lambda t: t  # type: ignore[attr-defined]
        fake_sc2_ids = ModuleType("sc2.ids")
        fake_sc2_unit_typeid = ModuleType("sc2.ids.unit_typeid")
        fake_sc2_unit_typeid.UnitTypeId = MagicMock()  # type: ignore[attr-defined]
        sys.modules["sc2"] = fake_sc2
        sys.modules["sc2.position"] = fake_sc2_position
        sys.modules["sc2.ids"] = fake_sc2_ids
        sys.modules["sc2.ids.unit_typeid"] = fake_sc2_unit_typeid

    sys.modules["sharpy"] = fake_sharpy_mod
    sys.modules["sharpy.knowledges"] = fake_knowledges_mod
    sys.modules["sharpy.knowledges.knowledge_bot"] = fake_kb_mod
    sys.modules["sharpy.plans"] = fake_plans_mod
    sys.modules["sharpy.plans.if_else"] = fake_if_else_mod
    sys.modules["sharpy.managers"] = ModuleType("sharpy.managers")
    sys.modules["sharpy.managers.core"] = fake_managers_core_mod
    sys.modules["sharpy.managers.core.roles"] = fake_roles_mod
    sys.modules["sharpy.managers.core.roles.unit_task"] = fake_unit_task_mod
    sys.modules["sharpy.managers.extensions"] = fake_managers_ext_mod

    return FakeKnowledgeBot, FakeUnitTask, FakeBuildOrder, FakeIfElse


@pytest.fixture(autouse=True)
def _clean_sharpy_modules() -> Any:
    """每个测试前后清理 sharpy / sc2 模块缓存，保证测试互相隔离。"""
    _prefixes = ("sc2", "sharpy", "vibecraft.bot.sharpy_adapter", "vibecraft.bot.auto_combat")
    for key in list(sys.modules.keys()):
        if any(key == p or key.startswith(p + ".") for p in _prefixes):
            del sys.modules[key]
    yield
    for key in list(sys.modules.keys()):
        if any(key == p or key.startswith(p + ".") for p in _prefixes):
            del sys.modules[key]


# ---------------------------------------------------------------------------
# 测试：make_bot_class 返回 KnowledgeBot 子类
# ---------------------------------------------------------------------------


class TestMakeBotClass:
    """make_bot_class 返回的类可以实例化，且继承 KnowledgeBot。"""

    def test_returns_class(self) -> None:
        _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        assert isinstance(BotClass, type)

    def test_class_is_knowledgebot_subclass(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        assert issubclass(BotClass, FakeKnowledgeBot)

    def test_unsupported_race_raises(self) -> None:
        _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        with pytest.raises(NotImplementedError):
            make_bot_class(lambda facade: None, race="Zerg")


# ---------------------------------------------------------------------------
# 测试：set_build → active_recipe（M1 占位）
# ---------------------------------------------------------------------------


class TestSetBuild:
    """_SharpyFacade.set_build M1 占位：写 bot.active_recipe，不抛。"""

    def test_set_build_updates_active_recipe(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        async def run() -> None:
            with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
                await instance.on_start()

        asyncio.run(run())

        facade = instance.facade
        assert facade is not None
        facade.set_build("1g_robo_immortal")
        assert instance.active_recipe == "1g_robo_immortal"

    def test_set_build_different_names(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        async def run() -> None:
            with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
                await instance.on_start()

        asyncio.run(run())

        instance.facade.set_build("iac_midgame")
        assert instance.active_recipe == "iac_midgame"

        instance.facade.set_build("skytoss")
        assert instance.active_recipe == "skytoss"


# ---------------------------------------------------------------------------
# 测试：on_start 构造 facade / director + callbacks
# ---------------------------------------------------------------------------


class TestOnStart:
    """on_start 正确构造 facade / director，注入 callbacks。"""

    async def test_facade_created_after_on_start(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        assert instance.facade is not None

    async def test_director_created_after_on_start(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        director_mock = MagicMock()
        BotClass = make_bot_class(lambda facade: director_mock)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        assert instance.director is director_mock

    async def test_snapshot_callback_injected(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        snap_calls: list[Any] = []
        director_mock = MagicMock()
        director_mock.set_snapshot_callback = lambda cb: snap_calls.append(cb)

        BotClass = make_bot_class(
            lambda facade: director_mock,
            snapshot_callback=lambda d: None,
        )
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        assert len(snap_calls) == 1

    async def test_status_callback_on_start(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        calls: list[tuple[str, str, str]] = []

        def status_cb(sc2: str, bot: str, detail: str = "") -> None:
            calls.append((sc2, bot, detail))

        BotClass = make_bot_class(
            lambda facade: MagicMock(),
            status_callback=status_cb,
        )
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        sc2_states = [c[0] for c in calls]
        assert "in_game" in sc2_states
        assert "playing" in sc2_states

    async def test_no_strategy_library_is_fine(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: MagicMock())
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        # 不应抛异常
        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

    async def test_strategy_library_sets_initial_recipe(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        lib = _make_library("build_alpha", "build_beta")
        director_mock = MagicMock()

        BotClass = make_bot_class(
            lambda facade: director_mock,
            strategy_library=lib,
        )
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        # bot 从所有 opening 中 random.choice 选一个(不绑定具体值,避免 random seed 依赖)
        assert instance.active_recipe in {"build_alpha", "build_beta"}
        # director.set_initial_strategy 被调
        director_mock.set_initial_strategy.assert_called_once()
        # 第二参数(strategy_id)也应是 random 选中的那个 id
        called_id = director_mock.set_initial_strategy.call_args.args[1]
        assert called_id == instance.active_recipe


# ---------------------------------------------------------------------------
# 测试：on_step 消费 down_q
# ---------------------------------------------------------------------------


class TestOnStep:
    """on_step 消费下行队列（command / view_move / leave）。"""

    async def test_on_step_creates_task_for_command(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        parse_calls: list[tuple[str, float]] = []

        async def fake_on_player_command(text: str, now: float) -> Any:
            parse_calls.append((text, now))
            from vibecraft.llm.schema import ParseError, ParseErrorKind

            return ParseError(kind=ParseErrorKind.PROVIDER_ERROR, message="test")

        director_mock = MagicMock()
        director_mock.on_player_command = fake_on_player_command
        director_mock.on_tick = MagicMock()

        down_q: queue.Queue[dict[str, Any]] = queue.Queue()
        down_q.put_nowait({"type": "command", "text": "切1门Robo", "issued_at": 42.0})

        BotClass = make_bot_class(
            director_factory=lambda facade: director_mock,
            down_q=down_q,
        )
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []
        instance.director = director_mock
        instance.facade = MagicMock()
        instance.facade.drain_pending_actions = AsyncMock(return_value=None)
        instance.time = 17.5

        with patch.object(FakeKnowledgeBot, "on_step", new_callable=AsyncMock):
            await instance.on_step(0)

        if instance._cmd_tasks:
            await asyncio.gather(*instance._cmd_tasks, return_exceptions=True)

        assert len(parse_calls) == 1
        assert parse_calls[0][0] == "切1门Robo"
        # 用 game_time（self.time=17.5），不是 issued_at
        assert parse_calls[0][1] == 17.5

    async def test_on_step_view_move_calls_facade_move_camera(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        director_mock = MagicMock()
        director_mock.on_tick = MagicMock()

        down_q: queue.Queue[dict[str, Any]] = queue.Queue()
        down_q.put_nowait({"type": "view_move", "target_point": [64.0, 32.0]})

        BotClass = make_bot_class(
            director_factory=lambda facade: director_mock,
            down_q=down_q,
        )
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []
        instance.director = director_mock
        facade_mock = MagicMock()
        facade_mock.drain_pending_actions = AsyncMock(return_value=None)
        instance.facade = facade_mock

        with patch.object(FakeKnowledgeBot, "on_step", new_callable=AsyncMock):
            await instance.on_step(0)

        facade_mock.move_camera.assert_called_once_with((64.0, 32.0))

    async def test_director_tick_called_each_step(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        director_mock = MagicMock()
        director_mock.on_tick = MagicMock()

        BotClass = make_bot_class(
            director_factory=lambda facade: director_mock,
        )
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []
        instance.director = director_mock
        instance.facade = MagicMock()
        instance.facade.drain_pending_actions = AsyncMock(return_value=None)

        with patch.object(FakeKnowledgeBot, "on_step", new_callable=AsyncMock):
            await instance.on_step(0)

        director_mock.on_tick.assert_called_once()


# ---------------------------------------------------------------------------
# 测试：move_camera 暂存模式（ADR 0008）
# ---------------------------------------------------------------------------


class TestMoveCameraStaged:
    """_SharpyFacade.move_camera 暂存模式 + drain_pending_actions。"""

    def test_move_camera_stores_pending_point(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        async def run() -> None:
            with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
                await instance.on_start()

        asyncio.run(run())

        instance.facade.move_camera((48.0, 72.0))
        assert instance.facade._pending_camera_point == (48.0, 72.0)

    def test_multiple_move_camera_collapses_to_latest(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        async def run() -> None:
            with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
                await instance.on_start()

        asyncio.run(run())

        instance.facade.move_camera((10.0, 20.0))
        instance.facade.move_camera((30.0, 40.0))
        instance.facade.move_camera((50.0, 60.0))
        assert instance.facade._pending_camera_point == (50.0, 60.0)

    async def test_drain_pending_actions_calls_client_move_camera(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        instance.facade.move_camera((88.0, 100.0))
        await instance.facade.drain_pending_actions()

        # client.move_camera 被调了一次
        instance.client.move_camera.assert_called_once()
        # pending_camera_point 被清空
        assert instance.facade._pending_camera_point is None

    async def test_drain_no_pending_is_noop(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        # 没有 move_camera 调用
        await instance.facade.drain_pending_actions()

        # client.move_camera 未被调
        instance.client.move_camera.assert_not_called()


# ---------------------------------------------------------------------------
# 测试：set_unit_role 调 sharpy roles.set_task
# ---------------------------------------------------------------------------


class TestSetUnitRole:
    """_SharpyFacade.set_unit_role 调 sharpy UnitRoleManager.set_task。"""

    async def test_set_unit_role_calls_set_task(self) -> None:
        FakeKnowledgeBot, FakeUnitTask, *_ = _inject_fake_sharpy()
        from vibecraft.bot.facade import UnitRole
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]
        instance._llm_controlled_tags = set()

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        fake_unit = MagicMock()
        fake_unit.tag = 42
        instance.knowledge.unit_cache.by_tag.return_value = fake_unit

        instance.facade.set_unit_role(42, UnitRole.LLM_CONTROLLED)

        # set_task(Reserved, unit) 被调
        instance.knowledge.roles.set_task.assert_called_once_with(FakeUnitTask.Reserved, fake_unit)

    async def test_set_unit_role_warns_if_unit_not_found(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.facade import UnitRole
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        # unit_cache 找不到单位
        instance.knowledge.unit_cache.by_tag.return_value = None

        # 不应抛，只 warn
        instance.facade.set_unit_role(999, UnitRole.ARMY)
        # roles.set_task 没被调
        instance.knowledge.roles.set_task.assert_not_called()


# ---------------------------------------------------------------------------
# 测试：on_end 等待 in-flight tasks + 推 status_callback
# ---------------------------------------------------------------------------


class TestOnEnd:
    """on_end 等待 cmd tasks + 推 ended 状态。"""

    async def test_on_end_calls_status_callback(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        calls: list[str] = []

        BotClass = make_bot_class(
            lambda facade: MagicMock(),
            status_callback=lambda sc2, bot, detail="": calls.append(sc2),
        )
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []

        await instance.on_end("Defeat")
        assert "ended" in calls

    async def test_on_end_awaits_cmd_tasks(self) -> None:
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        task_finished = asyncio.Event()
        status_calls: list[str] = []

        async def slow_task() -> None:
            await asyncio.sleep(0.02)
            task_finished.set()

        BotClass = make_bot_class(
            lambda facade: MagicMock(),
            status_callback=lambda sc2, bot, detail="": status_calls.append(sc2),
        )
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []

        t = asyncio.create_task(slow_task())
        instance._cmd_tasks.append(t)

        await instance.on_end("Victory")

        assert task_finished.is_set()
        assert "ended" in status_calls


# ---------------------------------------------------------------------------
# 测试：build_role_map 返回正确 UnitTask 映射
# ---------------------------------------------------------------------------


class TestBuildRoleMap:
    """auto_combat.common.build_role_map 返回 sharpy UnitTask 映射。"""

    def test_role_map_contains_all_vibecraft_roles(self) -> None:
        _inject_fake_sharpy()
        # 注意：build_role_map 依赖 _ensure_sharpy_on_path，需要先注入

        # 覆盖注入，让 sharpy 模块从 sys.modules 获取
        with patch(
            "vibecraft.bot.auto_combat.protoss.bot._ensure_sharpy_on_path",
            return_value=None,
        ):
            from vibecraft.bot.auto_combat.common import build_role_map
            from vibecraft.bot.facade import UnitRole

            role_map = build_role_map()

        for role in UnitRole:
            assert role in role_map

    def test_llm_controlled_maps_to_reserved(self) -> None:
        _, FakeUnitTask, *_ = _inject_fake_sharpy()
        from vibecraft.bot.facade import UnitRole

        with patch(
            "vibecraft.bot.auto_combat.protoss.bot._ensure_sharpy_on_path",
            return_value=None,
        ):
            from vibecraft.bot.auto_combat.common import build_role_map

            role_map = build_role_map()

        assert role_map[UnitRole.LLM_CONTROLLED] == FakeUnitTask.Reserved


# ---------------------------------------------------------------------------
# 测试：create_plan() IfElse 路由树（M2+M3）
# ---------------------------------------------------------------------------


def _make_opening_with_dummy(opening_id: str, dummy_spec: str) -> Any:
    """构造带 sharpy_dummy_class 字段的 OpeningBuild。"""
    from vibecraft.strategy.models import OpeningBuild

    return OpeningBuild.model_validate(
        {
            "kind": "opening_build",
            "id": opening_id,
            "display_name_zh": f"测试 {opening_id}",
            "phases": [{"id": "p1", "display": "P1"}],
            "steps": ["13 build Pylon", "14 build Gateway"],
            "sharpy_dummy_class": dummy_spec,
            "default_transitions": [],
        }
    )


def _make_library_with_dummies(*specs: tuple[str, str]) -> Any:
    """构造包含 sharpy_dummy_class 的 StrategyLibrary。

    specs: list of (opening_id, dummy_spec)
    """
    from vibecraft.strategy.library import StrategyLibrary

    openings = [_make_opening_with_dummy(oid, spec) for oid, spec in specs]
    return StrategyLibrary(openings=openings, midgames=[], lategames=[])


class TestCreatePlan:
    """create_plan() 构建 IfElse 路由树；set_build → active_recipe 让条件切换。"""

    async def test_create_plan_returns_build_order_with_dummies(self) -> None:
        """create_plan() 成功 import dummy + 返回包含 IfElse 的 BuildOrder。"""
        FakeKnowledgeBot, *_, FakeBuildOrder, _FakeIfElse = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        # 构造 fake dummy module
        fake_dummy_plan = FakeBuildOrder([])

        class FakeDummy:
            def create_plan(self) -> Any:
                return fake_dummy_plan

        fake_dummy_mod = ModuleType("dummies.protoss.fake_a")
        fake_dummy_mod.FakeDummyA = FakeDummy  # type: ignore[attr-defined]
        sys.modules["dummies"] = ModuleType("dummies")
        sys.modules["dummies.protoss"] = ModuleType("dummies.protoss")
        sys.modules["dummies.protoss.fake_a"] = fake_dummy_mod

        lib = _make_library_with_dummies(("recipe_a", "dummies.protoss.fake_a:FakeDummyA"))

        BotClass = make_bot_class(
            lambda facade: None,
            strategy_library=lib,
        )
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        # create_plan 返回 BuildOrder（包含 IfElse 或单 plan）
        plan = await instance.create_plan()
        assert isinstance(plan, FakeBuildOrder)

    async def test_create_plan_builds_ifl_else_for_multiple_recipes(self) -> None:
        """多个 dummy → IfElse 嵌套结构（BuildOrder args 非空）。"""
        FakeKnowledgeBot, *_, FakeBuildOrder, FakeIfElse = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        class FakeDummyA:
            def create_plan(self) -> Any:
                return FakeBuildOrder(["plan_a"])

        class FakeDummyB:
            def create_plan(self) -> Any:
                return FakeBuildOrder(["plan_b"])

        for mod_name, cls_name, cls in [
            ("dummies.protoss.fake_aa", "FakeDummyA", FakeDummyA),
            ("dummies.protoss.fake_bb", "FakeDummyB", FakeDummyB),
        ]:
            m = ModuleType(mod_name)
            setattr(m, cls_name, cls)
            sys.modules["dummies"] = sys.modules.get("dummies", ModuleType("dummies"))
            sys.modules["dummies.protoss"] = sys.modules.get(
                "dummies.protoss", ModuleType("dummies.protoss")
            )
            sys.modules[mod_name] = m

        lib = _make_library_with_dummies(
            ("recipe_a", "dummies.protoss.fake_aa:FakeDummyA"),
            ("recipe_b", "dummies.protoss.fake_bb:FakeDummyB"),
        )

        BotClass = make_bot_class(
            lambda facade: None,
            strategy_library=lib,
        )
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        plan = await instance.create_plan()
        # BuildOrder 包含 IfElse（args[0] 是 FakeIfElse）
        assert isinstance(plan, FakeBuildOrder)
        assert len(plan.args) > 0
        inner = plan.args[0]  # IfElse(cond_a, plan_a, plan_b)
        # 两个 recipe → 有 IfElse（嵌套）或直接是 FakeBuildOrder（单 recipe 时）
        assert isinstance(inner, (FakeIfElse, FakeBuildOrder)), (
            f"expected IfElse or BuildOrder, got {type(inner)}: {inner}"
        )

    async def test_set_build_then_lambda_condition_matches(self) -> None:
        """set_build("recipe_b") 后，active_recipe == "recipe_b" → IfElse condition 为 True。"""
        FakeKnowledgeBot, *_, FakeBuildOrder, FakeIfElse = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        class FakeDummyA:
            def create_plan(self) -> Any:
                return FakeBuildOrder(["plan_a"])

        class FakeDummyB:
            def create_plan(self) -> Any:
                return FakeBuildOrder(["plan_b"])

        for mod_name, cls_name, cls in [
            ("dummies.protoss.cond_a", "FakeDummyA", FakeDummyA),
            ("dummies.protoss.cond_b", "FakeDummyB", FakeDummyB),
        ]:
            m = ModuleType(mod_name)
            setattr(m, cls_name, cls)
            sys.modules["dummies"] = sys.modules.get("dummies", ModuleType("dummies"))
            sys.modules["dummies.protoss"] = sys.modules.get(
                "dummies.protoss", ModuleType("dummies.protoss")
            )
            sys.modules[mod_name] = m

        lib = _make_library_with_dummies(
            ("recipe_a", "dummies.protoss.cond_a:FakeDummyA"),
            ("recipe_b", "dummies.protoss.cond_b:FakeDummyB"),
        )

        BotClass = make_bot_class(
            lambda facade: None,
            strategy_library=lib,
        )
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        plan = await instance.create_plan()
        inner = plan.args[0]  # top-level IfElse

        # on_start 用 random.choice 从 [recipe_a, recipe_b] 选一个 →
        # 顶层 IfElse 的 condition 是 lambda: active_recipe == "recipe_a"
        # (candidates 顺序;最外层是第一个 candidate)
        assert isinstance(inner, FakeIfElse)
        # 不绑定具体值,只校验 lambda 评估结果与 active_recipe 一致
        expected_initial = instance.active_recipe == "recipe_a"
        assert inner.check(None) is expected_initial

        # set_build 切到 recipe_b
        instance.facade.set_build("recipe_b")
        assert instance.active_recipe == "recipe_b"
        # 切到 recipe_b 后,top IfElse condition 必定 false(它判 recipe_a)
        assert inner.check(None) is False

    async def test_set_build_invalid_still_works(self) -> None:
        """set_build("nonexistent") 不崩，active_recipe 更新；IfElse 条件全 False 走 else 兜底。"""
        FakeKnowledgeBot, *_, FakeBuildOrder, _FakeIfElse = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        class FakeDummyA:
            def create_plan(self) -> Any:
                return FakeBuildOrder(["plan_a"])

        m = ModuleType("dummies.protoss.inv_a")
        m.FakeDummyA = FakeDummyA  # type: ignore[attr-defined]
        sys.modules["dummies"] = sys.modules.get("dummies", ModuleType("dummies"))
        sys.modules["dummies.protoss"] = sys.modules.get(
            "dummies.protoss", ModuleType("dummies.protoss")
        )
        sys.modules["dummies.protoss.inv_a"] = m

        lib = _make_library_with_dummies(("recipe_a", "dummies.protoss.inv_a:FakeDummyA"))

        BotClass = make_bot_class(
            lambda facade: None,
            strategy_library=lib,
        )
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        # set_build 不存在的 id → active_recipe 更新，不抛
        instance.facade.set_build("nonexistent_id")
        assert instance.active_recipe == "nonexistent_id"

    async def test_create_plan_fallback_on_import_error(self) -> None:
        """dummy import 失败 → fallback BuildOrder，不抛，整体 create_plan 仍返回 BuildOrder。"""
        FakeKnowledgeBot, *_, FakeBuildOrder, _ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        # "dummies.protoss.bad_module" 不在 sys.modules → importlib.import_module 失败
        # （确保不意外在 sys.modules 里）
        sys.modules.pop("dummies.protoss.bad_module", None)

        lib = _make_library_with_dummies(
            ("recipe_bad", "dummies.protoss.bad_module:NonExistentClass")
        )

        BotClass = make_bot_class(
            lambda facade: None,
            strategy_library=lib,
        )
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        # 即使 dummy import 失败，create_plan 仍返回 BuildOrder（fallback）
        plan = await instance.create_plan()
        assert isinstance(plan, FakeBuildOrder)

    async def test_create_plan_no_strategy_library_returns_empty(self) -> None:
        """无 strategy_library 时 create_plan 返回空 BuildOrder。"""
        FakeKnowledgeBot, *_, FakeBuildOrder, _ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)  # no strategy_library
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        plan = await instance.create_plan()
        assert isinstance(plan, FakeBuildOrder)

    def test_default_active_recipe_is_4bg(self) -> None:
        """_VibeCraftProtossBot.__init__ 设 active_recipe = '4bg'(用户指定默认开局)。"""
        _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        # 直接访问类属性（类级别默认值）
        assert BotClass.active_recipe == "4bg"


# ---------------------------------------------------------------------------
# 测试：M4 LLM_CONTROLLED role 隔离 — _llm_controlled_tags / is_vibecraft_controlled
# ---------------------------------------------------------------------------


class TestLLMControlledTags:
    """M4: set_unit_role(LLM_CONTROLLED) → _llm_controlled_tags 管理。"""

    async def test_set_unit_role_llm_controlled_adds_to_tags(self) -> None:
        """set_unit_role(tag, LLM_CONTROLLED) 后 _llm_controlled_tags 含该 tag。"""
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.facade import UnitRole
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]
        instance._llm_controlled_tags = set()

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        fake_unit = MagicMock()
        fake_unit.tag = 101
        instance.knowledge.unit_cache.by_tag.return_value = fake_unit

        instance.facade.set_unit_role(101, UnitRole.LLM_CONTROLLED)

        assert 101 in instance._llm_controlled_tags

    async def test_set_unit_role_non_llm_does_not_add_to_tags(self) -> None:
        """set_unit_role(tag, ARMY) 不写入 _llm_controlled_tags。"""
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.facade import UnitRole
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]
        instance._llm_controlled_tags = set()

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        fake_unit = MagicMock()
        fake_unit.tag = 202
        instance.knowledge.unit_cache.by_tag.return_value = fake_unit

        instance.facade.set_unit_role(202, UnitRole.ARMY)

        assert 202 not in instance._llm_controlled_tags

    async def test_set_unit_role_reassign_removes_from_llm_tags(self) -> None:
        """LLM_CONTROLLED 单位 release 时（设 ARMY）从 _llm_controlled_tags 移除。"""
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.facade import UnitRole
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]
        instance._llm_controlled_tags = {303}

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        fake_unit = MagicMock()
        fake_unit.tag = 303
        instance.knowledge.unit_cache.by_tag.return_value = fake_unit

        instance.facade.set_unit_role(303, UnitRole.ARMY)

        assert 303 not in instance._llm_controlled_tags

    async def test_is_vibecraft_controlled_true_for_claimed_unit(self) -> None:
        """is_vibecraft_controlled(unit) → True 当 tag 在 _llm_controlled_tags。"""
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]
        instance._llm_controlled_tags = {404}

        unit_mock = MagicMock()
        unit_mock.tag = 404

        assert instance.is_vibecraft_controlled(unit_mock) is True

    async def test_is_vibecraft_controlled_false_for_unclaimed_unit(self) -> None:
        """is_vibecraft_controlled(unit) → False 当 tag 不在 _llm_controlled_tags。"""
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]
        instance._llm_controlled_tags = set()

        unit_mock = MagicMock()
        unit_mock.tag = 505

        assert instance.is_vibecraft_controlled(unit_mock) is False

    async def test_refresh_llm_controlled_roles_calls_set_task_each_step(self) -> None:
        """_refresh_llm_controlled_roles() 对 _llm_controlled_tags 里的单位重新声明 Reserved。"""
        FakeKnowledgeBot, FakeUnitTask, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]
        instance._llm_controlled_tags = {601}

        fake_unit = MagicMock()
        instance.knowledge.unit_cache.by_tag.return_value = fake_unit

        # 直接调 _refresh，不走 on_step
        instance._refresh_llm_controlled_roles()

        instance.knowledge.roles.set_task.assert_called_once_with(FakeUnitTask.Reserved, fake_unit)

    async def test_refresh_llm_controlled_roles_removes_dead_units(self) -> None:
        """unit_cache.by_tag 返回 None（单位已死）→ tag 从 _llm_controlled_tags 移除。"""
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]
        instance._llm_controlled_tags = {702}

        # 单位不在 cache（已死）
        instance.knowledge.unit_cache.by_tag.return_value = None

        instance._refresh_llm_controlled_roles()

        assert 702 not in instance._llm_controlled_tags

    async def test_on_unit_destroyed_removes_from_llm_controlled_tags(self) -> None:
        """on_unit_destroyed(tag) → tag 从 _llm_controlled_tags 移除。"""
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]
        instance._llm_controlled_tags = {801, 802}

        await instance.on_unit_destroyed(801)

        assert 801 not in instance._llm_controlled_tags
        assert 802 in instance._llm_controlled_tags  # 未受影响

    async def test_on_unit_destroyed_non_llm_tag_is_noop(self) -> None:
        """on_unit_destroyed(tag) 当 tag 不在 _llm_controlled_tags 时不崩。"""
        FakeKnowledgeBot, *_ = _inject_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]
        instance._llm_controlled_tags = set()

        # tag 不在集合 → 不崩
        await instance.on_unit_destroyed(999)

        assert instance._llm_controlled_tags == set()
