"""EventBus 核心 pub/sub 单测(P1.0a) + bot lifecycle hook wire 测试(P1.0b)。"""

from __future__ import annotations

from unittest.mock import MagicMock

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


def _make_mock_unit(tag: int, alliance: int, type_name: str, x: float = 10.0, y: float = 20.0) -> MagicMock:
    """构造 mock sc2 Unit 对象。"""
    unit = MagicMock()
    unit.tag = tag
    unit.alliance = alliance
    unit.type_id.__str__ = lambda self: type_name
    unit.position.x = x
    unit.position.y = y
    return unit


def _make_mock_bot(bus: EventBus | None = None, with_named_spots: bool = False) -> MagicMock:
    """构造 mock bot_self：只含 _publish_xxx helpers 需要的最小接口。

    with_named_spots=True 时注入一个 named_spots mock，返回固定 area="enemy_natural"。
    """
    bot = MagicMock()
    bot.time = 12.5
    bot.event_bus = bus if bus is not None else EventBus()
    bot._enemy_units_dict = {}
    bot._own_units_dict = {}
    if with_named_spots:
        ns = MagicMock()
        ns.closest_named_spot.return_value = "enemy_natural"
        bot.named_spots = ns
    else:
        # 旧测试：不传 named_spots → getattr 返回 None → area=None
        del bot.named_spots  # MagicMock 默认会 auto-create attr，需显式删掉
    return bot


