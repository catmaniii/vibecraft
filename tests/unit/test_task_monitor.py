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

        assert monitor._unit_built_counts["d1"]["Sentry"] == 0

        bus.publish(_make_unit_created_event(unit_type="Sentry", ts=10.0))
        assert monitor._unit_built_counts["d1"]["Sentry"] == 1

        bus.publish(_make_unit_created_event(unit_type="Sentry", ts=11.0))
        assert monitor._unit_built_counts["d1"]["Sentry"] == 2

    def test_different_unit_type_does_not_increment(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "unit_count_built_since", "unit_type": "Sentry", "op": ">=", "value": 2}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        # Stalker != Sentry → 不应累加
        bus.publish(_make_unit_created_event(unit_type="Stalker", ts=5.0))
        assert monitor._unit_built_counts["d1"]["Sentry"] == 0

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
        assert monitor._unit_built_counts["d1"]["Zealot"] == 0

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
        assert monitor._unit_built_counts["d1"]["Probe"] == 0


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
        # M3 fix: timeout 用 wall(monotonic),注入可控 fake
        wall = [0.0]
        monitor._monotonic = lambda: wall[0]
        done_when = {"kind": "unit_count_built_since", "unit_type": "Sentry", "op": ">=", "value": 9999}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=60)
        wall[0] = 70.0  # wall +70 > timeout 60
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
        wall = [0.0]
        monitor._monotonic = lambda: wall[0]
        monitor.attach_directive("d1", None, issued_at=0.0, timeout_s=90)
        wall[0] = 90.0
        gs = _make_game_state(game_time=90.0)
        completed = monitor.tick(now=90.0, game_state=gs)
        assert "d1" in completed

    def test_multiple_directives_only_expired_returned(self):
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        wall = [0.0]
        monitor._monotonic = lambda: wall[0]
        monitor.attach_directive("d1", None, issued_at=0.0, timeout_s=60)
        monitor.attach_directive("d2", None, issued_at=0.0, timeout_s=120)
        wall[0] = 70.0  # d1 过(60), d2 未过(120)
        gs = _make_game_state(game_time=70.0)
        completed = monitor.tick(now=70.0, game_state=gs)
        assert "d1" in completed
        assert "d2" not in completed


# ---------------------------------------------------------------------------
# P3.3: pydantic attach_directive (retrofit)
# ---------------------------------------------------------------------------


class TestPydanticAttach:
    def test_attach_accepts_pydantic_model(self) -> None:
        """attach_directive 传 pydantic obj 自动 model_dump，行为等同 dict。"""
        from vibecraft.directives.models import TimeElapsedSince

        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        dw = TimeElapsedSince(kind="time_elapsed_since", seconds=30, ref="directive_issued")
        monitor.attach_directive("d1", dw, issued_at=10.0, timeout_s=None)

        # 内部应存为 dict
        assert isinstance(monitor._done_when["d1"], dict)
        assert monitor._done_when["d1"]["kind"] == "time_elapsed_since"
        assert monitor._done_when["d1"]["seconds"] == 30

    def test_attach_pydantic_unit_count_subscribes_event(self) -> None:
        """pydantic UnitCountBuiltSince → 依然订阅 UNIT_CREATED。"""
        from vibecraft.directives.models import UnitCountBuiltSince

        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        dw = UnitCountBuiltSince(kind="unit_count_built_since", unit_type="Zealot", op=">=", value=1)
        monitor.attach_directive("d1", dw, issued_at=0.0, timeout_s=None)

        assert len(monitor._sub_ids["d1"]) == 1
        bus.publish(
            Event(kind=EventKind.UNIT_CREATED, ts=1.0, payload={}, owner="own", unit_type="Zealot")
        )
        assert monitor._unit_built_counts["d1"]["Zealot"] == 1


# ---------------------------------------------------------------------------
# P3.3: expansion_count checker
# ---------------------------------------------------------------------------


class TestExpansionCount:
    def _gs(self, townhall_count: int) -> MagicMock:
        gs = MagicMock()
        gs.townhalls.__len__ = MagicMock(return_value=townhall_count)
        return gs

    def test_triggers_when_expansion_count_met(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "expansion_count", "op": ">=", "value": 3}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        gs = self._gs(3)
        completed = monitor.tick(now=1.0, game_state=gs)
        assert "d1" in completed

    def test_not_triggered_when_count_below(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "expansion_count", "op": ">=", "value": 3}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        gs = self._gs(2)
        completed = monitor.tick(now=1.0, game_state=gs)
        assert "d1" not in completed

    def test_none_game_state_returns_false(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "expansion_count", "op": ">=", "value": 1}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        completed = monitor.tick(now=1.0, game_state=None)
        assert "d1" not in completed


