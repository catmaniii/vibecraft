"""RoomService 单测（多人联网 Task 6）。

覆盖：
- game_process_for：按 player_id 返回对应 gp（路由正确）
- start_match：广播 room_state{state: starting}
- _on_player_frame：异常被 catch，不向上传播（不杀死 monitor）
- _on_player_frame：room.state 变化时触发广播
- stop_match：广播 room_state{state: lobby}
- broadcast_room_state：发到所有注册连接
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibecraft.server.room_service import RoomService
from vibecraft.server.tokens import RoomRegistry

# ---------------------------------------------------------------------------
# 测试桩
# ---------------------------------------------------------------------------


class _FakeGp:
    """最小 GameProcess stub。"""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self._sc2_state = "playing"
        self._bot_state = "running"
        self.is_running = True

    def send_command(self, cmd: dict[str, Any]) -> None:
        self.sent.append(cmd)


class _FakeConn:
    """最小 Connection stub：收集 send_text 调用。"""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, frame: str) -> None:
        self.sent.append(frame)

    async def close(self, reason: str) -> None:
        pass


class _StubOrch:
    """最小 orchestrator stub（仅路由查询 + 生命周期 stub）。"""

    def __init__(self, procs: dict[str, Any] | None = None) -> None:
        self._procs: dict[str, Any] = procs or {}

    @property
    def processes(self) -> dict[str, Any]:
        return self._procs

    def process_for(self, pid: str) -> Any:
        return self._procs.get(pid)

    async def start_match(self, room: Any, **_kw: Any) -> dict[str, Any]:
        return dict(self._procs)

    async def stop_match(self) -> None:
        pass


# ---------------------------------------------------------------------------
# game_process_for：路由正确
# ---------------------------------------------------------------------------


class TestRoomServiceRouting:
    def test_game_process_for_returns_per_player(self) -> None:
        """game_process_for 按 player_id 返回正确 gp；不存在的 pid 返回 None。"""
        gpA, gpB = _FakeGp(), _FakeGp()
        registry = RoomRegistry(token="tok")
        rs = RoomService(registry, orchestrator=_StubOrch({"pa": gpA, "pb": gpB}))  # type: ignore[arg-type]

        assert rs.game_process_for("pa") is gpA
        assert rs.game_process_for("pb") is gpB
        assert rs.game_process_for("px") is None

    def test_game_process_for_returns_none_when_no_match(self) -> None:
        """对局未开始（_procs 空）→ game_process_for 返回 None。"""
        registry = RoomRegistry(token="tok")
        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]
        assert rs.game_process_for("anyone") is None


# ---------------------------------------------------------------------------
# start_match：广播 starting 状态
# ---------------------------------------------------------------------------


class TestRoomServiceStartMatch:
    async def test_start_match_broadcasts_starting_state(self) -> None:
        """start_match 调用后向所有注册连接广播 room_state{state: starting}。"""
        registry = RoomRegistry(token="tok")
        conn = _FakeConn()
        registry.attach(conn, player_id="p1")

        # 需要 room 里有玩家 + 电脑才能 start
        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]
        rs.room.join("p1", "神族玩家")
        rs.room.add_computer("p1", race="Random", difficulty="VeryHard")
        rs.room.set_ready("p1", True)

        await rs.start_match("p1")

        frames = [json.loads(f) for f in conn.sent]
        room_states = [f for f in frames if f.get("type") == "room_state"]
        assert any(f.get("state") == "starting" for f in room_states), (
            f"期望收到 room_state{{state:starting}}，实际帧：{frames}"
        )

    async def test_start_match_raises_on_bad_room_state(self) -> None:
        """无效 start（房间已在 starting）→ RoomError 向上传播，广播回 lobby。"""

        registry = RoomRegistry(token="tok")
        conn = _FakeConn()
        registry.attach(conn, player_id="p1")

        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]
        # 不 join、不 add_computer → 少于 2 个参与者 → RoomError
        rs.room.join("p1", "玩家")
        rs.room.set_ready("p1", True)

        from vibecraft.server.room import RoomError

        with pytest.raises(RoomError):
            await rs.start_match("p1")


# ---------------------------------------------------------------------------
# _on_player_frame：异常兜底
# ---------------------------------------------------------------------------


class TestOnPlayerFrame:
    async def test_exception_is_caught_and_not_raised(self) -> None:
        """_on_player_frame 内部任何异常都不应向上传播（不杀死 monitor）。"""
        registry = RoomRegistry(token="tok")
        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]

        # 伪造一个让 send_text 总抛的连接
        bad_conn: Any = MagicMock()
        bad_conn.send_text = AsyncMock(side_effect=RuntimeError("网络断了"))
        registry.attach(bad_conn, player_id="p1")

        # 注入假 gp 进 orchestrator
        gp = _FakeGp()
        rs.orchestrator._procs["p1"] = gp  # type: ignore[attr-defined]

        # 放一个 game_status 类 raw（会走 build_downstream_frames → game_status 帧）
        raw = {"sc2": "playing", "bot": "running", "detail": ""}

        # 不应抛
        await rs._on_player_frame("p1", raw)

    async def test_frame_delivered_when_conn_registered(self) -> None:
        """_on_player_frame 成功时帧通过 send_text 到达注册连接。"""
        registry = RoomRegistry(token="tok")
        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]

        conn = _FakeConn()
        registry.attach(conn, player_id="p1")

        gp = _FakeGp()
        rs.orchestrator._procs["p1"] = gp  # type: ignore[attr-defined]

        # echo 类 raw
        raw = {
            "kind": "echo",
            "user_text": "全军进攻",
            "interpretation": "tactical_action(attack)",
        }
        await rs._on_player_frame("p1", raw)

        assert len(conn.sent) == 1
        frame = json.loads(conn.sent[0])
        assert frame["type"] == "command_echo"

    async def test_no_frame_when_conn_not_registered(self) -> None:
        """玩家断线（无注册连接）→ 帧静默丢弃，无报错。"""
        registry = RoomRegistry(token="tok")
        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]

        gp = _FakeGp()
        rs.orchestrator._procs["p1"] = gp  # type: ignore[attr-defined]

        raw = {"kind": "echo", "user_text": "x", "interpretation": ""}
        # p1 未注册到 registry → 静默丢弃
        await rs._on_player_frame("p1", raw)  # 不抛


# ---------------------------------------------------------------------------
# _on_player_frame：room.state 变化时广播
# ---------------------------------------------------------------------------


class TestOnPlayerFrameStateBroadcast:
    async def test_state_change_triggers_broadcast(self) -> None:
        """room.state 改变（如 starting→in_game）→ _on_player_frame 广播新状态。"""
        registry = RoomRegistry(token="tok")
        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]

        conn = _FakeConn()
        registry.attach(conn, player_id="p1")

        gp = _FakeGp()
        rs.orchestrator._procs["p1"] = gp  # type: ignore[attr-defined]

        # 手动改 room 状态（模拟 mark_in_game 被 monitor 调过）
        # 直接 patch _last_room_state 让对比失配，触发广播
        rs._last_room_state = "starting"
        rs.room.state = "in_game"  # type: ignore[assignment]  # Room.state 是公开属性

        raw = {"kind": "echo", "user_text": "x", "interpretation": ""}
        await rs._on_player_frame("p1", raw)

        frames = [json.loads(f) for f in conn.sent]
        room_states = [f for f in frames if f.get("type") == "room_state"]
        # 广播了 in_game 状态
        assert any(f.get("state") == "in_game" for f in room_states), (
            f"期望 room_state{{state:in_game}} 广播，实际：{frames}"
        )


# ---------------------------------------------------------------------------
# stop_match：广播回 lobby
# ---------------------------------------------------------------------------


class TestRoomServiceStopMatch:
    async def test_stop_match_broadcasts_lobby_state(self) -> None:
        """stop_match → room.mark_ended() → 广播 room_state{state: lobby}。"""
        registry = RoomRegistry(token="tok")
        conn = _FakeConn()
        registry.attach(conn, player_id="p1")

        # 先把 room 置 in_game（mark_ended 需要非 lobby 状态）
        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]
        rs.room.state = "in_game"  # type: ignore[assignment]  # Room.state 是公开属性

        await rs.stop_match()

        frames = [json.loads(f) for f in conn.sent]
        room_states = [f for f in frames if f.get("type") == "room_state"]
        assert any(f.get("state") == "lobby" for f in room_states), (
            f"期望 room_state{{state:lobby}} 广播，实际：{frames}"
        )


# ---------------------------------------------------------------------------
# broadcast_room_state：发到所有连接
# ---------------------------------------------------------------------------


class TestBroadcastRoomState:
    async def test_broadcast_reaches_all_conns(self) -> None:
        """broadcast_room_state 的帧到达所有注册连接。"""
        registry = RoomRegistry(token="tok")
        connA, connB = _FakeConn(), _FakeConn()
        registry.attach(connA, player_id="pa")
        registry.attach(connB, player_id="pb")

        rs = RoomService(registry)
        await rs.broadcast_room_state()

        assert len(connA.sent) == 1
        assert len(connB.sent) == 1
        frameA = json.loads(connA.sent[0])
        frameB = json.loads(connB.sent[0])
        assert frameA["type"] == "room_state"
        assert frameA == frameB  # 所有玩家收到同一帧
