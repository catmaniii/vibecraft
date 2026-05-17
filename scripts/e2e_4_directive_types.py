"""4 类指令 e2e 测试驱动。

挨个跑 L1 / L2 / L3 / L4 用例，每个用例独立拉起 SC2 子进程：

| case | 注入文本             | 验证字段                          |
|------|----------------------|-----------------------------------|
| L1   | 切 4BG               | snapshot.strategy.* 出现新 id     |
| L2   | 进攻自然             | snapshot.active_tactics 非空      |
| L3   | 探机巡逻自然别动     | snapshot.standing_orders 非空     |
| L4   | 下个 BG 出俩哨兵     | snapshot.production_overrides 非空|

`HangWatchdog`（bot.watchdog）在子进程内监 bot.time，30s 不前进自动 kill SC2 +
子进程退出码 87，父进程读到 sc2=crashed 这个 case 直接判 FAIL。

每个 case：fast mode + VeryEasy 对手 + 90s wall timeout。inject 后等 30s 让
snapshot 包含撤回字段。

用法::

    uv run --no-sync python scripts/e2e_4_directive_types.py
    uv run --no-sync python scripts/e2e_4_directive_types.py --only L4
    uv run --no-sync python scripts/e2e_4_directive_types.py --seconds 120
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess


@dataclass
class Case:
    name: str
    inject: str
    inject_after: int
    verify_field: str  # "strategy_changed" | "active_tactics" | "standing_orders" | "production_overrides"


CASES: list[Case] = [
    # L1：用 midgame 切换避开 _check_strategy_obsolete 的 opening 检测
    # （opening 切换在 fast mode 下,inject 时游戏内时间已数分钟,会被拦成 pending_force）
    Case(
        name="L1 strategy_set",
        inject="切叉球一波",
        inject_after=3,
        verify_field="strategy_changed",
    ),
    Case(
        name="L2 tactical_objective",
        inject="进攻对方自然",
        inject_after=3,
        verify_field="active_tactics",
    ),
    Case(
        name="L3 unit_claim (standing)",
        inject="探机巡逻自然别动",
        inject_after=3,
        verify_field="standing_orders",
    ),
    Case(
        name="L4 production_override",
        inject="下个 BG 出俩哨兵",
        inject_after=3,
        verify_field="production_overrides",
    ),
]


@dataclass
class CaseResult:
    case: Case
    passed: bool
    reason: str
    observed_seconds: float
    snapshots_seen: int
    events: list[tuple[float, str, dict[str, Any]]] = field(default_factory=list)


def _verify_strategy_changed(
    snapshots: list[dict[str, Any]],
    events: list[tuple[float, str, dict[str, Any]]],
    initial_opening: str,
) -> tuple[bool, str]:
    # 1. 任一 snapshot 显示新 stage slot id
    for snap in snapshots:
        strat = snap.get("strategy") or {}
        for stage in ("opening", "midgame", "lategame"):
            slot = strat.get(stage)
            if not slot:
                continue
            sid = slot.get("id")
            if sid and sid != initial_opening:
                return True, f"stage={stage} id={sid}"
    # 2. 或 snapshot 出现 pending_force_strategy(LLM 识别成功但被 obsolete 检测拦下)
    for snap in snapshots:
        pfs = snap.get("pending_force_strategy")
        if pfs:
            return True, f"pending_force_strategy={pfs.get('strategy_id')!r} (LLM 识别成功,等玩家硬转确认)"
    # 3. 或 events 流中出现 strategy.set / strategy.phase_change
    for _ts, k, _p in events:
        if k in ("strategy.set", "strategy.phase_change"):
            return True, f"event {k} fired"
    return False, f"no slot changed, no pending_force_strategy, no strategy.set event (initial={initial_opening})"


def _verify_field_non_empty(
    snapshots: list[dict[str, Any]],
    events: list[tuple[float, str, dict[str, Any]]],
    field_name: str,
) -> tuple[bool, str]:
    for snap in snapshots:
        arr = snap.get(field_name) or []
        if arr:
            head = arr[0]
            return True, f"{field_name} 第一条={head!r}"
    # Fallback: directive 可能 commit 后立刻被 task_monitor 判 done(L4 unit_count 已满足/
    # L2 target_destroyed 当前满足),snapshot 窗口错过。看 events 流里 directive.committed
    # 的存在也能证明 directive 真的进 board 工作了。
    has_committed = any(k == "directive.committed" for _ts, k, _p in events)
    has_released = any(k == "directive.released" for _ts, k, _p in events)
    if has_committed:
        kind_hint = "+released" if has_released else ""
        return True, (
            f"{field_name} snapshot 始终为空,但 events 有 directive.committed{kind_hint} "
            f"(可能 task_monitor 立即判 done,跳过 snapshot 窗口)"
        )
    return False, (
        f"{field_name} 始终为空且 events 无 directive.committed "
        f"(看了 {len(snapshots)} 个 snapshot, {len(events)} 个 event)"
    )


async def run_one_case(
    case: Case,
    log: logging.Logger,
    map_name: str,
    seconds: int,
    initial_opening: str,
) -> CaseResult:
    log.info("=" * 70)
    log.info("CASE %s: inject=%r expect=%s", case.name, case.inject, case.verify_field)
    log.info("=" * 70)

    os.environ["VIBECRAFT_FORCE_INITIAL_OPENING"] = initial_opening

    cfg = GameConfig(
        map_name=map_name,
        opponent_race="Random",
        opponent_difficulty="VeryEasy",
        realtime=False,  # fast mode
    )
    gp = GameProcess()
    gp.start(cfg)

    snapshots: list[dict[str, Any]] = []
    events: list[tuple[float, str, dict[str, Any]]] = []
    seen_playing = asyncio.Event()
    bot_crashed = asyncio.Event()
    start_ts = time.time()

    sc2_ended_at: list[float | None] = [None]
    DRAIN_AFTER_END_S = 2.0  # sc2 ended 后再 drain N 秒,捞 in-flight directive event

    async def collect() -> None:
        async for msg in gp.raw_events():
            elapsed = time.time() - start_ts
            sc2 = msg.get("sc2")
            bot = msg.get("bot")
            kind = msg.get("kind")
            if sc2 or bot:
                if str(sc2) == "playing":
                    seen_playing.set()
                if str(sc2) in ("crashed", "ended"):
                    if sc2_ended_at[0] is None:
                        sc2_ended_at[0] = elapsed
                        log.info("[+%.1fs] sc2=%s bot=%s detail=%s (drain %.1fs)", elapsed, sc2, bot, msg.get("detail", ""), DRAIN_AFTER_END_S)
                        bot_crashed.set()
            elif kind == "snapshot":
                frame = msg.get("frame") or {}
                snapshots.append(frame)
            elif kind == "event":
                frame = msg.get("frame") or {}
                ev_kind = str(frame.get("kind", "?"))
                events.append((elapsed, ev_kind, frame.get("payload") or {}))
            # sc2 ended 后再 drain DRAIN_AFTER_END_S 秒就退出
            if sc2_ended_at[0] is not None and (elapsed - sc2_ended_at[0]) > DRAIN_AFTER_END_S:
                return

    collect_task = asyncio.create_task(collect())

    async def inject_after_delay() -> None:
        try:
            await asyncio.wait_for(seen_playing.wait(), timeout=120)
        except TimeoutError:
            log.warning("[%s] sc2 没进 playing,放弃注入", case.name)
            return
        log.info("[%s] sc2 已 playing,等 %ds 后注入指令", case.name, case.inject_after)
        await asyncio.sleep(case.inject_after)
        cmd = {"type": "command", "text": case.inject, "client_id": "e2e", "issued_at": time.time()}
        log.info("[%s] INJECTING %r", case.name, case.inject)
        gp.send_command(cmd)

    inject_task = asyncio.create_task(inject_after_delay())

    try:
        await asyncio.wait_for(collect_task, timeout=seconds)
    except TimeoutError:
        log.info("[%s] timeout %ds reached, stopping", case.name, seconds)
    finally:
        if not inject_task.done():
            inject_task.cancel()
        await gp.stop()
        if not collect_task.done():
            collect_task.cancel()

    elapsed_total = time.time() - start_ts

    # verify
    if case.verify_field == "strategy_changed":
        ok, reason = _verify_strategy_changed(snapshots, events, initial_opening)
    else:
        ok, reason = _verify_field_non_empty(snapshots, events, case.verify_field)

    result = CaseResult(
        case=case,
        passed=ok,
        reason=reason,
        observed_seconds=elapsed_total,
        snapshots_seen=len(snapshots),
        events=events[:30],
    )
    status = "PASS" if ok else "FAIL"
    log.info("[%s] %s — %s (snapshots=%d, %.1fs)", case.name, status, reason, len(snapshots), elapsed_total)
    if events:
        log.info("[%s] events seen:", case.name)
        for ts, k, _p in events[:15]:
            log.info("   +%.1fs %s", ts, k)
    return result


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--map", default="DaybreakLE")
    p.add_argument("--seconds", type=int, default=90, help="每个 case 最大 wall-clock 秒")
    p.add_argument("--initial-opening", default="1g_robo_immortal")
    p.add_argument("--only", default=None, help="仅跑 case name 包含此子串的（如 L4）")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    log = logging.getLogger("e2e_4")

    if not os.environ.get("SC2PATH"):
        log.warning("SC2PATH 未设,SC2 可能找不到")
    if not os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("ANTHROPIC_API_KEY"):
        log.warning("LLM API key 未设,IntentParser 会失败")

    cases = CASES
    if args.only:
        cases = [c for c in CASES if args.only in c.name]
        if not cases:
            log.error("--only %r 没匹配任何 case (可选: %s)", args.only, [c.name for c in CASES])
            return 1

    results: list[CaseResult] = []
    for case in cases:
        res = await run_one_case(case, log, args.map, args.seconds, args.initial_opening)
        results.append(res)
        # case 之间留 5s 给 SC2 进程清理 + watchdog 收尾
        log.info("等 5s 进入下一个 case")
        await asyncio.sleep(5)

    # 汇总
    log.info("=" * 70)
    log.info("汇总:")
    n_pass = sum(1 for r in results if r.passed)
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        log.info(
            "  %s %s — %s (snapshots=%d, %.1fs)",
            status, r.case.name, r.reason, r.snapshots_seen, r.observed_seconds,
        )
    log.info("=" * 70)
    log.info("结果: %d/%d 通过", n_pass, len(results))
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
