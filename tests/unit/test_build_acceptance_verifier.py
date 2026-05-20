"""Verifier: telemetry × spec → pass/fail。"""
from __future__ import annotations

from vibecraft.build_acceptance.spec import AcceptanceSpec
from vibecraft.build_acceptance.verifier import verify

_GAME_START = {
    "kind": "game_start", "t": 0.0,
    "home": [127.5, 119.5], "enemy_main": [48.5, 28.5], "natural": [145.5, 98.5],
}


def _spec(checks):
    return AcceptanceSpec.model_validate(
        {"strategy_id": "t", "my_race": "Protoss", "checks": checks}
    )


def test_building_started_by_pass():
    tel = [_GAME_START,
           {"kind": "building_started", "t": 20.0, "unit": "GATEWAY"}]
    spec = _spec([{"id": "g1", "type": "building_started",
                   "unit": "GATEWAY", "by": "0:35"}])
    report = verify(tel, spec, opponent="veryeasy")
    assert report.passed
    assert report.results[0].ok


def test_building_started_by_fail_too_late():
    tel = [_GAME_START,
           {"kind": "building_started", "t": 50.0, "unit": "GATEWAY"}]
    spec = _spec([{"id": "g1", "type": "building_started",
                   "unit": "GATEWAY", "by": "0:35"}])
    report = verify(tel, spec, opponent="veryeasy")
    assert not report.passed
    assert not report.results[0].ok


def test_building_complete_at_window():
    tel = [_GAME_START,
           {"kind": "building_complete", "t": 200.0, "unit": "DARKSHRINE"}]
    spec = _spec([{"id": "ds", "type": "building_complete",
                   "unit": "DARKSHRINE", "at": "3:14", "tol": 25}])
    assert verify(tel, spec, opponent="veryeasy").passed


def test_worker_count_at():
    tel = [_GAME_START,
           {"kind": "snapshot", "t": 240.0, "workers": 42},
           {"kind": "snapshot", "t": 242.0, "workers": 44}]
    spec = _spec([{"id": "w", "type": "worker_count", "at": "4:00", "min": 40}])
    assert verify(tel, spec, opponent="veryeasy").passed


def test_key_unit_at_near_anchor():
    tel = [_GAME_START,
           {"kind": "snapshot", "t": 270.0,
            "key_units": {"WARPPRISM": [[55.0, 35.0]]}}]
    spec = _spec([{"id": "p", "type": "key_unit_at", "unit": "WARPPRISM",
                   "at": "4:30", "near": "enemy_main", "within": 25}])
    assert verify(tel, spec, opponent="veryeasy").passed


def test_cheatmoney_skips_position_checks():
    """CheatMoney 档跳过位置类断言(抗压下位置必乱)。"""
    tel = [_GAME_START,
           {"kind": "snapshot", "t": 270.0,
            "key_units": {"WARPPRISM": [[999.0, 999.0]]}}]
    spec = _spec([{"id": "p", "type": "key_unit_at", "unit": "WARPPRISM",
                   "at": "4:30", "near": "enemy_main", "within": 25}])
    report = verify(tel, spec, opponent="cheatmoney")
    assert report.results[0].skipped
    assert report.passed


def test_attack_moveout_detects_army_leaving_home():
    tel = [_GAME_START,
           {"kind": "snapshot", "t": 400.0, "army_center": [120.0, 110.0]},
           {"kind": "snapshot", "t": 500.0, "army_center": [70.0, 70.0]}]
    spec = _spec([{"id": "out", "type": "attack_moveout", "by": "9:00"}])
    assert verify(tel, spec, opponent="veryeasy").passed


def test_building_count_merges_gateway_warpgate():
    """building_count 验 GATEWAY 时自动合并 WARPGATE(折跃后 morph)。"""
    tel = [_GAME_START,
           {"kind": "snapshot", "t": 240.0,
            "buildings": {"GATEWAY": 1, "WARPGATE": 2}}]
    spec = _spec([{"id": "bg", "type": "building_count", "unit": "GATEWAY",
                   "at": "4:00", "min": 3}])
    assert verify(tel, spec, opponent="veryeasy").passed


def test_building_count_fail_not_enough():
    tel = [_GAME_START,
           {"kind": "snapshot", "t": 240.0,
            "buildings": {"GATEWAY": 1, "WARPGATE": 0}}]
    spec = _spec([{"id": "bg", "type": "building_count", "unit": "GATEWAY",
                   "at": "4:00", "min": 3}])
    assert not verify(tel, spec, opponent="veryeasy").passed
