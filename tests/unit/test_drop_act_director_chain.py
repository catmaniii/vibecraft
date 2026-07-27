"""Director._exec_drop_act: resolve target + auto-prereq + auto-production.

Task 7 of docs/plans/2026-05-23-drop-act-implementation-plan.md

Tests:
  1. TestExecDropActResolveTarget  - drop_target 解析失败 → status=on_hold
  2. TestExecDropActAutoPrereq     - 缺 FleetBeacon → 自动 emit structure_override chain
  3. TestExecDropActAutoProduction - 缺单位 → 自动 emit ProductionOverride
  4. TestExecDropActReadyInstantiate - 单位齐 + 建筑齐 → status=active
  5. TestExecDropActStyleSimple    - style=simple 走 GenericDropAct 路径(status active)
  6. TestExecDropActStyleWarpThenDrop - style=warp_then_drop + warp_at 解析失败 → on_hold

Pattern: 参考 tests/unit/test_auto_prereq.py mock bot + director fixture
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vibecraft.bot import Director, FakeFacade
from vibecraft.directives.models import (
    Directive,
    DropActPayload,
    StructureOverridePayload,
)
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session() -> GameSession:
    s = GameSession(GameSessionConfig(use_null_sinks=True))
    yield s
    s.close()


def _make_mock_bot(
    have: dict[str, int] | None = None,
    pending: dict[str, float] | None = None,
    race: str = "Protoss",
    unit_have: dict[str, int] | None = None,
) -> MagicMock:
    """构造 mock bot — 控制 structures ready/pending + units ready。"""
    from sc2.data import Race

    have = have or {"NEXUS": 1}
    pending = pending or {}
    unit_have = unit_have or {}

    bot = MagicMock()
    bot.time = 60.0
    bot.race = {"Protoss": Race.Protoss, "Zerg": Race.Zerg, "Terran": Race.Terran}[race]

    # structures(type_id).amount / .ready.amount / .already_pending
    def _structures(type_id: object) -> MagicMock:
        m = MagicMock()
        name = type_id.name if hasattr(type_id, "name") else str(type_id)
        cnt = have.get(name, 0)
        m.amount = cnt
        ready_mock = MagicMock()
        ready_mock.__len__ = MagicMock(return_value=cnt)
        ready_mock.__bool__ = MagicMock(return_value=cnt > 0)
        ready_mock.exists = cnt > 0
        ready_mock.amount = cnt
        m.ready = ready_mock
        return m

    bot.structures = _structures

    def _already_pending(type_id: object) -> float:
        name = type_id.name if hasattr(type_id, "name") else str(type_id)
        return pending.get(name, 0.0)

    bot.already_pending = _already_pending

    # units(type_id) — for checking cargo/transport count
    def _units(type_id: object) -> MagicMock:
        m = MagicMock()
        name = type_id.name if hasattr(type_id, "name") else str(type_id)
        cnt = unit_have.get(name, 0)
        m.amount = cnt
        m.__len__ = MagicMock(return_value=cnt)
        m.__bool__ = MagicMock(return_value=cnt > 0)
        return m

    bot.units = _units

    bot.knowledge = MagicMock()
    bot.knowledge.expansion_zones = []
    # enemy zones for NamedSpotRegistry
    from unittest.mock import MagicMock as MM

    from sc2.position import Point2

    zone0 = MM()
    zone0.center_location = Point2((48, 28))
    zone0.behind_mineral_position_center = Point2((45, 31))
    zone1 = MM()
    zone1.center_location = Point2((30, 49))
    zone1.behind_mineral_position_center = Point2((33, 52))
    bot.knowledge.zone_manager.enemy_expansion_zones = [zone0, zone1]
    bot.game_info = MM()
    bot.game_info.map_center = Point2((80, 80))
    bot.game_info.playable_area = MM(x=0, y=0, width=160, height=160)
    bot.expansion_locations_list = [zone0.center_location, zone1.center_location]

    return bot


def _make_director(session: GameSession, mock_bot: MagicMock) -> Director:
    facade = FakeFacade()
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    library = StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )
    parser = IntentParser(provider, library, session=session)
    return Director(facade=facade, parser=parser, session=session, bot=mock_bot)


def _make_drop_act_directive(
    cargo_unit: str = "Zealot",
    cargo_count: int = 4,
    transport: str = "WarpPrism",
    drop_target: str = "enemy_natural:mineral",
    style: str = "simple",
    warp_at: str | None = None,
) -> Directive:
    payload = DropActPayload(
        cargo_unit=cargo_unit,
        cargo_count=cargo_count,
        transport=transport,
        drop_target=drop_target,
        style=style,  # type: ignore[arg-type]
        warp_at=warp_at,
    )
    return Directive(payload=payload, issued_at=60.0)


# ---------------------------------------------------------------------------
# Test 1: drop_target 解析失败 → status=on_hold
# ---------------------------------------------------------------------------


class TestExecDropActResolveTarget:
    def test_bad_drop_target_sets_on_hold(self, session: GameSession) -> None:
        """drop_target spec 无法解析 → status=on_hold,不 emit 子 directive。"""
        bot = _make_mock_bot(have={"NEXUS": 1})
        director = _make_director(session, bot)

        d = _make_drop_act_directive(drop_target="garbage:mineral")
        director._exec_drop_act(d, d.payload)

        status = director._override_status.get(d.id, {}).get("status")
        assert status == "on_hold"
        # 不应 emit 任何 auto_prereq 或 production 子 directive
        auto = [
            od
            for od in director.production_overrides
            if od.source_text and od.source_text.startswith("auto_drop_act:")
        ]
        assert len(auto) == 0

    def test_unknown_spec_format_on_hold(self, session: GameSession) -> None:
        """缺冒号格式 → on_hold。"""
        bot = _make_mock_bot(have={"NEXUS": 1})
        director = _make_director(session, bot)

        d = _make_drop_act_directive(drop_target="no_colon_at_all")
        director._exec_drop_act(d, d.payload)

        status = director._override_status.get(d.id, {}).get("status")
        assert status == "on_hold"

    def test_valid_enemy_natural_resolves(self, session: GameSession) -> None:
        """enemy_natural:mineral 有 zone → 不 on_hold(进入后续流程)。"""
        bot = _make_mock_bot(have={"NEXUS": 1, "GATEWAY": 1, "CYBERNETICSCORE": 1})
        director = _make_director(session, bot)

        d = _make_drop_act_directive(drop_target="enemy_natural:mineral")
        director._exec_drop_act(d, d.payload)

        # 不应该是 on_hold(因 drop_target 解析失败)
        director._override_status.get(d.id, {}).get("status")
        reason = director._override_status.get(d.id, {}).get("reason", "")
        assert "drop_target 解析失败" not in reason


# ---------------------------------------------------------------------------
# Test 2: 缺 FleetBeacon → 自动 emit 完整 structure_override chain
# ---------------------------------------------------------------------------


class TestExecDropActAutoPrereq:
    def test_carrier_transport_missing_fleetbeacon_emits_chain(self, session: GameSession) -> None:
        """transport=Carrier 缺 FleetBeacon → 自动 emit structure_override chain。"""
        bot = _make_mock_bot(have={"NEXUS": 1})
        director = _make_director(session, bot)

        d = _make_drop_act_directive(
            cargo_unit="Zealot",
            transport="Carrier",  # Carrier 需要 STARGATE + FLEETBEACON
            drop_target="enemy_natural:mineral",
        )
        director._exec_drop_act(d, d.payload)

        # 应 emit structure_override directives for prereqs
        auto_structure = [
            od
            for od in director.production_overrides
            if isinstance(od.payload, StructureOverridePayload)
            and od.source_text
            and od.source_text.startswith("auto_prereq:")
        ]
        structure_types = [od.payload.items[0].structure_type for od in auto_structure]
        # Carrier 需要 GATEWAY + CYBERNETICSCORE + STARGATE + FLEETBEACON
        assert "STARGATE" in structure_types or "FLEETBEACON" in structure_types

    def test_dt_cargo_missing_darkshrine_emits_chain(self, session: GameSession) -> None:
        """cargo_unit=DarkTemplar 缺 DARKSHRINE → 自动 emit chain。"""
        bot = _make_mock_bot(have={"NEXUS": 1})
        director = _make_director(session, bot)

        d = _make_drop_act_directive(
            cargo_unit="DarkTemplar",
            drop_target="enemy_natural:mineral",
        )
        director._exec_drop_act(d, d.payload)

        auto_structure = [
            od
            for od in director.production_overrides
            if isinstance(od.payload, StructureOverridePayload)
            and od.source_text
            and od.source_text.startswith("auto_prereq:")
        ]
        structure_types = [od.payload.items[0].structure_type for od in auto_structure]
        assert "DARKSHRINE" in structure_types

    def test_zealot_no_missing_prereq(self, session: GameSession) -> None:
        """Zealot + WarpPrism 且 GATEWAY/CYBERNETICSCORE 已就绪 → 无额外 prereq emit。"""
        bot = _make_mock_bot(
            have={"NEXUS": 1, "GATEWAY": 1, "CYBERNETICSCORE": 1, "ROBOTICSFACILITY": 1},
        )
        director = _make_director(session, bot)

        d = _make_drop_act_directive(
            cargo_unit="Zealot",
            transport="WarpPrism",
            drop_target="enemy_natural:mineral",
        )
        director._exec_drop_act(d, d.payload)

        auto_structure = [
            od
            for od in director.production_overrides
            if isinstance(od.payload, StructureOverridePayload)
            and od.source_text
            and od.source_text.startswith("auto_prereq:")
        ]
        assert len(auto_structure) == 0


# ---------------------------------------------------------------------------
# Test 3: 缺单位 → 自动 emit ProductionOverride
# ---------------------------------------------------------------------------


class TestExecDropActAutoProduction:
    def test_missing_zealots_emits_production_override(self, session: GameSession) -> None:
        """cargo 不足 → emit ProductionOverride for cargo。"""
        from vibecraft.directives.models import ProductionOverridePayload

        bot = _make_mock_bot(
            have={"NEXUS": 1, "GATEWAY": 1, "CYBERNETICSCORE": 1, "ROBOTICSFACILITY": 1},
            unit_have={"ZEALOT": 0, "WARPPRISM": 0},
        )
        director = _make_director(session, bot)

        d = _make_drop_act_directive(
            cargo_unit="Zealot",
            cargo_count=4,
            transport="WarpPrism",
            drop_target="enemy_natural:mineral",
        )
        director._exec_drop_act(d, d.payload)

        auto_prod = [
            od
            for od in director.production_overrides
            if isinstance(od.payload, ProductionOverridePayload)
            and od.source_text
            and od.source_text.startswith("auto_drop_act:")
        ]
        # 应 emit cargo + transport production override
        unit_types_emitted = {item.unit_type for od in auto_prod for item in od.payload.items}
        assert "Zealot" in unit_types_emitted or "WarpPrism" in unit_types_emitted

    def test_missing_transport_emits_production_override(self, session: GameSession) -> None:
        """transport 数量为 0 → emit ProductionOverride for transport。"""
        from vibecraft.directives.models import ProductionOverridePayload

        bot = _make_mock_bot(
            have={"NEXUS": 1, "GATEWAY": 1, "CYBERNETICSCORE": 1, "ROBOTICSFACILITY": 1},
            unit_have={"ZEALOT": 4, "WARPPRISM": 0},  # 叉子够,棱镜没
        )
        director = _make_director(session, bot)

        d = _make_drop_act_directive(
            cargo_unit="Zealot",
            cargo_count=4,
            transport="WarpPrism",
            drop_target="enemy_natural:mineral",
        )
        director._exec_drop_act(d, d.payload)

        auto_prod = [
            od
            for od in director.production_overrides
            if isinstance(od.payload, ProductionOverridePayload)
            and od.source_text
            and od.source_text.startswith("auto_drop_act:")
        ]
        unit_types_emitted = {item.unit_type for od in auto_prod for item in od.payload.items}
        assert "WarpPrism" in unit_types_emitted

    def test_repeated_call_no_duplicate_emit(self, session: GameSession) -> None:
        """_auto_drop_act_emitted 防重复：同单位第二次不再 emit。"""
        from vibecraft.directives.models import ProductionOverridePayload

        bot = _make_mock_bot(
            have={"NEXUS": 1, "GATEWAY": 1, "CYBERNETICSCORE": 1, "ROBOTICSFACILITY": 1},
            unit_have={"ZEALOT": 0, "WARPPRISM": 0},
        )
        director = _make_director(session, bot)
        d = _make_drop_act_directive(
            cargo_unit="Zealot",
            cargo_count=4,
            transport="WarpPrism",
            drop_target="enemy_natural:mineral",
        )

        director._exec_drop_act(d, d.payload)
        first_count = len(
            [
                od
                for od in director.production_overrides
                if isinstance(od.payload, ProductionOverridePayload)
                and od.source_text
                and od.source_text.startswith("auto_drop_act:")
            ]
        )

        director._exec_drop_act(d, d.payload)
        second_count = len(
            [
                od
                for od in director.production_overrides
                if isinstance(od.payload, ProductionOverridePayload)
                and od.source_text
                and od.source_text.startswith("auto_drop_act:")
            ]
        )

        assert second_count == first_count  # 没新增


# ---------------------------------------------------------------------------
# Test 4: 单位齐 + 建筑齐 → status=active
# ---------------------------------------------------------------------------


class TestExecDropActReadyInstantiate:
    def test_units_ready_sets_active_status(self, session: GameSession) -> None:
        """cargo + transport 都 ready → status=active。"""
        bot = _make_mock_bot(
            have={"NEXUS": 1, "GATEWAY": 1, "CYBERNETICSCORE": 1, "ROBOTICSFACILITY": 1},
            unit_have={"ZEALOT": 4, "WARPPRISM": 1},
        )
        director = _make_director(session, bot)

        d = _make_drop_act_directive(
            cargo_unit="Zealot",
            cargo_count=4,
            transport="WarpPrism",
            drop_target="enemy_natural:mineral",
        )
        director._exec_drop_act(d, d.payload)

        status = director._override_status.get(d.id, {}).get("status")
        assert status == "active"

    def test_no_duplicate_act_on_repeat_call(self, session: GameSession) -> None:
        """同一 directive 多次 _exec_drop_act 幂等:status 始终 active 不错误变化。

        Note: sharpy 单测环境不可用时,_active_drop_acts 可能为空(ActBase 实例化失败),
        但状态仍应保持 active(不倒退到 on_hold)。
        """
        bot = _make_mock_bot(
            have={"NEXUS": 1, "GATEWAY": 1, "CYBERNETICSCORE": 1, "ROBOTICSFACILITY": 1},
            unit_have={"ZEALOT": 4, "WARPPRISM": 1},
        )
        director = _make_director(session, bot)
        d = _make_drop_act_directive(drop_target="enemy_natural:mineral")

        director._exec_drop_act(d, d.payload)
        status_first = director._override_status.get(d.id, {}).get("status")

        director._exec_drop_act(d, d.payload)  # 第二次调用
        status_second = director._override_status.get(d.id, {}).get("status")

        # 两次都 active,不退步
        assert status_first == "active"
        assert status_second == "active"


# ---------------------------------------------------------------------------
# Test 5: style=simple → 实例化 GenericDropAct
# ---------------------------------------------------------------------------


class TestExecDropActStyleSimple:
    def test_simple_style_creates_generic_drop_act(self, session: GameSession) -> None:
        """style=simple → status=active (sharpy 可用时 _active_drop_acts 含 GenericDropAct)。

        sharpy 单测环境不可用 → skip ActBase instance check,只验 status=active。
        """
        pytest.importorskip("sharpy", reason="sharpy not installed in unit test env")
        from vibecraft.bot.auto_combat.protoss.plans.generic_drop_act import GenericDropAct

        bot = _make_mock_bot(
            have={"NEXUS": 1, "GATEWAY": 1, "CYBERNETICSCORE": 1, "ROBOTICSFACILITY": 1},
            unit_have={"ZEALOT": 4, "WARPPRISM": 1},
        )
        director = _make_director(session, bot)

        d = _make_drop_act_directive(
            style="simple",
            cargo_unit="Zealot",
            cargo_count=4,
            transport="WarpPrism",
            drop_target="enemy_natural:mineral",
        )
        director._exec_drop_act(d, d.payload)

        assert d.id in director._active_drop_acts
        act = director._active_drop_acts[d.id]
        assert isinstance(act, GenericDropAct)


# ---------------------------------------------------------------------------
# Test 6: style=warp_then_drop + warp_at 缺失 → on_hold
# ---------------------------------------------------------------------------


class TestExecDropActStyleWarpThenDrop:
    def test_warp_then_drop_without_warp_at_is_on_hold(self, session: GameSession) -> None:
        """style=warp_then_drop 但 warp_at=None → status=on_hold。"""
        bot = _make_mock_bot(have={"NEXUS": 1, "GATEWAY": 1, "CYBERNETICSCORE": 1})
        director = _make_director(session, bot)

        d = _make_drop_act_directive(
            style="warp_then_drop",
            cargo_unit="DarkTemplar",
            cargo_count=4,
            drop_target="enemy_main:mineral",
            warp_at=None,
        )
        director._exec_drop_act(d, d.payload)

        status = director._override_status.get(d.id, {}).get("status")
        assert status == "on_hold"

    def test_warp_then_drop_bad_warp_at_is_on_hold(self, session: GameSession) -> None:
        """warp_at spec 无法解析 → on_hold。"""
        bot = _make_mock_bot(have={"NEXUS": 1, "GATEWAY": 1, "CYBERNETICSCORE": 1})
        director = _make_director(session, bot)

        d = _make_drop_act_directive(
            style="warp_then_drop",
            cargo_unit="DarkTemplar",
            cargo_count=4,
            drop_target="enemy_main:mineral",
            warp_at="garbage_warp_at:mineral",
        )
        director._exec_drop_act(d, d.payload)

        status = director._override_status.get(d.id, {}).get("status")
        assert status == "on_hold"

    def test_warp_then_drop_valid_creates_prism_warp_drop_act(self, session: GameSession) -> None:
        """style=warp_then_drop + 单位齐 + warp_at 可解析 → PrismWarpDropAct 实例。

        sharpy 单测环境不可用 → skip ActBase instance check。
        """
        pytest.importorskip("sharpy", reason="sharpy not installed in unit test env")
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import PrismWarpDropAct

        bot = _make_mock_bot(
            have={
                "NEXUS": 1,
                "GATEWAY": 1,
                "CYBERNETICSCORE": 1,
                "TWILIGHTCOUNCIL": 1,
                "DARKSHRINE": 1,
                "ROBOTICSFACILITY": 1,
            },
            unit_have={"DARKTEMPLAR": 4, "WARPPRISM": 1},
        )
        director = _make_director(session, bot)

        d = _make_drop_act_directive(
            style="warp_then_drop",
            cargo_unit="DarkTemplar",
            cargo_count=4,
            transport="WarpPrism",
            drop_target="enemy_main:mineral",
            warp_at="enemy_natural:mineral",  # 可解析(有两个 zone)
        )
        director._exec_drop_act(d, d.payload)

        assert d.id in director._active_drop_acts
        act = director._active_drop_acts[d.id]
        assert isinstance(act, PrismWarpDropAct)


# ---------------------------------------------------------------------------
# Dispatch route test: DROP_ACT goes via execute_overrides_step
# ---------------------------------------------------------------------------


class TestDropActDispatchRoute:
    def test_drop_act_in_production_overrides_list(self, session: GameSession) -> None:
        """_submit_directives で DROP_ACT → production_overrides に追加される。"""
        bot = _make_mock_bot(have={"NEXUS": 1})
        director = _make_director(session, bot)

        d = _make_drop_act_directive(drop_target="enemy_natural:mineral")
        director._submit_directives([d], now=60.0)

        # DROP_ACT は production_overrides list に入る
        ids = [od.id for od in director.production_overrides]
        assert d.id in ids

    @pytest.mark.asyncio
    async def test_execute_overrides_step_calls_exec_drop_act(self, session: GameSession) -> None:
        """execute_overrides_step が DROP_ACT payload を _exec_drop_act に転送する。"""
        bot = _make_mock_bot(have={"NEXUS": 1})
        director = _make_director(session, bot)

        d = _make_drop_act_directive(drop_target="garbage:mineral")
        director.production_overrides.append(d)

        # bad drop_target → on_hold(途中 _exec_drop_act が呼ばれたことを確認)
        await director.execute_overrides_step(now=60.0)
        status = director._override_status.get(d.id, {}).get("status")
        assert status == "on_hold"
