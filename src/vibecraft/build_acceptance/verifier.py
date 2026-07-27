"""Verifier: 解析 telemetry record 列表,对比 AcceptanceSpec,出 pass/fail。

CheatMoney 档:tol×2 + 跳过位置类断言(key_unit_at/army_gather) +
其余只验 by 类。VeryEasy 档:按 spec 精确判定。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from vibecraft.build_acceptance.spec import (
    AcceptanceSpec,
    Check,
    EconomyCheckpoint,
    PlayerAction,
)

# 部队"离家"判定阈值:army_center 距 home 超过此值算出门
_MOVEOUT_HOME_DIST: float = 60.0
# 位置类 check(CheatMoney 跳过)
_POSITION_TYPES = frozenset({"key_unit_at", "army_gather"})
# 经济曲线相对偏差的归一化下限(小期望值不放大偏差)
_ECONOMY_FLOOR: dict[str, float] = {"workers": 12.0, "minerals": 200.0, "vespene": 120.0}
_ECONOMY_LABEL: dict[str, str] = {"workers": "农民", "minerals": "余矿", "vespene": "余气"}
# 建筑别名:某些建筑会 morph(GATEWAY→WARPGATE),验收"有几个 BG"时合并计数
_BUILDING_ALIASES: dict[str, list[str]] = {
    "GATEWAY": ["GATEWAY", "WARPGATE"],
    # 人族指挥中心会 morph 成轨道指挥中心 / 行星要塞 ——
    # 验收"有几个基地"时合并计数,否则升轨道后的 command_center check 误判
    "COMMANDCENTER": ["COMMANDCENTER", "ORBITALCOMMAND", "PLANETARYFORTRESS"],
}
# 单位别名:某些单位会临时 morph(WARPPRISM↔WARPPRISMPHASING),验收
# unit_count / key_unit_at 时合并(否则 prism 飞到敌方后 morph phasing,
# 用 WARPPRISM check 找不到 → 假 FAIL,实测 dt_drop_iac game_20260523_120656)
_UNIT_ALIASES: dict[str, list[str]] = {
    "WARPPRISM": ["WARPPRISM", "WARPPRISMPHASING"],
    # 寡妇雷埋地后 morph 成 WIDOWMINEBURROWED
    "WIDOWMINE": ["WIDOWMINE", "WIDOWMINEBURROWED"],
}


@dataclass
class CheckResult:
    check_id: str
    ok: bool
    skipped: bool = False
    detail: str = ""


@dataclass
class EconomyPoint:
    """经济曲线一个 (时间点, 字段) 的标准值 vs 实测。"""

    at_s: float
    field: str  # workers | minerals | vespene
    expected: float
    actual: float

    @property
    def signed_dev(self) -> float:
        return self.actual - self.expected

    @property
    def rel_dev(self) -> float:
        """归一化的绝对偏差,越小越好。"""
        floor = _ECONOMY_FLOOR.get(self.field, 1.0)
        return abs(self.signed_dev) / max(self.expected, floor)


@dataclass
class EconomyReport:
    """经济曲线偏差报告 —— 纯分数,不做 pass/fail。

    一次 run 与 spec.economy_profile 标准值的偏差;score 越小越贴近标准。
    标准值本身是迭代改进的(见 EconomyCheckpoint)。
    """

    points: list[EconomyPoint] = field(default_factory=list)

    @property
    def score(self) -> float:
        """平均相对偏差,越小越好。无 economy_profile → 0。"""
        if not self.points:
            return 0.0
        return sum(p.rel_dev for p in self.points) / len(self.points)

    def summary(self) -> str:
        if not self.points:
            return "经济曲线: spec 未定义 economy_profile"
        lines = [f"经济曲线偏差分: {self.score * 100:.0f}%（越小越贴近标准）"]
        by_t: dict[float, list[EconomyPoint]] = {}
        for p in self.points:
            by_t.setdefault(p.at_s, []).append(p)
        for t in sorted(by_t):
            parts = []
            for p in by_t[t]:
                sign = "+" if p.signed_dev >= 0 else ""
                label = _ECONOMY_LABEL.get(p.field, p.field)
                parts.append(f"{label} {p.actual:.0f}/{p.expected:.0f}({sign}{p.signed_dev:.0f})")
            lines.append(f"  {int(t // 60)}:{int(t % 60):02d}  " + "  ".join(parts))
        return "\n".join(lines)


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)
    economy: EconomyReport = field(default_factory=EconomyReport)

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
        lines.append(self.economy.summary())
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
    economy: EconomyReport = field(default_factory=EconomyReport)

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
        lines.append(self.economy.summary())
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
    return AggregateReport(results=out, economy=_aggregate_economy(reports))


def score_economy(
    telemetry: list[dict[str, Any]], profile: list[EconomyCheckpoint]
) -> EconomyReport:
    """对比 telemetry 与 economy_profile 标准值,出偏差报告(纯分数,不 pass/fail)。"""
    rep = EconomyReport()
    for cp in profile:
        snap = _snapshot_at(telemetry, cp.at_s)
        if snap is None:
            continue
        for fld, expected in (
            ("workers", cp.workers),
            ("minerals", cp.minerals),
            ("vespene", cp.vespene),
        ):
            if expected is None:
                continue
            actual = float(snap.get(fld, 0) or 0)
            rep.points.append(EconomyPoint(cp.at_s, fld, float(expected), actual))
    return rep


def _aggregate_economy(reports: list[Report]) -> EconomyReport:
    """多局 economy 按 (时间点, 字段) 聚合,actual 取平均。"""
    groups: dict[tuple[float, str], list[EconomyPoint]] = {}
    for rep in reports:
        for p in rep.economy.points:
            groups.setdefault((p.at_s, p.field), []).append(p)
    out = EconomyReport()
    for (at_s, fld), pts in sorted(groups.items()):
        actual = sum(p.actual for p in pts) / len(pts)
        out.points.append(EconomyPoint(at_s, fld, pts[0].expected, actual))
    return out


def _dist(a: list[float], b: list[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _anchors(telemetry: list[dict[str, Any]]) -> dict[str, Any]:
    for rec in telemetry:
        if rec.get("kind") == "game_start":
            return {
                "home": rec.get("home"),
                "enemy_main": rec.get("enemy_main"),
                "natural": rec.get("natural"),
                "enemy_natural": rec.get("enemy_natural"),
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


# army_after_player_action 容差:超过此秒数没 snapshot → 判 FAIL(数据缺失)
_PLAYER_ACTION_SNAPSHOT_TOL_S: float = 5.0


def _compare(actual: float, op: str, target: float) -> bool:
    if op == "<":
        return actual < target
    if op == "<=":
        return actual <= target
    if op == ">":
        return actual > target
    if op == ">=":
        return actual >= target
    if op == "==":
        return actual == target
    if op == "!=":
        return actual != target
    return False


def _check_one(
    check: Check,
    telemetry: list[dict[str, Any]],
    anchors: dict[str, Any],
    tol_mult: float,
    player_actions: list[PlayerAction] | None = None,
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
            assert check.unit is not None
            unames = _UNIT_ALIASES.get(check.unit, [check.unit])
            actual = max(sum(int(s.get("units", {}).get(n, 0)) for n in unames) for s in snaps)
            label = "+".join(unames) if len(unames) > 1 else check.unit
        else:  # building_count — check.unit 已在上方保证非 None
            assert check.unit is not None
            names = _BUILDING_ALIASES.get(check.unit, [check.unit])
            actual = max(sum(int(s.get("buildings", {}).get(n, 0)) for n in names) for s in snaps)
            label = "+".join(names) if len(names) > 1 else check.unit

        min_ok = actual >= (check.min or 0)
        max_ok = (check.max is None) or (actual <= check.max)
        ok = min_ok and max_ok
        bounds = []
        if check.min is not None:
            bounds.append(f">={check.min}")
        if check.max is not None:
            bounds.append(f"<={check.max}")
        bounds_str = " ".join(bounds) if bounds else ">=0"
        return CheckResult(
            check.id,
            ok,
            detail=f"actual={actual} ({label}) need {bounds_str} @ {win_label}",
        )

    if ctype == "key_unit_at":
        t = check.at_s if check.at_s is not None else check.by_s
        if t is None or check.near is None:
            return CheckResult(check.id, False, detail="无 at/by 或 near")
        anchor = anchors.get(check.near)
        snap = _snapshot_at(telemetry, t)
        if snap is None or anchor is None:
            return CheckResult(check.id, False, detail="无 snapshot/锚点")
        # 合并 morph alias(WARPPRISM↔PHASING、WIDOWMINE↔BURROWED)
        unames = _UNIT_ALIASES.get(check.unit, [check.unit]) if check.unit else []
        positions: list = []
        for n in unames:
            positions.extend(snap.get("key_units", {}).get(n, []))
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

    if ctype == "pressure_contact":
        # L2「前压」：截至 deadline，主力到过敌方分矿（near，默认 enemy_natural）
        # within 格内 —— 或与敌方主力接触过（双方 army_center 距 within 内，
        # 且当时视野里有敌方军队）。后者天然涵盖「敌方压家、我方在家防御」。
        deadline = check.by_s
        if deadline is None and check.at_s is not None:
            deadline = check.at_s + tol
        if deadline is None:
            return CheckResult(check.id, False, detail="无 at/by")
        within = check.within or 0.0
        anchor = anchors.get(check.near or "enemy_natural")
        best_arrive = 1e9
        best_contact = 1e9
        for s in _snapshots_in_window(telemetry, 0.0, deadline):
            ac = s.get("army_center")
            if not ac:
                continue
            if anchor is not None:
                best_arrive = min(best_arrive, _dist(ac, anchor))
            enemy = s.get("enemy") or {}
            eac = enemy.get("enemy_army_center")
            if eac and int(enemy.get("enemy_army_count", 0) or 0) > 0:
                best_contact = min(best_contact, _dist(ac, eac))
        ok = best_arrive <= within or best_contact <= within
        return CheckResult(
            check.id,
            ok,
            detail=(
                f"到敌方分矿最近 {best_arrive:.0f} / 接触敌方主力最近 "
                f"{best_contact:.0f} (need<={within:.0f}) by {deadline:.0f}s"
            ),
        )

    if ctype == "harass_damage":
        # L3「骚扰见效」：截至 deadline，被我方打到过的敌方农民数 >= min。
        # enemy_workers_harassed = 受我方伤害 ∪ 视野内阵亡的农民(打到即算,
        # 不强求击杀)。累计单调值，取窗口内最大即截至 deadline 的累计。
        deadline = check.by_s
        if deadline is None and check.at_s is not None:
            deadline = check.at_s + tol
        if deadline is None:
            return CheckResult(check.id, False, detail="无 at/by")
        need = check.min or 0
        harassed = 0
        for s in _snapshots_in_window(telemetry, 0.0, deadline):
            harassed = max(
                harassed,
                int((s.get("enemy") or {}).get("enemy_workers_harassed", 0) or 0),
            )
        ok = harassed >= need
        return CheckResult(
            check.id,
            ok,
            detail=f"被骚扰的敌方农民 {harassed} (need>={need}) by {deadline:.0f}s",
        )

    if ctype == "scout_value":
        # 侦查价值：骚扰单位「最后一次出现在对方基地」的游戏时刻 >= min 秒。
        # 不是「在对方基地活多久」,而是「最后一次摸进去侦查是几分钟」——
        # 越晚说明侦查单位活得越久 / 一直在补位侦查,价值越大。给单兵保命
        # 侦查(死神)用:它难稳定杀农民,但只要还能摸进对方家就有侦查价值。
        if check.unit is None or check.near is None or check.within is None:
            return CheckResult(check.id, False, detail="缺 unit/near/within")
        anchor = anchors.get(check.near)
        if anchor is None:
            return CheckResult(check.id, False, detail=f"无 {check.near} 锚点")
        within = check.within
        need_s = float(check.min or 0)
        last_t = 0.0
        unames = _UNIT_ALIASES.get(check.unit, [check.unit])
        for s in telemetry:
            if s.get("kind") != "snapshot":
                continue
            positions: list = []
            for n in unames:
                positions.extend(s.get("key_units", {}).get(n, []))
            if any(_dist(p, anchor) <= within for p in positions):
                last_t = max(last_t, float(s.get("t", 0.0)))
        ok = last_t >= need_s
        return CheckResult(
            check.id,
            ok,
            detail=f"最后一次进对方基地侦查 {last_t:.0f}s (need>={need_s:.0f}s)",
        )

    if ctype == "army_after_player_action":
        # Task #311 player override e2e: 玩家在 player_actions[action_idx].at_s
        # 按 verb 按钮,after_s 秒后 army_center 距 anchors[near] 满足 op + within。
        # spec.player_actions[action_idx] 是 Director 自动 fire 的玩家时间线项。
        if player_actions is None or check.action_idx is None:
            return CheckResult(check.id, False, detail="缺 player_actions/action_idx")
        if check.action_idx >= len(player_actions):
            return CheckResult(
                check.id,
                False,
                detail=f"action_idx {check.action_idx} 越界(player_actions 长 {len(player_actions)})",
            )
        if check.after_s is None or check.near is None or check.within is None:
            return CheckResult(check.id, False, detail="缺 after_s/near/within")
        action = player_actions[check.action_idx]
        target_t = action.at_s + check.after_s
        snap = _snapshot_at(telemetry, target_t)
        if snap is None:
            return CheckResult(check.id, False, detail="无 snapshot 数据")
        # 容差检查:snap 偏离 target_t 太远 → 数据缺失,FAIL 而非凑数判定
        actual_t = float(snap.get("t", -1e9))
        if abs(actual_t - target_t) > _PLAYER_ACTION_SNAPSHOT_TOL_S:
            return CheckResult(
                check.id,
                False,
                detail=f"snapshot 偏离 target {target_t:.0f}s 过 "
                f"{abs(actual_t - target_t):.0f}s(>{_PLAYER_ACTION_SNAPSHOT_TOL_S:.0f}s)",
            )
        army_center = snap.get("army_center")
        anchor = anchors.get(check.near)
        if army_center is None:
            return CheckResult(check.id, False, detail=f"snapshot 无 army_center @ {target_t:.0f}s")
        if anchor is None:
            return CheckResult(check.id, False, detail=f"无 {check.near} 锚点")
        dist = _dist(army_center, anchor)
        ok = _compare(dist, check.op, check.within)
        return CheckResult(
            check.id,
            ok,
            detail=(
                f"action[{check.action_idx}] {action.verb} @ {action.at_s:.0f}s + "
                f"{check.after_s:.0f}s → 主力距 {check.near} {dist:.1f} "
                f"(need {check.op} {check.within:.0f})"
            ),
        )

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
    report = Report(economy=score_economy(telemetry, spec.economy_profile))
    for check in spec.checks:
        if cheat and check.type in _POSITION_TYPES:
            report.results.append(
                CheckResult(check.id, ok=True, skipped=True, detail="CheatMoney 档跳过位置类")
            )
            continue
        report.results.append(
            _check_one(
                check,
                telemetry,
                anchors,
                tol_mult,
                player_actions=spec.player_actions,
            )
        )
    return report
