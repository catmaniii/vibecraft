"""Task C: 镜头"这里"注入单测。

覆盖:
- FakeFacade.get_camera_center() / camera_center_stub
- Director.build_parse_context 把 facade.get_camera_center() 注入 camera_point
- Director._inject_camera_point 把 kind=CAMERA 的 TargetSpec 替换成镜头坐标
- Director._inject_camera_point 把 tactical target_area="camera" 替换成 tuple
- camera_point=None 时 noop（不崩）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.bot import BotState, Director, FakeFacade
from vibecraft.directives.models import (
    Directive,
    MovePayload,
    TacticalObjectivePayload,
    UnitClaimPayload,
)
from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
from vibecraft.directives.task import Action, Task, Verb
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
) -> Director:
    provider = MockLLMProvider(
        scripted=[
            ProviderResponse(
                raw={"interpretation_zh": "ok", "confidence": 0.9, "directives": []},
                input_tokens=100,
                output_tokens=20,
                latency_ms=10.0,
            )
        ]
    )
    parser = IntentParser(provider, library, session=session, my_race="protoss")
    return Director(facade=facade, parser=parser, session=session, library=library)


def _camera_target() -> TargetSpec:
    return TargetSpec(kind=TargetKind.CAMERA)


def _unit_claim_camera() -> Directive:
    return Directive(
        payload=UnitClaimPayload(
            selector=Selector(unit_type="Probe", count=1),
            task=Task(
                primary_action=Action(
                    verb=Verb.HOLD_POSITION,
                    target=_camera_target(),
                )
            ),
        ),
        issued_at=1.0,
    )


# =========================================================================
# FakeFacade.get_camera_center
# =========================================================================


def test_fake_facade_get_camera_center_default_none() -> None:
    f = FakeFacade()
    assert f.get_camera_center() is None


def test_fake_facade_get_camera_center_stub() -> None:
    f = FakeFacade()
    f.camera_center_stub = (12.0, 34.0)
    assert f.get_camera_center() == (12.0, 34.0)


# =========================================================================
# build_parse_context 注入 camera_point
# =========================================================================


def test_build_parse_context_camera_point_none(
    library: StrategyLibrary, session: GameSession
) -> None:
    facade = FakeFacade(state=BotState(game_time=10.0))
    # camera_center_stub 默认 None
    director = _make_director(library, session, facade)
    ctx = director.build_parse_context(now=10.0)
    assert ctx.camera_point is None


def test_build_parse_context_camera_point_injected(
    library: StrategyLibrary, session: GameSession
) -> None:
    facade = FakeFacade(state=BotState(game_time=10.0))
    facade.camera_center_stub = (40.0, 50.0)
    director = _make_director(library, session, facade)
    ctx = director.build_parse_context(now=10.0)
    assert ctx.camera_point == (40.0, 50.0)


# =========================================================================
# _inject_camera_point：unit_claim target
# =========================================================================


def test_inject_camera_into_unit_claim_target(
    library: StrategyLibrary, session: GameSession
) -> None:
    director = _make_director(library, session, FakeFacade())
    d = _unit_claim_camera()
    director._inject_camera_point([d], (40.0, 50.0))
    assert d.payload.task.primary_action.target.point == (40.0, 50.0)


def test_inject_camera_does_not_touch_non_camera_target(
    library: StrategyLibrary, session: GameSession
) -> None:
    director = _make_director(library, session, FakeFacade())
    d = Directive(
        payload=UnitClaimPayload(
            selector=Selector(unit_type="Probe", count=1),
            task=Task(
                primary_action=Action(
                    verb=Verb.HOLD_POSITION,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="natural"),
                )
            ),
        ),
        issued_at=1.0,
    )
    director._inject_camera_point([d], (40.0, 50.0))
    # named_spot target 不受影响
    assert d.payload.task.primary_action.target.named_spot == "natural"
    assert d.payload.task.primary_action.target.point is None


# =========================================================================
# _inject_camera_point：move target
# =========================================================================


def test_inject_camera_into_move_target(library: StrategyLibrary, session: GameSession) -> None:
    director = _make_director(library, session, FakeFacade())
    d = Directive(
        payload=MovePayload(
            selector=Selector(unit_type="Stalker", count=2),
            target=_camera_target(),
        ),
        issued_at=2.0,
    )
    director._inject_camera_point([d], (10.0, 20.0))
    assert d.payload.target.point == (10.0, 20.0)


# =========================================================================
# _inject_camera_point：tactical target_area
# =========================================================================


def test_inject_camera_into_tactical_target_area(
    library: StrategyLibrary, session: GameSession
) -> None:
    director = _make_director(library, session, FakeFacade())
    d = Directive(
        payload=TacticalObjectivePayload(verb="attack", target_area="camera"),
        issued_at=1.0,
    )
    director._inject_camera_point([d], (30.0, 60.0))
    assert d.payload.target_area == (30.0, 60.0)


def test_inject_camera_tactical_non_camera_target_area_unchanged(
    library: StrategyLibrary, session: GameSession
) -> None:
    director = _make_director(library, session, FakeFacade())
    d = Directive(
        payload=TacticalObjectivePayload(verb="attack", target_area="enemy_natural"),
        issued_at=1.0,
    )
    director._inject_camera_point([d], (30.0, 60.0))
    assert d.payload.target_area == "enemy_natural"


# =========================================================================
# _inject_camera_point：camera_point=None → noop
# =========================================================================


def test_inject_camera_none_is_noop(library: StrategyLibrary, session: GameSession) -> None:
    director = _make_director(library, session, FakeFacade())
    d = _unit_claim_camera()
    director._inject_camera_point([d], None)  # 不崩
    assert d.payload.task.primary_action.target.point is None


def test_inject_camera_none_tactical_noop(library: StrategyLibrary, session: GameSession) -> None:
    director = _make_director(library, session, FakeFacade())
    d = Directive(
        payload=TacticalObjectivePayload(verb="attack", target_area="camera"),
        issued_at=1.0,
    )
    director._inject_camera_point([d], None)  # 不崩
    assert d.payload.target_area == "camera"


# =========================================================================
# _inject_camera_point：waypoints 内 CAMERA 也注入
# =========================================================================


def test_inject_camera_into_waypoints(library: StrategyLibrary, session: GameSession) -> None:
    director = _make_director(library, session, FakeFacade())
    wp_camera = _camera_target()
    d = Directive(
        payload=UnitClaimPayload(
            selector=Selector(unit_type="Probe", count=1),
            task=Task(
                primary_action=Action(
                    verb=Verb.PATROL,
                    target=TargetSpec(
                        kind=TargetKind.NAMED_SPOT,
                        named_spot="natural",
                        waypoints=[wp_camera],
                    ),
                )
            ),
        ),
        issued_at=3.0,
    )
    director._inject_camera_point([d], (77.0, 88.0))
    # 主 target 没动（named_spot）
    assert d.payload.task.primary_action.target.named_spot == "natural"
    # waypoint[0] 注入了坐标
    assert d.payload.task.primary_action.target.waypoints[0].point == (77.0, 88.0)
