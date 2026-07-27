"""文字聊天单测：ChatHub（id/maxlen/字段）+ WsConnection chat handler
（广播给所有连接 / strip+截断 / 限频 / 历史）。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibecraft.server.chat import ChatHub
from vibecraft.server.room_service import RoomService
from vibecraft.server.tokens import RoomRegistry
from vibecraft.server.ws import WsConnection


def _ws_mock() -> MagicMock:
    ws = MagicMock()
    ws.remote_address = ("127.0.0.1", 1)
    ws.send = AsyncMock()
    return ws


def _sent(ws: MagicMock) -> list[dict]:
    return [json.loads(c.args[0]) for c in ws.send.call_args_list]


def _chat_msgs(ws: MagicMock) -> list[dict]:
    return [f for f in _sent(ws) if f.get("type") == "chat_msg"]


# ── ChatHub ─────────────────────────────────────────────────────────────────


def test_chathub_id_increment_and_maxlen():
    h = ChatHub(max_history=3)
    for i in range(5):
        h.add(name=f"p{i}", pid=f"x{i}", text=f"m{i}")
    hist = h.history()
    assert len(hist) == 3  # maxlen 裁掉前 2 条
    assert [m["id"] for m in hist] == [3, 4, 5]  # id 单调递增
    assert hist[0]["type"] == "chat_msg"
    assert hist[0]["pid"] == "x2"
    assert isinstance(hist[0]["ts"], int)  # server 时间戳


# ── chat handler ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_send_broadcasts_to_all_connections():
    registry = RoomRegistry(token="t")
    rs = RoomService(registry)
    wsA, wsB = _ws_mock(), _ws_mock()
    connA = WsConnection(wsA, registry, room_service=rs, player_id="pa", player_name="老王")
    connB = WsConnection(wsB, registry, room_service=rs, player_id="pb", player_name="小李")
    registry.attach(connA, "pa")
    registry.attach(connB, "pb")

    await connA._handle_chat_send({"type": "chat_send", "text": "  打它  "})

    # 两端都收到同一条 chat_msg（room-global 广播）
    for ws in (wsA, wsB):
        msgs = _chat_msgs(ws)
        assert len(msgs) == 1
        assert msgs[0]["text"] == "打它"  # strip
        assert msgs[0]["name"] == "老王"
        assert msgs[0]["pid"] == "pa"


@pytest.mark.asyncio
async def test_chat_send_empty_ignored():
    registry = RoomRegistry(token="t")
    rs = RoomService(registry)
    ws = _ws_mock()
    conn = WsConnection(ws, registry, room_service=rs, player_id="p")
    registry.attach(conn, "p")
    await conn._handle_chat_send({"text": "   "})
    assert _chat_msgs(ws) == []


@pytest.mark.asyncio
async def test_chat_send_truncates_to_500():
    registry = RoomRegistry(token="t")
    rs = RoomService(registry)
    ws = _ws_mock()
    conn = WsConnection(ws, registry, room_service=rs, player_id="p")
    registry.attach(conn, "p")
    await conn._handle_chat_send({"text": "x" * 1000})
    assert len(_chat_msgs(ws)[0]["text"]) == 500


@pytest.mark.asyncio
async def test_chat_rate_limited():
    """~2 条/秒：4 条快速发只过 2 条（限频在 server，前端不可信）。"""
    registry = RoomRegistry(token="t")
    rs = RoomService(registry)
    ws = _ws_mock()
    conn = WsConnection(ws, registry, room_service=rs, player_id="p")
    registry.attach(conn, "p")
    for _ in range(4):
        await conn._handle_chat_send({"text": "spam"})
    assert len(_chat_msgs(ws)) == 2


@pytest.mark.asyncio
async def test_chat_history_req_returns_empty_for_players():
    """2026-06-17 用户:后加入的玩家不推送之前的聊天 → 玩家请求历史一律回空。
    但 ChatHub 仍累积历史(供 admin 经 HTTP /api/admin/chat 读完整记录)。"""
    registry = RoomRegistry(token="t")
    rs = RoomService(registry)
    ws = _ws_mock()
    conn = WsConnection(ws, registry, room_service=rs, player_id="p", player_name="A")
    registry.attach(conn, "p")
    rs.chat.add(name="A", pid="p", text="hi")
    rs.chat.add(name="B", pid="q", text="yo")
    await conn._handle_chat_history_req()
    hists = [f for f in _sent(ws) if f.get("type") == "chat_history"]
    assert len(hists) == 1
    # 玩家收到空历史(不回放之前的聊天)
    assert hists[0]["messages"] == []
    # 但 ChatHub 仍存着完整历史 → admin 路径 (_serve_admin_chat) 照样读得到
    assert [m["text"] for m in rs.chat.history()] == ["hi", "yo"]
