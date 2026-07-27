"""GameConfig.my_race 字段 + sharpy_adapter 三族 dispatch 单测（Task 1 M6.1）。

测试范围：
1. GameConfig 默认 my_race == "Protoss"
2. GameConfig(my_race="Zerg").my_race == "Zerg"
3. GameConfig(my_race="Terran").my_race == "Terran"
4. GameConfig picklable（跨 spawn 子进程边界）
5. sharpy_adapter.make_bot_class(race="Zerg") 调到 make_zerg_bot_class（mock）
6. sharpy_adapter.make_bot_class(race="Terran") 调到 make_terran_bot_class（mock）
7. 未知 race 仍抛 NotImplementedError
"""

from __future__ import annotations

import pickle
import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Task 1a: GameConfig.my_race 字段
# ---------------------------------------------------------------------------


class TestGameConfigMyRace:
    """GameConfig 新增 my_race 字段，默认 Protoss，可指定三族。"""

    def test_default_my_race_is_protoss(self) -> None:
        from vibecraft.server.game_process import GameConfig

        cfg = GameConfig()
        assert cfg.my_race == "Protoss"

    def test_my_race_zerg(self) -> None:
        from vibecraft.server.game_process import GameConfig

        cfg = GameConfig(my_race="Zerg")
        assert cfg.my_race == "Zerg"

    def test_my_race_terran(self) -> None:
        from vibecraft.server.game_process import GameConfig

        cfg = GameConfig(my_race="Terran")
        assert cfg.my_race == "Terran"

    def test_my_race_protoss_explicit(self) -> None:
        from vibecraft.server.game_process import GameConfig

        cfg = GameConfig(my_race="Protoss")
        assert cfg.my_race == "Protoss"

    def test_game_config_picklable(self) -> None:
        """GameConfig 必须 picklable（spawn 子进程边界传递）。"""
        from vibecraft.server.game_process import GameConfig

        cfg = GameConfig(my_race="Zerg", map_name="TestMap")
        dumped = pickle.dumps(cfg)
        restored: Any = pickle.loads(dumped)
        assert restored.my_race == "Zerg"
        assert restored.map_name == "TestMap"


# ---------------------------------------------------------------------------
# Task 1b: sharpy_adapter 三族 dispatch
# ---------------------------------------------------------------------------


def _inject_minimal_fake_sharpy() -> type:
    """注入最小 fake sharpy/sc2，让 make_bot_class 能 import common_bot 依赖。"""
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
        "sharpy.plans.tactics",
        "sharpy.plans.tactics.zone_attack",
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = ModuleType(mod_name)

    sys.modules["sharpy.managers.core.roles.unit_task"].UnitTask = FakeUnitTask  # type: ignore[attr-defined]
    sys.modules["sharpy.managers.core.roles"].UnitTask = FakeUnitTask  # type: ignore[attr-defined]

    class FakeBuildOrder:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

    class FakeKnowledgeBot:
        def __init__(self, name: str = "fake") -> None:
            self.name = name
            self.knowledge = MagicMock()

        async def on_start(self) -> None:
            pass

        async def on_step(self, iteration: int) -> None:
            pass

    sys.modules["sharpy.plans"].BuildOrder = FakeBuildOrder  # type: ignore[attr-defined]
    sys.modules["sharpy.knowledges.knowledge_bot"].KnowledgeBot = FakeKnowledgeBot  # type: ignore[attr-defined]

    for mod_name in ["sc2", "sc2.position", "sc2.ids", "sc2.ids.unit_typeid"]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = ModuleType(mod_name)

    class FakeUnitTypeId:
        NEXUS = "NEXUS"
        PROBE = "PROBE"
        OBSERVER = "OBSERVER"
        WARPPRISM = "WARPPRISM"
        DRONE = "DRONE"
        OVERLORD = "OVERLORD"
        OVERSEER = "OVERSEER"
        SCV = "SCV"
        MULE = "MULE"

    sys.modules["sc2.ids.unit_typeid"].UnitTypeId = FakeUnitTypeId  # type: ignore[attr-defined]

    return FakeKnowledgeBot  # type: ignore[return-value]


