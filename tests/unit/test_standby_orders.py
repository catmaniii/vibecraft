"""2026-05-24 用户:STANDBY 待命指令单测。

Verb.STANDBY 的 unit_claim persistent=True directive,_tick_standby_orders 每 tick:
- 距 target > _STANDBY_RADIUS → unit.move(target) 拉回
- 范围内有敌方 → unit.attack(nearest) 自动战斗
- 其余 hold(不发新命令)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vibecraft.bot import Director, FakeFacade
from vibecraft.directives.models import Directive, UnitClaimPayload
from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
from vibecraft.directives.task import Action, Task, Verb
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def session() -> GameSession:
    s = GameSession(GameSessionConfig(use_null_sinks=True))
    yield s
    s.close()


@pytest.fixture
def library() -> StrategyLibrary:
    return StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )


def _make_standby_directive(
    unit_type: str = "Zealot", named_spot: str = "enemy_third"
) -> Directive:
    payload = UnitClaimPayload(
        selector=Selector(unit_type=unit_type),
        task=Task(
            primary_action=Action(
                verb=Verb.STANDBY,
                target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot=named_spot),
            )
        ),
        persistent=True,
    )
    return Directive(payload=payload, issued_at=10.0)


def _make_unit(tag: int, pos: tuple[float, float]):
    """假 Unit class(python-sc2 Unit-compatible)。"""

    class Unit:
        pass

    u = Unit()
    u.tag = tag
    u.position = MagicMock(x=pos[0], y=pos[1])
    u.distance_to = MagicMock()
    u.move = MagicMock()
    u.attack = MagicMock()
    return u


def _setup_director_with_bot(session, library, units, enemy_units=None, target_pos=(100, 100)):
    """造 director + mock bot,units 是 standing 的 selector 结果。"""
    facade = FakeFacade()
    facade.selector_stub["Zealot"] = [u.tag for u in units]
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    parser = IntentParser(provider, library, session=session)
    director = Director(facade=facade, parser=parser, session=session, library=library)

    # mock bot
    bot = MagicMock()
    director._bot = bot

    # bot.units.tags_in 返回 units
    units_collection = MagicMock()
    units_collection.tags_in = lambda tags: [u for u in units if u.tag in tags]
    bot.units = units_collection

    # bot.enemy_units.closer_than 返回类 list + closest_to
    class FakeUnits:
        def __init__(self, items):
            self._items = list(items)

        def __bool__(self):
            return bool(self._items)

        def __iter__(self):
            return iter(self._items)

        def __len__(self):
            return len(self._items)

        def closest_to(self, u):
            return self._items[0]

    enemy_collection = MagicMock()
    if enemy_units:
        enemy_collection.closer_than = lambda r, u: FakeUnits(enemy_units)
    else:
        enemy_collection.closer_than = lambda r, u: FakeUnits([])
    bot.enemy_units = enemy_collection

    # mock NamedSpotRegistry.resolve 返回 target_pos
    def _patch_resolve():
        from sc2.position import Point2

        from vibecraft.bot import named_spot

        original = named_spot.NamedSpotRegistry.resolve

        def patched(self_reg, name, bot_):
            return Point2(target_pos)

        named_spot.NamedSpotRegistry.resolve = patched
        return original

    director._original_resolve = _patch_resolve()

    return director, bot


class TestStandbyOrders:
    @pytest.fixture(autouse=True)
    def _restore_named_spot_resolve(self):
        """2026-07-08 修复：`_setup_director_with_bot._patch_resolve` 直接改写
        `NamedSpotRegistry.resolve`**类方法**且从不还原——全量跑 pytest 时这个 patch
        永久生效，导致本文件跑完之后**同进程内所有测试**的 named_spot 解析都被
        锁死成 `Point2(target_pos)`（与 name 参数无关），是隐蔽的跨文件测试污染源
        （被 test_terran_worker_task.py 的 `_resolve_target_area` 断言意外揪出：同一
        subset 换个文件顺序就命中不同受害测试，`git stash` 到改动前也能复现）。
        这里用 autouse fixture 保证测试后必还原，不管 `_patch_resolve` 内部是否忘记调用。
        """
        from vibecraft.bot import named_spot

        original = named_spot.NamedSpotRegistry.resolve
        yield
        named_spot.NamedSpotRegistry.resolve = original

    def test_unit_far_from_target_gets_move(self, library, session) -> None:
        """单位距 target > 10 → 调 unit.move(target) 拉回。"""
        u = _make_unit(tag=100, pos=(50, 50))
        u.distance_to = MagicMock(return_value=70.0)  # 远
        director, _bot = _setup_director_with_bot(session, library, [u], target_pos=(100, 100))

        d = _make_standby_directive()
        director._submit_directives([d], now=10.0)

        director._tick_standby_orders()

        u.move.assert_called_once()
        u.attack.assert_not_called()

    def test_unit_with_enemy_in_range_attacks(self, library, session) -> None:
        """范围内有敌方 → 优先 attack(覆盖距离逻辑)。"""
        u = _make_unit(tag=100, pos=(100, 100))
        u.distance_to = MagicMock(return_value=2.0)  # 已在 target
        enemy = _make_unit(tag=200, pos=(105, 100))
        director, _bot = _setup_director_with_bot(
            session, library, [u], enemy_units=[enemy], target_pos=(100, 100)
        )

        d = _make_standby_directive()
        director._submit_directives([d], now=10.0)

        director._tick_standby_orders()

        u.attack.assert_called_once_with(enemy)
        u.move.assert_not_called()

    def test_unit_in_range_no_enemy_holds(self, library, session) -> None:
        """范围内 + 无敌 → 不发命令(hold)。"""
        u = _make_unit(tag=100, pos=(100, 100))
        u.distance_to = MagicMock(return_value=2.0)
        director, _bot = _setup_director_with_bot(session, library, [u], target_pos=(100, 100))

        d = _make_standby_directive()
        director._submit_directives([d], now=10.0)

        director._tick_standby_orders()

        u.move.assert_not_called()
        u.attack.assert_not_called()
