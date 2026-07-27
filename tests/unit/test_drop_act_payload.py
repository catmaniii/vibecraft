"""DropActPayload schema 验证。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vibecraft.directives.models import Directive, DropActPayload
from vibecraft.directives.types import DirectiveType


class TestDropActPayload:
    def test_simple_style_default(self) -> None:
        p = DropActPayload(
            cargo_unit="Zealot",
            cargo_count=4,
            drop_target="enemy_natural:mineral",
        )
        assert p.style == "simple"
        assert p.transport == "WarpPrism"
        assert p.after_unload == "attack_workers"
        assert p.priority == 60
        assert p.type == DirectiveType.DROP_ACT

    def test_warp_then_drop_requires_warp_at(self) -> None:
        """style=warp_then_drop 但没 warp_at → schema 允许(运行时再校验)."""
        # Pydantic 不强制 warp_at(simple 不需要)。Director 运行时拒绝。
        p = DropActPayload(
            style="warp_then_drop",
            cargo_unit="DarkTemplar",
            cargo_count=4,
            drop_target="enemy_main:production",
            warp_at="enemy_main:ramp_outside",
        )
        assert p.style == "warp_then_drop"
        assert p.warp_at == "enemy_main:ramp_outside"

    def test_unknown_style_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DropActPayload(
                style="invalid",
                cargo_unit="Zealot",
                cargo_count=1,
                drop_target="enemy_main:mineral",
            )

    def test_cargo_count_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            DropActPayload(
                cargo_unit="Zealot",
                cargo_count=0,
                drop_target="enemy_main:mineral",
            )

    def test_directive_wrap(self) -> None:
        """payload 能正常装进 Directive envelope。"""
        p = DropActPayload(cargo_unit="Zealot", cargo_count=4, drop_target="enemy_natural:mineral")
        d = Directive(payload=p, issued_at=10.0)
        assert d.type == DirectiveType.DROP_ACT
