"""locale 穿透：GameConfig.locale 字段 + IntentParser 接收并存储 locale（review #2/#3 的 plumbing）。

端到端链路：PWA → ws ?locale= → GameConfig.locale → 子进程 env VIBECRAFT_LOCALE → IntentParser.locale。
本测试覆盖可在无 SC2 下验证的两端：GameConfig 字段 + IntentParser 存储。
"""

from __future__ import annotations

import pytest

from vibecraft.llm.provider import MockLLMProvider, ProviderResponse
from vibecraft.server.game_process import GameConfig
from vibecraft.strategy import StrategyLibrary


def test_gameconfig_locale_default_and_override() -> None:
    assert GameConfig().locale == "zh"
    assert GameConfig(locale="en").locale == "en"


def _mk_parser(locale: str | None = None):
    from vibecraft.llm.parser import IntentParser

    provider = MockLLMProvider(
        handler=lambda **_: ProviderResponse(raw={}, raw_text="{}", model="mock")
    )
    lib = StrategyLibrary()
    if locale is None:
        return IntentParser(provider=provider, library=lib)
    return IntentParser(provider=provider, library=lib, locale=locale)


def test_parser_locale_default_zh() -> None:
    assert _mk_parser().locale == "zh"


def test_parser_locale_override_en() -> None:
    assert _mk_parser("en").locale == "en"


def test_parser_locale_empty_falls_back_zh() -> None:
    assert _mk_parser("").locale == "zh"


def test_room_join_stores_locale() -> None:
    """ws 握手 locale → room.join → Slot.locale（→ match.py 写进本位 GameConfig.locale）。"""
    from vibecraft.server.room import Room

    r = Room()
    s = r.join("p1", "Alice", "en")
    assert s.locale == "en"
    # 重连可改语言
    s2 = r.join("p1", "Alice", "zh")
    assert s2.locale == "zh"


def test_room_join_default_locale_zh() -> None:
    from vibecraft.server.room import Room

    assert Room().join("p1", "Alice").locale == "zh"


def test_room_error_localized_en() -> None:
    """RoomError 带 i18n key → localized('en') 返回英文；str() 仍是 zh（日志/测试断言）。"""
    from vibecraft.server.room import RoomError

    e = RoomError("房间满了", key="room.err.full")
    assert str(e) == "房间满了"  # 既有 match= 断言依赖
    assert e.localized("zh") == "房间满了"
    assert e.localized("en") == "Room is full"


def test_room_error_localized_params() -> None:
    """带参数的 RoomError（如未知种族）localized 正确插值。"""
    from vibecraft.server.room import RoomError

    e = RoomError("未知种族 Xyz", key="room.err.unknown_race", race="Xyz")
    assert e.localized("en") == "Unknown race Xyz"


def test_room_error_no_key_falls_back_zh() -> None:
    """无 key 的 RoomError → localized 回退 zh 原文（向后兼容）。"""
    from vibecraft.server.room import RoomError

    e = RoomError("自定义原因")
    assert e.localized("en") == "自定义原因"


def test_room_raise_sites_have_valid_keys() -> None:
    """所有 room.py raise RoomError 的 key 都在 strings.json 里有英文翻译（防漏译）。"""
    import json
    import re
    from pathlib import Path

    room_src = (
        Path(__file__).resolve().parents[2] / "src" / "vibecraft" / "server" / "room.py"
    ).read_text(encoding="utf-8")
    keys = set(re.findall(r'key="(room\.err\.[a-z0-9_]+)"', room_src))
    assert len(keys) >= 15, f"应抓到全部 room.err.* key，实际 {len(keys)}"
    strings = json.loads(
        (Path(__file__).resolve().parents[2] / "locales" / "strings.json").read_text(
            encoding="utf-8"
        )
    )
    for k in keys:
        assert k in strings, f"strings.json 缺 key: {k}"
        assert strings[k].get("en"), f"{k} 缺英文翻译"


class _FakeDirector:
    """run_command_with_echo 用的最小 director：parser.locale + on_player_command 返回固定 outcome。"""

    def __init__(self, locale: str, outcome: object) -> None:
        self.parser = type("P", (), {"locale": locale})()
        self._outcome = outcome

    async def on_player_command(self, text: str, now: float) -> object:
        return self._outcome


@pytest.mark.asyncio
async def test_echo_prefix_localized_en() -> None:
    """locale=en 时 [解析失败]/[模糊] echo 前缀走英文（interpretation 本身由 LLM 生成,这里只验前缀）。"""
    from vibecraft.bot.auto_combat.common import run_command_with_echo
    from vibecraft.llm.schema import (
        AmbiguousParse,
        IntentParseResult,
        ParseError,
        ParseErrorKind,
    )

    captured: list[tuple[str, str]] = []

    def echo(user_text: str, interp: str) -> None:
        captured.append((user_text, interp))

    err = ParseError(kind=ParseErrorKind.SCHEMA_MISMATCH, message="boom")
    await run_command_with_echo(_FakeDirector("en", err), "do x", 0.0, echo)
    assert captured[-1][1] == "[Parse failed] boom"

    amb = AmbiguousParse(result=IntentParseResult(interpretation_zh="Maybe attack", confidence=0.3))
    await run_command_with_echo(_FakeDirector("en", amb), "do y", 0.0, echo)
    assert captured[-1][1] == "[Ambiguous] Maybe attack"


@pytest.mark.asyncio
async def test_echo_prefix_zh_default() -> None:
    """locale=zh（默认）时前缀保持中文。"""
    from vibecraft.bot.auto_combat.common import run_command_with_echo
    from vibecraft.llm.schema import ParseError, ParseErrorKind

    captured: list[tuple[str, str]] = []
    err = ParseError(kind=ParseErrorKind.SCHEMA_MISMATCH, message="炸了")
    await run_command_with_echo(
        _FakeDirector("zh", err), "做啥", 0.0, lambda u, i: captured.append((u, i))
    )
    assert captured[-1][1] == "[解析失败] 炸了"
