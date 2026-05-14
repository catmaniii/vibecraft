"""ares_adapter 单元测试。

**不需要 ares-sc2**：全部用 Mock 模拟 AresBot，验证：
1. `set_build(name)` → `bot.build_order_runner.switch_opening(name)`
2. `on_start` 在调 `super().on_start()` 之前把 voicecraft 剧本注入
   `bot.config["Builds"]`（spike B）
3. `make_bot_class(factory, strategy_library=None)` 向后兼容不注入
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from voicecraft.strategy.library import StrategyLibrary
from voicecraft.strategy.models import OpeningBuild

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
    """无 midgame/lategame cross-ref 的简化 library。"""
    openings = [_make_opening(oid) for oid in opening_ids]
    return StrategyLibrary(openings=openings, midgames=[], lategames=[])


# ---------------------------------------------------------------------------
# make_bot_class + _AresFacade mock 测试
# ---------------------------------------------------------------------------

# ares 和 ares.consts 都不存在于 worktree venv；我们用 fake 模块填上。
_FAKE_ARES_MODULES: dict[str, ModuleType] = {}


def _inject_fake_ares() -> tuple[MagicMock, MagicMock]:
    """
    向 sys.modules 注入伪 ares 模块，返回 (FakeAresBot_class, FakeAresUnitRole_class)。
    调用者必须在测试结束后清理（或使用 conftest fixture）。
    """
    fake_ares_consts = ModuleType("ares.consts")
    FakeUnitRole = MagicMock()
    FakeUnitRole.CONTROL_GROUP_ONE = "CONTROL_GROUP_ONE"
    FakeUnitRole.IDLE = "IDLE"
    FakeUnitRole.ATTACKING = "ATTACKING"
    FakeUnitRole.DEFENDING = "DEFENDING"
    FakeUnitRole.HARASSING = "HARASSING"
    FakeUnitRole.SCOUTING = "SCOUTING"
    fake_ares_consts.UnitRole = FakeUnitRole  # type: ignore[attr-defined]

    class FakeAresBot:
        """ares.AresBot 的极简 stub。"""

        def __init__(self) -> None:
            self.config: dict[str, Any] = {}
            self.build_order_runner = MagicMock()
            self.mediator = MagicMock()
            self.time = 0.0
            self.minerals = 0
            self.vespene = 0
            self.supply_used = 0
            self.supply_cap = 0
            self.townhalls: list[Any] = []
            self.units = MagicMock()

        async def on_start(self) -> None:
            """模拟 super().on_start()：已经构造好了 build_order_runner。"""
            pass  # 在真实 ares 里会构造 BuildOrderRunner；这里已经在 __init__ mock 好了

    fake_ares = ModuleType("ares")
    fake_ares.AresBot = FakeAresBot  # type: ignore[attr-defined]

    sys.modules["ares"] = fake_ares
    sys.modules["ares.consts"] = fake_ares_consts
    # sc2.position 被 _AresFacade.move_camera 用到
    if "sc2" not in sys.modules:
        fake_sc2 = ModuleType("sc2")
        fake_sc2_position = ModuleType("sc2.position")
        fake_sc2_position.Point2 = lambda t: t  # type: ignore[attr-defined]
        sys.modules["sc2"] = fake_sc2
        sys.modules["sc2.position"] = fake_sc2_position

    return FakeAresBot, FakeUnitRole


@pytest.fixture(autouse=True)
def _clean_ares_modules():
    """每个测试前清理 ares 模块缓存，保证测试互相隔离。"""
    for key in list(sys.modules.keys()):
        if key.startswith("ares"):
            del sys.modules[key]
    # 也强制重新导入 ares_adapter，使其内部 lazy import 能重新解析
    if "voicecraft.bot.ares_adapter" in sys.modules:
        del sys.modules["voicecraft.bot.ares_adapter"]
    yield
    for key in list(sys.modules.keys()):
        if key.startswith("ares"):
            del sys.modules[key]
    if "voicecraft.bot.ares_adapter" in sys.modules:
        del sys.modules["voicecraft.bot.ares_adapter"]


# ---------------------------------------------------------------------------
# 测试：set_build → switch_opening
# ---------------------------------------------------------------------------


class TestSetBuildCallsSwitchOpening:
    """_AresFacade.set_build 应转发到 bot.build_order_runner.switch_opening。"""

    def test_set_build_calls_switch_opening(self) -> None:
        FakeAresBot, _ = _inject_fake_ares()
        from voicecraft.bot.ares_adapter import make_bot_class

        calls: list[str] = []
        BotClass = make_bot_class(lambda facade: None)
        bot_instance = BotClass.__new__(BotClass)
        FakeAresBot.__init__(bot_instance)  # 初始化 mock 属性

        # 直接实例化 _AresFacade（通过 make_bot_class 内部的 closure）
        # 用一个简单的方法：创建 bot 类，手动 inject facade 并测试
        bot_mock = FakeAresBot()
        bot_mock.build_order_runner = MagicMock()

        # 通过 make_bot_class 产生的 bot 类，间接访问 _AresFacade
        # 方法：直接测试 set_build 路径
        def _director_factory(facade: Any) -> None:
            calls.append("director_created")
            return

        BotClass2 = make_bot_class(_director_factory)

        # 创建 facade 实例（内部类，需通过类名访问）
        # 用 isinstance 检查绕过：直接用 bot mock 调 run_build_set
        import asyncio

        instance = object.__new__(BotClass2)
        FakeAresBot.__init__(instance)  # type: ignore[arg-type]
        instance.build_order_runner = MagicMock()

        # 手工调 on_start（不会真正调 super；我们 mock 了 super）
        # 用 patch 替换 super 的 on_start
        async def run() -> None:
            with patch.object(FakeAresBot, "on_start", new_callable=AsyncMock):
                await instance.on_start()

        asyncio.run(run())

        # 现在 facade 已创建；通过 facade 调 set_build
        facade = instance.facade
        assert facade is not None
        facade.set_build("test_build")
        instance.build_order_runner.switch_opening.assert_called_once_with("test_build")

    def test_set_build_with_different_name(self) -> None:
        FakeAresBot, _ = _inject_fake_ares()
        import asyncio

        from voicecraft.bot.ares_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None)
        instance = object.__new__(BotClass)
        FakeAresBot.__init__(instance)  # type: ignore[arg-type]
        instance.build_order_runner = MagicMock()

        async def run() -> None:
            with patch.object(FakeAresBot, "on_start", new_callable=AsyncMock):
                await instance.on_start()

        asyncio.run(run())

        instance.facade.set_build("1g_robo_immortal")
        instance.build_order_runner.switch_opening.assert_called_once_with("1g_robo_immortal")


# ---------------------------------------------------------------------------
# 测试：config 注入（spike B）
# ---------------------------------------------------------------------------


class TestConfigInjection:
    """on_start 在 super() 前把 voicecraft openings 注入 bot.config["Builds"]。"""

    def _run_on_start_with_library(self, library: StrategyLibrary | None) -> dict[str, Any]:
        """运行 on_start，返回 on_start 结束时的 bot.config。"""
        FakeAresBot, _ = _inject_fake_ares()
        import asyncio

        from voicecraft.bot.ares_adapter import make_bot_class

        injected_config_at_super: dict[str, Any] = {}

        class CapturingFakeAresBot(FakeAresBot):  # type: ignore[valid-type,misc]
            async def on_start(self) -> None:
                # 在 super().on_start() 被调用时捕获 config 快照
                injected_config_at_super.update(self.config)

        # 替换 FakeAresBot
        import ares as ares_mod

        ares_mod.AresBot = CapturingFakeAresBot  # type: ignore[attr-defined]

        BotClass = make_bot_class(lambda facade: None, strategy_library=library)
        instance = object.__new__(BotClass)
        CapturingFakeAresBot.__init__(instance)  # type: ignore[arg-type]

        asyncio.run(instance.on_start())
        return injected_config_at_super

    def test_library_openings_injected_into_config_builds(self) -> None:
        lib = _make_library("build_alpha", "build_beta")
        config = self._run_on_start_with_library(lib)
        assert "Builds" in config
        assert "build_alpha" in config["Builds"]
        assert "build_beta" in config["Builds"]

    def test_injected_builds_have_opening_build_order(self) -> None:
        lib = _make_library("my_build")
        config = self._run_on_start_with_library(lib)
        entry = config["Builds"]["my_build"]
        assert "OpeningBuildOrder" in entry
        order: list[str] = entry["OpeningBuildOrder"]
        assert "13 PYLON" in order
        assert "14 GATEWAY" in order

    def test_no_library_does_not_inject_builds(self) -> None:
        config = self._run_on_start_with_library(None)
        # strategy_library=None 时不注入，config 里可以没有 Builds
        # （ares 自己会从 config.yml 加载，我们不应强行覆盖）
        # 只验证我们没有写入空 dict
        builds = config.get("Builds")
        assert builds is None or isinstance(builds, dict)
        # 确认没有我们注入的 key（FakeAresBot 初始 config 是空的）
        if builds is not None:
            assert len(builds) == 0

    def test_existing_builds_not_overwritten(self) -> None:
        """如果 config 已有 Builds，我们的 openings update 到现有 dict 上，不替换整个 dict。"""
        FakeAresBot, _ = _inject_fake_ares()
        import asyncio

        from voicecraft.bot.ares_adapter import make_bot_class

        lib = _make_library("new_build")

        BotClass = make_bot_class(lambda facade: None, strategy_library=lib)
        instance = object.__new__(BotClass)
        FakeAresBot.__init__(instance)  # type: ignore[arg-type]
        # 预先写入已有 build
        instance.config["Builds"] = {"existing_build": {"OpeningBuildOrder": ["13 PYLON"]}}

        with patch.object(FakeAresBot, "on_start", new_callable=AsyncMock):
            asyncio.run(instance.on_start())

        # 旧 build 保留，新 build 也在
        assert "existing_build" in instance.config["Builds"]
        assert "new_build" in instance.config["Builds"]
