"""Task E β：代理建造（两卡组合）单测。

覆盖：
- BuildAtPayload.by_probe 字段
- FakeFacade.proxy_build_orders
- Director._is_activation_satisfied unit_arrived（近/远/named_spot）
- Director._apply_to_facade BUILD_AT by_probe=True → order_probe_build
- Director._apply_to_facade BUILD_AT by_probe=False → set_build_location_override（无回归）
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vibecraft.bot import BotState, Director, FakeFacade
from vibecraft.directives.models import BuildAtPayload
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def library() -> StrategyLibrary:
    return StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )


@pytest.fixture
def session() -> GameSession:
    s = GameSession(GameSessionConfig(use_null_sinks=True))
    yield s
    s.close()


def _make_director(
    library: StrategyLibrary,
    session: GameSession,
    facade: FakeFacade,
    provider_response: dict,
    my_race: str = "protoss",
) -> Director:
    provider = MockLLMProvider(
        scripted=[
            ProviderResponse(
                raw=provider_response,
                input_tokens=100,
                output_tokens=20,
                latency_ms=10.0,
            )
        ]
    )
    parser = IntentParser(provider, library, session=session, my_race=my_race)
    return Director(facade=facade, parser=parser, session=session, library=library)


def _make_bare_director(session: GameSession, facade: FakeFacade) -> Director:
    """不需要 LLM 的最简 director，用于直接测 helper 方法。"""
    provider = MockLLMProvider(scripted=[])
    parser = IntentParser(
        provider,
        StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        ),
        session=session,
        my_race="protoss",
    )
    return Director(facade=facade, parser=parser, session=session)


# ===========================================================================
# 1. Schema
# ===========================================================================


def test_buildat_by_probe_field_default_false() -> None:
    """BuildAtPayload.by_probe 默认 False。"""
    p = BuildAtPayload(structure_type="Pylon", point=(1.0, 1.0))
    assert p.by_probe is False


def test_buildat_by_probe_field_true() -> None:
    """BuildAtPayload.by_probe=True 可设置。"""
    p = BuildAtPayload(structure_type="Pylon", point=(50.0, 50.0), by_probe=True)
    assert p.by_probe is True


def test_buildat_by_probe_named_spot() -> None:
    """by_probe 与 named_spot 一起用。"""
    p = BuildAtPayload(
        structure_type="Pylon",
        named_spot="forward",
        by_probe=True,
    )
    assert p.by_probe is True
    assert p.named_spot == "forward"
    assert p.point is None


# ===========================================================================
# 2. FakeFacade.proxy_build_orders
# ===========================================================================


def test_fake_facade_order_probe_build() -> None:
    """FakeFacade.order_probe_build 记录到 proxy_build_orders。"""
    f = FakeFacade()
    f.order_probe_build(probe_tag=42, structure_type="Pylon", point=(5.0, 6.0))
    assert len(f.proxy_build_orders) == 1
    rec = f.proxy_build_orders[0]
    assert rec["probe"] == 42
    assert rec["structure"] == "Pylon"
    assert rec["point"] == (5.0, 6.0)


def test_fake_facade_order_probe_build_recorded_in_calls() -> None:
    """order_probe_build 也进 FakeFacade.calls。"""
    f = FakeFacade()
    f.order_probe_build(probe_tag=99, structure_type="Nexus", point=(100.0, 200.0))
    methods = [c.method for c in f.calls]
    assert "order_probe_build" in methods


# ===========================================================================
# 3. _is_activation_satisfied unit_arrived
# ===========================================================================


def _make_worker_mock(distance: float):
    """产生距离目标 distance 的 mock worker。"""
    w = MagicMock()
    w.distance_to.return_value = distance
    return w


def _attach_bot_with_workers(director: Director, *distances: float) -> None:
    """给 director 挂 mock bot，workers 包含按距离设定的农民列表。"""
    bot = MagicMock()
    bot.workers = [_make_worker_mock(d) for d in distances]
    director._bot = bot


def test_unit_arrived_true_when_worker_near(session: GameSession) -> None:
    """worker 距离 2.0 <= within_grid=5.0 → unit_arrived 满足。"""
    f = FakeFacade(state=BotState(game_time=0.0))
    d = _make_bare_director(session, f)
    _attach_bot_with_workers(d, 2.0)

    cond = {"kind": "unit_arrived", "area": "(50.0, 50.0)", "within_grid": 5.0}
    assert d._is_activation_satisfied(cond) is True


def test_unit_arrived_false_when_far(session: GameSession) -> None:
    """worker 距离 50.0 > within_grid=5.0 → unit_arrived 不满足。"""
    f = FakeFacade(state=BotState(game_time=0.0))
    d = _make_bare_director(session, f)
    _attach_bot_with_workers(d, 50.0)

    cond = {"kind": "unit_arrived", "area": "(50.0, 50.0)", "within_grid": 5.0}
    assert d._is_activation_satisfied(cond) is False


def test_unit_arrived_false_no_workers(session: GameSession) -> None:
    """无 workers → unit_arrived 不满足。"""
    f = FakeFacade(state=BotState(game_time=0.0))
    d = _make_bare_director(session, f)
    bot = MagicMock()
    bot.workers = []
    d._bot = bot

    cond = {"kind": "unit_arrived", "area": "(50.0, 50.0)", "within_grid": 5.0}
    assert d._is_activation_satisfied(cond) is False


def test_unit_arrived_default_within_grid(session: GameSession) -> None:
    """within_grid 缺省 5.0；距离 4.9 应满足。"""
    f = FakeFacade(state=BotState(game_time=0.0))
    d = _make_bare_director(session, f)
    _attach_bot_with_workers(d, 4.9)

    cond = {"kind": "unit_arrived", "area": "(10.0, 20.0)"}
    assert d._is_activation_satisfied(cond) is True


def test_unit_arrived_false_when_bot_none(session: GameSession) -> None:
    """_bot=None → _is_activation_satisfied 返 False（第一个 guard）。"""
    f = FakeFacade(state=BotState(game_time=0.0))
    d = _make_bare_director(session, f)
    d._bot = None

    cond = {"kind": "unit_arrived", "area": "(50.0, 50.0)", "within_grid": 5.0}
    assert d._is_activation_satisfied(cond) is False


# ===========================================================================
# 4. BUILD_AT by_probe 分支 → order_probe_build
# ===========================================================================


def _make_directive_build_at(
    structure_type: str,
    point: tuple[float, float] | None = None,
    named_spot: str | None = None,
    by_probe: bool = False,
):
    """构造一个最简 build_at Directive（不走 LLM）。"""
    from vibecraft.directives.models import BuildAtPayload, Directive
    from vibecraft.directives.types import IssuedBy

    payload = BuildAtPayload(
        structure_type=structure_type,
        point=point,
        named_spot=named_spot,
        by_probe=by_probe,
    )
    return Directive(
        payload=payload,
        issued_at=0.0,
        issued_by=IssuedBy.VOICE,
    )


def test_buildat_by_probe_calls_order_probe_build(session: GameSession) -> None:
    """build_at(by_probe=True) _apply_to_facade → facade.order_probe_build 被调用。"""
    f = FakeFacade(state=BotState(game_time=0.0))
    d = _make_bare_director(session, f)

    # 给 bot 一个 mock worker，距离目标点 2.0（tag=777）
    bot = MagicMock()
    w = MagicMock()
    w.tag = 777
    w.distance_to.return_value = 2.0
    bot.workers = [w]
    d._bot = bot

    directive = _make_directive_build_at(structure_type="Pylon", point=(50.0, 50.0), by_probe=True)
    d._apply_to_facade(directive, now=0.0)

    assert len(f.proxy_build_orders) == 1
    rec = f.proxy_build_orders[0]
    assert rec["probe"] == 777
    assert rec["structure"] == "Pylon"
    # set_build_location_override 不应被调用
    assert len(f.build_location_overrides) == 0


def test_buildat_no_by_probe_calls_set_build_location_override(
    session: GameSession,
) -> None:
    """build_at(by_probe=False,默认) _apply_to_facade → set_build_location_override，无 order_probe_build。"""
    f = FakeFacade(state=BotState(game_time=0.0))
    d = _make_bare_director(session, f)
    d._bot = MagicMock()

    directive = _make_directive_build_at(structure_type="Pylon", point=(30.0, 30.0), by_probe=False)
    d._apply_to_facade(directive, now=0.0)

    assert len(f.build_location_overrides) == 1
    assert f.build_location_overrides[0] == ("Pylon", (30.0, 30.0))
    assert len(f.proxy_build_orders) == 0


def test_buildat_by_probe_no_workers_skips(session: GameSession) -> None:
    """by_probe=True 但无 workers → order_probe_build 不调，也不报错。"""
    f = FakeFacade(state=BotState(game_time=0.0))
    d = _make_bare_director(session, f)
    bot = MagicMock()
    bot.workers = []
    d._bot = bot

    directive = _make_directive_build_at(structure_type="Pylon", point=(50.0, 50.0), by_probe=True)
    d._apply_to_facade(directive, now=0.0)

    assert len(f.proxy_build_orders) == 0
