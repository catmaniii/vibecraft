"""TelemetryLogger record 构造纯函数测试。"""
from __future__ import annotations

from types import SimpleNamespace

from vibecraft.bot.telemetry import (
    build_event_record,
    build_game_start_record,
    build_snapshot_record,
)


def _pt(x, y):
    return SimpleNamespace(x=x, y=y)


def test_event_record_building():
    rec = build_event_record(
        t=18.3, kind="building_started", unit="GATEWAY", tag=123, pos=_pt(94.4, 104.4)
    )
    assert rec == {
        "t": 18.3, "kind": "building_started", "unit": "GATEWAY",
        "tag": 123, "pos": [94.4, 104.4],
    }


def test_event_record_upgrade_no_pos():
    rec = build_event_record(t=211.0, kind="upgrade_complete", upgrade="WARPGATERESEARCH")
    assert rec == {"t": 211.0, "kind": "upgrade_complete", "upgrade": "WARPGATERESEARCH"}


def test_game_start_record():
    rec = build_game_start_record(
        t=0.0, home=_pt(127.5, 119.5), enemy_main=_pt(48.5, 28.5),
        natural=_pt(145.5, 98.5), active_recipe="dt_drop_iac", my_race="Protoss",
    )
    assert rec["kind"] == "game_start"
    assert rec["home"] == [127.5, 119.5]
    assert rec["enemy_main"] == [48.5, 28.5]
    assert rec["natural"] == [145.5, 98.5]
    assert rec["active_recipe"] == "dt_drop_iac"


def test_snapshot_record():
    rec = build_snapshot_record(
        t=120.0, supply_used=24, supply_cap=39, workers=22, army_supply=4,
        minerals=150, vespene=80, bases=2, army_center=_pt(100, 110),
        units={"STALKER": 2, "ZEALOT": 0},
        buildings={"GATEWAY": 3, "WARPGATE": 1},
        key_units={"WARPPRISM": [_pt(114, 115)]},
        active_recipe="dt_drop_iac",
    )
    assert rec["kind"] == "snapshot"
    assert rec["army_center"] == [100.0, 110.0]
    assert rec["units"] == {"STALKER": 2, "ZEALOT": 0}
    assert rec["buildings"] == {"GATEWAY": 3, "WARPGATE": 1}
    assert rec["key_units"] == {"WARPPRISM": [[114.0, 115.0]]}


def test_telemetry_logger_snapshot_throttle():
    """maybe_write_snapshot 每 ~2s 才真正写一次。"""
    from vibecraft.bot.telemetry import TelemetryLogger

    written: list[dict] = []
    tl = TelemetryLogger(sink_fn=written.append, snapshot_interval_s=2.0)
    snap = {"kind": "snapshot", "t": 0.0}
    tl.maybe_write_snapshot(now=0.0, record=snap)      # 第一次:写
    tl.maybe_write_snapshot(now=1.0, record=snap)      # 1s:节流跳过
    tl.maybe_write_snapshot(now=2.5, record=snap)      # 2.5s:写
    assert len(written) == 2


def test_telemetry_logger_event_passthrough():
    """write_event 直接落 sink,不节流。"""
    from vibecraft.bot.telemetry import TelemetryLogger

    written: list[dict] = []
    tl = TelemetryLogger(sink_fn=written.append)
    tl.write_event({"kind": "building_started", "t": 1.0})
    tl.write_event({"kind": "building_complete", "t": 2.0})
    assert len(written) == 2
