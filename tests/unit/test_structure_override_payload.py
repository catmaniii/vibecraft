"""structure_override payload 模型测试。

P0e Task 8 初稿用单一 structure_type/target_count；后续改为 items 列表
支持一条 directive 多建筑（同次语音的多建筑任务作为单卡跟踪）。
"""

import pytest
from pydantic import ValidationError

from vibecraft.directives.models import Directive
from vibecraft.directives.types import DirectiveType


def test_structure_override_payload_validates():
    d = Directive(
        payload={
            "type": "structure_override",
            "items": [{"structure_type": "Gateway", "target_count": 8, "location_hint": "main"}],
        },
        issued_at=10.0,
    )
    assert d.payload.type == DirectiveType.STRUCTURE_OVERRIDE
    assert d.payload.items[0].structure_type == "Gateway"
    assert d.payload.items[0].target_count == 8
    assert d.payload.items[0].location_hint == "main"


def test_structure_override_location_hint_optional():
    d = Directive(
        payload={
            "type": "structure_override",
            "items": [{"structure_type": "Pylon", "target_count": 2}],
        },
        issued_at=5.0,
    )
    assert d.payload.items[0].location_hint is None


def test_structure_override_target_count_must_be_positive():
    with pytest.raises(ValidationError):
        Directive(
            payload={
                "type": "structure_override",
                "items": [{"structure_type": "Gateway", "target_count": 0}],
            },
            issued_at=10.0,
        )


def test_structure_override_with_done_when_structure_count():
    """structure_override 可带 done_when=structure_count（O1 场景"补到 8 BG"）。"""
    d = Directive(
        payload={
            "type": "structure_override",
            "items": [{"structure_type": "Gateway", "target_count": 8}],
            "done_when": {
                "kind": "structure_count",
                "structure_type": "Gateway",
                "op": ">=",
                "value": 8,
            },
            "timeout_s": 180,
        },
        issued_at=10.0,
    )
    assert d.payload.done_when.kind == "structure_count"
    assert d.payload.timeout_s == 180


def test_structure_override_in_payload_union():
    """discriminator 路由 type=structure_override → StructureOverridePayload"""
    from vibecraft.directives.models import StructureOverridePayload

    d = Directive(
        payload={
            "type": "structure_override",
            "items": [{"structure_type": "Forge", "target_count": 1}],
        },
        issued_at=0.0,
    )
    assert isinstance(d.payload, StructureOverridePayload)


def test_structure_override_multi_items_one_card():
    """一条 directive 多建筑（"ramp 放 2 cannon 1 BF"），整体追踪。"""
    d = Directive(
        payload={
            "type": "structure_override",
            "items": [
                {"structure_type": "PhotonCannon", "target_count": 2, "location_hint": "ramp"},
                {"structure_type": "Forge", "target_count": 1, "location_hint": "ramp"},
            ],
        },
        issued_at=10.0,
    )
    assert len(d.payload.items) == 2
    assert d.payload.items[0].structure_type == "PhotonCannon"
    assert d.payload.items[1].structure_type == "Forge"
