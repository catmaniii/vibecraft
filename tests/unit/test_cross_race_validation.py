"""指令跨族校验单测（Task #523）。

校验范围：
- race_of(canonical) 函数正确识别三族 canonical。
- Director._reject_if_cross_race：
    - 人族局提交神族 production_override → failed + "神族" in reason，不进 production_overrides。
    - 同族（Marine）→ 正常通过。
    - 例外：facade 有目标族农民（Probe）→ 放行。
    - unit_claim selector.unit_type=Stalker（人族局、facade 无该单位）→ failed。
    - unit_claim：facade 有 Stalker → 放行。
    - my_race 为空 → 不拦。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.bot import Director, FakeFacade
from vibecraft.directives.models import (
    Directive,
    GroupAssignPayload,
    ProductionItem,
    ProductionOverridePayload,
    UnitClaimPayload,
)
from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
from vibecraft.directives.task import Action, Task, Verb
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary
from vibecraft.strategy.aliases import race_of

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# race_of 函数测试
# ---------------------------------------------------------------------------


class TestRaceOf:
    def test_observer_is_protoss(self) -> None:
        assert race_of("Observer") == "protoss"

    def test_marine_is_terran(self) -> None:
        assert race_of("Marine") == "terran"

    def test_roach_is_zerg(self) -> None:
        assert race_of("Roach") == "zerg"

    def test_unknown_is_none(self) -> None:
        assert race_of("SomeUnknownUnit12345") is None

    def test_stargate_is_protoss(self) -> None:
        assert race_of("Stargate") == "protoss"

    def test_barracks_is_terran(self) -> None:
        assert race_of("Barracks") == "terran"

    def test_hatchery_is_zerg(self) -> None:
        assert race_of("Hatchery") == "zerg"

    def test_probe_is_protoss(self) -> None:
        assert race_of("Probe") == "protoss"

    def test_scv_is_terran(self) -> None:
        assert race_of("SCV") == "terran"

    def test_drone_is_zerg(self) -> None:
        assert race_of("Drone") == "zerg"


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
    my_race: str = "terran",
) -> Director:
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=10, output_tokens=5, latency_ms=1.0)]
    )
    parser = IntentParser(provider, library, session=session, my_race=my_race)
    return Director(facade=facade, parser=parser, session=session, library=library)


def _standby_task(named_spot: str = "natural") -> Task:
    return Task(
        primary_action=Action(
            verb=Verb.STANDBY,
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot=named_spot),
        )
    )


# ---------------------------------------------------------------------------
# 主校验场景
# ---------------------------------------------------------------------------


class TestCrossRaceRejection:
    def test_terran_observer_production_override_rejected(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """人族局提交神族 Observer production_override → failed，不进 production_overrides。"""
        facade = FakeFacade()
        director = _make_director(library, session, facade, my_race="terran")

        d = Directive(
            payload=ProductionOverridePayload(
                items=[ProductionItem(unit_type="Observer", count=2)]
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        # 应进 _in_flight（显示 failed 卡）
        assert d.id in director._in_flight
        # 不进 production_overrides
        assert not any(x.id == d.id for x in director.production_overrides)
        # status 是 failed，reason 含"神族"
        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") == "failed"
        assert "神族" in status_info.get("reason", "")

    def test_same_race_marine_production_override_accepted(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """人族局提交同族 Marine → 正常通过，进 production_overrides。"""
        facade = FakeFacade()
        director = _make_director(library, session, facade, my_race="terran")

        d = Directive(
            payload=ProductionOverridePayload(items=[ProductionItem(unit_type="Marine", count=1)]),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        # 正常进 production_overrides，状态不是 failed
        assert any(x.id == d.id for x in director.production_overrides)
        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") != "failed"

    def test_exception_probe_present_allows_observer(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """人族局，但 facade 有 Probe（如心灵控制）→ Observer 放行。"""
        facade = FakeFacade()
        facade.selector_stub["Probe"] = [999]  # 拥有神族农民

        director = _make_director(library, session, facade, my_race="terran")

        d = Directive(
            payload=ProductionOverridePayload(
                items=[ProductionItem(unit_type="Observer", count=1)]
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        # 有神族农民 → 不拒绝
        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") != "failed"
        assert any(x.id == d.id for x in director.production_overrides)

    def test_unit_claim_stalker_terran_rejected(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """人族局，unit_claim selector.unit_type=Stalker（神族追猎），facade 无该单位 → failed。"""
        facade = FakeFacade()
        # selector_stub 没有 Stalker → resolve_selector 返回 []
        director = _make_director(library, session, facade, my_race="terran")

        d = Directive(
            payload=UnitClaimPayload(
                selector=Selector(unit_type="Stalker"),
                task=_standby_task(),
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") == "failed"
        assert d.id in director._in_flight

    def test_unit_claim_stalker_present_allowed(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """人族局，unit_claim Stalker，但 facade 真实有 Stalker → 放行。"""
        facade = FakeFacade()
        facade.selector_stub["Stalker"] = [111]  # 真实拥有追猎（如捕获）
        director = _make_director(library, session, facade, my_race="terran")

        d = Directive(
            payload=UnitClaimPayload(
                selector=Selector(unit_type="Stalker"),
                task=_standby_task(),
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        # 有该单位 → 不拒绝
        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") != "failed"

    def test_my_race_empty_no_check(self, library: StrategyLibrary, session: GameSession) -> None:
        """my_race 为空（种族未知）→ 不校验，直接放行。"""
        facade = FakeFacade()
        director = _make_director(library, session, facade, my_race="")

        d = Directive(
            payload=ProductionOverridePayload(
                items=[ProductionItem(unit_type="Observer", count=1)]
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        # my_race 空 → 不拦
        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") != "failed"

    def test_group_assign_stalker_terran_rejected(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """人族局，group_assign selector.unit_type=Stalker，无该单位 → failed。"""
        facade = FakeFacade()
        director = _make_director(library, session, facade, my_race="terran")

        d = Directive(
            payload=GroupAssignPayload(
                group_id=1,
                selector=Selector(unit_type="Stalker"),
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") == "failed"

    def test_protoss_race_same_race_zealot_allowed(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """神族局，production_override Zealot → 同族，放行。"""
        facade = FakeFacade()
        director = _make_director(library, session, facade, my_race="protoss")

        d = Directive(
            payload=ProductionOverridePayload(items=[ProductionItem(unit_type="Zealot", count=2)]),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        assert any(x.id == d.id for x in director.production_overrides)
        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") != "failed"

    def test_zerg_marine_production_override_rejected(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """虫族局提交人族 Marine → failed，reason 含"人族"。"""
        facade = FakeFacade()
        director = _make_director(library, session, facade, my_race="zerg")

        d = Directive(
            payload=ProductionOverridePayload(items=[ProductionItem(unit_type="Marine", count=1)]),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") == "failed"
        assert "人族" in status_info.get("reason", "")
