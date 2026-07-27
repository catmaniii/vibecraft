"""多人联网 WS 路由 + lobby 操作单测（Task 6/7）。

覆盖：
- 指令路由不变量：玩家 A 的指令绝不进玩家 B 的 down_q
- lobby_start → 广播 room_state{state: starting}
- 非房主操作 lobby_add_computer → room_error 帧
- 旧 start_game 流程（M3 shim）仍能触发游戏启动
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from vibecraft.server.room_service import RoomService
from vibecraft.server.tokens import RoomRegistry
from vibecraft.server.ws import WsConnection

# ---------------------------------------------------------------------------
# 测试桩
# ---------------------------------------------------------------------------


class _FakeGp:
    """最小 GameProcess stub，记录 send_command 调用。"""

    def __init__(self, is_running: bool = True) -> None:
        self.sent: list[dict[str, Any]] = []
        self._sc2_state = "playing"
        self._bot_state = "running"
        self.is_running = is_running

    def send_command(self, cmd: dict[str, Any]) -> None:
        self.sent.append(cmd)


class _StubOrch:
    """最小 orchestrator stub，仅路由查询 + start/stop stub。"""

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


def _make_ws_mock(player_id: str = "default") -> MagicMock:
    ws = MagicMock()
    ws.remote_address = ("127.0.0.1", 12345)
    ws.send = AsyncMock()
    return ws


# ---------------------------------------------------------------------------
# 指令路由不变量：A 的指令不进 B 的 down_q
# ---------------------------------------------------------------------------


class TestCommandRoutingInvariant:
    async def test_command_routes_to_own_process_only(self) -> None:
        """玩家 A 的指令绝不能进玩家 B 的 down_q —— 多人路由根本不变量（S7）。"""
        gpA = _FakeGp()
        gpB = _FakeGp()

        registry = RoomRegistry(token="tok")
        orch = _StubOrch({"pa": gpA, "pb": gpB})
        rs = RoomService(registry, orchestrator=orch)  # type: ignore[arg-type]

        ws_a = _make_ws_mock("pa")
        ws_b = _make_ws_mock("pb")

        connA = WsConnection(ws_a, registry, room_service=rs, player_id="pa")
        connB = WsConnection(ws_b, registry, room_service=rs, player_id="pb")
        registry.attach(connA, "pa")
        registry.attach(connB, "pb")

        await connA._handle_command({"type": "command", "text": "全军进攻"})

        assert len(gpA.sent) == 1, "A 的命令应进 A 的 down_q"
        assert gpA.sent[0]["text"] == "全军进攻"
        assert gpB.sent == [], "A 的命令绝不进 B 的 down_q（路由不变量）"

    async def test_two_players_command_to_own_gp(self) -> None:
        """A 和 B 各发指令，分别只进自己的 down_q。"""
        gpA = _FakeGp()
        gpB = _FakeGp()

        registry = RoomRegistry(token="tok")
        orch = _StubOrch({"pa": gpA, "pb": gpB})
        rs = RoomService(registry, orchestrator=orch)  # type: ignore[arg-type]

        connA = WsConnection(_make_ws_mock("pa"), registry, room_service=rs, player_id="pa")
        connB = WsConnection(_make_ws_mock("pb"), registry, room_service=rs, player_id="pb")
        registry.attach(connA, "pa")
        registry.attach(connB, "pb")

        await connA._handle_command({"type": "command", "text": "进攻"})
        await connB._handle_command({"type": "command", "text": "撤退"})

        assert len(gpA.sent) == 1 and gpA.sent[0]["text"] == "进攻"
        assert len(gpB.sent) == 1 and gpB.sent[0]["text"] == "撤退"


# ---------------------------------------------------------------------------
# lobby_start → 广播 starting 状态
# ---------------------------------------------------------------------------


class TestLobbyStart:
    async def test_lobby_start_broadcasts_starting_state(self) -> None:
        """lobby_start 帧 → room_service.start_match → 广播 room_state{state: starting}。"""

        class _FakeConn:
            def __init__(self) -> None:
                self.sent: list[str] = []

            async def send_text(self, frame: str) -> None:
                self.sent.append(frame)

            async def close(self, reason: str) -> None:
                pass

        registry = RoomRegistry(token="tok")
        fake_conn = _FakeConn()

        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]
        rs.room.join("p1", "玩家1")
        rs.room.add_computer("p1", race="Random", difficulty="VeryHard")
        rs.room.set_ready("p1", True)

        ws = _make_ws_mock("p1")
        conn = WsConnection(ws, registry, room_service=rs, player_id="p1")
        # 注册两条连接：WsConnection 本身（ws.send）+ fake_conn（send_text）
        registry.attach(conn, "p1")
        # 注册另一观察连接（广播覆盖）
        registry.attach(fake_conn, "observer")

        await conn._handle_raw(json.dumps({"type": "lobby_start"}))

        frames = [json.loads(f) for f in fake_conn.sent]
        room_state_frames = [f for f in frames if f.get("type") == "room_state"]
        assert any(f.get("state") == "starting" for f in room_state_frames), (
            f"期望 room_state{{state:starting}}，实际：{frames}"
        )

    async def test_lobby_start_non_host_gets_room_error(self) -> None:
        """非房主触发 lobby_start → room_error 帧（不能开局）。"""
        registry = RoomRegistry(token="tok")
        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]
        # 双人局：2 个真人都 ready，不加电脑（双人对战，2 个参与者满足 room.start 条件）
        rs.room.join("host", "房主")
        rs.room.join("guest", "客人")
        rs.room.set_ready("host", True)
        rs.room.set_ready("guest", True)

        ws = _make_ws_mock("guest")
        conn = WsConnection(ws, registry, room_service=rs, player_id="guest")
        registry.attach(conn, "guest")

        await conn._handle_raw(json.dumps({"type": "lobby_start"}))

        # guest 不是房主，应该收到 room_error
        calls = ws.send.call_args_list
        frames = [json.loads(c.args[0]) for c in calls]
        error_frames = [f for f in frames if f.get("type") == "room_error"]
        assert len(error_frames) >= 1, f"期望 room_error 帧，实际：{frames}"


# ---------------------------------------------------------------------------
# 非房主 lobby_add_computer → room_error
# ---------------------------------------------------------------------------


class TestLobbyNonHostRejected:
    async def test_non_host_add_computer_rejected(self) -> None:
        """非房主发 lobby_add_computer → room_error 帧（不广播 room_state）。"""
        registry = RoomRegistry(token="tok")
        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]
        rs.room.join("host", "房主")
        rs.room.join("guest", "客人")

        ws_guest = _make_ws_mock("guest")
        conn_guest = WsConnection(ws_guest, registry, room_service=rs, player_id="guest")
        registry.attach(conn_guest, "guest")

        # 非房主尝试加电脑
        await conn_guest._handle_raw(
            json.dumps({"type": "lobby_add_computer", "race": "Terran", "difficulty": "Hard"})
        )

        calls = ws_guest.send.call_args_list
        frames = [json.loads(c.args[0]) for c in calls]
        error_frames = [f for f in frames if f.get("type") == "room_error"]
        assert len(error_frames) >= 1, f"期望 room_error，实际：{frames}"

    async def test_host_add_computer_succeeds(self) -> None:
        """房主发 lobby_add_computer → 广播 room_state（成功）。"""

        class _FakeConn:
            def __init__(self) -> None:
                self.sent: list[str] = []

            async def send_text(self, frame: str) -> None:
                self.sent.append(frame)

            async def close(self, reason: str) -> None:
                pass

        registry = RoomRegistry(token="tok")
        observer = _FakeConn()
        registry.attach(observer, "obs")

        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]
        rs.room.join("host", "房主")

        ws_host = _make_ws_mock("host")
        conn_host = WsConnection(ws_host, registry, room_service=rs, player_id="host")
        registry.attach(conn_host, "host")

        await conn_host._handle_raw(
            json.dumps({"type": "lobby_add_computer", "race": "Terran", "difficulty": "Hard"})
        )

        frames = [json.loads(f) for f in observer.sent]
        room_state_frames = [f for f in frames if f.get("type") == "room_state"]
        assert len(room_state_frames) >= 1, f"期望 room_state 广播，实际：{frames}"


# ---------------------------------------------------------------------------
# 旧 start_game 流程（M3 shim）仍然能触发游戏启动
# ---------------------------------------------------------------------------


class TestLegacySoloStartGame:
    async def test_legacy_start_game_triggers_start_match(self) -> None:
        """旧 start_game 帧（M3 shim）→ room_service.start_match 被调用。"""

        class _FakeConn:
            def __init__(self) -> None:
                self.sent: list[str] = []

            async def send_text(self, frame: str) -> None:
                self.sent.append(frame)

            async def close(self, reason: str) -> None:
                pass

        registry = RoomRegistry(token="tok")
        observer = _FakeConn()
        registry.attach(observer, "obs")

        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]

        ws = _make_ws_mock("default")
        conn = WsConnection(ws, registry, room_service=rs, player_id="default")
        registry.attach(conn, "default")

        # 旧 PWA 发 start_game（无 lobby 流程）
        await conn._handle_raw(json.dumps({"type": "start_game", "config": {"realtime": False}}))

        # 广播里应有 room_state{state: starting}
        frames = [json.loads(f) for f in observer.sent]
        room_state_frames = [f for f in frames if f.get("type") == "room_state"]
        assert any(f.get("state") == "starting" for f in room_state_frames), (
            f"期望 room_state{{state:starting}}，实际：{frames}"
        )

    async def test_legacy_start_game_realtime_config_flows_through(self) -> None:
        """旧 start_game 帧的 realtime 配置被正确传入 room。"""
        registry = RoomRegistry(token="tok")
        rs = RoomService(registry, orchestrator=_StubOrch(), default_realtime=True)  # type: ignore[arg-type]

        ws = _make_ws_mock("default")
        conn = WsConnection(ws, registry, room_service=rs, player_id="default")

        await conn._handle_raw(json.dumps({"type": "start_game", "config": {"realtime": False}}))

        # room.realtime 应被 shim 更新为 False
        assert rs.room.realtime is False


# ---- 2026-06-12 用户实测反馈 #1:lobby 断线延迟 leave(防名单狂闪)----


class _FakeRegistryForLeave:
    def __init__(self, reconnected: bool) -> None:
        self._reconnected = reconnected

    def connection_of(self, pid: str):
        return object() if self._reconnected else None


class _FakeRoomServiceForLeave:
    def __init__(self) -> None:
        from vibecraft.server.room import Room

        self.room = Room(max_slots=4)
        self.broadcasts = 0

    async def broadcast_room_state(self) -> None:
        self.broadcasts += 1


async def test_delayed_leave_fires_when_not_reconnected(monkeypatch):
    """宽限期后仍没重连 → leave + 广播。"""
    import vibecraft.server.ws as ws_mod

    monkeypatch.setattr(ws_mod, "_LOBBY_LEAVE_GRACE_S", 0.01)
    rs = _FakeRoomServiceForLeave()
    rs.room.join("pa", "alice")
    await ws_mod._delayed_lobby_leave(rs, _FakeRegistryForLeave(reconnected=False), "pa")
    assert rs.room.slot_of("pa") is None
    assert rs.broadcasts == 1


async def test_delayed_leave_noop_when_reconnected(monkeypatch):
    """宽限期内重连(registry 有新连接) → slot 保留,不广播。"""
    import vibecraft.server.ws as ws_mod

    monkeypatch.setattr(ws_mod, "_LOBBY_LEAVE_GRACE_S", 0.01)
    rs = _FakeRoomServiceForLeave()
    rs.room.join("pa", "alice")
    await ws_mod._delayed_lobby_leave(rs, _FakeRegistryForLeave(reconnected=True), "pa")
    assert rs.room.slot_of("pa") is not None
    assert rs.broadcasts == 0


# ---- 2026-06-12 用户反馈 #7：end_game 仅房主 + surrender 帧 ----


class TestEndGameHostOnly:
    async def test_non_host_end_game_gets_room_error(self) -> None:
        """非房主触发 end_game → room_error 帧，不结束对局。"""
        registry = RoomRegistry(token="tok")
        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]
        rs.room.join("host", "房主")
        rs.room.join("guest", "客人")
        # 纯双真人 1v1：引擎限制不允许双真人+电脑，所以不加电脑
        rs.room.set_ready("guest", True)
        rs.room.start("host")  # 房主免准备
        rs.room.mark_in_game()

        ws_guest = _make_ws_mock("guest")
        conn_guest = WsConnection(ws_guest, registry, room_service=rs, player_id="guest")
        registry.attach(conn_guest, "guest")

        await conn_guest._handle_raw(json.dumps({"type": "end_game"}))

        calls = ws_guest.send.call_args_list
        frames = [json.loads(c.args[0]) for c in calls]
        error_frames = [f for f in frames if f.get("type") == "room_error"]
        assert len(error_frames) >= 1, f"期望 room_error，实际：{frames}"
        assert any("房主" in f.get("message", "") for f in error_frames)

    async def test_host_end_game_calls_stop_match(self) -> None:
        """房主触发 end_game → stop_match 被调。"""

        class _StopRecordOrch(_StubOrch):
            def __init__(self) -> None:
                super().__init__()
                self.stopped = False

            async def stop_match(self) -> None:
                self.stopped = True

        registry = RoomRegistry(token="tok")
        orch = _StopRecordOrch()
        rs = RoomService(registry, orchestrator=orch)  # type: ignore[arg-type]
        rs.room.join("host", "房主")
        rs.room.add_computer("host", race="Random", difficulty="VeryHard")
        rs.room.set_ready("host", True)
        rs.room.start("host")
        rs.room.mark_in_game()

        ws_host = _make_ws_mock("host")
        conn_host = WsConnection(ws_host, registry, room_service=rs, player_id="host")
        registry.attach(conn_host, "host")

        await conn_host._handle_raw(json.dumps({"type": "end_game"}))

        assert orch.stopped, "房主 end_game 应触发 stop_match"


class TestSurrender:
    async def test_surrender_calls_gp_stop(self) -> None:
        """surrender 帧 → gp.stop() 被调。"""

        class _FakeGpWithStop(_FakeGp):
            def __init__(self) -> None:
                super().__init__(is_running=True)
                self.stopped = False

            async def stop(self) -> None:
                self.stopped = True

        gp = _FakeGpWithStop()
        registry = RoomRegistry(token="tok")
        orch = _StubOrch({"player1": gp})
        rs = RoomService(registry, orchestrator=orch)  # type: ignore[arg-type]
        rs.room.join("player1", "玩家")
        rs.room.add_computer("player1", race="Random", difficulty="VeryHard")
        rs.room.set_ready("player1", True)
        rs.room.start("player1")
        rs.room.mark_in_game()

        ws = _make_ws_mock("player1")
        conn = WsConnection(ws, registry, room_service=rs, player_id="player1")
        registry.attach(conn, "player1")

        await conn._handle_raw(json.dumps({"type": "surrender"}))

        assert gp.stopped, "surrender 应调 gp.stop()"

    async def test_surrender_lobby_gets_room_error(self) -> None:
        """lobby 态发 surrender → room_error（不在对局中）。"""
        registry = RoomRegistry(token="tok")
        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]
        rs.room.join("player1", "玩家")

        ws = _make_ws_mock("player1")
        conn = WsConnection(ws, registry, room_service=rs, player_id="player1")
        registry.attach(conn, "player1")

        await conn._handle_raw(json.dumps({"type": "surrender"}))

        calls = ws.send.call_args_list
        frames = [json.loads(c.args[0]) for c in calls]
        error_frames = [f for f in frames if f.get("type") == "room_error"]
        assert len(error_frames) >= 1, f"期望 room_error，实际：{frames}"

    async def test_surrender_no_gp_gets_room_error(self) -> None:
        """in_game 但 gp 不运行 → room_error("没有进行中的对局")。"""

        class _FakeGpNotRunning(_FakeGp):
            def __init__(self) -> None:
                super().__init__(is_running=False)

        gp = _FakeGpNotRunning()
        registry = RoomRegistry(token="tok")
        orch = _StubOrch({"player1": gp})
        rs = RoomService(registry, orchestrator=orch)  # type: ignore[arg-type]
        rs.room.join("player1", "玩家")
        rs.room.add_computer("player1", race="Random", difficulty="VeryHard")
        rs.room.set_ready("player1", True)
        rs.room.start("player1")
        rs.room.mark_in_game()

        ws = _make_ws_mock("player1")
        conn = WsConnection(ws, registry, room_service=rs, player_id="player1")
        registry.attach(conn, "player1")

        await conn._handle_raw(json.dumps({"type": "surrender"}))

        calls = ws.send.call_args_list
        frames = [json.loads(c.args[0]) for c in calls]
        error_frames = [f for f in frames if f.get("type") == "room_error"]
        assert len(error_frames) >= 1, f"期望 room_error，实际：{frames}"


# ---- lobby_join 帧 + 连接与入房解耦 ----


class TestLobbyJoinFrame:
    async def test_lobby_join_puts_player_in_room(self) -> None:
        """lobby_join 帧 → 玩家加入 room slot，广播 room_state。"""

        class _FakeConn:
            def __init__(self) -> None:
                self.sent: list[str] = []

            async def send_text(self, frame: str) -> None:
                self.sent.append(frame)

            async def close(self, reason: str) -> None:
                pass

        registry = RoomRegistry(token="tok")
        observer = _FakeConn()
        registry.attach(observer, "obs")

        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]

        ws = _make_ws_mock("p1")
        # player_name 传给 WsConnection，握手时不再 join
        conn = WsConnection(ws, registry, room_service=rs, player_id="p1", player_name="alice")
        registry.attach(conn, "p1")

        # 入房前：slot 为空
        assert rs.room.slot_of("p1") is None

        await conn._handle_raw(json.dumps({"type": "lobby_join"}))

        # 入房后：slot 已填，广播了 room_state
        assert rs.room.slot_of("p1") is not None
        assert rs.room.slot_of("p1").name == "alice"
        frames = [json.loads(f) for f in observer.sent]
        assert any(f.get("type") == "room_state" for f in frames)

    async def test_lobby_join_in_game_with_existing_slot_is_idempotent(self) -> None:
        """in_game 态，已有 slot 的 pid 发 lobby_join → 幂等返回，不报错（断线重连场景）。"""
        registry = RoomRegistry(token="tok")
        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]
        rs.room.join("p1", "alice")
        rs.room.add_computer("p1", race="Random", difficulty="VeryHard")
        rs.room.set_ready("p1", True)
        rs.room.start("p1")
        rs.room.mark_in_game()

        ws = _make_ws_mock("p1")
        conn = WsConnection(
            ws, registry, room_service=rs, player_id="p1", player_name="alice_reconnect"
        )
        registry.attach(conn, "p1")

        await conn._handle_raw(json.dumps({"type": "lobby_join"}))

        # slot 仍在，昵称已更新
        slot = rs.room.slot_of("p1")
        assert slot is not None
        assert slot.name == "alice_reconnect"
        # 没有 room_error
        calls = ws.send.call_args_list
        frames = [json.loads(c.args[0]) for c in calls]
        assert not any(f.get("type") == "room_error" for f in frames), (
            f"不应有 room_error: {frames}"
        )

    async def test_lobby_join_in_game_new_pid_rejected(self) -> None:
        """in_game 态，新 pid 发 lobby_join → room_error（"对局进行中"）。"""
        registry = RoomRegistry(token="tok")
        rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]
        rs.room.join("host", "房主")
        rs.room.add_computer("host", race="Random", difficulty="VeryHard")
        rs.room.set_ready("host", True)
        rs.room.start("host")
        rs.room.mark_in_game()

        ws_new = _make_ws_mock("newcomer")
        conn_new = WsConnection(
            ws_new, registry, room_service=rs, player_id="newcomer", player_name="新人"
        )
        registry.attach(conn_new, "newcomer")

        await conn_new._handle_raw(json.dumps({"type": "lobby_join"}))

        calls = ws_new.send.call_args_list
        frames = [json.loads(c.args[0]) for c in calls]
        error_frames = [f for f in frames if f.get("type") == "room_error"]
        assert len(error_frames) >= 1, f"期望 room_error，实际：{frames}"


# ---- 对局结束踢离线玩家 ----


async def test_match_ended_offline_player_leaves_after_grace(monkeypatch) -> None:
    """对局结束后，离线玩家经宽限期（缩短为 0.01s）被踢出；在线玩家保留。"""
    import asyncio as _asyncio

    import vibecraft.server.ws as ws_mod

    monkeypatch.setattr(ws_mod, "_LOBBY_LEAVE_GRACE_S", 0.01)

    registry = RoomRegistry(token="tok")
    rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]
    rs.room.join("pa", "alice")
    rs.room.join("pb", "bob")

    # 强制进入 in_game 态（模拟对局已开始）
    rs.room.set_ready("pb", True)
    rs.room.start("pa")  # 房主免准备
    rs.room.mark_in_game()

    # 对局结束 → mark_ended（slot 保留，ready 清零）
    rs.room.mark_ended()

    # alice 在线，bob 离线（未 attach）
    class _OnlineConn:
        async def send_text(self, frame: str) -> None:
            pass

        async def close(self, reason: str) -> None:
            pass

    registry.attach(_OnlineConn(), "pa")
    # pb 不 attach → connection_of("pb") is None

    # 触发 _on_match_ended（含广播 + 调度离线踢出）
    await rs._on_match_ended()

    # 宽限期前：bob slot 仍在
    assert rs.room.slot_of("pb") is not None, "宽限期前不应立即踢出"

    # 等宽限期（0.01s）过去后
    await _asyncio.sleep(0.05)

    # bob 被踢
    assert rs.room.slot_of("pb") is None, "宽限期后离线玩家应被踢出"
    # alice 保留
    assert rs.room.slot_of("pa") is not None, "在线玩家不应被踢出"


async def test_match_ended_online_player_slot_kept(monkeypatch) -> None:
    """对局结束后，在线玩家的 slot 宽限期后仍保留（重连检测到有连接）。"""
    import asyncio as _asyncio

    import vibecraft.server.ws as ws_mod

    monkeypatch.setattr(ws_mod, "_LOBBY_LEAVE_GRACE_S", 0.01)

    registry = RoomRegistry(token="tok")
    rs = RoomService(registry, orchestrator=_StubOrch())  # type: ignore[arg-type]
    rs.room.join("pa", "alice")
    rs.room.add_computer("pa", race="Random", difficulty="VeryHard")
    rs.room.set_ready("pa", True)
    rs.room.start("pa")
    rs.room.mark_in_game()
    rs.room.mark_ended()

    class _OnlineConn:
        async def send_text(self, frame: str) -> None:
            pass

        async def close(self, reason: str) -> None:
            pass

    registry.attach(_OnlineConn(), "pa")

    await rs._on_match_ended()
    await _asyncio.sleep(0.05)

    # alice 在线 → slot 保留
    assert rs.room.slot_of("pa") is not None, "在线玩家 slot 不应被踢出"
