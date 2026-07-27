"""build 效率打分器单测：M1 余钱积分 / M2 产能利用率·larva 闲置 / M3 卡人口(滤短 block)。"""

from __future__ import annotations

from vibecraft.build_efficiency import ScoreConfig, score_snapshots


def _snap(t, minerals=0, vespene=0, supply_used=20, supply_cap=50, production=None, **extra):
    # 默认 supply 20/50：有人口余量 + 没到 200 引擎上限 → M1 囤钱闸打开（正常 ramp 态）。
    rec = {
        "kind": "snapshot",
        "t": t,
        "minerals": minerals,
        "vespene": vespene,
        "supply_used": supply_used,
        "supply_cap": supply_cap,
    }
    if production is not None:
        rec["production"] = production
    rec.update(extra)
    return rec


def test_m1_bank_integral_dt_weighted():
    # 3 帧 t=0,2,4，矿 700（floor=0 → excess 700），气 0。dt=2，两段（末帧丢）→ 700*2+700*2=2800
    snaps = [_snap(0, minerals=700), _snap(2, minerals=700), _snap(4, minerals=700)]
    rep = score_snapshots(snaps)
    assert rep.bank_integral == 2800.0
    assert rep.avg_excess_bank == 700.0  # 2800 / 4s
    assert rep.worst_dimension == "bank"


def test_m2_protoss_prod_util_time_weighted():
    prod_hi = {"gateway": {"total": 2, "busy": 2}, "util": 1.0}
    prod_lo = {"gateway": {"total": 2, "busy": 0}, "util": 0.0}
    # t=0(util1.0,dt2) + t=2(util0.0,dt2) + t=4(末帧丢) → 加权 (1.0*2+0.0*2)/4 = 0.5
    snaps = [
        _snap(0, production=prod_hi),
        _snap(2, production=prod_lo),
        _snap(4, production=prod_hi),
    ]
    rep = score_snapshots(snaps)
    assert rep.race == "PROTOSS"
    assert rep.prod_util == 0.5
    assert rep.larva_idle_integral is None


def test_m2_zerg_larva_idle_integral():
    # larva 堆 8（floor 3 → idle 5），有钱(300>100)+成长期，两段 dt=2 → 5*2+5*2=20
    prod = {"larva": 8, "util": None}
    snaps = [_snap(t, minerals=300, production=prod) for t in (0, 2, 4)]
    rep = score_snapshots(snaps, ScoreConfig(larva_floor=3))
    assert rep.race == "ZERG"
    assert rep.larva_idle_integral == 20.0
    assert rep.avg_larva_idle == 5.0
    assert rep.prod_util is None


def test_m3_supply_block_counts_sustained_run():
    # 持续卡口 6s（t=0..6，supply_used==cap，有钱）→ 计入（>4s）
    snaps = [_snap(t, minerals=300, supply_used=30, supply_cap=30) for t in (0, 2, 4, 6)] + [
        _snap(8, minerals=300, supply_used=20, supply_cap=40)
    ]
    rep = score_snapshots(snaps, ScoreConfig(supply_block_min_run_s=4.0))
    # 卡口段 t=0,2,4,6 各 dt=2（末帧 t=6 的 dt 到 t=8=2，但 t=6 not blocked→其实 t=0,2,4 blocked dt 共 6）
    assert rep.supply_block_time >= 6.0
    assert rep.worst_dimension == "supply"


def test_m3_short_block_filtered():
    # 只卡 1 帧（2s < 4s）→ 滤掉
    snaps = [
        _snap(0, minerals=300, supply_used=10, supply_cap=40),
        _snap(2, minerals=300, supply_used=30, supply_cap=30),  # blocked 1 帧
        _snap(4, minerals=300, supply_used=10, supply_cap=50),
        _snap(6, minerals=300, supply_used=10, supply_cap=50),
    ]
    rep = score_snapshots(snaps, ScoreConfig(supply_block_min_run_s=4.0))
    assert rep.supply_block_time == 0.0


def test_m3_block_needs_money():
    # 卡口但没钱（minerals < can_build）→ 不算浪费（卡口正常，没东西可造）
    snaps = [_snap(t, minerals=10, supply_used=30, supply_cap=30) for t in (0, 2, 4, 6)]
    rep = score_snapshots(snaps, ScoreConfig(supply_can_build_minerals=100))
    assert rep.supply_block_time == 0.0


def test_window_from_opening_completed():
    snaps = [
        _snap(0, minerals=5000),  # 开局前囤（应被窗口排除）
        _snap(2, minerals=5000),
        _snap(100, minerals=300, opening_completed_at=100.0),
        _snap(102, minerals=300),
        _snap(104, minerals=300),
    ]
    rep = score_snapshots(snaps, ScoreConfig(from_opening_completed=True))
    assert rep.window[0] >= 100.0  # 窗口从 opening_completed 起
    # 早期 5000 囤金被窗口排除 → avg_excess 只剩 100 后的 ~300，远低于 5000
    assert rep.avg_excess_bank < 1000


def test_normalization_only_when_ref_given():
    snaps = [
        _snap(0, minerals=700, production={"gateway": {"total": 1, "busy": 1}, "util": 1.0}),
        _snap(2, minerals=700, production={"gateway": {"total": 1, "busy": 1}, "util": 1.0}),
        _snap(4, minerals=700),
    ]
    # 无 REF → subscores 空、total None（决策不靠 0-100）
    rep = score_snapshots(snaps)
    assert rep.total is None
    # 给 REF → 算
    rep2 = score_snapshots(
        snaps,
        ScoreConfig(ref_bank=1000, ref_supply_block=60, ref_larva_idle=10),
    )
    assert "bank" in rep2.subscores
    assert rep2.total is not None


def test_m1_excludes_supply_cap_saturation_artifact():
    # 到 200/200 引擎上限后囤钱（沙盒人造现象）不算 build 缺陷 → M1 不计
    snaps = [_snap(t, minerals=20000, supply_used=197, supply_cap=200) for t in (0, 2, 4)]
    rep = score_snapshots(snaps, ScoreConfig(m_floor=200))
    assert rep.bank_integral == 0.0  # cap==200 → 闸关，囤钱不罚
    assert rep.worst_dimension != "bank"


def test_m1_counts_banking_with_supply_room():
    # 有人口余量（30/60，cap<200）却囤 5000 矿 → 真"钱没花干净"，M1 要罚
    snaps = [_snap(t, minerals=5000, supply_used=30, supply_cap=60) for t in (0, 2, 4)]
    rep = score_snapshots(snaps, ScoreConfig(m_floor=200))
    assert rep.bank_integral > 0
    assert rep.worst_dimension == "bank"


def test_too_few_snapshots():
    rep = score_snapshots([_snap(0)], ScoreConfig())
    assert rep.n_snapshots == 1
    assert "快照不足" in rep.diagnosis[0]
