"""真实 SC2 端到端 directive 验收 runner(2026-05-24 用户)。

跟 build_acceptance.py 同一框架,但加 directive injection:
- 启 N 个 SC2 (default 4 并行) VeryEasy 模式
- 每局按 scenario yaml 的 schedule 在指定 game_time 注入 text 指令
  (走 GameProcess.send_command,等价于玩家在 PWA 输入文本)
- 局结束读 logs/<game_id>/directives.jsonl 统计每条指令的 lifecycle event
- 报告每条 directive 在 N 局中的状态:
    PASS         至少 1 局拿到 released event
    FAIL         submitted 后异常(provider error / validation failed) 等
    NOT_REACHED  全部 N 局都未 release(注入超时 / done_when 没触发) → 需要再开一局重跑

用法:
    .venv/Scripts/python.exe scripts/directive_e2e.py <scenario.yaml> \
        --runs 4 --parallel 4 --opponent veryeasy

输出:
    报告 + 列出 NOT_REACHED 指令(--auto-retry 自动循环重跑直到全 PASS 或达到上限)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

_MAX_INFRA_RETRY = 2
_WALL_CLOCK_LIMIT_S = 900
# 注入轮询间隔(wall-clock 秒;poll telemetry.jsonl 最后一行 t 字段)
_POLL_INTERVAL_S = 0.5
_OPPONENT_DIFFICULTY = {
    "veryeasy": "VeryEasy",
    "easy": "Easy",
    "medium": "Medium",
    "veryhard": "VeryHard",
}


# ============================================================
# Scenario 定义
# ============================================================


@dataclass
class ScheduleItem:
    at_s: float
    text: str
    expect_directive_types: list[str]
    expect_done_within_s: float = 90.0
    # 2026-05-24 用户:持续型 directive(L1 strategy_set / L2 attack 持续 /
    # L3 standing)没有 done_when 自动 released,只需验 committed 即算端到端通。
    # released(default): submitted + committed + released 都齐
    # committed: 仅 submitted + committed(持续型用)
    # submitted: 仅 submitted(最宽松,LLM 解析+ submit 即通)
    # clarification: LLM 应输出 clarification 字段(给玩家选项),不进 directives.jsonl;
    #                acceptance 看 llm_calls/<call>.json 里 outcome_kind 或 response_raw.clarification
    expect_lifecycle: str = "released"
    # 2026-05-24:验 LLM 用了 history(在 interpretation_zh / parsed directive
    # 中含某些关键字)。empty list = 不验。
    expect_interpretation_contains: list[str] = field(default_factory=list)
    # 允许这条 item 失败也算 scenario PASS(指代/延续是 LLM 探索性能力,
    # 部分 case 失败不应破坏整体 scenario)。
    optional: bool = False


@dataclass
class Scenario:
    strategy_id: str
    description: str
    schedule: list[ScheduleItem]


def load_scenario(path: Path) -> Scenario:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Scenario(
        strategy_id=raw["strategy_id"],
        description=raw.get("description", ""),
        schedule=[
            ScheduleItem(
                at_s=float(item["at_s"]),
                text=item["text"],
                expect_directive_types=list(item["expect_directive_types"]),
                expect_done_within_s=float(item.get("expect_done_within_s", 90.0)),
                expect_lifecycle=str(item.get("expect_lifecycle", "released")),
                expect_interpretation_contains=list(item.get("expect_interpretation_contains", [])),
                optional=bool(item.get("optional", False)),
            )
            for item in raw["schedule"]
        ],
    )


# ============================================================
# Race detection (复用 build_acceptance 逻辑)
# ============================================================


def _detect_race(strategy_id: str) -> str:
    for race in ("Protoss", "Terran", "Zerg"):
        if (_ROOT / "strategies" / race.lower() / f"{strategy_id}.yaml").exists():
            return race
    return "Protoss"


def _make_game_id() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    pid = os.getpid()
    suffix = uuid.uuid4().hex[:6]
    return f"e2e_{ts}_{pid}_{suffix}"


def _detect_desktop_size() -> tuple[int, int]:
    import contextlib

    try:
        import ctypes

        with contextlib.suppress(Exception):
            ctypes.windll.shcore.SetProcessDpiAwareness(2)

        class _RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = _RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return int(rect.right - rect.left), int(rect.bottom - rect.top)
    except Exception:
        pass
    return 3440, 1440


# ============================================================
# 单局执行：启 SC2 + 监控 game_time + 注入指令 + 等结束
# ============================================================


def _read_last_game_time(telemetry_path: Path) -> float | None:
    """Tail telemetry.jsonl 最后一行 `t` 字段 = 当前 game_time(秒)。

    None 表示文件不存在或解析失败(初始几秒文件还没创建)。
    """
    try:
        if not telemetry_path.exists():
            return None
        # 简单读 last line(文件不大,full read 也 OK)
        lines = telemetry_path.read_bytes().split(b"\n")
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                t = rec.get("t")
                if isinstance(t, (int, float)):
                    return float(t)
            except Exception:
                continue
        return None
    except Exception:
        return None


@dataclass
class InjectionResult:
    item: ScheduleItem
    injected_at_game_s: float | None = None  # 注入时刻 game_time
    injected_at_wall_s: float | None = None  # 注入时刻 wall_clock(相对启动)


async def _run_one_game(
    scenario: Scenario,
    opponent: str,
    window_x: int,
    window_y: int,
    window_w: int,
    window_h: int,
    label: str,
) -> tuple[str | None, list[InjectionResult]]:
    """跑一局,按 schedule 注入指令。

    返回 (game_id, injections)。game_id=None 表示 infra-fail(SC2 崩溃 / 超时)。
    injections 记录每条指令是否真的注入了(注入时 game_time / wall_clock)。
    """
    game_id = _make_game_id()
    cfg = GameConfig(
        map_name="DaybreakLE",
        my_race=_detect_race(scenario.strategy_id),
        opponent_race="Random",
        opponent_difficulty=_OPPONENT_DIFFICULTY[opponent],
        realtime=False,
        window_x=window_x,
        window_y=window_y,
        window_width=window_w,
        window_height=window_h,
        forced_opening=scenario.strategy_id,
        game_id=game_id,
    )

    telemetry_path = _ROOT / "logs" / game_id / "telemetry.jsonl"
    start_wall = time.monotonic()

    gp = GameProcess()
    gp.start(cfg)

    injections = [InjectionResult(item=it) for it in scenario.schedule]
    pending_idx = list(range(len(scenario.schedule)))  # 还没注入的 index
    ended_normally = False
    crashed = False

    async def _injector() -> None:
        """周期性 poll game_time,触发到时间的注入。"""
        while pending_idx:
            # 检查 SC2 是否还活着
            if not gp.is_running:
                return
            cur_t = _read_last_game_time(telemetry_path)
            if cur_t is None:
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue
            # 注入所有 at_s <= cur_t 的 item
            to_remove = []
            for i in pending_idx:
                item = scenario.schedule[i]
                if cur_t >= item.at_s:
                    cmd = {
                        "type": "command",
                        "text": item.text,
                        "issued_at": round(time.time(), 3),
                    }
                    try:
                        gp.send_command(cmd)
                        injections[i].injected_at_game_s = cur_t
                        injections[i].injected_at_wall_s = time.monotonic() - start_wall
                        print(
                            f"[{label}] inject @ game_t={cur_t:.1f}s "
                            f"(wall {time.monotonic() - start_wall:.1f}s): "
                            f"{item.text!r}"
                        )
                    except Exception as exc:
                        print(f"[{label}] inject FAIL: {exc!r}")
                    to_remove.append(i)
            for i in to_remove:
                pending_idx.remove(i)
            await asyncio.sleep(_POLL_INTERVAL_S)

    async def _consume() -> None:
        nonlocal ended_normally, crashed
        async for msg in gp.raw_events():
            sc2 = msg.get("sc2")
            if sc2 == "ended":
                ended_normally = True
                return
            if sc2 == "crashed":
                crashed = True
                return

    try:
        injector_task = asyncio.create_task(_injector())
        try:
            await asyncio.wait_for(_consume(), timeout=_WALL_CLOCK_LIMIT_S)
        except TimeoutError:
            print(f"[{label}] wall-clock {_WALL_CLOCK_LIMIT_S}s 超时,强制 stop")
            await gp.stop()
            injector_task.cancel()
            return None, injections
        injector_task.cancel()
    finally:
        if not ended_normally:
            await gp.stop()

    if not ended_normally:
        return None, injections

    elapsed = time.monotonic() - start_wall
    print(f"[{label}] 游戏结束({elapsed:.1f}s wall),log: logs/{game_id}/")
    return game_id, injections


# ============================================================
# directives.jsonl 读取 + 状态判定
# ============================================================


@dataclass
class DirectiveLifecycle:
    directive_id: str
    type_: str
    source_text: str = ""
    submitted_at_game_s: float | None = None
    released_at_game_s: float | None = None
    revoked: bool = False
    fail_reason: str | None = None
    raw_events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_released(self) -> bool:
        return self.released_at_game_s is not None

    @property
    def is_fail(self) -> bool:
        return self.fail_reason is not None


def _read_interpretation_for_text(game_id: str, user_text: str) -> str:
    """从 llm_calls/ 找 matching user_text 的 call,返回 interpretation_zh。

    找不到返回空字符串。
    """
    calls_dir = _ROOT / "logs" / game_id / "llm_calls"
    if not calls_dir.exists():
        return ""
    for f in sorted(calls_dir.iterdir()):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if rec.get("user_text") != user_text:
            continue
        out = rec.get("outcome")
        if isinstance(out, dict):
            return str(out.get("interpretation_zh", ""))
    return ""


def _read_clarification_for_text(game_id: str, user_text: str) -> dict | None:
    """从 llm_calls/ 找 user_text 对应的 clarification(response_raw.clarification)。

    含 retry 的多次 call 都看;任一 call 返回 clarification 即视为存在。
    """
    calls_dir = _ROOT / "logs" / game_id / "llm_calls"
    if not calls_dir.exists():
        return None
    for f in sorted(calls_dir.iterdir()):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if rec.get("user_text") != user_text:
            continue
        raw = rec.get("response_raw")
        if isinstance(raw, dict):
            clarif = raw.get("clarification")
            if isinstance(clarif, dict) and clarif.get("options"):
                return clarif
    return None


def _read_directive_log(game_id: str) -> dict[str, DirectiveLifecycle]:
    """读 logs/<game_id>/directives.jsonl,按 directive_id 聚合事件。"""
    dj = _ROOT / "logs" / game_id / "directives.jsonl"
    by_id: dict[str, DirectiveLifecycle] = {}
    if not dj.exists():
        return by_id
    for line in dj.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        did = rec.get("directive_id")
        if not did:
            continue
        if did not in by_id:
            by_id[did] = DirectiveLifecycle(
                directive_id=did,
                type_=rec.get("type", "unknown"),
                source_text=rec.get("source_text", ""),
            )
        lc = by_id[did]
        lc.raw_events.append(rec)
        event = rec.get("event", "")
        ts = rec.get("ts", 0.0)
        if event == "submitted":
            lc.submitted_at_game_s = float(ts)
            lc.source_text = lc.source_text or rec.get("source_text", "")
        elif event == "released":
            lc.released_at_game_s = float(ts)
        elif event == "revoked":
            lc.revoked = True
        elif event == "validation_failed":
            lc.fail_reason = rec.get("reason", "validation_failed")
    return by_id


# ============================================================
# 单局聚合：每条 schedule item 在该局的状态
# ============================================================


@dataclass
class ItemRunResult:
    item: ScheduleItem
    injection: InjectionResult
    matched_directive: DirectiveLifecycle | None = None
    status: str = "NOT_INJECTED"  # NOT_INJECTED / PASS / TIMEOUT / FAIL / NOT_REACHED
    reason: str = ""


def _aggregate_one_game(
    scenario: Scenario,
    game_id: str | None,
    injections: list[InjectionResult],
) -> list[ItemRunResult]:
    """对一局的 schedule items 做状态判定。"""
    results: list[ItemRunResult] = []
    if game_id is None:
        # infra-fail
        for inj in injections:
            results.append(
                ItemRunResult(
                    item=inj.item,
                    injection=inj,
                    status="NOT_REACHED",
                    reason="infra_fail",
                )
            )
        return results

    log = _read_directive_log(game_id)

    for inj in injections:
        item = inj.item
        r = ItemRunResult(item=item, injection=inj)

        if inj.injected_at_game_s is None:
            r.status = "NOT_INJECTED"
            r.reason = "game ended before injection time"
            results.append(r)
            continue

        # 严格匹配:source_text 字面 == 玩家原话(VOICE 来源 directive 必然如此)。
        # 不用时间窗 fallback — 否则会误匹 ProtossBot 自动产生的同类型 directive
        # (如 AutoPylon 的 build_at),导致 LLM 真失败被掩盖。
        matched: DirectiveLifecycle | None = None
        for lc in log.values():
            # 类型必须在 expect 列表里(防同源文本多 directive 误匹)
            if lc.source_text == item.text and (
                not item.expect_directive_types or lc.type_ in item.expect_directive_types
            ):
                matched = lc
                break

        r.matched_directive = matched

        # 2026-05-24: expect_lifecycle=clarification 时,LLM 不应产生 directive
        # 而是输出 clarification 字段。验 llm_calls 里 raw_response 含 clarification。
        if item.expect_lifecycle == "clarification":
            clarif = _read_clarification_for_text(game_id, item.text)
            if clarif is not None:
                opts = clarif.get("options", [])
                r.status = "PASS"
                r.reason = (
                    f"clarification emitted: question={clarif.get('question', '')!r}, "
                    f"{len(opts)} options"
                )
            else:
                r.status = "NOT_REACHED"
                r.reason = (
                    "expected clarification but LLM did not emit it "
                    f"(matched directive: {matched.type_ if matched else 'None'})"
                )
            results.append(r)
            continue

        if matched is None:
            r.status = "FAIL"
            r.reason = (
                f"submitted directive not found in log "
                f"(expected types={item.expect_directive_types})"
            )
            results.append(r)
            continue

        if matched.is_fail:
            r.status = "FAIL"
            r.reason = f"directive failed: {matched.fail_reason}"
            results.append(r)
            continue

        # 按 expect_lifecycle 判定:
        # - released: 必须真 released
        # - committed: 有 committed event 即可(持续型,玩家撤销才关)
        # - submitted: 仅 submitted 即可(最宽松)
        lifecycle = item.expect_lifecycle
        has_committed = any(e.get("event") == "committed" for e in matched.raw_events)

        if lifecycle == "submitted":
            r.status = "PASS"
            r.reason = "submitted (lifecycle=submitted)"
        elif lifecycle == "committed":
            if has_committed:
                r.status = "PASS"
                r.reason = "committed (lifecycle=committed, 持续型 directive)"
            else:
                r.status = "NOT_REACHED"
                r.reason = "submitted but never committed"
        else:  # default "released"
            if matched.is_released:
                elapsed = (matched.released_at_game_s or 0) - (matched.submitted_at_game_s or 0)
                r.status = "PASS"
                r.reason = f"released after {elapsed:.1f}s game_time"
            else:
                r.status = "NOT_REACHED"
                r.reason = "directive submitted but never released before game ended"

        # 2026-05-24: 验证 interpretation_zh 含期望关键字(验 LLM 真用 history)
        if r.status == "PASS" and item.expect_interpretation_contains:
            interp = _read_interpretation_for_text(game_id, item.text)
            missing = [kw for kw in item.expect_interpretation_contains if kw not in interp]
            if missing:
                r.status = "FAIL"
                r.reason = (
                    f"interpretation 不含期望关键字 {missing}: 实际 interpretation={interp!r}"
                )
        results.append(r)
    return results


# ============================================================
# 多局聚合
# ============================================================


@dataclass
class AggregateResult:
    item: ScheduleItem
    per_run: list[ItemRunResult] = field(default_factory=list)

    @property
    def status(self) -> str:
        """多局聚合:任 1 局 PASS → PASS;任意 FAIL 且无 PASS → FAIL;否则 NOT_REACHED。"""
        statuses = [r.status for r in self.per_run]
        if "PASS" in statuses:
            return "PASS"
        if "FAIL" in statuses:
            return "FAIL"
        return "NOT_REACHED"

    @property
    def reason(self) -> str:
        if self.status == "PASS":
            pass_runs = [r for r in self.per_run if r.status == "PASS"]
            return pass_runs[0].reason
        if self.status == "FAIL":
            fail_runs = [r for r in self.per_run if r.status == "FAIL"]
            return f"FAIL ({len(fail_runs)}/{len(self.per_run)} runs): {fail_runs[0].reason}"
        # NOT_REACHED
        return f"{len(self.per_run)}/{len(self.per_run)} runs not reached"


# ============================================================
# Main runner
# ============================================================


async def _run_scenario(
    scenario: Scenario,
    runs: int,
    parallel: int,
    opponent: str,
    win_w: int,
    win_h: int,
) -> list[AggregateResult]:
    """跑 N 局,聚合每条 item 的结果。"""
    desktop_w, desktop_h = _detect_desktop_size()
    grid_cols = min(4, parallel)
    grid_rows = max(1, (parallel + grid_cols - 1) // grid_cols)
    cell_w = desktop_w // grid_cols
    cell_h = desktop_h // grid_rows

    print(
        f"[runner] 跑 {runs} 局,parallel={parallel},opponent={opponent}\n"
        f"[runner] 桌面 {desktop_w}x{desktop_h},网格 {grid_cols}×{grid_rows} "
        f"cell {cell_w}×{cell_h} 窗口 {win_w}×{win_h}\n"
        f"[runner] strategy={scenario.strategy_id}\n"
        f"[runner] schedule:"
    )
    for it in scenario.schedule:
        print(f"  - at {it.at_s:.0f}s: {it.text!r} expect={it.expect_directive_types}")

    sem = asyncio.Semaphore(parallel)
    per_run_results: list[list[ItemRunResult]] = []

    async def _guarded(run_idx: int, gslot: int) -> list[ItemRunResult]:
        async with sem:
            col, row = gslot % grid_cols, gslot // grid_cols
            label = f"run {run_idx}/{runs}"
            for attempt in range(_MAX_INFRA_RETRY + 1):
                game_id, injections = await _run_one_game(
                    scenario,
                    opponent,
                    window_x=col * cell_w,
                    window_y=row * cell_h,
                    window_w=win_w,
                    window_h=win_h,
                    label=f"{label} try {attempt + 1}",
                )
                if game_id is not None:
                    return _aggregate_one_game(scenario, game_id, injections)
                print(f"[runner] {label} infra-fail attempt {attempt + 1}/{_MAX_INFRA_RETRY + 1}")
            return _aggregate_one_game(scenario, None, injections)

    tasks = [asyncio.create_task(_guarded(i + 1, i)) for i in range(runs)]
    per_run_results = await asyncio.gather(*tasks)

    # 聚合
    aggs = [AggregateResult(item=it) for it in scenario.schedule]
    for run_results in per_run_results:
        for i, r in enumerate(run_results):
            aggs[i].per_run.append(r)
    return aggs


def _print_report(aggs: list[AggregateResult]) -> None:
    print("\n" + "=" * 70)
    print("=== Directive E2E Report ===")
    n_pass = sum(1 for a in aggs if a.status == "PASS")
    n_total = len(aggs)
    print(f"{n_pass}/{n_total} passed\n")
    for agg in aggs:
        tag = {"PASS": "[PASS]", "FAIL": "[FAIL]", "NOT_REACHED": "[????]"}[agg.status]
        print(f"  {tag} at {agg.item.at_s:.0f}s {agg.item.text!r}")
        print(f"         {agg.reason}")
        if agg.status != "PASS":
            for i, r in enumerate(agg.per_run):
                print(f"           run {i + 1}: {r.status} - {r.reason}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    ap.add_argument("scenario", help="scenario yaml 路径")
    ap.add_argument("--runs", type=int, default=4, help="总局数")
    ap.add_argument("--parallel", type=int, default=4, help="并发度")
    ap.add_argument(
        "--opponent",
        default="veryeasy",
        choices=sorted(_OPPONENT_DIFFICULTY),
    )
    ap.add_argument("--window-w", type=int, default=800)
    ap.add_argument("--window-h", type=int, default=600)
    ap.add_argument(
        "--auto-retry",
        type=int,
        default=0,
        help="NOT_REACHED 自动重跑次数(default 0 = 不重跑)",
    )
    args = ap.parse_args()

    scenario_path = Path(args.scenario)
    if not scenario_path.exists():
        scenario_path = _ROOT / args.scenario
    if not scenario_path.exists():
        print(f"ERROR: scenario not found: {args.scenario}")
        return 2
    scenario = load_scenario(scenario_path)

    aggs = asyncio.run(
        _run_scenario(
            scenario,
            args.runs,
            args.parallel,
            args.opponent,
            args.window_w,
            args.window_h,
        )
    )
    _print_report(aggs)

    # 自动重跑 NOT_REACHED 的指令
    for retry_idx in range(args.auto_retry):
        not_reached = [a for a in aggs if a.status == "NOT_REACHED"]
        if not not_reached:
            print("\n[runner] 全部 PASS,无需重跑")
            break
        print(
            f"\n[runner] 自动重跑 round {retry_idx + 1}/{args.auto_retry},"
            f"NOT_REACHED 指令 {len(not_reached)} 条"
        )
        sub_scenario = Scenario(
            strategy_id=scenario.strategy_id,
            description=f"{scenario.description} (retry {retry_idx + 1})",
            schedule=[a.item for a in not_reached],
        )
        sub_aggs = asyncio.run(
            _run_scenario(
                sub_scenario,
                args.runs,
                args.parallel,
                args.opponent,
                args.window_w,
                args.window_h,
            )
        )
        _print_report(sub_aggs)
        # merge 回原 aggs
        for sub_agg in sub_aggs:
            for orig_agg in aggs:
                if (
                    orig_agg.item.at_s == sub_agg.item.at_s
                    and orig_agg.item.text == sub_agg.item.text
                ):
                    orig_agg.per_run.extend(sub_agg.per_run)
                    break

    n_pass = sum(1 for a in aggs if a.status == "PASS")
    n_total = len(aggs)
    print(f"\n=== Final: {n_pass}/{n_total} PASSED ===")
    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
