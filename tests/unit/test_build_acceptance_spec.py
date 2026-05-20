"""Acceptance spec 模型 + loader。"""
from __future__ import annotations

import pytest

from vibecraft.build_acceptance.spec import AcceptanceSpec, parse_mmss


def test_parse_mmss():
    assert parse_mmss("0:35") == 35.0
    assert parse_mmss("3:14") == 194.0
    assert parse_mmss("10:06") == 606.0


def test_spec_loads_from_dict():
    spec = AcceptanceSpec.model_validate({
        "strategy_id": "demo",
        "my_race": "Protoss",
        "checks": [
            {"id": "g1", "type": "building_started", "unit": "GATEWAY", "by": "0:35"},
            {"id": "ds", "type": "building_complete", "unit": "DARKSHRINE",
             "at": "3:14", "tol": 25},
        ],
    })
    assert spec.strategy_id == "demo"
    assert len(spec.checks) == 2
    assert spec.checks[0].by_s == 35.0
    assert spec.checks[1].at_s == 194.0
    assert spec.checks[1].tol == 25


def test_spec_check_needs_at_or_by():
    with pytest.raises(ValueError):
        AcceptanceSpec.model_validate({
            "strategy_id": "demo", "my_race": "Protoss",
            "checks": [{"id": "bad", "type": "building_started", "unit": "GATEWAY"}],
        })
