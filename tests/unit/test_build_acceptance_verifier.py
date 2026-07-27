"""Verifier: telemetry × spec → pass/fail。"""

from __future__ import annotations

from vibecraft.build_acceptance.spec import AcceptanceSpec
from vibecraft.build_acceptance.verifier import (
    CheckResult,
    EconomyPoint,
    EconomyReport,
    Report,
    aggregate_reports,
    score_economy,
    verify,
)

_GAME_START = {
    "kind": "game_start",
    "t": 0.0,
    "home": [127.5, 119.5],
    "enemy_main": [48.5, 28.5],
    "natural": [145.5, 98.5],
    "enemy_natural": [60.5, 40.5],
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


# --- L2 前压 / L3 骚扰验收 ----------------------------------------------


def test_pressure_contact_pass_reaches_enemy_natural():
    """主力 army_center 到达敌方分矿 within 内 → L2 PASS。"""
    tel = [
        _GAME_START,
        {
            "kind": "snapshot",
            "t": 300.0,
            "army_center": [65.0, 45.0],
            "enemy": {"enemy_army_count": 0, "enemy_army_center": None},
        },
    ]
    spec = _spec([{"id": "p", "type": "pressure_contact", "by": "6:00", "within": 20}])
    assert verify(tel, spec, opponent="veryeasy").passed


def test_pressure_contact_pass_via_army_contact_at_home():
    """敌方主力压到家、我方在家防御（两 army_center 接近）→ L2 PASS。"""
    tel = [
        _GAME_START,
        {
            "kind": "snapshot",
            "t": 300.0,
            "army_center": [125.0, 117.0],
            "enemy": {"enemy_army_count": 6, "enemy_army_center": [128.0, 120.0]},
        },
    ]
    spec = _spec([{"id": "p", "type": "pressure_contact", "by": "6:00", "within": 20}])
    assert verify(tel, spec, opponent="veryeasy").passed


def test_pressure_contact_fail_army_stays_home_no_contact():
    """主力一直窝家、从未接触敌方 → L2 FAIL。"""
    tel = [
        _GAME_START,
        {
            "kind": "snapshot",
            "t": 300.0,
            "army_center": [125.0, 117.0],
            "enemy": {"enemy_army_count": 0, "enemy_army_center": None},
        },
    ]
    spec = _spec([{"id": "p", "type": "pressure_contact", "by": "6:00", "within": 20}])
    assert not verify(tel, spec, opponent="veryeasy").passed


def test_harass_damage_pass_workers_killed():
    """视野内打死 >=min 个敌方农民 → L3 PASS（取累计最大）。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 200.0, "enemy": {"enemy_workers_harassed": 1}},
        {"kind": "snapshot", "t": 280.0, "enemy": {"enemy_workers_harassed": 5}},
    ]
    spec = _spec([{"id": "h", "type": "harass_damage", "by": "5:00", "min": 4}])
    assert verify(tel, spec, opponent="veryeasy").passed


def test_harass_damage_fail_not_enough_kills():
    """农民阵亡数不足 min → L3 FAIL。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 280.0, "enemy": {"enemy_workers_harassed": 1}},
    ]
    spec = _spec([{"id": "h", "type": "harass_damage", "by": "5:00", "min": 4}])
    assert not verify(tel, spec, opponent="veryeasy").passed


def test_harass_damage_ignores_kills_after_deadline():
    """deadline 之后才打死的农民不计入。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 280.0, "enemy": {"enemy_workers_harassed": 0}},
        {"kind": "snapshot", "t": 400.0, "enemy": {"enemy_workers_harassed": 9}},
    ]
    spec = _spec([{"id": "h", "type": "harass_damage", "by": "5:00", "min": 4}])
    assert not verify(tel, spec, opponent="veryeasy").passed


def _scout_spec(min_s=300):
    return _spec(
        [
            {
                "id": "sv",
                "type": "scout_value",
                "unit": "REAPER",
                "near": "enemy_main",
                "within": 40,
                "min": min_s,
            }
        ]
    )


def test_scout_value_pass_late_last_scout():
    """死神「最后一次进对方基地」的时刻 >= min → PASS。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 200.0, "key_units": {"REAPER": [[50.0, 30.0]]}},
        {"kind": "snapshot", "t": 600.0, "key_units": {"REAPER": [[49.0, 29.0]]}},
    ]
    # enemy_main=[48.5,28.5];死神最后一次在基地内是 t=600 >= 300 → PASS
    assert verify(tel, _scout_spec(300), opponent="veryeasy").passed


