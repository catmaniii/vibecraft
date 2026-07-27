"""P0d Task 5: 7 个新 done_when kind 的 pydantic 模型 + discriminator 路由。"""

import pytest
from pydantic import TypeAdapter, ValidationError

from vibecraft.directives.models import DoneWhen

ADAPTER = TypeAdapter(DoneWhen)


@pytest.mark.parametrize(
    "payload,expected_kind",
    [
        (
            {"kind": "structure_count", "structure_type": "Gateway", "op": ">=", "value": 8},
            "structure_count",
        ),
        (
            {"kind": "own_unit_count", "unit_type": "Immortal", "op": ">=", "value": 6},
            "own_unit_count",
        ),
        ({"kind": "supply_used", "op": ">=", "value": 70}, "supply_used"),
        ({"kind": "supply_cap", "op": ">=", "value": 200}, "supply_cap"),
        ({"kind": "minerals", "op": ">=", "value": 1000}, "minerals"),
        ({"kind": "gas", "op": ">=", "value": 200}, "gas"),
        ({"kind": "worker_count", "op": ">=", "value": 50}, "worker_count"),
    ],
)
def test_new_done_when_kinds_validate(payload, expected_kind):
    obj = ADAPTER.validate_python(payload)
    assert obj.kind == expected_kind


def test_invalid_op_rejected():
    """所有新 kind 都用 _OP Literal[">=", "<=", "==", ">", "<"]，非法 op 被拒。"""
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(
            {
                "kind": "structure_count",
                "structure_type": "Gateway",
                "op": "!!",
                "value": 8,
            }
        )


def test_structure_count_requires_structure_type():
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"kind": "structure_count", "op": ">=", "value": 8})


def test_own_unit_count_requires_unit_type():
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"kind": "own_unit_count", "op": ">=", "value": 6})


def test_resource_kinds_dont_require_type_field():
    """supply_used / supply_cap / minerals / gas / worker_count 不带 type 字段。"""
    for kind in ["supply_used", "supply_cap", "minerals", "gas", "worker_count"]:
        obj = ADAPTER.validate_python({"kind": kind, "op": ">=", "value": 100})
        assert obj.kind == kind


def test_chain_structure_ready_kind_validates():
    """2026-06-06 回归:chain_structure_ready 必须在 schema 里(否则 LLM 一发就解析失败)。"""
    obj = ADAPTER.validate_python({"kind": "chain_structure_ready", "chain_id": "proxy_6"})
    assert obj.kind == "chain_structure_ready"
    assert obj.chain_id == "proxy_6"


def test_structure_ready_near_kind_validates():
    """2026-06-06 回归:structure_ready_near 也在 schema 里。"""
    obj = ADAPTER.validate_python(
        {"kind": "structure_ready_near", "structure_type": "Pylon", "area": "own_clock_6"}
    )
    assert obj.kind == "structure_ready_near"
    assert obj.within_grid == 8.0
