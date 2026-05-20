"""Verifier: telemetry × spec → pass/fail。"""

from __future__ import annotations

from vibecraft.build_acceptance.spec import AcceptanceSpec
from vibecraft.build_acceptance.verifier import (
    CheckResult,
    Report,
    aggregate_reports,
    verify,
)

_GAME_START = {
    "kind": "game_start",
    "t": 0.0,
    "home": [127.5, 119.5],
    "enemy_main": [48.5, 28.5],
    "natural": [145.5, 98.5],
}


def _spec(checks):
    return AcceptanceSpec.model_validate(
        {"strategy_id": "t", "my_race": "Protoss", "checks": checks}
    )


def test_building_started_by_pass():
    tel = [_GAME_START, {"kind": "building_started", "t": 20.0, "unit": "GATEWAY"}]
    spec = _spec([{"id": "g1", "type": "building_started", "unit": "GATEWAY", "by": "0:35"}])
    report = verify(tel, spec, opponent="veryeasy")
    assert report.passed
    assert report.results[0].ok


def test_building_started_by_fail_too_late():
    tel = [_GAME_START, {"kind": "building_started", "t": 50.0, "unit": "GATEWAY"}]
    spec = _spec([{"id": "g1", "type": "building_started", "unit": "GATEWAY", "by": "0:35"}])
    report = verify(tel, spec, opponent="veryeasy")
    assert not report.passed
    assert not report.results[0].ok


def test_building_complete_at_window():
    tel = [_GAME_START, {"kind": "building_complete", "t": 200.0, "unit": "DARKSHRINE"}]
    spec = _spec(
        [{"id": "ds", "type": "building_complete", "unit": "DARKSHRINE", "at": "3:14", "tol": 25}]
    )
    assert verify(tel, spec, opponent="veryeasy").passed


def test_worker_count_at():
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 240.0, "workers": 42},
        {"kind": "snapshot", "t": 242.0, "workers": 44},
    ]
    spec = _spec([{"id": "w", "type": "worker_count", "at": "4:00", "min": 40}])
    assert verify(tel, spec, opponent="veryeasy").passed


def test_key_unit_at_near_anchor():
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 270.0, "key_units": {"WARPPRISM": [[55.0, 35.0]]}},
    ]
    spec = _spec(
        [
            {
                "id": "p",
                "type": "key_unit_at",
                "unit": "WARPPRISM",
                "at": "4:30",
                "near": "enemy_main",
                "within": 25,
            }
        ]
    )
    assert verify(tel, spec, opponent="veryeasy").passed


def test_cheatmoney_skips_position_checks():
    """CheatMoney 档跳过位置类断言(抗压下位置必乱)。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 270.0, "key_units": {"WARPPRISM": [[999.0, 999.0]]}},
    ]
    spec = _spec(
        [
            {
                "id": "p",
                "type": "key_unit_at",
                "unit": "WARPPRISM",
                "at": "4:30",
                "near": "enemy_main",
                "within": 25,
            }
        ]
    )
    report = verify(tel, spec, opponent="cheatmoney")
    assert report.results[0].skipped
    assert report.passed


def test_attack_moveout_detects_army_leaving_home():
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 400.0, "army_center": [120.0, 110.0]},
        {"kind": "snapshot", "t": 500.0, "army_center": [70.0, 70.0]},
    ]
    spec = _spec([{"id": "out", "type": "attack_moveout", "by": "9:00"}])
    assert verify(tel, spec, opponent="veryeasy").passed


def test_building_count_merges_gateway_warpgate():
    """building_count 验 GATEWAY 时自动合并 WARPGATE(折跃后 morph)。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 240.0, "buildings": {"GATEWAY": 1, "WARPGATE": 2}},
    ]
    spec = _spec(
        [{"id": "bg", "type": "building_count", "unit": "GATEWAY", "at": "4:00", "min": 3}]
    )
    assert verify(tel, spec, opponent="veryeasy").passed


def test_building_count_fail_not_enough():
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 240.0, "buildings": {"GATEWAY": 1, "WARPGATE": 0}},
    ]
    spec = _spec(
        [{"id": "bg", "type": "building_count", "unit": "GATEWAY", "at": "4:00", "min": 3}]
    )
    assert not verify(tel, spec, opponent="veryeasy").passed


