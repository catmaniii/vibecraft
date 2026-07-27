"""单元测试：凤凰骚扰持久指令卡（Director 侧，2026-05-30）。

覆盖：
  - notify_phoenix_harass_started → 记 state + facade.set_phoenix_harass_active(True)
    + 渲染 "phoenix_harass" 卡片（含倒计时 condition）
  - 重复 notify（已有卡）→ 忽略（返回 False）
  - 玩家 × revoke_directive("phoenix_harass") → set_phoenix_harass_active(False) + 清卡
  - 硬性截止：on_tick now>=deadline → 自动收卡 + set_phoenix_harass_active(False)
"""

from __future__ import annotations

from pathlib import Path

from vibecraft.bot.director import Director, DirectorConfig
from vibecraft.bot.facade import BotState, FakeFacade
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _session() -> GameSession:
    return GameSession(GameSessionConfig(use_null_sinks=True))


def _make_library() -> StrategyLibrary:
    return StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )


def _director(facade: FakeFacade, session: GameSession) -> Director:
    from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    library = _make_library()
    parser = IntentParser(provider, library, session=session)
    return Director(
        facade=facade,
        parser=parser,
        session=session,
        library=library,
        config=DirectorConfig(commit_delay_s=0.0),
    )


def _phoenix_harass_card(director: Director, now: float):
    cards = director._build_command_cards(now)
    for c in cards:
        if c["type"] == "phoenix_harass":
            return c
    return None


class TestPhoenixHarassCard:
    def test_notify_creates_card_and_sets_flag(self) -> None:
        facade = FakeFacade(state=BotState())
        director = _director(facade, _session())

        created = director.notify_phoenix_harass_started(270.0, 570.0)
        assert created is True
        assert director._phoenix_harass == {"started_at": 270.0, "deadline": 570.0}
        # flag 显式置 active=True
        assert facade.phoenix_harass_active_calls[-1] is True

        # 卡片渲染（含倒计时 condition）
        card = _phoenix_harass_card(director, now=300.0)
        assert card is not None
        assert card["id"] == "phoenix_harass"
        assert card["revokable"] is True
        assert "凤凰骚扰" in card["display"]
        assert card["conditions"], "应有倒计时 condition"
        prog = card["conditions"][0]["progress"]
        # total=300, now=300 → elapsed=30, remaining=270
        assert prog["target"] == 300
        assert prog["current"] == 30

    def test_duplicate_notify_ignored(self) -> None:
        facade = FakeFacade(state=BotState())
        director = _director(facade, _session())

        assert director.notify_phoenix_harass_started(270.0, 570.0) is True
        # 已有卡 → 第二次 notify 返回 False，state 不变
        assert director.notify_phoenix_harass_started(280.0, 580.0) is False
        assert director._phoenix_harass == {"started_at": 270.0, "deadline": 570.0}

    def test_player_cancel_ends_harass(self) -> None:
        facade = FakeFacade(state=BotState())
        director = _director(facade, _session())
        director.notify_phoenix_harass_started(270.0, 570.0)

        ok = director.revoke_directive("phoenix_harass", now=320.0)
        assert ok is True
        assert director._phoenix_harass is None
        # flag 置 False（凤凰归队主力）
        assert facade.phoenix_harass_active_calls[-1] is False
        # 卡片消失
        assert _phoenix_harass_card(director, now=320.0) is None

    def test_deadline_auto_ends_harass(self) -> None:
        facade = FakeFacade(state=BotState())
        director = _director(facade, _session())
        director.notify_phoenix_harass_started(270.0, 570.0)

        # 截止前 on_tick → 卡仍在
        director.on_tick(now=500.0)
        assert director._phoenix_harass is not None

        # 截止后 on_tick → 自动收卡 + flag False
        director.on_tick(now=571.0)
        assert director._phoenix_harass is None
        assert facade.phoenix_harass_active_calls[-1] is False
        assert _phoenix_harass_card(director, now=571.0) is None
