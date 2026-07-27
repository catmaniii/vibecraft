"""RoomRegistry 多连接化单测（阶段 0 多人联网，Task 4）。

覆盖：
- attach 同 player_id 顶旧、不同 player_id 共存
- detach 已被顶掉的旧连接不误清新连接
- broadcast 发给所有活跃连接
"""

from __future__ import annotations

from vibecraft.server.tokens import RoomRegistry


class _FakeConn:
    """实现 Connection Protocol 的测试桩（含 send_text）。"""

    def __init__(self) -> None:
        self.closed_reason: str | None = None
        self.sent: list[str] = []

    async def close(self, reason: str) -> None:
        self.closed_reason = reason

    async def send_text(self, frame: str) -> None:
        self.sent.append(frame)


# asyncio_mode = "auto"（见 pyproject.toml），async def 自动作为协程用例运行


async def test_attach_evicts_same_player_only() -> None:
    """同 player_id 第二次 attach 顶旧；不同 player_id 互不干扰。"""
    reg = RoomRegistry(token="t")
    a1, a2, b = _FakeConn(), _FakeConn(), _FakeConn()
    assert reg.attach(a1, player_id="pa") is None
    assert reg.attach(b, player_id="pb") is None  # 不同玩家共存，不顶旧
    assert reg.attach(a2, player_id="pa") is a1  # 同玩家顶旧，返回被顶掉的
    assert reg.connection_of("pa") is a2  # 新连接生效
    assert reg.connection_of("pb") is b  # 另一玩家未受影响


async def test_detach_only_clears_current() -> None:
    """已被顶掉的旧连接迟到断开，不应误清掉当前新连接。"""
    reg = RoomRegistry(token="t")
    a1, a2 = _FakeConn(), _FakeConn()
    reg.attach(a1, player_id="pa")
    reg.attach(a2, player_id="pa")  # a1 被顶掉
    reg.detach(a1)  # a1 延迟断开
    assert reg.connection_of("pa") is a2  # a2 不受影响


async def test_broadcast_sends_to_all() -> None:
    """broadcast 给所有活跃连接发同一帧。"""
    reg = RoomRegistry(token="t")
    a, b = _FakeConn(), _FakeConn()
    reg.attach(a, player_id="pa")
    reg.attach(b, player_id="pb")
    await reg.broadcast('{"type":"room_state"}')
    assert a.sent == ['{"type":"room_state"}']
    assert b.sent == ['{"type":"room_state"}']