def test_unit_count_window_passes_off_center():
    """计数类 check 在 at±tol 窗口内达到过 min 即 PASS(不必在 at 整点)。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 300.0, "units": {"DARKTEMPLAR": 1}},
        {"kind": "snapshot", "t": 325.0, "units": {"DARKTEMPLAR": 4}},
    ]
    spec = _spec(
        [
            {
                "id": "dt",
                "type": "unit_count",
                "unit": "DARKTEMPLAR",
                "at": "5:00",
                "tol": 30,
                "min": 4,
            }
        ]
    )
    assert verify(tel, spec, opponent="veryeasy").passed


def test_unit_count_window_max_survives_later_drop():
    """窗口内峰值达 min，即便之后掉下来(DT 阵亡/合 Archon)仍 PASS。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 340.0, "units": {"DARKTEMPLAR": 8}},
        {"kind": "snapshot", "t": 355.0, "units": {"DARKTEMPLAR": 2}},
    ]
    spec = _spec(
        [
            {
                "id": "dt2",
                "type": "unit_count",
                "unit": "DARKTEMPLAR",
                "at": "5:50",
                "tol": 30,
                "min": 8,
            }
        ]
    )
    assert verify(tel, spec, opponent="veryeasy").passed


def test_unit_count_window_fail_outside_window():
    """窗口外达到 min 不算 —— at±tol 之外的 snapshot 不计入。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 200.0, "units": {"DARKTEMPLAR": 8}},
        {"kind": "snapshot", "t": 300.0, "units": {"DARKTEMPLAR": 1}},
        {"kind": "snapshot", "t": 400.0, "units": {"DARKTEMPLAR": 8}},
    ]
    spec = _spec(
        [
            {
                "id": "dt",
                "type": "unit_count",
                "unit": "DARKTEMPLAR",
                "at": "5:00",
                "tol": 30,
                "min": 4,
            }
        ]
    )
    assert not verify(tel, spec, opponent="veryeasy").passed


def test_count_by_uses_zero_to_by_window():
    """by 模式计数类用 [0, by] 窗口取最大。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 100.0, "units": {"DARKTEMPLAR": 3}},
        {"kind": "snapshot", "t": 350.0, "units": {"DARKTEMPLAR": 0}},
    ]
    spec = _spec(
        [{"id": "dtw", "type": "unit_count", "unit": "DARKTEMPLAR", "by": "5:00", "min": 3}]
    )
    assert verify(tel, spec, opponent="veryeasy").passed


def test_count_window_no_snapshot_fails():
    """窗口内无 snapshot → FAIL。"""
    tel = [_GAME_START, {"kind": "snapshot", "t": 50.0, "workers": 12}]
    spec = _spec([{"id": "w", "type": "worker_count", "at": "5:00", "tol": 20, "min": 30}])
    report = verify(tel, spec, opponent="veryeasy")
    assert not report.passed
    assert "无 snapshot" in report.results[0].detail


def _report(*results):
    return Report(results=list(results))


def test_aggregate_majority_pass():
    """3 跑里某 check 2 PASS 1 FAIL → 多数票 PASS。"""
    reports = [
        _report(CheckResult("c1", True), CheckResult("c2", True)),
        _report(CheckResult("c1", True), CheckResult("c2", False)),
        _report(CheckResult("c1", False), CheckResult("c2", True)),
    ]
    agg = aggregate_reports(reports)
    by = {r.check_id: r for r in agg.results}
    assert by["c1"].ok and by["c1"].pass_count == 2
    assert by["c2"].ok and by["c2"].pass_count == 2
    assert agg.passed


def test_aggregate_majority_fail():
    """某 check 仅 1/3 PASS → 多数票 FAIL。"""
    reports = [
        _report(CheckResult("c1", True)),
        _report(CheckResult("c1", False)),
        _report(CheckResult("c1", False)),
    ]
    agg = aggregate_reports(reports)
    assert not agg.results[0].ok
    assert agg.results[0].pass_count == 1
    assert not agg.passed


def test_aggregate_all_skipped():
    """所有 run 都 skip 某 check → 聚合 skipped。"""
    reports = [
        _report(CheckResult("c1", True, skipped=True)),
        _report(CheckResult("c1", True, skipped=True)),
    ]
    agg = aggregate_reports(reports)
    assert agg.results[0].skipped
    assert agg.passed


def test_aggregate_single_run():
    """runs=1：单局结果直接生效。"""
    agg = aggregate_reports([_report(CheckResult("c1", False))])
    assert not agg.results[0].ok
    assert agg.results[0].run_count == 1