# ---------------------------------------------------------------------------
# P3.3: tech_done checker
# ---------------------------------------------------------------------------


class TestTechDone:
    def test_triggers_when_upgrade_complete_event_fires(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "tech_done", "upgrade_id": "ProtossGroundWeaponsLevel1"}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        # 发 UPGRADE_COMPLETE 事件，payload.upgrade_id 匹配
        bus.publish(
            Event(
                kind=EventKind.UPGRADE_COMPLETE,
                ts=5.0,
                payload={"upgrade_id": "ProtossGroundWeaponsLevel1"},
            )
        )

        gs = _make_game_state(game_time=10.0)
        completed = monitor.tick(now=10.0, game_state=gs)
        assert "d1" in completed

    def test_not_triggered_before_upgrade_complete(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "tech_done", "upgrade_id": "ProtossGroundWeaponsLevel1"}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        # 没有发任何事件
        gs = _make_game_state(game_time=10.0)
        completed = monitor.tick(now=10.0, game_state=gs)
        assert "d1" not in completed

    def test_wrong_upgrade_id_does_not_trigger(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "tech_done", "upgrade_id": "Blink"}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        # 发的是 Charge，跟 Blink 完全不同（归一化也不匹配）
        bus.publish(
            Event(
                kind=EventKind.UPGRADE_COMPLETE,
                ts=5.0,
                payload={"upgrade_id": "CHARGE"},
            )
        )
        gs = _make_game_state(game_time=10.0)
        completed = monitor.tick(now=10.0, game_state=gs)
        assert "d1" not in completed

    def test_upgrade_id_normalization_canonical_matches_enum_name(self) -> None:
        """LLM 给 'ProtossGroundWeapons'，python-sc2 发 'PROTOSSGROUNDWEAPONSLEVEL1'，应匹配。"""
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        # LLM canonical 形式（无 LEVEL 后缀）
        done_when = {"kind": "tech_done", "upgrade_id": "ProtossGroundWeapons"}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        # publisher 发的形式（python-sc2 enum.name，带 LEVEL1）
        bus.publish(
            Event(
                kind=EventKind.UPGRADE_COMPLETE,
                ts=5.0,
                payload={"upgrade_id": "PROTOSSGROUNDWEAPONSLEVEL1"},
            )
        )
        gs = _make_game_state(game_time=10.0)
        completed = monitor.tick(now=10.0, game_state=gs)
        assert "d1" in completed

    def test_upgrade_id_normalization_handles_str_enum_prefix(self) -> None:
        """publisher 老版本可能发 'UpgradeId.X'，也要能匹配。"""
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "tech_done", "upgrade_id": "BlinkTech"}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        bus.publish(
            Event(
                kind=EventKind.UPGRADE_COMPLETE,
                ts=5.0,
                payload={"upgrade_id": "UpgradeId.BLINKTECH"},
            )
        )
        gs = _make_game_state(game_time=10.0)
        completed = monitor.tick(now=10.0, game_state=gs)
        assert "d1" in completed

    def test_upgrade_id_normalization_strips_tech_suffix(self) -> None:
        """LLM 给 'PsiStorm'，enum 是 'PSISTORMTECH'，应匹配。"""
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "tech_done", "upgrade_id": "PsiStorm"}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        bus.publish(
            Event(
                kind=EventKind.UPGRADE_COMPLETE,
                ts=5.0,
                payload={"upgrade_id": "PSISTORMTECH"},
            )
        )
        gs = _make_game_state(game_time=10.0)
        completed = monitor.tick(now=10.0, game_state=gs)
        assert "d1" in completed

    def test_detach_clears_tech_done_flag(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "tech_done", "upgrade_id": "Blink"}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)
        monitor.detach("d1")
        assert "d1" not in monitor._tech_done_flags


# ---------------------------------------------------------------------------
# P3.3: target_destroyed checker
# ---------------------------------------------------------------------------


