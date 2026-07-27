"""六维执行质量自检单测 — 纯函数，无 SC2。

测试 compute_exec_quality / format_exec_quality_block 在各种合成 telemetry
records 下的行为，确认每一维的 WARN 判定逻辑正确。
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import build_acceptance as runner
from vibecraft.build_acceptance.spec import AcceptanceSpec
from vibecraft.build_acceptance.verifier import CheckResult, Report

# ── Helpers ──────────────────────────────────────────────────────────────────


def _snap(t: float, **kwargs: object) -> dict[str, object]:
    """构造一个 kind=snapshot 的 telemetry record。

    worker 明细(idle_workers/gas_workers/mineral_workers)嵌在 `economy` 子字段里,
    与真实 telemetry 一致(top-level 没有这些字段)——所以 _snap(idle_workers=4) 也路由进 economy。
    """
    economy = {
        "idle_workers": kwargs.pop("idle_workers", 0),
        "gas_workers": kwargs.pop("gas_workers", 0),
        "mineral_workers": kwargs.pop("mineral_workers", 0),
    }
    rec: dict[str, object] = {
        "kind": "snapshot",
        "t": t,
        "economy": economy,
        "supply_used": 20,
        "supply_cap": 50,
        "minerals": 50,
        "vespene": 0,
        "workers": 12,
    }
    rec.update(kwargs)
    return rec


def _make_report(
    results: list[tuple[str, bool, str]] | None = None,
) -> Report:
    """构造一个含给定 check 结果的 Report。"""
    report = Report()
    for check_id, ok, detail in results or []:
        report.results.append(CheckResult(check_id=check_id, ok=ok, detail=detail))
    return report


def _make_spec(checks: list[dict[str, object]] | None = None) -> AcceptanceSpec:
    """构造最小 AcceptanceSpec。"""
    raw: dict[str, object] = {
        "strategy_id": "test_strat",
        "my_race": "Protoss",
        "checks": checks or [],
    }
    return AcceptanceSpec.model_validate(raw)


# ── ① 农民不闲置 ─────────────────────────────────────────────────────────────


def test_dim1_warn_on_high_idle() -> None:
    """早期 idle_workers 均值 > 1.5 → ⚠️。"""
    records = [
        _snap(float(t), idle_workers=4, supply_used=20, supply_cap=50, minerals=100)
        for t in range(0, 300, 5)
    ]
    quality = runner.compute_exec_quality([records], [_make_report()], _make_spec())
    assert quality["dim1"]["warn"] is True
    assert quality["dim1"]["idle_mean"] > 1.5


def test_dim1_ok_on_low_idle() -> None:
    """早期 idle_workers 均值 0 → OK。"""
    records = [
        _snap(float(t), idle_workers=0, supply_used=20, supply_cap=50, minerals=100)
        for t in range(0, 300, 5)
    ]
    quality = runner.compute_exec_quality([records], [_make_report()], _make_spec())
    assert quality["dim1"]["warn"] is False
    assert quality["dim1"]["idle_mean"] == 0.0


def test_dim1_gas_no_worker_counted() -> None:
    """有气矿建筑但 gas_workers==0 → gas_no_worker_s 累计非零。"""
    records = [
        _snap(
            float(t),
            idle_workers=0,
            gas_workers=0,
            buildings={"ASSIMILATOR": 1},
            supply_used=20,
            supply_cap=50,
            minerals=50,
        )
        for t in range(0, 60, 1)
    ]
    quality = runner.compute_exec_quality([records], [_make_report()], _make_spec())
    assert quality["dim1"]["gas_no_worker_s"] > 0


# ── ② 资源堆积 ───────────────────────────────────────────────────────────────


def test_dim2_warn_on_high_bank() -> None:
    """avg_excess_bank > 500 → ⚠️。"""
    records = [
        _snap(float(t), minerals=1000, vespene=0, supply_used=20, supply_cap=50)
        for t in range(0, 600, 5)
    ]
    quality = runner.compute_exec_quality([records], [_make_report()], _make_spec())
    assert quality["dim2"]["warn"] is True
    assert quality["dim2"]["avg_excess_bank"] > 500


def test_dim2_ok_on_low_bank() -> None:
    """矿量低 → OK。"""
    records = [
        _snap(float(t), minerals=80, vespene=0, supply_used=20, supply_cap=50)
        for t in range(0, 600, 5)
    ]
    quality = runner.compute_exec_quality([records], [_make_report()], _make_spec())
    assert quality["dim2"]["warn"] is False


# ── ③ 产能利用率 ──────────────────────────────────────────────────────────────


def test_dim3_warn_on_low_prod_util() -> None:
    """Protoss prod_util=0.0 < 0.6 → ⚠️。"""
    records = [
        _snap(
            float(t),
            minerals=50,
            supply_used=20,
            supply_cap=50,
            production={"gateway": {"total": 2, "busy": 0}, "util": 0.0},
        )
        for t in range(0, 600, 5)
    ]
    quality = runner.compute_exec_quality([records], [_make_report()], _make_spec())
    assert quality["dim3"]["warn"] is True


def test_dim3_ok_on_high_prod_util() -> None:
    """Protoss prod_util=1.0 → OK。"""
    records = [
        _snap(
            float(t),
            minerals=50,
            supply_used=20,
            supply_cap=50,
            production={"gateway": {"total": 2, "busy": 2}, "util": 1.0},
        )
        for t in range(0, 600, 5)
    ]
    quality = runner.compute_exec_quality([records], [_make_report()], _make_spec())
    assert quality["dim3"]["warn"] is False


def test_dim3_zerg_warn_on_high_larva() -> None:
    """Zerg avg_larva_idle > larva_floor(3) → ⚠️。"""
    records = [
        _snap(
            float(t),
            minerals=300,
            supply_used=20,
            supply_cap=50,
            production={"larva": 9},
        )
        for t in range(0, 600, 5)
    ]
    quality = runner.compute_exec_quality([records], [_make_report()], _make_spec())
    # scorer 会把 race 推断为 ZERG
    if quality["race"] == "ZERG":
        assert quality["dim3"]["warn"] is True


# ── ④ 不卡人口 ───────────────────────────────────────────────────────────────


def test_dim4_warn_on_supply_block() -> None:
    """持续卡口 30s → supply_block_time > 15 → ⚠️。"""
    # 30 秒 supply_used == supply_cap，有钱
    blocked = [
        _snap(float(t), minerals=300, supply_used=30, supply_cap=30) for t in range(0, 30, 1)
    ]
    free = [_snap(float(t), minerals=300, supply_used=20, supply_cap=50) for t in range(30, 300, 5)]
    quality = runner.compute_exec_quality([blocked + free], [_make_report()], _make_spec())
    assert quality["dim4"]["warn"] is True
    assert quality["dim4"]["supply_block_time"] > 15


def test_dim4_ok_on_no_block() -> None:
    """无卡口 → OK。"""
    records = [
        _snap(float(t), minerals=100, supply_used=20, supply_cap=50) for t in range(0, 300, 5)
    ]
    quality = runner.compute_exec_quality([records], [_make_report()], _make_spec())
    assert quality["dim4"]["warn"] is False


# ── ⑤ 科技链 timing ──────────────────────────────────────────────────────────


def test_dim5_warn_on_failed_tech_check() -> None:
    """building_complete check FAIL → ⑤ WARN。"""
    spec = _make_spec(
        [{"id": "gateway_1", "type": "building_complete", "unit": "GATEWAY", "by": "2:00"}]
    )
    report = _make_report([("gateway_1", False, "GATEWAY 无 building_complete 事件")])
    records = [_snap(0.0)]
    quality = runner.compute_exec_quality([records], [report], spec)
    assert quality["dim5"]["warn"] is True
    assert quality["dim5"]["pass_count"] == 0
    assert quality["dim5"]["total"] == 1


def test_dim5_ok_on_all_tech_pass() -> None:
    """所有 building/upgrade check 全 PASS → OK。"""
    spec = _make_spec(
        [
            {
                "id": "pylon_1",
                "type": "building_started",
                "unit": "PYLON",
                "by": "0:40",
            },
            {
                "id": "gateway_1",
                "type": "building_complete",
                "unit": "GATEWAY",
                "at": "1:40",
                "tol": 20,
            },
            {
                "id": "wgr",
                "type": "upgrade_complete",
                "upgrade": "WARPGATERESEARCH",
                "by": "4:10",
            },
        ]
    )
    report = _make_report(
        [
            ("pylon_1", True, "actual 20s, by 40s"),
            ("gateway_1", True, "actual 100s, want 100±20s"),
            ("wgr", True, "actual 240s, by 250s"),
        ]
    )
    records = [_snap(0.0)]
    quality = runner.compute_exec_quality([records], [report], spec)
    assert quality["dim5"]["warn"] is False
    assert quality["dim5"]["pass_count"] == 3
    assert quality["dim5"]["total"] == 3


def test_dim5_ignores_non_tech_checks() -> None:
    """attack_moveout / unit_count 等非科技类 check 不影响 ⑤ 维度。"""
    spec = _make_spec(
        [
            {
                "id": "moveout",
                "type": "attack_moveout",
                "by": "5:00",
            }
        ]
    )
    report = _make_report([("moveout", False, "部队从未出门")])
    records = [_snap(0.0)]
    quality = runner.compute_exec_quality([records], [report], spec)
    # attack_moveout 不属于 _TECH_CHECK_TYPES → total=0 → warn=False
    assert quality["dim5"]["warn"] is False
    assert quality["dim5"]["total"] == 0


# ── ⑥ 后劲 ──────────────────────────────────────────────────────────────────


def test_dim6_warn_on_low_peak() -> None:
    """全程 supply_used=60 < 150 → ⚠️。"""
    records = [
        _snap(float(t), supply_used=60, supply_cap=100, minerals=50) for t in range(0, 600, 5)
    ]
    quality = runner.compute_exec_quality([records], [_make_report()], _make_spec())
    assert quality["dim6"]["warn"] is True
    assert quality["dim6"]["peak_supply"] < 150


def test_dim6_ok_on_high_peak() -> None:
    """supply 线性爬到 200 → 峰值 ≥ 150 且无回落 → OK。"""
    records = []
    for i, t in enumerate(range(0, 600, 3)):
        su = min(200, 20 + i * 2)
        records.append(_snap(float(t), supply_used=su, supply_cap=200, minerals=50))
    quality = runner.compute_exec_quality([records], [_make_report()], _make_spec())
    assert quality["dim6"]["warn"] is False
    assert quality["dim6"]["peak_supply"] >= 150


def test_dim6_warn_on_late_drop() -> None:
    """前期 supply 爬到 200，后 1/3 掉到 50 → 后期回落 → ⚠️。"""
    # 前 2/3：supply 爬到 200
    n_total = 120
    n_front = n_total * 2 // 3
    records = []
    for i in range(n_front):
        records.append(_snap(float(i * 5), supply_used=200, supply_cap=200, minerals=50))
    # 后 1/3：supply 掉到 50
    for i in range(n_front, n_total):
        records.append(_snap(float(i * 5), supply_used=50, supply_cap=200, minerals=50))
    quality = runner.compute_exec_quality([records], [_make_report()], _make_spec())
    assert quality["dim6"]["late_drop"] is True
    assert quality["dim6"]["warn"] is True


# ── 跨 N 局聚合 ──────────────────────────────────────────────────────────────


def test_multi_run_averages_idle() -> None:
    """两局 idle_mean 取平均。"""
    run1 = [_snap(float(t), idle_workers=0) for t in range(0, 60, 5)]
    run2 = [_snap(float(t), idle_workers=4) for t in range(0, 60, 5)]
    quality = runner.compute_exec_quality(
        [run1, run2], [_make_report(), _make_report()], _make_spec()
    )
    # 平均值 ≈ 2.0 → 应 WARN（>1.5）
    assert quality["n_runs"] == 2
    assert quality["dim1"]["idle_mean"] > 1.0


def test_empty_records_returns_error() -> None:
    """空 all_records → 返回 error 字段而不崩。"""
    quality = runner.compute_exec_quality([], [], _make_spec())
    assert "error" in quality


# ── format 格式化 ─────────────────────────────────────────────────────────────


def test_format_block_contains_all_dim_labels() -> None:
    """format_exec_quality_block 包含六个维度标签。"""
    records = [_snap(0.0)]
    quality = runner.compute_exec_quality([records], [_make_report()], _make_spec())
    block = runner.format_exec_quality_block("test_strat", quality)
    for marker in [
        "① 农民闲置",
        "② 资源堆积",
        "③ 产能利用率",
        "④ 卡人口",
        "⑤ 科技链timing",
        "⑥ 后劲",
    ]:
        assert marker in block, f"缺少 {marker!r}"


def test_format_block_shows_warn_marker() -> None:
    """WARN 维度在输出中含 ASCII "WARN" 标记（不用 emoji —— GBK console print() 会崩）。"""
    records = [
        _snap(float(t), idle_workers=5, supply_used=20, supply_cap=50, minerals=100)
        for t in range(0, 300, 5)
    ]
    quality = runner.compute_exec_quality([records], [_make_report()], _make_spec())
    block = runner.format_exec_quality_block("test_strat", quality)
    assert "WARN" in block


def test_format_block_error_does_not_crash() -> None:
    """空 records → error 分支 → format 不崩且含 sid。"""
    quality = runner.compute_exec_quality([], [], _make_spec())
    block = runner.format_exec_quality_block("test_strat", quality)
    assert "test_strat" in block


def test_format_block_worst_dims_shown() -> None:
    """有 WARN 维度时，'最差维度' 行包含对应标签。"""
    records = [
        _snap(float(t), idle_workers=5, supply_used=20, supply_cap=50, minerals=100)
        for t in range(0, 300, 5)
    ]
    quality = runner.compute_exec_quality([records], [_make_report()], _make_spec())
    block = runner.format_exec_quality_block("test_strat", quality)
    assert "最差维度:" in block
    assert "①农民闲置" in block