def test_scout_value_fail_only_early_scout():
    """死神只前期摸过一次、之后再没进去 → 最后侦查时刻早 → FAIL。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 150.0, "key_units": {"REAPER": [[50.0, 30.0]]}},
        {"kind": "snapshot", "t": 600.0, "key_units": {"REAPER": [[200.0, 200.0]]}},
    ]
    # 最后一次进基地 t=150 < 300 → FAIL(t=600 那次死神远在 200,200,不在基地)
    assert not verify(tel, _scout_spec(300), opponent="veryeasy").passed


def test_scout_value_fail_never_in_base():
    """死神整局没摸进对方基地 → last_t=0 → FAIL。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 400.0, "key_units": {"REAPER": [[200.0, 200.0]]}},
    ]
    assert not verify(tel, _scout_spec(300), opponent="veryeasy").passed


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


# --- 经济曲线偏差评分 ---------------------------------------------------


def _spec_econ(profile, checks=None):
    return AcceptanceSpec.model_validate(
        {
            "strategy_id": "t",
            "my_race": "Terran",
            "checks": checks or [],
            "economy_profile": profile,
        }
    )


def test_score_economy_zero_when_actual_matches_standard():
    """实测与标准值完全一致 → 偏差分 0。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 120.0, "workers": 19, "minerals": 150},
    ]
    spec = _spec_econ([{"at": "2:00", "workers": 19, "minerals": 150}])
    rep = verify(tel, spec, opponent="veryeasy")
    assert rep.economy.score == 0.0
    assert all(p.signed_dev == 0 for p in rep.economy.points)


def test_score_economy_deviation_is_relative():
    """偏差归一化：workers 差 10/20→0.5，minerals 完美→0，均值 0.25。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 240.0, "workers": 30, "minerals": 400},
    ]
    spec = _spec_econ([{"at": "4:00", "workers": 20, "minerals": 400}])
    rep = verify(tel, spec, opponent="veryeasy")
    assert abs(rep.economy.score - 0.25) < 1e-6
    pts = {p.field: p for p in rep.economy.points}
    assert pts["workers"].signed_dev == 10
    assert pts["minerals"].signed_dev == 0


def test_score_economy_empty_profile_score_zero():
    """spec 无 economy_profile → 偏差分 0、无 points。"""
    tel = [_GAME_START, {"kind": "snapshot", "t": 120.0, "workers": 12}]
    spec = _spec_econ([], checks=[{"id": "w", "type": "worker_count", "by": "2:30", "min": 1}])
    rep = verify(tel, spec, opponent="veryeasy")
    assert rep.economy.points == []
    assert rep.economy.score == 0.0


def test_score_economy_does_not_affect_passed():
    """经济偏差大但里程碑 check 全过 → report.passed 仍 True（经济纯分数）。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 120.0, "workers": 12, "minerals": 5000},
    ]
    spec = _spec_econ(
        [{"at": "2:00", "minerals": 100}],
        checks=[{"id": "w", "type": "worker_count", "by": "2:30", "min": 1}],
    )
    rep = verify(tel, spec, opponent="veryeasy")
    assert rep.passed  # 里程碑过
    assert rep.economy.score > 1.0  # 经济偏差大


def test_score_economy_skips_unset_fields():
    """checkpoint 只填 workers → 不为 minerals/vespene 造 point。"""
    tel = [_GAME_START, {"kind": "snapshot", "t": 120.0, "workers": 19, "minerals": 999}]
    rep = score_economy(tel, _spec_econ([{"at": "2:00", "workers": 19}]).economy_profile)
    assert len(rep.points) == 1
    assert rep.points[0].field == "workers"


def test_aggregate_economy_averages_actual_across_runs():
    """多局 economy 按 (时间点,字段) 取实测均值。"""
    r1 = Report(economy=EconomyReport(points=[EconomyPoint(120.0, "workers", 20.0, 18.0)]))
    r2 = Report(economy=EconomyReport(points=[EconomyPoint(120.0, "workers", 20.0, 22.0)]))
    agg = aggregate_reports([r1, r2])
    assert len(agg.economy.points) == 1
    assert agg.economy.points[0].actual == 20.0
    assert agg.economy.score == 0.0


# =========================================================================
# Task #311 army_after_player_action check
# =========================================================================


def _spec_with_player_action(player_actions, checks):
    return AcceptanceSpec.model_validate(
        {
            "strategy_id": "t",
            "my_race": "Protoss",
            "player_actions": player_actions,
            "checks": checks,
        }
    )


def test_army_after_player_action_retreat_pass():
    """5:00 retreat,5:30 时主力距家 5(<=25)→ PASS。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 0.0, "army_center": [60.0, 50.0]},  # 出门远
        {"kind": "snapshot", "t": 330.0, "army_center": [130.0, 122.0]},  # 5:30 在家 ~3
    ]
    spec = _spec_with_player_action(
        [{"at": "5:00", "verb": "retreat"}],
        [
            {
                "id": "r",
                "type": "army_after_player_action",
                "action_idx": 0,
                "after_s": 30,
                "near": "home",
                "within": 25.0,
                "op": "<=",
            }
        ],
    )
    rep = verify(tel, spec, opponent="veryeasy")
    assert rep.passed
    assert rep.results[0].ok


