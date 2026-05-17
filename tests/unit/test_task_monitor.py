"""TaskMonitor 单测 (P3 task_monitor skeleton + 2 reference checkers)。

覆盖:
1. attach + detach 工作
2. time_elapsed_since: 时间到了触发 done
3. time_elapsed_since: 时间没到不触发
4. unit_count_built_since: EventBus UNIT_CREATED 事件累加 counter
5. unit_count_built_since: counter 达 value 触发 done
6. timeout 触发 (独立于 done_when)
"""

from __future__ import annotations

from unittest.mock import MagicMock

from vibecraft.bot.event_bus import Event, EventBus, EventKind
from vibecraft.bot.task_monitor import TaskMonitor

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_game_state(game_time: float = 0.0) -> MagicMock:
    gs = MagicMock()
    gs.game_time = game_time
    return gs


def _make_unit_created_event(unit_type: str = "Sentry", ts: float = 10.0) -> Event:
    return Event(
        kind=EventKind.UNIT_CREATED,
        ts=ts,
        payload={"unit_type": unit_type},
        owner="own",
        unit_type=unit_type,
    )


# ---------------------------------------------------------------------------
# 1. attach + detach 工作
# ---------------------------------------------------------------------------


class TestAttachDetach:
    def test_attach_registers_state(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "time_elapsed_since", "seconds": 30, "ref": "directive_issued"}
        monitor.attach_directive("d1", done_when, issued_at=5.0, timeout_s=60)

        assert "d1" in monitor._issued_at
        assert monitor._issued_at["d1"] == 5.0
        assert monitor._timeout_s["d1"] == 60
        assert monitor._done_when["d1"] == done_when

    def test_attach_none_done_when_registers_empty_dict(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        monitor.attach_directive("d2", None, issued_at=0.0, timeout_s=None)

        assert "d2" in monitor._issued_at
        assert monitor._done_when["d2"] == {}

    def test_detach_clears_all_state(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "unit_count_built_since", "unit_type": "Sentry", "op": ">=", "value": 2}
        monitor.attach_directive("d3", done_when, issued_at=0.0, timeout_s=30)

        # 确认 sub_id 已注册
        assert len(monitor._sub_ids.get("d3", [])) == 1

        monitor.detach("d3")

        assert "d3" not in monitor._issued_at
        assert "d3" not in monitor._done_when
        assert "d3" not in monitor._timeout_s
        assert "d3" not in monitor._unit_built_counts
        assert "d3" not in monitor._sub_ids

    def test_detach_unsubscribes_from_event_bus(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "unit_count_built_since", "unit_type": "Probe", "op": ">=", "value": 1}
        monitor.attach_directive("d4", done_when, issued_at=0.0, timeout_s=None)

        # 订阅前 bus 里有 handler
        count_before = sum(len(subs) for subs in bus._subs.values())
        monitor.detach("d4")
        count_after = sum(len(subs) for subs in bus._subs.values())

        assert count_after < count_before

    def test_detach_nonexistent_is_noop(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        # 不应抛错
        monitor.detach("nonexistent")


# ---------------------------------------------------------------------------
# 2. time_elapsed_since: 时间到了触发 done
# ---------------------------------------------------------------------------


class TestTimeElapsedSinceDone:
    def test_triggers_when_elapsed_equals_threshold(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "time_elapsed_since", "seconds": 30, "ref": "directive_issued"}
        monitor.attach_directive("d1", done_when, issued_at=10.0, timeout_s=None)

        # game_time=40 → elapsed=30, 刚好等于 seconds=30
        gs = _make_game_state(game_time=40.0)
        completed = monitor.tick(now=40.0, game_state=gs)

        assert "d1" in completed

    def test_triggers_when_elapsed_exceeds_threshold(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "time_elapsed_since", "seconds": 60, "ref": "directive_issued"}
        monitor.attach_directive("d2", done_when, issued_at=0.0, timeout_s=None)

        gs = _make_game_state(game_time=90.0)
        completed = monitor.tick(now=90.0, game_state=gs)

        assert "d2" in completed

    def test_triggers_with_ref_game_start(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "time_elapsed_since", "seconds": 120, "ref": "game_start"}
        monitor.attach_directive("d3", done_when, issued_at=0.0, timeout_s=None)

        gs = _make_game_state(game_time=125.0)
        completed = monitor.tick(now=125.0, game_state=gs)

        assert "d3" in completed


# ---------------------------------------------------------------------------
# 3. time_elapsed_since: 时间没到不触发
# ---------------------------------------------------------------------------


class TestTimeElapsedSinceNotDone:
    def test_does_not_trigger_before_threshold(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "time_elapsed_since", "seconds": 90, "ref": "directive_issued"}
        monitor.attach_directive("d1", done_when, issued_at=5.0, timeout_s=None)

        # elapsed = 50 - 5 = 45 < 90
        gs = _make_game_state(game_time=50.0)
        completed = monitor.tick(now=50.0, game_state=gs)

        assert "d1" not in completed

    def test_does_not_trigger_exactly_before_threshold(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "time_elapsed_since", "seconds": 30, "ref": "directive_issued"}
        monitor.attach_directive("d2", done_when, issued_at=10.0, timeout_s=None)

        # elapsed = 39 - 10 = 29 < 30
        gs = _make_game_state(game_time=39.0)
        completed = monitor.tick(now=39.0, game_state=gs)

        assert "d2" not in completed


# ---------------------------------------------------------------------------
# 4. unit_count_built_since: EventBus UNIT_CREATED 事件累加 counter
# ---------------------------------------------------------------------------


class TestUnitCountBuiltCounter:
    def test_unit_created_event_increments_counter(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "unit_count_built_since", "unit_type": "Sentry", "op": ">=", "value": 3}
        monitor.attach_directive("d1", done_when, issued_at=5.0, timeout_s=None)

        assert monitor._unit_built_counts["d1"] == 0

        bus.publish(_make_unit_created_event(unit_type="Sentry", ts=10.0))
        assert monitor._unit_built_counts["d1"] == 1

        bus.publish(_make_unit_created_event(unit_type="Sentry", ts=11.0))
        assert monitor._unit_built_counts["d1"] == 2

    def test_different_unit_type_does_not_increment(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "unit_count_built_since", "unit_type": "Sentry", "op": ">=", "value": 2}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        # Stalker != Sentry → 不应累加
        bus.publish(_make_unit_created_event(unit_type="Stalker", ts=5.0))
        assert monitor._unit_built_counts["d1"] == 0

    def test_enemy_unit_created_does_not_increment(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "unit_count_built_since", "unit_type": "Zealot", "op": ">=", "value": 1}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        # owner=enemy → filter 拦掉
        enemy_event = Event(
            kind=EventKind.UNIT_CREATED,
            ts=5.0,
            payload={},
            owner="enemy",
            unit_type="Zealot",
        )
        bus.publish(enemy_event)
        assert monitor._unit_built_counts["d1"] == 0

    def test_unit_created_before_issued_at_does_not_increment(self):
        """ts < issued_at 的事件不应计入 (filter 按 ts 过滤)。"""
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "unit_count_built_since", "unit_type": "Probe", "op": ">=", "value": 1}
        monitor.attach_directive("d1", done_when, issued_at=20.0, timeout_s=None)

        # ts=15 < issued_at=20 → 不计
        old_event = Event(
            kind=EventKind.UNIT_CREATED,
            ts=15.0,
            payload={},
            owner="own",
            unit_type="Probe",
        )
        bus.publish(old_event)
        assert monitor._unit_built_counts["d1"] == 0


# ---------------------------------------------------------------------------
# 5. unit_count_built_since: counter 达 value 触发 done
# ---------------------------------------------------------------------------


class TestUnitCountBuiltDone:
    def test_triggers_when_count_reaches_value(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "unit_count_built_since", "unit_type": "Sentry", "op": ">=", "value": 2}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        # 发 2 个 UNIT_CREATED
        bus.publish(_make_unit_created_event("Sentry", ts=5.0))
        bus.publish(_make_unit_created_event("Sentry", ts=6.0))

        gs = _make_game_state(game_time=10.0)
        completed = monitor.tick(now=10.0, game_state=gs)

        assert "d1" in completed

    def test_does_not_trigger_when_count_below_value(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "unit_count_built_since", "unit_type": "Sentry", "op": ">=", "value": 3}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        bus.publish(_make_unit_created_event("Sentry", ts=5.0))
        bus.publish(_make_unit_created_event("Sentry", ts=6.0))
        # count=2 < value=3

        gs = _make_game_state(game_time=10.0)
        completed = monitor.tick(now=10.0, game_state=gs)

        assert "d1" not in completed

    def test_strict_gt_op(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "unit_count_built_since", "unit_type": "Zealot", "op": ">", "value": 2}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        # count=2, op=">", value=2 → 不触发
        bus.publish(_make_unit_created_event("Zealot", ts=2.0))
        bus.publish(_make_unit_created_event("Zealot", ts=3.0))

        gs = _make_game_state(game_time=5.0)
        assert "d1" not in monitor.tick(now=5.0, game_state=gs)

        # count=3 → 触发
        bus.publish(_make_unit_created_event("Zealot", ts=4.0))
        assert "d1" in monitor.tick(now=6.0, game_state=gs)


# ---------------------------------------------------------------------------
# 6. timeout 触发 (独立于 done_when)
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_timeout_triggers_regardless_of_done_when(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        # done_when 条件很难满足 (需要 9999 个单位)
        done_when = {"kind": "unit_count_built_since", "unit_type": "Sentry", "op": ">=", "value": 9999}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=60)

        # elapsed = 70 > timeout_s=60
        gs = _make_game_state(game_time=70.0)
        completed = monitor.tick(now=70.0, game_state=gs)

        assert "d1" in completed

    def test_timeout_not_triggered_before_deadline(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        monitor.attach_directive("d1", None, issued_at=10.0, timeout_s=30)

        # elapsed = 30 - 10 = 20 < 30
        gs = _make_game_state(game_time=30.0)
        completed = monitor.tick(now=30.0, game_state=gs)

        assert "d1" not in completed

    def test_timeout_none_never_triggers_timeout(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        monitor.attach_directive("d1", None, issued_at=0.0, timeout_s=None)

        # 经过很长时间
        gs = _make_game_state(game_time=99999.0)
        completed = monitor.tick(now=99999.0, game_state=gs)

        # done_when 也是 None → 不 trigger
        assert "d1" not in completed

    def test_timeout_triggers_exactly_at_deadline(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        monitor.attach_directive("d1", None, issued_at=0.0, timeout_s=90)

        gs = _make_game_state(game_time=90.0)
        completed = monitor.tick(now=90.0, game_state=gs)

        assert "d1" in completed

    def test_multiple_directives_only_expired_returned(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        monitor.attach_directive("d1", None, issued_at=0.0, timeout_s=60)
        monitor.attach_directive("d2", None, issued_at=0.0, timeout_s=120)

        gs = _make_game_state(game_time=70.0)
        completed = monitor.tick(now=70.0, game_state=gs)

        assert "d1" in completed
        assert "d2" not in completed
