"""WS tactical_action / strategy_action 帧处理单测。

覆盖：
- 收到 tactical_action 帧后正确 forward 到 down_q
- 无效 verb 被拒绝
- 游戏未运行时静默丢弃
- 收到 strategy_action 帧后正确 forward 到 down_q
- 无效 strategy_id 被拒绝
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from vibecraft.server.ws import WsConnection


# ---------------------------------------------------------------------------
# 轻量 mock 工具
# ---------------------------------------------------------------------------


class _FakeWs:
    """最小 WS 连接 mock。"""

    remote_address = ("127.0.0.1", 12345)

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)


class _FakeGameProcess:
    """最小 GameProcess mock。"""

    def __init__(self, is_running: bool = True) -> None:
        self._is_running = is_running
        self.sent_commands: list[dict[str, Any]] = []
        # GameProcess._sc2_state / _bot_state 供 ws.py 读
        self._sc2_state = "playing"
        self._bot_state = "running"

    @property
    def is_running(self) -> bool:
        return self._is_running

    def send_command(self, cmd: dict[str, Any]) -> None:
        self.sent_commands.append(cmd)


def _make_conn(is_running: bool = True) -> tuple[WsConnection, _FakeGameProcess]:
    fake_ws = _FakeWs()
    gp = _FakeGameProcess(is_running=is_running)
    registry = MagicMock()
    registry.verify.return_value = True
    conn = WsConnection(ws=fake_ws, registry=registry, game_process=gp)  # type: ignore[arg-type]
    return conn, gp


# ---------------------------------------------------------------------------
# tactical_action 测试
# ---------------------------------------------------------------------------


class TestTacticalAction:
    @pytest.mark.asyncio
    async def test_valid_verb_attack_forwarded_to_down_q(self) -> None:
        conn, gp = _make_conn()
        await conn._handle_raw('{"type": "tactical_action", "verb": "attack"}')
        assert len(gp.sent_commands) == 1
        assert gp.sent_commands[0] == {"type": "tactical_action", "verb": "attack"}

    @pytest.mark.asyncio
    async def test_valid_verb_defend_forwarded(self) -> None:
        conn, gp = _make_conn()
        await conn._handle_raw('{"type": "tactical_action", "verb": "defend"}')
        assert gp.sent_commands[0]["verb"] == "defend"

    @pytest.mark.asyncio
    async def test_valid_verb_retreat_forwarded(self) -> None:
        conn, gp = _make_conn()
        await conn._handle_raw('{"type": "tactical_action", "verb": "retreat"}')
        assert gp.sent_commands[0]["verb"] == "retreat"

    @pytest.mark.asyncio
    async def test_valid_verb_recon_forwarded(self) -> None:
        conn, gp = _make_conn()
        await conn._handle_raw('{"type": "tactical_action", "verb": "recon"}')
        assert gp.sent_commands[0]["verb"] == "recon"

    @pytest.mark.asyncio
    async def test_valid_verb_scout_forwarded(self) -> None:
        conn, gp = _make_conn()
        await conn._handle_raw('{"type": "tactical_action", "verb": "scout"}')
        assert gp.sent_commands[0]["verb"] == "scout"

    @pytest.mark.asyncio
    async def test_invalid_verb_rejected(self) -> None:
        conn, gp = _make_conn()
        await conn._handle_raw('{"type": "tactical_action", "verb": "dance"}')
        # 无效 verb → 不 forward
        assert len(gp.sent_commands) == 0

    @pytest.mark.asyncio
    async def test_missing_verb_rejected(self) -> None:
        conn, gp = _make_conn()
        await conn._handle_raw('{"type": "tactical_action"}')
        assert len(gp.sent_commands) == 0

    @pytest.mark.asyncio
    async def test_game_not_running_silently_dropped(self) -> None:
        conn, gp = _make_conn(is_running=False)
        await conn._handle_raw('{"type": "tactical_action", "verb": "attack"}')
        assert len(gp.sent_commands) == 0


# ---------------------------------------------------------------------------
# strategy_action 测试
# ---------------------------------------------------------------------------


class TestStrategyAction:
    @pytest.mark.asyncio
    async def test_valid_strategy_id_forwarded(self) -> None:
        conn, gp = _make_conn()
        await conn._handle_raw('{"type": "strategy_action", "strategy_id": "iac_2base"}')
        assert len(gp.sent_commands) == 1
        assert gp.sent_commands[0] == {"type": "strategy_action", "strategy_id": "iac_2base"}

    @pytest.mark.asyncio
    async def test_missing_strategy_id_rejected(self) -> None:
        conn, gp = _make_conn()
        await conn._handle_raw('{"type": "strategy_action"}')
        assert len(gp.sent_commands) == 0

    @pytest.mark.asyncio
    async def test_empty_strategy_id_rejected(self) -> None:
        conn, gp = _make_conn()
        await conn._handle_raw('{"type": "strategy_action", "strategy_id": ""}')
        assert len(gp.sent_commands) == 0

    @pytest.mark.asyncio
    async def test_whitespace_strategy_id_rejected(self) -> None:
        conn, gp = _make_conn()
        await conn._handle_raw('{"type": "strategy_action", "strategy_id": "   "}')
        assert len(gp.sent_commands) == 0

    @pytest.mark.asyncio
    async def test_game_not_running_silently_dropped(self) -> None:
        conn, gp = _make_conn(is_running=False)
        await conn._handle_raw('{"type": "strategy_action", "strategy_id": "iac_2base"}')
        assert len(gp.sent_commands) == 0