def test_army_after_player_action_retreat_fail_too_far():
    """5:00 retreat,5:30 时主力距家 80(>25)→ FAIL(单位没回)。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 330.0, "army_center": [60.0, 50.0]},  # 离家远
    ]
    spec = _spec_with_player_action(
        [{"at": "5:00", "verb": "retreat"}],
        [
            {
                "id": "r",
                "type": "army_after_player_action",
                "action_idx": 0,
                "after_s": 30,
                "near": "home",
                "within": 25.0,
                "op": "<=",
            }
        ],
    )
    rep = verify(tel, spec, opponent="veryeasy")
    assert not rep.passed
    assert not rep.results[0].ok


def test_army_after_player_action_attack_pass_with_op_le():
    """4:00 attack enemy_main,5:00 主力距 enemy_main < 30 → PASS。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 300.0, "army_center": [55.0, 35.0]},  # 距 enemy_main ~9
    ]
    spec = _spec_with_player_action(
        [{"at": "4:00", "verb": "attack", "mode": "all_in", "target_area": "enemy_main"}],
        [
            {
                "id": "a",
                "type": "army_after_player_action",
                "action_idx": 0,
                "after_s": 60,
                "near": "enemy_main",
                "within": 30.0,
                "op": "<=",
            }
        ],
    )
    rep = verify(tel, spec, opponent="veryeasy")
    assert rep.passed


def test_army_after_player_action_op_gt_for_retreat_distance_from_enemy():
    """retreat 后验"离敌方远":op=">",距 enemy_main >30。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 330.0, "army_center": [130.0, 120.0]},  # 距 enemy_main ~120
    ]
    spec = _spec_with_player_action(
        [{"at": "5:00", "verb": "retreat"}],
        [
            {
                "id": "r2",
                "type": "army_after_player_action",
                "action_idx": 0,
                "after_s": 30,
                "near": "enemy_main",
                "within": 30.0,
                "op": ">",
            }
        ],
    )
    rep = verify(tel, spec, opponent="veryeasy")
    assert rep.passed


def test_army_after_player_action_missing_snapshot_fails():
    """指定时间点附近没 snapshot(>5s 容差)→ FAIL。"""
    tel = [
        _GAME_START,
        # 唯一 snapshot 在 100s,但 check 要 5:30 = 330s,远超 5s 容差
        {"kind": "snapshot", "t": 100.0, "army_center": [130.0, 120.0]},
    ]
    spec = _spec_with_player_action(
        [{"at": "5:00", "verb": "retreat"}],
        [
            {
                "id": "r",
                "type": "army_after_player_action",
                "action_idx": 0,
                "after_s": 30,
                "near": "home",
                "within": 25.0,
                "op": "<=",
            }
        ],
    )
    rep = verify(tel, spec, opponent="veryeasy")
    assert not rep.passed


def test_army_after_player_action_missing_army_center_fails():
    """snapshot 时刻在但没 army_center(单位全死光)→ FAIL。"""
    tel = [
        _GAME_START,
        {"kind": "snapshot", "t": 330.0, "workers": 30},  # 无 army_center
    ]
    spec = _spec_with_player_action(
        [{"at": "5:00", "verb": "retreat"}],
        [
            {
                "id": "r",
                "type": "army_after_player_action",
                "action_idx": 0,
                "after_s": 30,
                "near": "home",
                "within": 25.0,
                "op": "<=",
            }
        ],
    )
    rep = verify(tel, spec, opponent="veryeasy")
    assert not rep.passed
