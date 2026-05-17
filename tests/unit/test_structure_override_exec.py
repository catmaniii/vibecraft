"""P0e Task 9: _exec_structure_override 执行器 + prereq + status 透传。

测试场景：
- build 成功（prereq OK + 当前 < target）
- 已达目标（current >= target_count）
- prereq 缺失（未知 Nexus）
- 未知 structure type
- routing：STRUCTURE_OVERRIDE → production_overrides slot
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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


def _make_director_with_bot(session: GameSession, mock_bot: MagicMock) -> Director:
    """构造带 mock_bot 的 Director（用于 execute_overrides_step 测试）。"""
    facade = FakeFacade()
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    library_inst = StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )
    parser = IntentParser(provider, library_inst, session=session)
    return Director(facade=facade, parser=parser, session=session, bot=mock_bot)


def _make_structure_override(
    structure_type: str = "Gateway",
    target_count: int = 4,
    location_hint: str | None = None,
) -> Directive:
    """构造 STRUCTURE_OVERRIDE Directive。"""
    from vibecraft.directives.models import StructureOverridePayload

    payload = StructureOverridePayload(
        structure_type=structure_type,
        target_count=target_count,
        location_hint=location_hint,
    )
    return Directive(payload=payload, issued_at=10.0)


def _make_mock_bot(
    gateway_count: int = 0,
    nexus_count: int = 1,
    pending_gateway: float = 0.0,
    pending_nexus: float = 0.0,
    minerals: int = 500,
) -> MagicMock:
    """构造模拟 bot，能响应 structures(type_id).amount 和 already_pending(type_id)。"""
    bot = MagicMock()
    # bot.build 是 async 方法
    bot.build = AsyncMock(return_value=None)

    # structures(type_id) → mock with .amount and .ready
    def _structures(type_id: object) -> MagicMock:
        m = MagicMock()
        name = type_id.name if hasattr(type_id, "name") else str(type_id)
        if name == "GATEWAY":
            m.amount = gateway_count
            m.ready = [MagicMock()] * gateway_count
        elif name == "NEXUS":
            m.amount = nexus_count
            m.ready = [MagicMock()] * nexus_count
        else:
            m.amount = 0
            m.ready = []
        return m

    bot.structures = _structures

    # already_pending(type_id) → float
    def _already_pending(type_id: object) -> float:
        name = type_id.name if hasattr(type_id, "name") else str(type_id)
        if name == "GATEWAY":
            return pending_gateway
        if name == "NEXUS":
            return pending_nexus
        return 0.0

    bot.already_pending = _already_pending
    bot.minerals = minerals

    # expansion_zones（用于 _resolve_location_hint，可能被调）
    bot.knowledge = MagicMock()
    bot.knowledge.expansion_zones = []

    return bot


# ---------------------------------------------------------------------------
# Tests: routing（STRUCTURE_OVERRIDE → production_overrides）
# ---------------------------------------------------------------------------


class TestStructureOverrideRouting:
    """STRUCTURE_OVERRIDE directive 经 _submit_directives 路由到 production_overrides。"""

    def test_structure_override_goes_to_production_overrides(
        self, session: GameSession
    ) -> None:
        """_submit_directives → production_overrides list（不进 _in_flight）。"""
        bot = _make_mock_bot()
        director = _make_director_with_bot(session, bot)
        d = _make_structure_override("Gateway", target_count=4)
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.production_overrides)
        assert d.id not in director._in_flight

    def test_structure_override_not_in_standing_orders(
        self, session: GameSession
    ) -> None:
        """STRUCTURE_OVERRIDE 不进 standing_orders。"""
        bot = _make_mock_bot()
        director = _make_director_with_bot(session, bot)
        d = _make_structure_override("Forge", target_count=2)
        director._submit_directives([d], now=10.0)
        assert not any(s.id == d.id for s in director.standing_orders)


# ---------------------------------------------------------------------------
# Tests: execute_overrides_step dispatch
# ---------------------------------------------------------------------------


class TestStructureOverrideExec:
    """P0e Task 9: _exec_structure_override 的各种场景。

    直接往 director.production_overrides 追加 directive（绕过 board delay），
    然后调 execute_overrides_step，验证 _override_status 结果。
    """

    @pytest.mark.asyncio
    async def test_structure_override_already_done(self, session: GameSession) -> None:
        """current >= target_count → status=active，reason 含 '已达成'，不调 bot.build。"""
        bot = _make_mock_bot(gateway_count=8, nexus_count=1, pending_gateway=0.0)
        director = _make_director_with_bot(session, bot)

        d = _make_structure_override("Gateway", target_count=8)
        director.production_overrides.append(d)
        await director.execute_overrides_step(now=10.0)

        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") == "active"
        assert "已达成" in status_info.get("reason", "")
        bot.build.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_structure_override_build_called_when_below_target(
        self, session: GameSession
    ) -> None:
        """current < target_count + prereq OK → bot.build 被 await，status=active。"""
        bot = _make_mock_bot(gateway_count=5, nexus_count=1, pending_gateway=0.0)
        director = _make_director_with_bot(session, bot)

        d = _make_structure_override("Gateway", target_count=8)
        director.production_overrides.append(d)
        await director.execute_overrides_step(now=10.0)

        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") == "active"
        bot.build.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_structure_override_prereq_missing_nexus(
        self, session: GameSession
    ) -> None:
        """GATEWAY 需要 Nexus，Nexus 数量为 0 → status=on_hold，reason 含 'Nexus'。"""
        bot = _make_mock_bot(gateway_count=0, nexus_count=0, pending_nexus=0.0)
        director = _make_director_with_bot(session, bot)

        d = _make_structure_override("Gateway", target_count=4)
        director.production_overrides.append(d)
        await director.execute_overrides_step(now=10.0)

        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") == "on_hold"
        assert "Nexus" in status_info.get("reason", "") or "NEXUS" in status_info.get("reason", "")

    @pytest.mark.asyncio
    async def test_structure_override_unknown_type(self, session: GameSession) -> None:
        """未知 structure_type → status=on_hold，reason 含 '未知'。"""
        bot = _make_mock_bot()
        director = _make_director_with_bot(session, bot)

        d = _make_structure_override("NotAStructure", target_count=1)
        director.production_overrides.append(d)
        await director.execute_overrides_step(now=10.0)

        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") == "on_hold"
        assert "未知" in status_info.get("reason", "")

    @pytest.mark.asyncio
    async def test_structure_override_pending_counts_toward_current(
        self, session: GameSession
    ) -> None:
        """structures.amount + already_pending 合计 >= target → 不造新的，status=active。"""
        # 5 ready + 3 pending = 8 = target → 已达成
        bot = _make_mock_bot(gateway_count=5, nexus_count=1, pending_gateway=3.0)
        director = _make_director_with_bot(session, bot)

        d = _make_structure_override("Gateway", target_count=8)
        director.production_overrides.append(d)
        await director.execute_overrides_step(now=10.0)

        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") == "active"
        bot.build.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_structure_override_cyberneticscore_prereq_gateway(
        self, session: GameSession
    ) -> None:
        """CYBERNETICSCORE 需要 GATEWAY，若无 Gateway → on_hold。"""
        bot = MagicMock()
        bot.build = AsyncMock(return_value=None)
        bot.knowledge = MagicMock()
        bot.knowledge.expansion_zones = []

        def _structures(type_id: object) -> MagicMock:
            m = MagicMock()
            # 无任何建筑
            m.amount = 0
            m.ready = []
            return m

        bot.structures = _structures
        bot.already_pending = MagicMock(return_value=0.0)

        director = _make_director_with_bot(session, bot)
        d = _make_structure_override("CyberneticsCore", target_count=1)
        director.production_overrides.append(d)
        await director.execute_overrides_step(now=10.0)

        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") == "on_hold"
        reason = status_info.get("reason", "")
        assert "Gateway" in reason or "GATEWAY" in reason


# ---------------------------------------------------------------------------
# Tests: _REQUIRED_STRUCTURE 扩展内容验证
# ---------------------------------------------------------------------------


class TestRequiredStructureMapping:
    """验证 _REQUIRED_STRUCTURE 新增 8 个 protoss 建筑 prereq 的内容。"""

    def test_gateway_requires_nexus(self) -> None:
        assert Director._REQUIRED_STRUCTURE.get("GATEWAY") == "NEXUS"

    def test_forge_requires_nexus(self) -> None:
        assert Director._REQUIRED_STRUCTURE.get("FORGE") == "NEXUS"

    def test_photoncannon_requires_forge(self) -> None:
        assert Director._REQUIRED_STRUCTURE.get("PHOTONCANNON") == "FORGE"

    def test_cyberneticscore_requires_gateway(self) -> None:
        assert Director._REQUIRED_STRUCTURE.get("CYBERNETICSCORE") == "GATEWAY"

    def test_roboticsfacility_requires_cyberneticscore(self) -> None:
        assert Director._REQUIRED_STRUCTURE.get("ROBOTICSFACILITY") == "CYBERNETICSCORE"

    def test_stargate_requires_cyberneticscore(self) -> None:
        assert Director._REQUIRED_STRUCTURE.get("STARGATE") == "CYBERNETICSCORE"

    def test_twilightcouncil_requires_cyberneticscore(self) -> None:
        assert Director._REQUIRED_STRUCTURE.get("TWILIGHTCOUNCIL") == "CYBERNETICSCORE"

    def test_roboticsbay_requires_roboticsfacility(self) -> None:
        assert Director._REQUIRED_STRUCTURE.get("ROBOTICSBAY") == "ROBOTICSFACILITY"
