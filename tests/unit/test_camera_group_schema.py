import pytest

from vibecraft.directives.models import (
    Directive,
    DirectiveType,
    GroupAssignPayload,
    GroupClearPayload,
)
from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
from vibecraft.directives.types import StageKind
from vibecraft.llm.prompt import ParseContext


def test_target_kind_camera_exists():
    t = TargetSpec(kind=TargetKind.CAMERA)
    assert t.kind.value == "camera"


def test_selector_group_id_valid():
    s = Selector(group_id=1)
    assert s.group_id == 1


def test_selector_group_id_out_of_range_rejected():
    with pytest.raises(Exception):
        Selector(group_id=6)
    with pytest.raises(Exception):
        Selector(group_id=0)


def test_group_assign_payload_roundtrip():
    p = GroupAssignPayload(group_id=2, selector=Selector(unit_type="WarpPrism"))
    d = Directive(payload=p, issued_at=1.0)
    assert d.type == DirectiveType.GROUP_ASSIGN
    assert d.payload.group_id == 2


def test_group_clear_payload():
    p = GroupClearPayload(group_id=3)
    d = Directive(payload=p, issued_at=1.0)
    assert d.type == DirectiveType.GROUP_CLEAR
    assert d.payload.group_id == 3


def test_target_waypoints():
    t = TargetSpec(
        kind=TargetKind.NAMED_SPOT,
        waypoints=[
            TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_clock_11"),
            TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_third"),
        ],
    )
    assert t.waypoints is not None and len(t.waypoints) == 2


def test_parse_context_camera_point():
    ctx = ParseContext(game_time=1.0, current_stage=StageKind.MIDGAME, camera_point=(50.0, 60.0))
    assert ctx.camera_point == (50.0, 60.0)
    ctx2 = ParseContext(game_time=1.0, current_stage=StageKind.MIDGAME)
    assert ctx2.camera_point is None