class TestBotEventWiring:
    """验 _VibeCraftProtossBot override 的 lifecycle hooks 正确 publish 到 EventBus (P1.0b)。"""

    def test_on_unit_created_own_unit_publishes(self):
        bus = EventBus()
        bot = _make_mock_bot(bus)
        received: list[Event] = []
        bus.subscribe(EventKind.UNIT_CREATED, received.append)

        unit = _make_mock_unit(tag=100, alliance=1, type_name="PROBE", x=50.0, y=60.0)

        from vibecraft.bot.auto_combat.protoss.bot import _publish_unit_created
        _publish_unit_created(bot, unit)

        assert len(received) == 1
        e = received[0]
        assert e.kind == EventKind.UNIT_CREATED
        assert e.owner == "own"
        assert e.unit_tag == 100
        assert e.position == (50.0, 60.0)

    def test_on_unit_created_enemy_unit_publishes(self):
        bus = EventBus()
        bot = _make_mock_bot(bus)
        received: list[Event] = []
        bus.subscribe(EventKind.UNIT_CREATED, received.append)

        unit = _make_mock_unit(tag=200, alliance=4, type_name="ZERGLING", x=5.0, y=5.0)

        from vibecraft.bot.auto_combat.protoss.bot import _publish_unit_created
        _publish_unit_created(bot, unit)

        assert len(received) == 1
        assert received[0].owner == "enemy"

    def test_on_unit_destroyed_publishes_with_enemy_lookup(self):
        bus = EventBus()
        bot = _make_mock_bot(bus)
        enemy_unit = _make_mock_unit(tag=200, alliance=4, type_name="STALKER", x=10.0, y=20.0)
        bot._enemy_units_dict = {200: enemy_unit}

        received: list[Event] = []
        bus.subscribe(EventKind.UNIT_DESTROYED, received.append)

        from vibecraft.bot.auto_combat.protoss.bot import _publish_unit_destroyed
        _publish_unit_destroyed(bot, 200)

        assert len(received) == 1
        e = received[0]
        assert e.kind == EventKind.UNIT_DESTROYED
        assert e.unit_tag == 200
        assert e.owner == "enemy"
        assert e.unit_type == "STALKER"
        # P5.D: area key must exist in payload (None when no named_spots)
        assert "area" in e.payload
        assert e.payload["area"] is None

    def test_on_unit_destroyed_unknown_tag_publishes_minimal(self):
        """tag 不在任何 dict(已被清)时,publish 仍发出,owner/type 为 None。"""
        bus = EventBus()
        bot = _make_mock_bot(bus)
        received: list[Event] = []
        bus.subscribe(EventKind.UNIT_DESTROYED, received.append)

        from vibecraft.bot.auto_combat.protoss.bot import _publish_unit_destroyed
        _publish_unit_destroyed(bot, 999)

        assert len(received) == 1
        assert received[0].unit_tag == 999
        assert received[0].owner is None
        # P5.D: area key still present even when unit not found
        assert "area" in received[0].payload

    def test_on_unit_destroyed_with_named_spots_fills_area(self):
        """P5.D: named_spots.closest_named_spot 被调用且 area 写入 payload。"""
        bus = EventBus()
        bot = _make_mock_bot(bus, with_named_spots=True)
        enemy_unit = _make_mock_unit(tag=201, alliance=4, type_name="ROACH", x=55.0, y=30.0)
        bot._enemy_units_dict = {201: enemy_unit}

        received: list[Event] = []
        bus.subscribe(EventKind.UNIT_DESTROYED, received.append)

        from vibecraft.bot.auto_combat.protoss.bot import _publish_unit_destroyed
        _publish_unit_destroyed(bot, 201)

        assert len(received) == 1
        e = received[0]
        assert e.payload["area"] == "enemy_natural"
        # 验证 closest_named_spot 被调用且传入了 position
        bot.named_spots.closest_named_spot.assert_called_once()

    def test_on_unit_type_changed_publishes(self):
        bus = EventBus()
        bot = _make_mock_bot(bus)
        received: list[Event] = []
        bus.subscribe(EventKind.UNIT_TYPE_CHANGED, received.append)

        unit = _make_mock_unit(tag=300, alliance=1, type_name="WARPGATE")
        previous_type = MagicMock()
        previous_type.__str__ = lambda self: "GATEWAY"

        from vibecraft.bot.auto_combat.protoss.bot import _publish_unit_type_changed
        _publish_unit_type_changed(bot, unit, previous_type)

        assert len(received) == 1
        e = received[0]
        assert e.kind == EventKind.UNIT_TYPE_CHANGED
        assert e.payload["previous_type"] == "GATEWAY"
        assert e.payload["current_type"] == "WARPGATE"

    def test_on_building_started_publishes(self):
        bus = EventBus()
        bot = _make_mock_bot(bus)
        received: list[Event] = []
        bus.subscribe(EventKind.BUILDING_STARTED, received.append)

        unit = _make_mock_unit(tag=400, alliance=1, type_name="GATEWAY", x=30.0, y=40.0)

        from vibecraft.bot.auto_combat.protoss.bot import _publish_building_started
        _publish_building_started(bot, unit)

        assert len(received) == 1
        e = received[0]
        assert e.kind == EventKind.BUILDING_STARTED
        assert e.owner == "own"
        assert e.unit_tag == 400
        assert e.position == (30.0, 40.0)

    def test_on_building_complete_publishes(self):
        bus = EventBus()
        bot = _make_mock_bot(bus)
        received: list[Event] = []
        bus.subscribe(EventKind.BUILDING_COMPLETE, received.append)

        unit = _make_mock_unit(tag=401, alliance=1, type_name="CYBERNETICSCORE")

        from vibecraft.bot.auto_combat.protoss.bot import _publish_building_complete
        _publish_building_complete(bot, unit)

        assert len(received) == 1
        assert received[0].kind == EventKind.BUILDING_COMPLETE

    def test_on_upgrade_complete_publishes(self):
        bus = EventBus()
        bot = _make_mock_bot(bus)
        received: list[Event] = []
        bus.subscribe(EventKind.UPGRADE_COMPLETE, received.append)

        # publisher 优先用 enum.name（python-sc2 真 UpgradeId 有 .name 属性）
        # 这样发出去的就是 "BLINKTECH" 不是 "UpgradeId.BLINKTECH"，便于 task_monitor 匹配
        upgrade = MagicMock()
        upgrade.name = "BLINKTECH"

        from vibecraft.bot.auto_combat.protoss.bot import _publish_upgrade_complete
        _publish_upgrade_complete(bot, upgrade)

        assert len(received) == 1
        e = received[0]
        assert e.kind == EventKind.UPGRADE_COMPLETE
        assert e.payload["upgrade_id"] == "BLINKTECH"

    def test_on_unit_took_damage_publishes(self):
        bus = EventBus()
        bot = _make_mock_bot(bus)
        received: list[Event] = []
        bus.subscribe(EventKind.UNIT_TOOK_DAMAGE, received.append)

        unit = _make_mock_unit(tag=500, alliance=1, type_name="STALKER")

        from vibecraft.bot.auto_combat.protoss.bot import _publish_unit_took_damage
        _publish_unit_took_damage(bot, unit, 35.5)

        assert len(received) == 1
        e = received[0]
        assert e.kind == EventKind.UNIT_TOOK_DAMAGE
        assert e.payload["amount"] == 35.5
        assert e.unit_tag == 500

    def test_on_enemy_unit_entered_vision_publishes(self):
        bus = EventBus()
        bot = _make_mock_bot(bus)
        received: list[Event] = []
        bus.subscribe(EventKind.ENEMY_UNIT_ENTERED_VISION, received.append)

        unit = _make_mock_unit(tag=600, alliance=4, type_name="ROACH", x=70.0, y=80.0)

        from vibecraft.bot.auto_combat.protoss.bot import _publish_enemy_unit_entered_vision
        _publish_enemy_unit_entered_vision(bot, unit)

        assert len(received) == 1
        e = received[0]
        assert e.kind == EventKind.ENEMY_UNIT_ENTERED_VISION
        assert e.owner == "enemy"
        assert e.unit_tag == 600

    def test_on_enemy_unit_left_vision_publishes(self):
        bus = EventBus()
        bot = _make_mock_bot(bus)
        received: list[Event] = []
        bus.subscribe(EventKind.ENEMY_UNIT_LEFT_VISION, received.append)

        from vibecraft.bot.auto_combat.protoss.bot import _publish_enemy_unit_left_vision
        _publish_enemy_unit_left_vision(bot, 700)

        assert len(received) == 1
        e = received[0]
        assert e.kind == EventKind.ENEMY_UNIT_LEFT_VISION
        assert e.unit_tag == 700
        assert e.owner == "enemy"
