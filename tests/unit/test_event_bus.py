"""EventBus 核心 pub/sub 单测(P1.0a)。"""

from __future__ import annotations

from vibecraft.bot.event_bus import Event, EventBus, EventKind


def _make_event(kind: EventKind = EventKind.UNIT_DESTROYED, **kw) -> Event:
    """构造 Event 的 helper,只填关键字段,其它给默认值。"""
    return Event(
        kind=kind,
        ts=kw.get("ts", 10.0),
        payload=kw.get("payload", {}),
        owner=kw.get("owner"),
        unit_tag=kw.get("unit_tag"),
        unit_type=kw.get("unit_type"),
        position=kw.get("position"),
    )


class TestEventBus:
    def test_subscribe_publish_handler_called(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(EventKind.UNIT_DESTROYED, received.append)
        bus.publish(_make_event(unit_tag=42))
        assert len(received) == 1
        assert received[0].unit_tag == 42

    def test_publish_only_matching_kind(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(EventKind.UNIT_DESTROYED, received.append)
        bus.publish(_make_event(kind=EventKind.UNIT_CREATED))
        assert received == []

    def test_filter_excludes_event(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(
            EventKind.UNIT_DESTROYED,
            received.append,
            filter=lambda e: e.owner == "enemy",
        )
        bus.publish(_make_event(owner="own"))
        bus.publish(_make_event(owner="enemy", unit_tag=99))
        assert len(received) == 1
        assert received[0].unit_tag == 99

    def test_unsubscribe_removes_handler(self):
        bus = EventBus()
        received: list[Event] = []
        sub_id = bus.subscribe(EventKind.UNIT_DESTROYED, received.append)
        bus.publish(_make_event())
        bus.unsubscribe(sub_id)
        bus.publish(_make_event())
        assert len(received) == 1  # 只收到 unsubscribe 前那条

    def test_handler_exception_does_not_break_other_handlers(self):
        bus = EventBus()
        called: list[str] = []

        def bad_handler(e: Event) -> None:
            called.append("bad")
            raise RuntimeError("boom")

        def good_handler(e: Event) -> None:
            called.append("good")

        bus.subscribe(EventKind.UNIT_DESTROYED, bad_handler)
        bus.subscribe(EventKind.UNIT_DESTROYED, good_handler)
        bus.publish(_make_event())
        # 两个 handler 都被 call,bad 抛错不影响 good
        assert called == ["bad", "good"]

    def test_multiple_subscribers_all_get_event(self):
        bus = EventBus()
        a, b = [], []
        bus.subscribe(EventKind.UNIT_DESTROYED, a.append)
        bus.subscribe(EventKind.UNIT_DESTROYED, b.append)
        bus.publish(_make_event())
        assert len(a) == 1 and len(b) == 1
