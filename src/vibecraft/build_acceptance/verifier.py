"""Verifier: 解析 telemetry record 列表,对比 AcceptanceSpec,出 pass/fail。

CheatMoney 档:tol×2 + 跳过位置类断言(key_unit_at/army_gather) +
其余只验 by 类。VeryEasy 档:按 spec 精确判定。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from vibecraft.build_acceptance.spec import AcceptanceSpec, Check

# 部队"离家"判定阈值:army_center 距 home 超过此值算出门
_MOVEOUT_HOME_DIST: float = 60.0
# 位置类 check(CheatMoney 跳过)
_POSITION_TYPES = frozenset({"key_unit_at", "army_gather"})
# 建筑别名:某些建筑会 morph(GATEWAY→WARPGATE),验收"有几个 BG"时合并计数
_BUILDING_ALIASES: dict[str, list[str]] = {
    "GATEWAY": ["GATEWAY", "WARPGATE"],
}


@dataclass
class CheckResult:
    check_id: str
    ok: bool
    skipped: bool = False
    detail: str = ""


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.ok or r.skipped for r in self.results)

    def summary(self) -> str:
        n_pass = sum(1 for r in self.results if r.ok)
        n_skip = sum(1 for r in self.results if r.skipped)
        n_total = len(self.results)
        lines = [f"{n_pass}/{n_total} passed ({n_skip} skipped)"]
        for r in self.results:
            tag = "SKIP" if r.skipped else ("PASS" if r.ok else "FAIL")
            lines.append(f"  [{tag}] {r.check_id}  {r.detail}")
        return "\n".join(lines)


@dataclass
class AggregateResult:
    """多局聚合后单个 check 的结果。"""

    check_id: str
    ok: bool  # 多数票通过
    skipped: bool  # 所有有效 run 都 skip
    pass_count: int  # PASS 的 run 数
    run_count: int  # 总 run 数
    detail: str  # 代表性 run 的 detail


@dataclass
class AggregateReport:
    results: list[AggregateResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.ok or r.skipped for r in self.results)

    def summary(self) -> str:
        n_pass = sum(1 for r in self.results if r.ok and not r.skipped)
        n_skip = sum(1 for r in self.results if r.skipped)
        n_total = len(self.results)
        lines = [f"{n_pass}/{n_total} passed ({n_skip} skipped) — 多数票"]
        for r in self.results:
            tag = "SKIP" if r.skipped else ("PASS" if r.ok else "FAIL")
            votes = f"[{r.pass_count}/{r.run_count}]"
            lines.append(f"  [{tag}] {r.check_id} {votes}  {r.detail}")
        return "\n".join(lines)


def aggregate_reports(reports: list[Report]) -> AggregateReport:
    """多局 Report 按 check_id 多数票聚合。

    每个 check：在非 skip 的 run 里 PASS 数 × 2 > 有效 run 数 → ok（严格多数）。
    所有 run 都 skip → skipped。代表性 detail：ok 取一条 PASS 的，否则取一条 FAIL 的。
    """
    if not reports:
        return AggregateReport()
    order = [c.check_id for c in reports[0].results]
    by_id: dict[str, list[CheckResult]] = {cid: [] for cid in order}
    for rep in reports:
        for cr in rep.results:
            by_id.setdefault(cr.check_id, []).append(cr)

    out: list[AggregateResult] = []
    for cid in order:
        crs = by_id[cid]
        run_count = len(crs)
        skip_count = sum(1 for c in crs if c.skipped)
        pass_count = sum(1 for c in crs if c.ok and not c.skipped)
        effective = run_count - skip_count
        if effective == 0:
            out.append(AggregateResult(cid, True, True, 0, run_count, "所有 run 跳过"))
            continue
        ok = pass_count * 2 > effective
        if ok:
            sample = next((c for c in crs if c.ok and not c.skipped), crs[0])
        else:
            sample = next((c for c in crs if not c.ok and not c.skipped), crs[0])
        out.append(AggregateResult(cid, ok, False, pass_count, run_count, sample.detail))
    return AggregateReport(results=out)


def _dist(a: list[float], b: list[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _anchors(telemetry: list[dict[str, Any]]) -> dict[str, Any]:
    for rec in telemetry:
        if rec.get("kind") == "game_start":
            return {
                "home": rec.get("home"),
                "enemy_main": rec.get("enemy_main"),
                "natural": rec.get("natural"),
            }
    return {}


def _snapshot_at(telemetry: list[dict[str, Any]], t: float) -> dict[str, Any] | None:
    """取 t 时刻最近的 snapshot record。"""
    best: dict[str, Any] | None = None
    best_dt = 1e9
    for rec in telemetry:
        if rec.get("kind") != "snapshot":
            continue
        dt = abs(rec.get("t", -1e9) - t)
        if dt < best_dt:
            best_dt = dt
            best = rec
    return best


def _snapshots_in_window(
    telemetry: list[dict[str, Any]], lo: float, hi: float
) -> list[dict[str, Any]]:
    """取 [lo, hi] 时间窗口内的所有 snapshot record。"""
    return [
        rec for rec in telemetry if rec.get("kind") == "snapshot" and lo <= rec.get("t", -1e9) <= hi
    ]


def _check_one(
    check: Check,
    telemetry: list[dict[str, Any]],
    anchors: dict[str, Any],
    tol_mult: float,
) -> CheckResult:
    tol = check.tol * tol_mult
    ctype = check.type

    if ctype in ("building_started", "building_complete"):
        evs = [r for r in telemetry if r.get("kind") == ctype and r.get("unit") == check.unit]
        if not evs:
            return CheckResult(check.id, False, detail=f"{check.unit} 无 {ctype} 事件")
        actual = min(r["t"] for r in evs)
        return _judge_time(check, actual, tol)

    if ctype == "upgrade_complete":
        evs = [
            r
            for r in telemetry
            if r.get("kind") == "upgrade_complete" and r.get("upgrade") == check.upgrade
        ]
        if not evs:
            return CheckResult(check.id, False, detail=f"{check.upgrade} 未完成")
        return _judge_time(check, min(r["t"] for r in evs), tol)

    if ctype in ("worker_count", "unit_count", "building_count"):
        # 计数类 check 用时间窗口内的最大值判定：at → [at-tol, at+tol]，
        # by → [0, by]。理由：① 单一精确时刻取样受 SC2 帧抖动影响大；
        # ② 单位数非单调（DT 边骚扰边死、2 DT 合 1 Archon），"某刻恰好 N 个"
        # 不可靠；③ spec 的 at±tol 注释本就是窗口语义。窗口内达到过 min 即 PASS。
        if ctype == "building_count" and check.unit is None:
            return CheckResult(check.id, False, detail="无 unit")
        if check.at_s is not None:
            lo, hi = check.at_s - tol, check.at_s + tol
            win_label = f"{check.at_s:.0f}±{tol:.0f}s"
        elif check.by_s is not None:
            lo, hi = 0.0, check.by_s
            win_label = f"by {check.by_s:.0f}s"
        else:
            return CheckResult(check.id, False, detail="无 at/by")
        snaps = _snapshots_in_window(telemetry, lo, hi)
        if not snaps:
            return CheckResult(check.id, False, detail=f"窗口 {win_label} 无 snapshot")

        if ctype == "worker_count":
            actual = max(int(s.get("workers", 0)) for s in snaps)
            label = "workers"
        elif ctype == "unit_count":
            actual = max(int(s.get("units", {}).get(check.unit, 0)) for s in snaps)
            label = check.unit or "?"
        else:  # building_count — check.unit 已在上方保证非 None
            assert check.unit is not None
            names = _BUILDING_ALIASES.get(check.unit, [check.unit])
            actual = max(sum(int(s.get("buildings", {}).get(n, 0)) for n in names) for s in snaps)
            label = "+".join(names) if len(names) > 1 else check.unit

        ok = actual >= (check.min or 0)
        return CheckResult(
            check.id,
            ok,
            detail=f"actual={actual} ({label}) need>={check.min} @ {win_label}",
        )

    if ctype == "key_unit_at":
        t = check.at_s if check.at_s is not None else check.by_s
        if t is None or check.near is None:
            return CheckResult(check.id, False, detail="无 at/by 或 near")
        anchor = anchors.get(check.near)
        snap = _snapshot_at(telemetry, t)
        if snap is None or anchor is None:
            return CheckResult(check.id, False, detail="无 snapshot/锚点")
        positions = snap.get("key_units", {}).get(check.unit, [])
        if not positions:
            return CheckResult(check.id, False, detail=f"{check.unit} 不在场")
        nearest = min(_dist(p, anchor) for p in positions)
        ok = nearest <= (check.within or 0)
        return CheckResult(
            check.id, ok, detail=f"距 {check.near} {nearest:.1f} (need<={check.within})"
        )

    if ctype == "army_gather":
        t = check.at_s if check.at_s is not None else check.by_s
        if t is None or check.near is None:
            return CheckResult(check.id, False, detail="无 at/by 或 near")
        anchor = anchors.get(check.near)
        snap = _snapshot_at(telemetry, t)
        if snap is None or anchor is None or snap.get("army_center") is None:
            return CheckResult(check.id, False, detail="无 army_center/锚点")
        d = _dist(snap["army_center"], anchor)
        ok = d <= (check.within or 0)
        return CheckResult(check.id, ok, detail=f"army 距 {check.near} {d:.1f}")

    if ctype == "attack_moveout":
        home = anchors.get("home")
        if home is None:
            return CheckResult(check.id, False, detail="无 home 锚点")
        moveout_t: float | None = None
        for rec in sorted(
            (r for r in telemetry if r.get("kind") == "snapshot"),
            key=lambda r: r.get("t", 0),
        ):
            ac = rec.get("army_center")
            if ac and _dist(ac, home) > _MOVEOUT_HOME_DIST:
                moveout_t = rec["t"]
                break
        if moveout_t is None:
            return CheckResult(check.id, False, detail="部队从未出门")
        return _judge_time(check, moveout_t, tol)

    return CheckResult(check.id, False, detail=f"未知 check type {ctype}")


def _judge_time(check: Check, actual: float, tol: float) -> CheckResult:
    """按 at±tol 或 by 上界判定一个时间值。"""
    if check.by_s is not None:
        ok = actual <= check.by_s
        return CheckResult(check.id, ok, detail=f"actual {actual:.0f}s, by {check.by_s:.0f}s")
    at_s = check.at_s
    if at_s is None:
        return CheckResult(check.id, False, detail="无 at/by")
    lo, hi = at_s - tol, at_s + tol
    ok = lo <= actual <= hi
    return CheckResult(check.id, ok, detail=f"actual {actual:.0f}s, want {at_s:.0f}±{tol:.0f}s")


def verify(
    telemetry: list[dict[str, Any]],
    spec: AcceptanceSpec,
    opponent: str = "veryeasy",
) -> Report:
    """主入口。opponent ∈ veryeasy / cheatmoney。"""
    cheat = opponent.lower() == "cheatmoney"
    tol_mult = 2.0 if cheat else 1.0
    anchors = _anchors(telemetry)
    report = Report()
    for check in spec.checks:
        if cheat and check.type in _POSITION_TYPES:
            report.results.append(
                CheckResult(check.id, ok=True, skipped=True, detail="CheatMoney 档跳过位置类")
            )
            continue
        report.results.append(_check_one(check, telemetry, anchors, tol_mult))
    return report