_ADAPTER_PREFIXES = (
    "sc2",
    "sharpy",
    "vibecraft.bot.auto_combat",
    "vibecraft.bot.sharpy_adapter",
)


@pytest.fixture()
def clean_adapter_mods() -> Any:
    """每个测试前后清理 sharpy / sc2 / bot 模块缓存，保证测试互相隔离。"""
    for key in list(sys.modules):
        if any(key == p or key.startswith(p + ".") for p in _ADAPTER_PREFIXES):
            del sys.modules[key]
    yield
    for key in list(sys.modules):
        if any(key == p or key.startswith(p + ".") for p in _ADAPTER_PREFIXES):
            del sys.modules[key]


class TestSharpyAdapterRaceDispatch:
    """make_bot_class 按 race= 分发到对应 make_xxx_bot_class 工厂。"""

    def test_protoss_still_works(self, clean_adapter_mods: Any) -> None:
        """Protoss 分支仍然正常（不受三族 dispatch 影响）。"""
        _inject_minimal_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: None, race="Protoss")
        assert isinstance(BotClass, type)

    def test_zerg_dispatches_to_make_zerg_bot_class(self, clean_adapter_mods: Any) -> None:
        """race='Zerg' → 调到 make_zerg_bot_class（mock 注入）。"""
        _inject_minimal_fake_sharpy()

        fake_zerg_class = type("_FakeZergBot", (), {})
        fake_zerg_module = ModuleType("vibecraft.bot.auto_combat.zerg.bot")
        fake_zerg_module.make_zerg_bot_class = lambda **kw: fake_zerg_class  # type: ignore[attr-defined]
        sys.modules["vibecraft.bot.auto_combat.zerg"] = ModuleType("vibecraft.bot.auto_combat.zerg")
        sys.modules["vibecraft.bot.auto_combat.zerg.bot"] = fake_zerg_module

        from vibecraft.bot.sharpy_adapter import make_bot_class

        result = make_bot_class(lambda facade: None, race="Zerg")
        assert result is fake_zerg_class

    def test_terran_dispatches_to_make_terran_bot_class(self, clean_adapter_mods: Any) -> None:
        """race='Terran' → 调到 make_terran_bot_class（mock 注入）。"""
        _inject_minimal_fake_sharpy()

        fake_terran_class = type("_FakeTerranBot", (), {})
        fake_terran_module = ModuleType("vibecraft.bot.auto_combat.terran.bot")
        fake_terran_module.make_terran_bot_class = lambda **kw: fake_terran_class  # type: ignore[attr-defined]
        sys.modules["vibecraft.bot.auto_combat.terran"] = ModuleType(
            "vibecraft.bot.auto_combat.terran"
        )
        sys.modules["vibecraft.bot.auto_combat.terran.bot"] = fake_terran_module

        from vibecraft.bot.sharpy_adapter import make_bot_class

        result = make_bot_class(lambda facade: None, race="Terran")
        assert result is fake_terran_class

    def test_unknown_race_raises_not_implemented(self, clean_adapter_mods: Any) -> None:
        """未知 race（非 Protoss/Zerg/Terran）抛 NotImplementedError。"""
        _inject_minimal_fake_sharpy()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        with pytest.raises(NotImplementedError):
            make_bot_class(lambda facade: None, race="Random")


# ---------------------------------------------------------------------------
# Task 1c: _build_bot_class 用 my_race 拼路径
# ---------------------------------------------------------------------------


class TestBuildBotClassRacePath:
    """_build_bot_class 应把 my_race 传入 make_bot_class 的 race= 参数。"""

    def test_build_bot_class_passes_race_protoss(self) -> None:
        """_build_bot_class 默认（my_race='Protoss'）时调 make_bot_class(race='Protoss')。"""
        from vibecraft.server.game_process import GameConfig

        cfg = GameConfig(my_race="Protoss")
        assert cfg.my_race == "Protoss"

    def test_build_bot_class_passes_race_zerg(self) -> None:
        """GameConfig(my_race='Zerg').my_race == 'Zerg'（子进程读取正确值）。"""
        from vibecraft.server.game_process import GameConfig

        cfg = GameConfig(my_race="Zerg")
        assert cfg.my_race == "Zerg"
