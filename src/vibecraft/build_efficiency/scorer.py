"""效率打分器：从 telemetry snapshots 算三维度原始指标 + 诊断时间线。

纯函数、可单测，不碰游戏。CLI 在 scripts/build_efficiency.py。

关键设计（2026-06-15 用户 + Opus 评审）：
- 决策用**原始积分配对比较**（同 build 变体间，同 seed）；0-100 子分/总分仅供人读，需 REF（数据驱动，
  Phase 2 基线分布 P90 给）才算，缺省不算、绝不进保留/淘汰判据。
- M1 floor 复用 verifier 经济刻度（minerals 200 / vespene 120）。
- M3 滤掉 <min_run_s 的短 block（JIT 补人口天然 1-2 帧 used≥cap）。
- 虫族 M2 = larva 闲置积分（跟 M1 同构），不套 busy/total。
- dt 加权用实测相邻差，最后一帧无 dt 丢弃（不进分母）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# verifier 已有经济刻度（_ECONOMY_FLOOR），M1 floor 复用之，避免两套不一致常量。
_M_FLOOR_DEFAULT = 200
_G_FLOOR_DEFAULT = 120


@dataclass
class ScoreConfig:
    """打分参数。floor/阈值是诊断启发式；REF 缺省不算 0-100（决策不靠它）。"""

    t_start: float = 0.0
    t_end: float = 600.0
    from_opening_completed: bool = False  # True → 窗口起点取 snapshot.opening_completed_at（若有）

    # 不设 floor（2026-06-15 用户）：只在同 build 变体间比，floor 是常数偏移、差分里抵消，
    # 不影响排序，反而多一个拍脑袋参数。余钱积分直接 Σ(矿+气)·dt。保留字段默认 0 以备特殊用。
    m_floor: int = 0
    g_floor: int = 0
    larva_floor: int = 3  # 虫族 larva 堆超此数算闲置
    supply_can_build_minerals: int = 100  # "本可出兵"的最低矿（有钱才算浪费）
    supply_block_min_run_s: float = 4.0  # 连续 block ≥此秒才计（滤 JIT 健康重叠）
    # 成长期阈值（三族统一 180，2026-06-15 用户）：supply_used ≥180 进 banking/买活阶段，
    # 囤钱/产能闲置/larva 堆积都是战略性的、不罚。M1 余钱 + M2 产能 + 虫卵闲置都用它 gate。
    growth_supply_max: int = 180
    supply_engine_max: int = 200  # M3 卡人口用：低于引擎上限被卡才算"该补补给没补"

    # 0-100 归一化参考刻度（None → 不算该子分；数据驱动，Phase 2 用 P90 填）
    ref_bank: float | None = None
    ref_supply_block: float | None = None
    ref_larva_idle: float | None = None
    weights: tuple[float, float, float] = (0.35, 0.40, 0.25)  # (bank, prod, supply)

    # 诊断启发式阈值（仅用于"最差维度"判断 + 人读提示，不进决策）
    diag_bank_bad: float = 500.0  # avg_excess_bank 超此=囤钱明显
    diag_prod_bad: float = 0.6  # prod_util 低于此=产能空
    diag_supply_bad: float = 15.0  # supply_block_time 超此秒=卡人口明显


@dataclass
class EfficiencyReport:
    race: str
    window: tuple[float, float]
    n_snapshots: int
    duration_s: float
    # M1
    bank_integral: float  # Σ excess·dt（resource·秒）
    avg_excess_bank: float  # bank_integral / duration
    # M2
    prod_util: float | None  # 神/人：时间加权产能利用率 0-1
    larva_idle_integral: float | None  # 虫：Σ max(0,larva-floor)·dt
    avg_larva_idle: float | None
    # M3
    supply_block_time: float  # 秒（已滤短 block）
    # 诊断
    worst_dimension: str  # "bank" / "prod" / "supply" / "none"
    diagnosis: list[str] = field(default_factory=list)
    # 0-100（仅当 REF 给定）
    subscores: dict[str, float] = field(default_factory=dict)
    total: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "race": self.race,
            "window": list(self.window),
            "n_snapshots": self.n_snapshots,
            "duration_s": round(self.duration_s, 1),
            "bank_integral": round(self.bank_integral, 1),
            "avg_excess_bank": round(self.avg_excess_bank, 1),
            "prod_util": (round(self.prod_util, 4) if self.prod_util is not None else None),
            "larva_idle_integral": (
                round(self.larva_idle_integral, 1) if self.larva_idle_integral is not None else None
            ),
            "avg_larva_idle": (
                round(self.avg_larva_idle, 2) if self.avg_larva_idle is not None else None
            ),
            "supply_block_time": round(self.supply_block_time, 1),
            "worst_dimension": self.worst_dimension,
            "diagnosis": self.diagnosis,
            "subscores": {k: round(v, 1) for k, v in self.subscores.items()},
            "total": (round(self.total, 1) if self.total is not None else None),
        }


def _infer_race(snaps: list[dict[str, Any]]) -> str:
    """从 production block 的种族特征键推断种族（snapshot 不直接存种族）。"""
    for s in snaps:
        prod = s.get("production")
        if not isinstance(prod, dict):
            continue
        if "larva" in prod:
            return "ZERG"
        if "barracks" in prod:
            return "TERRAN"
        if any(k in prod for k in ("gateway", "warpgate", "robo", "stargate")):
            return "PROTOSS"
    return "UNKNOWN"


def _window(snaps: list[dict[str, Any]], cfg: ScoreConfig) -> list[dict[str, Any]]:
    """切评测窗口。from_opening_completed → 起点取首个带 opening_completed_at 的值。"""
    t_start = cfg.t_start
    if cfg.from_opening_completed:
        for s in snaps:
            oc = s.get("opening_completed_at")
            if oc is not None:
                t_start = max(t_start, float(oc))
                break
    return [s for s in snaps if t_start <= float(s.get("t", 0.0)) <= cfg.t_end]


def score_snapshots(
    records: list[dict[str, Any]], cfg: ScoreConfig | None = None
) -> EfficiencyReport:
    """主入口：telemetry records → EfficiencyReport。"""
    cfg = cfg or ScoreConfig()
    snaps = sorted(
        (r for r in records if r.get("kind") == "snapshot"), key=lambda r: float(r.get("t", 0.0))
    )
    snaps = _window(snaps, cfg)
    race = _infer_race(snaps)

    if len(snaps) < 2:
        return EfficiencyReport(
            race=race,
            window=(cfg.t_start, cfg.t_end),
            n_snapshots=len(snaps),
            duration_s=0.0,
            bank_integral=0.0,
            avg_excess_bank=0.0,
            prod_util=None,
            larva_idle_integral=None,
            avg_larva_idle=None,
            supply_block_time=0.0,
            worst_dimension="none",
            diagnosis=["快照不足（<2），无法打分"],
        )

    bank_integral = 0.0
    larva_idle_integral = 0.0
    util_weighted = 0.0
    util_dt = 0.0
    duration = 0.0
    # 卡人口：先逐帧标记，再按"连续 run ≥ min_run_s"汇总（滤 JIT 短重叠）
    block_flags: list[tuple[float, float, bool]] = []  # (t, dt, blocked)

    for i in range(len(snaps) - 1):  # 最后一帧无 dt，丢弃
        s = snaps[i]
        dt = float(snaps[i + 1].get("t", 0.0)) - float(s.get("t", 0.0))
        if dt <= 0:
            continue
        duration += dt
        minerals = float(s.get("minerals", 0))
        vespene = float(s.get("vespene", 0))
        supply_used = float(s.get("supply_used", 0))
        supply_cap = float(s.get("supply_cap", 0))
        # M1 余钱积分：囤钱只在"成长期（人口 <180）且有人口余量"时才算浪费（2026-06-15 用户）。
        # **人口 ≥180 = 不再扣**（满了/买活储备阶段，矿/气/larva 都不罚）。
        # 人口被堵（used≥cap）属 M3 管，逐帧互斥不双罚。floor=0 → excess=矿+气。
        in_growth = supply_used < cfg.growth_supply_max and supply_used < supply_cap
        if in_growth:
            excess = max(0.0, minerals - cfg.m_floor) + max(0.0, vespene - cfg.g_floor)
            bank_integral += excess * dt
        # M2 产能
        prod = s.get("production")
        if isinstance(prod, dict):
            if race == "ZERG":
                # 虫卵闲置（spend 不足）：人口 <180 + 有钱时才算浪费；≥180 不扣（买活储备，用户）。
                if (
                    supply_used < cfg.growth_supply_max
                    and minerals >= cfg.supply_can_build_minerals
                ):
                    larva = float(prod.get("larva", 0))
                    larva_idle_integral += max(0.0, larva - cfg.larva_floor) * dt
            else:
                util = prod.get("util")
                # ≥180 banking 阶段 util≈0 是被动（满人口造不了兵），非 build 缺陷 → 不计入。
                if util is not None and supply_used < cfg.growth_supply_max:
                    util_weighted += float(util) * dt
                    util_dt += dt
        # M3 卡人口逐帧标记（supply_used/supply_cap 已在 M1 处读）
        blocked = (
            supply_used >= supply_cap
            and supply_cap < cfg.supply_engine_max
            and minerals >= cfg.supply_can_build_minerals
        )
        block_flags.append((float(s.get("t", 0.0)), dt, blocked))

    avg_excess_bank = bank_integral / duration if duration > 0 else 0.0
    prod_util = (util_weighted / util_dt) if util_dt > 0 else None
    avg_larva_idle = (larva_idle_integral / duration) if (race == "ZERG" and duration > 0) else None
    if race != "ZERG":
        larva_idle_integral_out: float | None = None
    else:
        larva_idle_integral_out = larva_idle_integral

    supply_block_time = _sum_qualifying_runs(block_flags, cfg.supply_block_min_run_s)

    worst, diagnosis = _diagnose(
        cfg, race, avg_excess_bank, prod_util, avg_larva_idle, supply_block_time, snaps
    )
    report = EfficiencyReport(
        race=race,
        window=(float(snaps[0].get("t", 0.0)), float(snaps[-1].get("t", 0.0))),
        n_snapshots=len(snaps),
        duration_s=duration,
        bank_integral=bank_integral,
        avg_excess_bank=avg_excess_bank,
        prod_util=prod_util,
        larva_idle_integral=larva_idle_integral_out,
        avg_larva_idle=avg_larva_idle,
        supply_block_time=supply_block_time,
        worst_dimension=worst,
        diagnosis=diagnosis,
    )
    _maybe_normalize(report, cfg, race)
    return report


def _sum_qualifying_runs(flags: list[tuple[float, float, bool]], min_run_s: float) -> float:
    """累加"连续 blocked 段总时长 ≥ min_run_s"的段（滤掉单/双帧 JIT 健康重叠）。"""
    total = 0.0
    run_dt = 0.0
    for _t, dt, blocked in flags:
        if blocked:
            run_dt += dt
        else:
            if run_dt >= min_run_s:
                total += run_dt
            run_dt = 0.0
    if run_dt >= min_run_s:
        total += run_dt
    return total


def _diagnose(
    cfg: ScoreConfig,
    race: str,
    avg_excess_bank: float,
    prod_util: float | None,
    avg_larva_idle: float | None,
    supply_block_time: float,
    snaps: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    """启发式定最差维度 + 人读诊断（不进决策，只给改进方向）。"""
    diag: list[str] = []
    # 各维度"超标比"（>1 = 超过 bad 阈值）
    bank_ratio = avg_excess_bank / cfg.diag_bank_bad if cfg.diag_bank_bad > 0 else 0.0
    if race == "ZERG":
        # 虫族 M2 用 larva 闲置，借 bank 同款刻度粗略归一
        prod_ratio = (avg_larva_idle or 0.0) / max(cfg.larva_floor, 1)
        prod_msg = (
            f"larva 平均闲置 {avg_larva_idle:.1f}" if avg_larva_idle is not None else "larva 数据缺"
        )
    else:
        prod_ratio = (
            (cfg.diag_prod_bad - prod_util) / cfg.diag_prod_bad
            if (prod_util is not None and prod_util < cfg.diag_prod_bad)
            else 0.0
        )
        prod_msg = f"产能利用率 {prod_util:.2f}" if prod_util is not None else "产能数据缺"
    supply_ratio = supply_block_time / cfg.diag_supply_bad if cfg.diag_supply_bad > 0 else 0.0

    if avg_excess_bank > cfg.diag_bank_bad:
        diag.append(f"囤钱：平均超储 {avg_excess_bank:.0f}（该补产能/出兵没补，钱没花干净）")
    if race != "ZERG" and prod_util is not None and prod_util < cfg.diag_prod_bad:
        diag.append(f"产能空：{prod_msg}（折跃门/产能建筑大量空闲 → 主力兵种产量没拉满）")
    if race == "ZERG" and avg_larva_idle is not None and avg_larva_idle > cfg.larva_floor:
        diag.append(f"larva 堆积：{prod_msg}（larva 没消耗 → 出兵不积极）")
    if supply_block_time > cfg.diag_supply_bad:
        diag.append(f"卡人口：累计 {supply_block_time:.0f}s 有钱有产能却卡口（补给节奏跟不上）")

    ratios = {"bank": bank_ratio, "prod": prod_ratio, "supply": supply_ratio}
    worst = max(ratios, key=lambda k: ratios[k])
    if ratios[worst] <= 0:
        worst = "none"
        diag.append("运营健康：三维度均未超启发式阈值")
    return worst, diag


def _maybe_normalize(report: EfficiencyReport, cfg: ScoreConfig, race: str) -> None:
    """REF 给定时算 0-100 子分 + 加权总分（仅人读；决策用原始指标）。"""

    def _clamp01(x: float) -> float:
        return max(0.0, min(1.0, x))

    subs: dict[str, float] = {}
    if cfg.ref_bank:
        subs["bank"] = 100.0 * _clamp01(1.0 - report.avg_excess_bank / cfg.ref_bank)
    if race == "ZERG":
        if cfg.ref_larva_idle and report.avg_larva_idle is not None:
            subs["prod"] = 100.0 * _clamp01(1.0 - report.avg_larva_idle / cfg.ref_larva_idle)
    elif report.prod_util is not None:
        subs["prod"] = 100.0 * _clamp01(report.prod_util)
    if cfg.ref_supply_block:
        subs["supply"] = 100.0 * _clamp01(1.0 - report.supply_block_time / cfg.ref_supply_block)
    report.subscores = subs
    if {"bank", "prod", "supply"} <= subs.keys():
        w1, w2, w3 = cfg.weights
        report.total = w1 * subs["bank"] + w2 * subs["prod"] + w3 * subs["supply"]
