"""P0f Task 10: snapshot 统一 command_cards array 透传 4 层 directive。

覆盖：
- 空状态 → command_cards 为空 list
- L1 strategy slot → command_cards 含 L1 卡片
- L3 standing order (persistent unit_claim) → command_cards 含 L3 卡片
- L4 production_override → command_cards 含 status / status_reason
- L2 tactical_objective → command_cards 含 L2 卡片
- 每张卡片必含 8 个必填字段
- 向后兼容：原字段 strategy / standing_orders / production_overrides 不删
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.bot import BotState, Director, FakeFacade
from vibecraft.directives.models import Directive
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


@pytest.fixture
def director(session: GameSession, library: StrategyLibrary) -> Director:
    """最小 Director 实例（直接调 _submit_directives）。"""
    facade = FakeFacade(state=BotState(game_time=10.0))
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    parser = IntentParser(provider, library, session=session)
    return Director(facade=facade, parser=parser, session=session)


# ---------------------------------------------------------------------------
# Helpers: directive constructors（复用 test_director.py 风格）
# ---------------------------------------------------------------------------


def _make_unit_claim_directive(persistent: bool = True) -> Directive:
    from vibecraft.directives.models import UnitClaimPayload
    from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
    from vibecraft.directives.task import Action, Task, Verb

    payload = UnitClaimPayload(
        selector=Selector(unit_type="Phoenix"),
        task=Task(
            primary_action=Action(
                verb=Verb.LIFT_TARGET,
                target=TargetSpec(kind=TargetKind.UNIT_TYPE, unit_type="Immortal"),
            )
        ),
        persistent=persistent,
    )
    return Directive(payload=payload, issued_at=15.0)


def _make_production_override_directive() -> Directive:
    from vibecraft.directives.models import ProductionItem, ProductionOverridePayload

    payload = ProductionOverridePayload(items=[ProductionItem(unit_type="Stalker", count=4)])
    return Directive(payload=payload, issued_at=15.0)


def _make_tactical_objective_directive() -> Directive:
    from vibecraft.directives.models import TacticalObjectivePayload

    payload = TacticalObjectivePayload(verb="attack", done_when=None, timeout_s=None)
    return Directive(payload=payload, issued_at=15.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCommandCardsFieldPresence:
    """空状态 / 字段存在性。"""

    def test_snapshot_has_command_cards_field(self, director: Director) -> None:
        """空状态：snapshot 应有 command_cards 字段（空 list）。"""
        snap = director.build_snapshot(now=10.0)
        assert "command_cards" in snap
        assert isinstance(snap["command_cards"], list)

    def test_snapshot_command_cards_empty_when_no_directives(self, director: Director) -> None:
        """无任何 directive 时 command_cards 为空 list。"""
        snap = director.build_snapshot(now=10.0)
        assert snap["command_cards"] == []

    def test_old_fields_still_present(self, director: Director) -> None:
        """向后兼容：原 strategy / standing_orders / production_overrides 字段不删。"""
        snap = director.build_snapshot(now=10.0)
        assert "strategy" in snap
        assert "standing_orders" in snap
        assert "production_overrides" in snap


class TestCommandCardRequiredFields:
    """每张卡片必须包含 8 个必填字段。"""

    REQUIRED_FIELDS = ("id", "layer", "type", "display", "issued_at", "status", "status_reason", "revokable")

    def test_l3_card_has_required_fields(self, director: Director) -> None:
        """L3 standing order 卡片包含所有必填字段。"""
        d = _make_unit_claim_directive(persistent=True)
        director._submit_directives([d], now=15.0)
        snap = director.build_snapshot(now=20.0)
        l3_cards = [c for c in snap["command_cards"] if c["layer"] == "L3"]
        assert len(l3_cards) >= 1, "L3 卡片不存在"
        for field in self.REQUIRED_FIELDS:
            assert field in l3_cards[0], f"command card 缺字段 {field}"
        # revokable 应为 bool
        assert isinstance(l3_cards[0]["revokable"], bool)

    def test_l4_card_has_required_fields(self, director: Director) -> None:
        """L4 production override 卡片包含所有必填字段。"""
        d = _make_production_override_directive()
        director._submit_directives([d], now=15.0)
        snap = director.build_snapshot(now=20.0)
        l4_cards = [c for c in snap["command_cards"] if c["layer"] == "L4"]
        assert len(l4_cards) >= 1, "L4 卡片不存在"
        for field in self.REQUIRED_FIELDS:
            assert field in l4_cards[0], f"command card 缺字段 {field}"


class TestL1StrategyCommandCard:
    """L1 strategy slot → command_cards 含 L1 卡片。

    注意：STRATEGY_SET 走 board pending → commit 流程，delay=1.5s。
    用 director.on_tick(now) 触发 commit（issued_at + delay 之后）。
    """

    def test_l1_strategy_appears_as_command_card(self, director: Director) -> None:
        """board 有 MIDGAME slot → snapshot.command_cards 含 L1 卡片。"""
        from vibecraft.directives.models import StrategySetPayload

        payload = StrategySetPayload(stage="midgame", strategy_id="iac_2base")
        d = Directive(payload=payload, issued_at=5.0)
        director._submit_directives([d], now=5.0)
        # effective_at = 5.0 + 1.5 = 6.5；tick 到 7.0 触发 commit
        director.on_tick(now=7.0)
        snap = director.build_snapshot(now=20.0)
        l1_cards = [c for c in snap["command_cards"] if c["layer"] == "L1"]
        assert len(l1_cards) >= 1
        card = l1_cards[0]
        assert card["type"] == "strategy_set"
        assert card["revokable"] is True
        assert card["status"] in {"pending", "active", "on_hold", "done"}

    def test_l1_card_has_required_fields(self, director: Director) -> None:
        """L1 卡片包含所有必填字段。"""
        from vibecraft.directives.models import StrategySetPayload

        payload = StrategySetPayload(stage="midgame", strategy_id="iac_2base")
        d = Directive(payload=payload, issued_at=5.0)
        director._submit_directives([d], now=5.0)
        director.on_tick(now=7.0)
        snap = director.build_snapshot(now=20.0)
        l1_cards = [c for c in snap["command_cards"] if c["layer"] == "L1"]
        assert len(l1_cards) >= 1
        card = l1_cards[0]
        for field in TestCommandCardRequiredFields.REQUIRED_FIELDS:
            assert field in card, f"L1 command card 缺字段 {field}"


class TestL2TacticalCommandCard:
    """L2 tactical_objective → command_cards 含 L2 卡片。"""

    def test_l2_tactical_appears_in_command_cards(self, director: Director) -> None:
        """注入 L2 tactical_objective → command_cards 含 layer=L2 卡片。"""
        d = _make_tactical_objective_directive()
        director._submit_directives([d], now=15.0)
        snap = director.build_snapshot(now=30.0)
        l2_cards = [c for c in snap["command_cards"] if c["layer"] == "L2"]
        assert len(l2_cards) >= 1

    def test_l2_card_type_is_tactical_objective(self, director: Director) -> None:
        """L2 卡片 type == 'tactical_objective'。"""
        d = _make_tactical_objective_directive()
        director._submit_directives([d], now=15.0)
        snap = director.build_snapshot(now=30.0)
        l2_cards = [c for c in snap["command_cards"] if c["layer"] == "L2"]
        assert len(l2_cards) >= 1
        assert l2_cards[0]["type"] == "tactical_objective"

    def test_l2_card_has_required_fields(self, director: Director) -> None:
        """L2 卡片包含所有必填字段。"""
        d = _make_tactical_objective_directive()
        director._submit_directives([d], now=15.0)
        snap = director.build_snapshot(now=30.0)
        l2_cards = [c for c in snap["command_cards"] if c["layer"] == "L2"]
        assert len(l2_cards) >= 1
        card = l2_cards[0]
        for field in TestCommandCardRequiredFields.REQUIRED_FIELDS:
            assert field in card, f"L2 command card 缺字段 {field}"


class TestL3StandingOrderCommandCard:
    """L3 unit_claim persistent → command_cards 含 L3 卡片。"""

    def test_l3_standing_orders_appear_as_command_cards(self, director: Director) -> None:
        """persistent unit_claim → standing_orders 也透到 command_cards (layer=L3)。"""
        d = _make_unit_claim_directive(persistent=True)
        director._submit_directives([d], now=15.0)
        snap = director.build_snapshot(now=20.0)
        l3_cards = [c for c in snap["command_cards"] if c["layer"] == "L3"]
        assert len(l3_cards) >= 1

    def test_l3_card_type_is_unit_claim(self, director: Director) -> None:
        """L3 卡片 type == 'unit_claim'。"""
        d = _make_unit_claim_directive(persistent=True)
        director._submit_directives([d], now=15.0)
        snap = director.build_snapshot(now=20.0)
        l3_cards = [c for c in snap["command_cards"] if c["layer"] == "L3"]
        assert len(l3_cards) >= 1
        assert l3_cards[0]["type"] == "unit_claim"

    def test_non_persistent_unit_claim_not_in_l3(self, director: Director) -> None:
        """persistent=False 的 unit_claim 进 _in_flight，不在 L3 command_cards。"""
        d = _make_unit_claim_directive(persistent=False)
        director._submit_directives([d], now=15.0)
        snap = director.build_snapshot(now=20.0)
        # 找这个特定 directive id 的 L3 卡片
        l3_ids = {c["id"] for c in snap["command_cards"] if c["layer"] == "L3"}
        assert d.id not in l3_ids


class TestL4ProductionOverrideCommandCard:
    """L4 production_override → command_cards 含 status / status_reason。"""

    def test_l4_production_override_appears_in_command_cards(self, director: Director) -> None:
        """production_override → command_cards 含 layer=L4 卡片。"""
        d = _make_production_override_directive()
        director._submit_directives([d], now=15.0)
        snap = director.build_snapshot(now=20.0)
        l4_cards = [c for c in snap["command_cards"] if c["layer"] == "L4"]
        assert len(l4_cards) >= 1

    def test_l4_card_has_status_and_status_reason(self, director: Director) -> None:
        """L4 卡片含 status 和 status_reason 字段。"""
        d = _make_production_override_directive()
        director._submit_directives([d], now=15.0)
        snap = director.build_snapshot(now=20.0)
        l4_cards = [c for c in snap["command_cards"] if c["layer"] == "L4"]
        assert len(l4_cards) >= 1
        card = l4_cards[0]
        assert "status" in card
        assert "status_reason" in card

    def test_l4_card_status_valid_value(self, director: Director) -> None:
        """L4 卡片 status 值合法。"""
        d = _make_production_override_directive()
        director._submit_directives([d], now=15.0)
        snap = director.build_snapshot(now=20.0)
        l4_cards = [c for c in snap["command_cards"] if c["layer"] == "L4"]
        assert len(l4_cards) >= 1
        assert l4_cards[0]["status"] in {"pending", "active", "on_hold", "done"}

    def test_l4_tech_override_appears(self, director: Director) -> None:
        """tech_override 也进 L4 command_cards。"""
        from vibecraft.directives.models import TechOverridePayload

        payload = TechOverridePayload(upgrade_id="Blink")
        d = Directive(payload=payload, issued_at=15.0)
        director._submit_directives([d], now=15.0)
        snap = director.build_snapshot(now=20.0)
        l4_cards = [c for c in snap["command_cards"] if c["layer"] == "L4"]
        assert len(l4_cards) >= 1

    def test_l4_expansion_override_appears(self, director: Director) -> None:
        """expansion_override 也进 L4 command_cards。"""
        from vibecraft.directives.models import ExpansionOverridePayload

        payload = ExpansionOverridePayload(target_count=3)
        d = Directive(payload=payload, issued_at=15.0)
        director._submit_directives([d], now=15.0)
        snap = director.build_snapshot(now=20.0)
        l4_cards = [c for c in snap["command_cards"] if c["layer"] == "L4"]
        assert len(l4_cards) >= 1

    def test_l4_structure_override_card_display_human_readable(self, director: Director) -> None:
        """STRUCTURE_OVERRIDE card display 不应是'未知 override'。
        display 应包含 structure_type（如 Gateway）。
        """
        from vibecraft.directives.models import StructureItem, StructureOverridePayload

        payload = StructureOverridePayload(
            items=[StructureItem(structure_type="Gateway", target_count=8, location_hint=None)],
        )
        d = Directive(payload=payload, issued_at=15.0)
        director._submit_directives([d], now=15.0)
        snap = director.build_snapshot(now=20.0)
        # 找 type=structure_override 的 L4 卡片
        l4_cards = [
            c for c in snap["command_cards"]
            if c["layer"] == "L4" and c.get("type") == "structure_override"
        ]
        assert len(l4_cards) >= 1, "缺 structure_override L4 卡片"
        display = l4_cards[0]["display"]
        assert "未知" not in display, f"display 仍是 fallback '未知 override': {display!r}"
        assert "Gateway" in display, f"display 未包含 structure_type: {display!r}"

    def test_l4_structure_override_with_location_hint(self, director: Director) -> None:
        """structure_override 带 location_hint 时 display 包含 location_hint。"""
        from vibecraft.directives.models import StructureItem, StructureOverridePayload

        payload = StructureOverridePayload(
            items=[StructureItem(structure_type="PhotonCannon", target_count=1, location_hint="ramp")],
        )
        d = Directive(payload=payload, issued_at=15.0)
        director._submit_directives([d], now=15.0)
        snap = director.build_snapshot(now=20.0)
        l4_cards = [
            c for c in snap["command_cards"]
            if c["layer"] == "L4" and c.get("type") == "structure_override"
        ]
        assert len(l4_cards) >= 1, "缺 structure_override L4 卡片"
        display = l4_cards[0]["display"]
        assert "ramp" in display, f"display 未含 location_hint: {display!r}"
