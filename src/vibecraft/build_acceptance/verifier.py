"""Verifier: 解析 telemetry record 列表,对比 AcceptanceSpec,出 pass/fail。

CheatMoney 档:tol×2 + 跳过位置类断言(key_unit_at/army_gather) +
其余只验 by 类。VeryEasy 档:按 spec 精确判定。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

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


def _dist(a: list[float], b: list[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _anchors(telemetry: list[dict]) -> dict[str, list[float]]:
    for rec in telemetry:
        if rec.get("kind") == "game_start":
            return {
                "home": rec.get("home"),
                "enemy_main": rec.get("enemy_main"),
                "natural": rec.get("natural"),
            }
    return {}


def _snapshot_at(telemetry: list[dict], t: float) -> dict | None:
    """取 t 时刻最近的 snapshot record。"""
    best: dict | None = None
    best_dt = 1e9
    for rec in telemetry:
        if rec.get("kind") != "snapshot":
            continue
        dt = abs(rec.get("t", -1e9) - t)
        if dt < best_dt:
            best_dt = dt
            best = rec
    return best


def _check_one(
    check: Check, telemetry: list[dict], anchors: dict, tol_mult: float
) -> CheckResult:
    tol = check.tol * tol_mult
    ctype = check.type

    if ctype in ("building_started", "building_complete"):
        evs = [r for r in telemetry
               if r.get("kind") == ctype and r.get("unit") == check.unit]
        if not evs:
            return CheckResult(check.id, False, detail=f"{check.unit} 无 {ctype} 事件")
        actual = min(r["t"] for r in evs)
        return _judge_time(check, actual, tol)

    if ctype == "upgrade_complete":
        evs = [r for r in telemetry
               if r.get("kind") == "upgrade_complete"
               and r.get("upgrade") == check.upgrade]
        if not evs:
            return CheckResult(check.id, False, detail=f"{check.upgrade} 未完成")
        return _judge_time(check, min(r["t"] for r in evs), tol)

    if ctype in ("worker_count", "unit_count"):
        t = check.at_s if check.at_s is not None else check.by_s
        snap = _snapshot_at(telemetry, t)
        if snap is None:
            return CheckResult(check.id, False, detail="无 snapshot")
        if ctype == "worker_count":
            actual = int(snap.get("workers", 0))
        else:
            actual = int(snap.get("units", {}).get(check.unit, 0))
        ok = actual >= (check.min or 0)
        return CheckResult(check.id, ok,
                           detail=f"actual={actual} need>={check.min} @ {t:.0f}s")

    if ctype == "building_count":
        t = check.at_s if check.at_s is not None else check.by_s
        snap = _snapshot_at(telemetry, t)
        if snap is None:
            return CheckResult(check.id, False, detail="无 snapshot")
        bdict = snap.get("buildings", {})
        names = _BUILDING_ALIASES.get(check.unit, [check.unit])
        actual = sum(int(bdict.get(n, 0)) for n in names)
        ok = actual >= (check.min or 0)
        merged = "+".join(names) if len(names) > 1 else check.unit
        return CheckResult(
            check.id, ok,
            detail=f"actual={actual} ({merged}) need>={check.min} @ {t:.0f}s",
        )

    if ctype == "key_unit_at":
        t = check.at_s
        snap = _snapshot_at(telemetry, t)
        anchor = anchors.get(check.near)
        if snap is None or anchor is None:
            return CheckResult(check.id, False, detail="无 snapshot/锚点")
        positions = snap.get("key_units", {}).get(check.unit, [])
        if not positions:
            return CheckResult(check.id, False, detail=f"{check.unit} 不在场")
        nearest = min(_dist(p, anchor) for p in positions)
        ok = nearest <= (check.within or 0)
        return CheckResult(check.id, ok,
                           detail=f"距 {check.near} {nearest:.1f} (need<={check.within})")

    if ctype == "army_gather":
        t = check.at_s
        snap = _snapshot_at(telemetry, t)
        anchor = anchors.get(check.near)
        if snap is None or anchor is None or snap.get("army_center") is None:
            return CheckResult(check.id, False, detail="无 army_center/锚点")
        d = _dist(snap["army_center"], anchor)
        ok = d <= (check.within or 0)
        return CheckResult(check.id, ok,
                           detail=f"army 距 {check.near} {d:.1f}")

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
        return CheckResult(check.id, ok,
                           detail=f"actual {actual:.0f}s, by {check.by_s:.0f}s")
    lo, hi = check.at_s - tol, check.at_s + tol
    ok = lo <= actual <= hi
    return CheckResult(check.id, ok,
                       detail=f"actual {actual:.0f}s, want {check.at_s:.0f}±{tol:.0f}s")


def verify(
    telemetry: list[dict], spec: AcceptanceSpec, opponent: str = "veryeasy"
) -> Report:
    """主入口。opponent ∈ veryeasy / cheatmoney。"""
    cheat = opponent.lower() == "cheatmoney"
    tol_mult = 2.0 if cheat else 1.0
    anchors = _anchors(telemetry)
    report = Report()
    for check in spec.checks:
        if cheat and check.type in _POSITION_TYPES:
            report.results.append(
                CheckResult(check.id, ok=True, skipped=True,
                            detail="CheatMoney 档跳过位置类")
            )
            continue
        report.results.append(_check_one(check, telemetry, anchors, tol_mult))
    return report
