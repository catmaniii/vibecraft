"""bot service 单测。

覆盖 (§9.2)：
- room_token 生成：URL-safe、随机、足够长
- RoomRegistry.verify：自己的 token 通过、错误 token 拒绝、显式 token
- attach 顶旧：首个连接不顶任何东西；第二个连接顶掉第一个
- detach：清当前 active；已被顶掉的旧连接延迟断开不误清新连接
"""

from __future__ import annotations

from voicecraft.server import RoomRegistry, generate_room_token


class _FakeConn:
    """实现 Connection Protocol 的测试桩。"""

    def __init__(self) -> None:
        self.closed_reason: str | None = None

    async def close(self, reason: str) -> None:
        self.closed_reason = reason


class TestRoomToken:
    def test_generate_is_url_safe(self) -> None:
        t = generate_room_token()
        assert all(c.isalnum() or c in "-_" for c in t)

    def test_generate_long_enough(self) -> None:
        assert len(generate_room_token()) >= 10

    def test_generate_is_random(self) -> None:
        assert generate_room_token() != generate_room_token()


class TestRoomRegistry:
    def test_verify_accepts_own_token(self) -> None:
        r = RoomRegistry()
        assert r.verify(r.token) is True

    def test_verify_rejects_wrong_token(self) -> None:
        r = RoomRegistry()
        assert r.verify("definitely-not-the-token") is False

    def test_explicit_token(self) -> None:
        r = RoomRegistry(token="fixed-token")
        assert r.token == "fixed-token"
        assert r.verify("fixed-token") is True

    def test_attach_first_connection_evicts_nothing(self) -> None:
        r = RoomRegistry()
        c1 = _FakeConn()
        assert r.attach(c1) is None
        assert r.active_connection is c1

    def test_attach_second_connection_evicts_first(self) -> None:
        r = RoomRegistry()
        c1, c2 = _FakeConn(), _FakeConn()
        r.attach(c1)
        evicted = r.attach(c2)
        assert evicted is c1
        assert r.active_connection is c2

    def test_detach_clears_active(self) -> None:
        r = RoomRegistry()
        c1 = _FakeConn()
        r.attach(c1)
        r.detach(c1)
        assert r.active_connection is None

    def test_detach_stale_connection_does_not_clear_new(self) -> None:
        r = RoomRegistry()
        c1, c2 = _FakeConn(), _FakeConn()
        r.attach(c1)
        r.attach(c2)  # c1 被顶掉
        r.detach(c1)  # c1 的延迟断开
        assert r.active_connection is c2  # 新连接没被误清