class TestTargetDestroyed:
    def test_unit_type_target_destroyed_when_no_enemy_units(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "target_destroyed", "target_kind": "unit_type", "target_param": "Roach"}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        gs = MagicMock()
        gs.enemy_units.of_type.return_value.__len__ = MagicMock(return_value=0)
        completed = monitor.tick(now=5.0, game_state=gs)
        assert "d1" in completed

    def test_unit_type_not_destroyed_when_enemy_units_remain(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "target_destroyed", "target_kind": "unit_type", "target_param": "Roach"}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        gs = MagicMock()
        gs.enemy_units.of_type.return_value.__len__ = MagicMock(return_value=5)
        completed = monitor.tick(now=5.0, game_state=gs)
        assert "d1" not in completed

    def test_natural_target_kind_returns_false_in_p3(self) -> None:
        """P3 阶段 natural/third/main 坐标 poll 未实现，返回 False。"""
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "target_destroyed", "target_kind": "natural"}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        gs = _make_game_state(game_time=10.0)
        completed = monitor.tick(now=10.0, game_state=gs)
        assert "d1" not in completed

    def test_none_game_state_returns_false(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "target_destroyed", "target_kind": "unit_type", "target_param": "Roach"}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        completed = monitor.tick(now=5.0, game_state=None)
        assert "d1" not in completed


# ---------------------------------------------------------------------------
# P3.3: own_army_size_ratio checker
# ---------------------------------------------------------------------------


class TestOwnArmySizeRatio:
    def test_triggers_when_ratio_below_threshold(self) -> None:
        """初始 supply=20, 当前 supply=8 → ratio=0.4 <= 0.5 → done。"""
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "own_army_size_ratio", "op": "<=", "value": 0.5}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        # 首次 tick: 建立 snapshot = 20
        gs_initial = MagicMock()
        gs_initial.supply_army = 20
        monitor.tick(now=1.0, game_state=gs_initial)

        # 第二 tick: supply 降至 8
        gs_low = MagicMock()
        gs_low.supply_army = 8
        completed = monitor.tick(now=2.0, game_state=gs_low)
        assert "d1" in completed

    def test_not_triggered_when_ratio_above_threshold(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "own_army_size_ratio", "op": "<=", "value": 0.3}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        gs = MagicMock()
        gs.supply_army = 20
        monitor.tick(now=1.0, game_state=gs)  # snapshot = 20

        gs2 = MagicMock()
        gs2.supply_army = 15  # ratio = 0.75 > 0.3
        completed = monitor.tick(now=2.0, game_state=gs2)
        assert "d1" not in completed

    def test_none_game_state_returns_false(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "own_army_size_ratio", "op": "<=", "value": 0.5}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        completed = monitor.tick(now=1.0, game_state=None)
        assert "d1" not in completed


# ---------------------------------------------------------------------------
# P5.B: vision_acquired checker (ts-diff based, 修复 step-count bug)
# ---------------------------------------------------------------------------


