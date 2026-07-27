"""WP1 Task 1.1：StealthMinePayload schema 单测。

验证：
- 合法 dict 能被 Directive payload 判别联合解析为 StealthMinePayload
- 缺 point 时使用默认值（(0,0)）；type 不匹配报 ValidationError
- 各字段默认值正确
- extra 字段被 extra=forbid 拒绝
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vibecraft.directives.models import Directive, StealthMinePayload
from vibecraft.directives.types import DirectiveType


class TestStealthMinePayloadParsing:
    def test_minimal_payload_parses(self) -> None:
        """最小合法 payload（仅 type + point）能被解析。"""
        payload = StealthMinePayload(point=(50.0, 60.0))
        assert payload.type == DirectiveType.STEALTH_MINE
        assert payload.point == (50.0, 60.0)
        assert payload.cell_id == 0
        assert payload.worker_target == 16
        assert payload.with_gas is True
        assert payload.on_attack == "flee"

    def test_full_payload_parses(self) -> None:
        """所有字段显式传入能被解析。"""
        payload = StealthMinePayload(
            point=(30.0, 40.0),
            cell_id=3,
            worker_target=8,
            with_gas=False,
            on_attack="hold",
        )
        assert payload.point == (30.0, 40.0)
        assert payload.cell_id == 3
        assert payload.worker_target == 8
        assert payload.with_gas is False
        assert payload.on_attack == "hold"

    def test_extra_field_rejected(self) -> None:
        """extra=forbid：未知字段被拒绝。"""
        with pytest.raises(ValidationError):
            StealthMinePayload(point=(50.0, 60.0), unknown_field="bad")

    def test_invalid_on_attack_rejected(self) -> None:
        """on_attack 只允许 flee / hold。"""
        with pytest.raises(ValidationError):
            StealthMinePayload(point=(50.0, 60.0), on_attack="run")

    def test_directive_envelope_wraps_stealth_mine(self) -> None:
        """Directive 信封能用 type=stealth_mine 构造 StealthMinePayload。"""
        d = Directive(
            payload={  # type: ignore[arg-type]
                "type": "stealth_mine",
                "point": [55.0, 65.0],
            },
            issued_at=100.0,
        )
        assert d.type == DirectiveType.STEALTH_MINE
        assert isinstance(d.payload, StealthMinePayload)
        assert d.payload.point == (55.0, 65.0)

    def test_directive_envelope_wrong_type(self) -> None:
        """type 不匹配 → ValidationError。"""
        with pytest.raises(ValidationError):
            Directive(
                payload={  # type: ignore[arg-type]
                    "type": "nonexistent_type",
                    "point": [50.0, 60.0],
                },
                issued_at=100.0,
            )

    def test_payload_models_includes_stealth_mine(self) -> None:
        """PAYLOAD_MODELS 白名单包含 stealth_mine。"""
        from vibecraft.directives.models import PAYLOAD_MODELS

        assert "stealth_mine" in PAYLOAD_MODELS
        assert PAYLOAD_MODELS["stealth_mine"] is StealthMinePayload