class TestVisionAcquired:
    def test_triggers_when_continuously_visible_for_hold_seconds(self) -> None:
        """连续可见 >= hold_seconds 秒 → done。

        now=100 首次可见 (first_ts=100), now=105 elapsed=5 >= hold_seconds=5 → done。
        """
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "vision_acquired", "area": "natural", "hold_seconds": 5.0}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        gs = MagicMock()
        gs.is_visible.return_value = True

        # tick 1: now=100 → first_ts=100, elapsed=0 → not done
        result = monitor.tick(now=100.0, game_state=gs)
        assert "d1" not in result

        # tick 2: now=105 → elapsed=5 >= 5 → done
        result = monitor.tick(now=105.0, game_state=gs)
        assert "d1" in result

    def test_not_triggered_when_visible_but_insufficient_duration(self) -> None:
        """连续可见 < hold_seconds 秒 → not done。"""
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "vision_acquired", "area": "natural", "hold_seconds": 5.0}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        gs = MagicMock()
        gs.is_visible.return_value = True

        # tick 1: now=100 → first_ts=100, elapsed=0
        result = monitor.tick(now=100.0, game_state=gs)
        assert "d1" not in result

        # tick 2: now=104 → elapsed=4 < 5 → not done
        result = monitor.tick(now=104.0, game_state=gs)
        assert "d1" not in result

    def test_vision_interrupted_resets_counter(self) -> None:
        """可见 → 不可见 → 可见: counter 需重置，从头计时。"""
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "vision_acquired", "area": "natural", "hold_seconds": 5.0}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        gs_visible = MagicMock()
        gs_visible.is_visible.return_value = True

        gs_hidden = MagicMock()
        gs_hidden.is_visible.return_value = False

        # tick 1: now=100 → first_ts=100
        monitor.tick(now=100.0, game_state=gs_visible)
        assert monitor._vision_first_visible_ts["d1"] == 100.0

        # tick 2: now=103 → 不可见 → reset
        monitor.tick(now=103.0, game_state=gs_hidden)
        assert monitor._vision_first_visible_ts["d1"] is None

        # tick 3: now=107 → 再次可见 → first_ts=107 (重新开始)
        result = monitor.tick(now=107.0, game_state=gs_visible)
        assert monitor._vision_first_visible_ts["d1"] == 107.0
        # elapsed=0 → not done (hold_seconds=5)
        assert "d1" not in result

        # tick 4: now=112 → elapsed=5 → done
        result = monitor.tick(now=112.0, game_state=gs_visible)
        assert "d1" in result

    def test_detach_clears_vision_first_visible_ts(self) -> None:
        """detach 后 _vision_first_visible_ts 无残留；再次 attach 干净。"""
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "vision_acquired", "area": "natural", "hold_seconds": 5.0}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        gs = MagicMock()
        gs.is_visible.return_value = True
        monitor.tick(now=100.0, game_state=gs)  # first_ts 设为 100.0

        monitor.detach("d1")
        assert "d1" not in monitor._vision_first_visible_ts

        # 再次 attach → first_ts 应为 None (干净状态)
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)
        assert monitor._vision_first_visible_ts["d1"] is None

    def test_unsupported_area_name_returns_false(self) -> None:
        """area 不在白名单 → named_spot 解析返回 None → not done。"""
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "vision_acquired", "area": "unknown_spot", "hold_seconds": 1.0}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        gs = MagicMock()
        gs.is_visible.return_value = True

        # 多次 tick 经过足够时间也不 done（area 解析失败）
        result = monitor.tick(now=100.0, game_state=gs)
        result = monitor.tick(now=110.0, game_state=gs)
        assert "d1" not in result

    def test_none_game_state_returns_false(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "vision_acquired", "area": "natural", "hold_seconds": 1.0}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        completed = monitor.tick(now=100.0, game_state=None)
        assert "d1" not in completed


# ---------------------------------------------------------------------------
# P3.3: enemy_killed_in_area checker
# ---------------------------------------------------------------------------


class TestEnemyKilledInArea:
    def test_triggers_when_kill_count_met(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {
            "kind": "enemy_killed_in_area",
            "area": "natural",
            "unit_type": "Roach",
            "op": ">=",
            "value": 3,
        }
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        # 发 3 个 UNIT_DESTROYED (enemy, Roach, area=natural)
        for _ in range(3):
            bus.publish(
                Event(
                    kind=EventKind.UNIT_DESTROYED,
                    ts=5.0,
                    payload={"area": "natural"},
                    owner="enemy",
                    unit_type="Roach",
                )
            )

        gs = _make_game_state(game_time=10.0)
        completed = monitor.tick(now=10.0, game_state=gs)
        assert "d1" in completed

    def test_not_triggered_when_kill_count_below(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {
            "kind": "enemy_killed_in_area",
            "area": "natural",
            "unit_type": "Roach",
            "op": ">=",
            "value": 5,
        }
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        for _ in range(2):
            bus.publish(
                Event(
                    kind=EventKind.UNIT_DESTROYED,
                    ts=5.0,
                    payload={"area": "natural"},
                    owner="enemy",
                    unit_type="Roach",
                )
            )

        gs = _make_game_state(game_time=10.0)
        completed = monitor.tick(now=10.0, game_state=gs)
        assert "d1" not in completed

    def test_own_unit_destroyed_does_not_increment(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {
            "kind": "enemy_killed_in_area",
            "area": "natural",
            "unit_type": "Roach",
            "op": ">=",
            "value": 1,
        }
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        # owner="own" → filter 拦掉
        bus.publish(
            Event(
                kind=EventKind.UNIT_DESTROYED,
                ts=5.0,
                payload={"area": "natural"},
                owner="own",
                unit_type="Roach",
            )
        )
        assert monitor._enemy_killed_counts.get("d1", 0) == 0

    def test_wrong_area_does_not_increment(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {
            "kind": "enemy_killed_in_area",
            "area": "natural",
            "unit_type": "Roach",
            "op": ">=",
            "value": 1,
        }
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        # area="third" != "natural" → 不计
        bus.publish(
            Event(
                kind=EventKind.UNIT_DESTROYED,
                ts=5.0,
                payload={"area": "third"},
                owner="enemy",
                unit_type="Roach",
            )
        )
        assert monitor._enemy_killed_counts.get("d1", 0) == 0


# ---------------------------------------------------------------------------
# P3.3: any_of / all_of 复合 checker
# ---------------------------------------------------------------------------


class TestAnyOf:
    def test_any_of_triggers_when_one_condition_met(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {
            "kind": "any_of",
            "conditions": [
                {"kind": "time_elapsed_since", "seconds": 999, "ref": "game_start"},
                {"kind": "time_elapsed_since", "seconds": 10, "ref": "game_start"},
            ],
        }
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        gs = _make_game_state(game_time=15.0)
        completed = monitor.tick(now=15.0, game_state=gs)
        assert "d1" in completed

    def test_any_of_not_triggered_when_no_condition_met(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {
            "kind": "any_of",
            "conditions": [
                {"kind": "time_elapsed_since", "seconds": 999, "ref": "game_start"},
                {"kind": "time_elapsed_since", "seconds": 100, "ref": "game_start"},
            ],
        }
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        gs = _make_game_state(game_time=15.0)
        completed = monitor.tick(now=15.0, game_state=gs)
        assert "d1" not in completed


# ---------------------------------------------------------------------------
# P5.C: vision_acquired 用 bot.named_spots registry 路径
# ---------------------------------------------------------------------------


class TestVisionAcquiredWithRegistry:
    def test_registry_path_used_when_named_spots_present(self) -> None:
        """game_state.named_spots 是 NamedSpotRegistry 实例时，走 registry.resolve 路径。

        用 patch 替换 NamedSpotRegistry.resolve 方法，返回一个 sentinel 点；
        is_visible 返回 True → first_ts 设置 → elapsed 满足 → done。
        """
        from unittest.mock import MagicMock, patch

        from vibecraft.bot.named_spot import NamedSpotRegistry

        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "vision_acquired", "area": "natural", "hold_seconds": 5.0}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        # 构造一个对象，named_spots 是真正的 NamedSpotRegistry（以通过 isinstance 检查）
        registry = NamedSpotRegistry()
        fake_point = MagicMock()

        mock_bot = MagicMock(spec=["named_spots", "is_visible"])
        mock_bot.named_spots = registry
        mock_bot.is_visible.return_value = True

        with patch.object(registry, "resolve", return_value=fake_point) as mock_resolve:
            # tick 1: now=100 → first_ts=100, elapsed=0 → not done
            result = monitor.tick(now=100.0, game_state=mock_bot)
            assert "d1" not in result
            # resolve 应被调用，传 area name 和 game_state
            mock_resolve.assert_called_with("natural", mock_bot)

            # tick 2: now=105 → elapsed=5 >= hold_seconds=5 → done
            result = monitor.tick(now=105.0, game_state=mock_bot)
            assert "d1" in result

    def test_registry_returns_none_causes_false(self) -> None:
        """registry.resolve 返回 None (spot 不可解析) → checker 返回 False。"""
        from unittest.mock import MagicMock, patch

        from vibecraft.bot.named_spot import NamedSpotRegistry

        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "vision_acquired", "area": "enemy_third", "hold_seconds": 1.0}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        registry = NamedSpotRegistry()
        mock_bot = MagicMock(spec=["named_spots", "is_visible"])
        mock_bot.named_spots = registry
        mock_bot.is_visible.return_value = True

        with patch.object(registry, "resolve", return_value=None):
            result = monitor.tick(now=100.0, game_state=mock_bot)
            assert "d1" not in result
            # 因为 point=None，is_visible 不该被调用（resolve 失败短路）
            mock_bot.is_visible.assert_not_called()

    def test_fallback_when_no_named_spots_attr(self) -> None:
        """game_state 无真实 named_spots 时 fallback 到 P3 白名单逻辑，向后兼容。

        用 spec 限定 MagicMock 属性，确保 named_spots 不存在（getattr 返回 None）。
        """
        from unittest.mock import MagicMock

        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "vision_acquired", "area": "natural", "hold_seconds": 5.0}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        # spec 限定不含 named_spots → getattr(..., "named_spots", None) 返回 None
        gs = MagicMock(spec=["game_time", "is_visible"])
        gs.is_visible.return_value = True

        # P3 fallback: natural 在白名单 → 返回 placeholder → is_visible 被调用
        # tick 1: now=100 → first_ts 设 100
        result = monitor.tick(now=100.0, game_state=gs)
        assert "d1" not in result

        # tick 2: now=105 → elapsed=5 >= 5 → done（P3 placeholder 让 is_visible 被调）
        result = monitor.tick(now=105.0, game_state=gs)
        assert "d1" in result


class TestAllOf:
    def test_all_of_triggers_when_all_conditions_met(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {
            "kind": "all_of",
            "conditions": [
                {"kind": "time_elapsed_since", "seconds": 10, "ref": "game_start"},
                {"kind": "time_elapsed_since", "seconds": 20, "ref": "game_start"},
            ],
        }
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        gs = _make_game_state(game_time=25.0)
        completed = monitor.tick(now=25.0, game_state=gs)
        assert "d1" in completed

    def test_all_of_not_triggered_when_one_condition_not_met(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {
            "kind": "all_of",
            "conditions": [
                {"kind": "time_elapsed_since", "seconds": 10, "ref": "game_start"},
                {"kind": "time_elapsed_since", "seconds": 999, "ref": "game_start"},  # 未满足
            ],
        }
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        gs = _make_game_state(game_time=25.0)
        completed = monitor.tick(now=25.0, game_state=gs)
        assert "d1" not in completed


# ---------------------------------------------------------------------------
# P5.G: 6 个 checker 真实 mock bot 路径
# ---------------------------------------------------------------------------


def _make_bot_with_registry(named_spots_resolve_return: object = None) -> MagicMock:
    """构造带 NamedSpotRegistry 的 mock bot，供 target_destroyed 路径用。"""
    from vibecraft.bot.named_spot import NamedSpotRegistry

    registry = NamedSpotRegistry()
    bot = MagicMock()
    bot.named_spots = registry
    return bot, registry


class TestExpansionCountMockBot:
    """expansion_count checker 用 mock bot.townhalls.amount。"""

    def _make_bot(self, amount: int) -> MagicMock:
        bot = MagicMock()
        bot.townhalls.amount = amount
        # 让 len() 也能用
        bot.townhalls.__len__ = MagicMock(return_value=amount)
        return bot

    def test_triggers_when_townhalls_amount_meets_condition(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "expansion_count", "op": ">=", "value": 3}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        bot = self._make_bot(3)
        completed = monitor.tick(now=1.0, game_state=bot)
        assert "d1" in completed

    def test_not_triggered_when_townhalls_below_value(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "expansion_count", "op": ">=", "value": 3}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        bot = self._make_bot(1)
        completed = monitor.tick(now=1.0, game_state=bot)
        assert "d1" not in completed

    def test_exact_boundary_with_eq_op(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "expansion_count", "op": "==", "value": 2}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        bot = self._make_bot(2)
        completed = monitor.tick(now=1.0, game_state=bot)
        assert "d1" in completed

        bot3 = self._make_bot(3)
        monitor.attach_directive("d2", done_when, issued_at=0.0, timeout_s=None)
        completed2 = monitor.tick(now=2.0, game_state=bot3)
        assert "d2" not in completed2


class TestTechDoneMockBot:
    """tech_done checker 用 mock bot + EventBus UPGRADE_COMPLETE。"""

    def test_triggers_after_upgrade_complete_event(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "tech_done", "upgrade_id": "Blink"}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        # 发 UPGRADE_COMPLETE 事件
        bus.publish(
            Event(
                kind=EventKind.UPGRADE_COMPLETE,
                ts=10.0,
                payload={"upgrade_id": "Blink"},
            )
        )

        bot = MagicMock()
        bot.game_time = 15.0
        completed = monitor.tick(now=15.0, game_state=bot)
        assert "d1" in completed

    def test_not_triggered_before_event(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "tech_done", "upgrade_id": "Blink"}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        bot = MagicMock()
        completed = monitor.tick(now=5.0, game_state=bot)
        assert "d1" not in completed

    def test_wrong_upgrade_id_does_not_trigger(self) -> None:
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "tech_done", "upgrade_id": "Blink"}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        bus.publish(
            Event(
                kind=EventKind.UPGRADE_COMPLETE,
                ts=5.0,
                payload={"upgrade_id": "ChronoBoostEnergyCost"},
            )
        )
        bot = MagicMock()
        completed = monitor.tick(now=10.0, game_state=bot)
        assert "d1" not in completed


class TestTargetDestroyedP5:
    """target_destroyed P5 路径: natural/third/main 用 NamedSpotRegistry + enemy_structures。"""

    def _make_bot_with_registry(self, spot_pos: object) -> tuple[MagicMock, object]:
        """返回 (bot, registry)，registry.resolve("enemy_natural", bot) 返回 spot_pos。"""
        from vibecraft.bot.named_spot import NamedSpotRegistry

        registry = NamedSpotRegistry()
        bot = MagicMock()
        bot.named_spots = registry
        return bot, registry

    def test_natural_target_destroyed_when_no_enemy_structures(self) -> None:
        """enemy_natural resolve 到 Point2(50,50)，enemy_structures.closer_than 返回空 → True。"""
        from unittest.mock import MagicMock, patch

        from vibecraft.bot.named_spot import NamedSpotRegistry

        registry = NamedSpotRegistry()
        fake_point = MagicMock()
        fake_point.x = 50.0
        fake_point.y = 50.0

        bot = MagicMock()
        bot.named_spots = registry
        # closer_than 返回空集合 (amount 0, len 0)
        bot.enemy_structures.closer_than.return_value.__len__ = MagicMock(return_value=0)

        with patch.object(registry, "resolve", return_value=fake_point):
            bus = EventBus()
            monitor = TaskMonitor(board=None, event_bus=bus)
            done_when = {"kind": "target_destroyed", "target_kind": "natural"}
            monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

            completed = monitor.tick(now=1.0, game_state=bot)
            assert "d1" in completed
            bot.enemy_structures.closer_than.assert_called_once_with(8, fake_point)

    def test_natural_not_destroyed_when_enemy_structures_remain(self) -> None:
        """enemy_structures.closer_than 返回非空 → False (敌方建筑还在)。"""
        from unittest.mock import MagicMock, patch

        from vibecraft.bot.named_spot import NamedSpotRegistry

        registry = NamedSpotRegistry()
        fake_point = MagicMock()

        bot = MagicMock()
        bot.named_spots = registry
        bot.enemy_structures.closer_than.return_value.__len__ = MagicMock(return_value=3)

        with patch.object(registry, "resolve", return_value=fake_point):
            bus = EventBus()
            monitor = TaskMonitor(board=None, event_bus=bus)
            done_when = {"kind": "target_destroyed", "target_kind": "natural"}
            monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

            completed = monitor.tick(now=1.0, game_state=bot)
            assert "d1" not in completed

    def test_resolve_none_returns_false(self) -> None:
        """registry.resolve 返回 None (spot 不可解析) → False。"""
        from unittest.mock import MagicMock, patch

        from vibecraft.bot.named_spot import NamedSpotRegistry

        registry = NamedSpotRegistry()
        bot = MagicMock()
        bot.named_spots = registry

        with patch.object(registry, "resolve", return_value=None):
            bus = EventBus()
            monitor = TaskMonitor(board=None, event_bus=bus)
            done_when = {"kind": "target_destroyed", "target_kind": "third"}
            monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

            completed = monitor.tick(now=1.0, game_state=bot)
            assert "d1" not in completed

    def test_target_kind_main_uses_enemy_main_spot(self) -> None:
        """target_kind='main' → resolve 传 'enemy_main' 而非 'main'。"""
        from unittest.mock import MagicMock, patch

        from vibecraft.bot.named_spot import NamedSpotRegistry

        registry = NamedSpotRegistry()
        fake_point = MagicMock()
        bot = MagicMock()
        bot.named_spots = registry
        bot.enemy_structures.closer_than.return_value.__len__ = MagicMock(return_value=0)

        with patch.object(registry, "resolve", return_value=fake_point) as mock_resolve:
            bus = EventBus()
            monitor = TaskMonitor(board=None, event_bus=bus)
            done_when = {"kind": "target_destroyed", "target_kind": "main"}
            monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

            monitor.tick(now=1.0, game_state=bot)
            # 必须用 "enemy_main" 而不是 "main"
            mock_resolve.assert_called_once_with("enemy_main", bot)

    def test_unit_type_target_with_mock_bot(self) -> None:
        """unit_type target_kind 路径：enemy_units.of_type 返回空 → True。"""
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "target_destroyed", "target_kind": "unit_type", "target_param": "Stalker"}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        bot = MagicMock()
        bot.enemy_units.of_type.return_value.__len__ = MagicMock(return_value=0)
        completed = monitor.tick(now=1.0, game_state=bot)
        assert "d1" in completed

        # 还有 stalker 时
        monitor.attach_directive("d2", done_when, issued_at=0.0, timeout_s=None)
        bot2 = MagicMock()
        bot2.enemy_units.of_type.return_value.__len__ = MagicMock(return_value=4)
        completed2 = monitor.tick(now=2.0, game_state=bot2)
        assert "d2" not in completed2


class TestOwnArmySizeRatioMockBot:
    """own_army_size_ratio checker 用 mock bot.supply_army。"""

    def test_triggers_when_army_ratio_below_threshold(self) -> None:
        """初始 supply=20, 2nd tick supply=10 → ratio=0.5 <= 0.6 → done。"""
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "own_army_size_ratio", "op": "<=", "value": 0.6}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        bot1 = MagicMock()
        bot1.supply_army = 20
        monitor.tick(now=1.0, game_state=bot1)  # snapshot = 20

        bot2 = MagicMock()
        bot2.supply_army = 10  # ratio = 10/20 = 0.5 <= 0.6
        completed = monitor.tick(now=2.0, game_state=bot2)
        assert "d1" in completed

    def test_not_triggered_when_army_ratio_above_threshold(self) -> None:
        """初始 supply=20, supply=18 → ratio=0.9 > 0.6 → not done。"""
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "own_army_size_ratio", "op": "<=", "value": 0.6}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        bot1 = MagicMock()
        bot1.supply_army = 20
        monitor.tick(now=1.0, game_state=bot1)

        bot2 = MagicMock()
        bot2.supply_army = 18  # ratio = 0.9 > 0.6
        completed = monitor.tick(now=2.0, game_state=bot2)
        assert "d1" not in completed

    def test_ratio_computed_from_first_tick_snapshot(self) -> None:
        """snapshot 建立于首次 tick，后续对比 current/initial。"""
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {"kind": "own_army_size_ratio", "op": "<=", "value": 0.5}
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        bot = MagicMock()
        bot.supply_army = 40
        monitor.tick(now=1.0, game_state=bot)
        assert monitor._initial_army_supply.get("d1") == 40.0

        # supply=20 → ratio=0.5 <= 0.5 → done
        bot2 = MagicMock()
        bot2.supply_army = 20
        completed = monitor.tick(now=2.0, game_state=bot2)
        assert "d1" in completed


class TestEnemyKilledInAreaMockBot:
    """enemy_killed_in_area checker 用 UNIT_DESTROYED event + area/unit_type filter。"""

    def test_triggers_when_kill_count_meets_value(self) -> None:
        """3 个 Probe UNIT_DESTROYED 事件 area=enemy_main → done (value=3)。"""
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {
            "kind": "enemy_killed_in_area",
            "area": "enemy_main",
            "unit_type": "Probe",
            "op": ">=",
            "value": 3,
        }
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        for _ in range(3):
            bus.publish(
                Event(
                    kind=EventKind.UNIT_DESTROYED,
                    ts=5.0,
                    payload={"area": "enemy_main"},
                    owner="enemy",
                    unit_type="Probe",
                )
            )

        bot = MagicMock()
        completed = monitor.tick(now=10.0, game_state=bot)
        assert "d1" in completed

    def test_not_triggered_when_kill_count_below_value(self) -> None:
        """只有 2 个击杀事件 → not done (value=3)。"""
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {
            "kind": "enemy_killed_in_area",
            "area": "enemy_main",
            "unit_type": "Probe",
            "op": ">=",
            "value": 3,
        }
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        for _ in range(2):
            bus.publish(
                Event(
                    kind=EventKind.UNIT_DESTROYED,
                    ts=5.0,
                    payload={"area": "enemy_main"},
                    owner="enemy",
                    unit_type="Probe",
                )
            )

        bot = MagicMock()
        completed = monitor.tick(now=10.0, game_state=bot)
        assert "d1" not in completed

    def test_area_mismatch_does_not_count(self) -> None:
        """3 个击杀事件 area 不匹配 → counter 不累加 → not done。"""
        bus = EventBus()
        monitor = TaskMonitor(board=None, event_bus=bus)
        done_when = {
            "kind": "enemy_killed_in_area",
            "area": "enemy_main",
            "unit_type": "Probe",
            "op": ">=",
            "value": 3,
        }
        monitor.attach_directive("d1", done_when, issued_at=0.0, timeout_s=None)

        for _ in range(3):
            bus.publish(
                Event(
                    kind=EventKind.UNIT_DESTROYED,
                    ts=5.0,
                    payload={"area": "enemy_natural"},  # 不匹配
                    owner="enemy",
                    unit_type="Probe",
                )
            )

        bot = MagicMock()
        completed = monitor.tick(now=10.0, game_state=bot)
        assert "d1" not in completed
        assert monitor._enemy_killed_counts.get("d1", 0) == 0
