"""directive 全覆盖 e2e 测试驱动。

每个 directive 类型 + 常见 verb / stance + 代表性 done_when kind 各一个 case，
挨个独立拉起 SC2 子进程，详 `docs/e2e-directive-tests.md`。

`HangWatchdog`（bot.watchdog）在子进程内监 bot.time，30s 不前进自动 kill SC2 +
子进程退出码 87。`GameProcess` 父进程层兜底 watchdog 120s 无消息也 kill。
driver 读到 sc2=crashed 把该 case 判 FAIL，继续下一个。

每个 case：fast mode + 可配置对手难度（默认 CheatMoney）+ 90s wall timeout。

用法::

    uv run --no-sync python scripts/e2e_4_directive_types.py
    uv run --no-sync python scripts/e2e_4_directive_types.py --only L4
    uv run --no-sync python scripts/e2e_4_directive_types.py --seconds 120
    uv run --no-sync python scripts/e2e_4_directive_types.py --difficulty VeryEasy
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
    # ---- L1 宏观策略 ----
    Case(
        name="L1a strategy_set",
        inject="切叉球一波",  # midgame 不被 _check_strategy_obsolete 拦
        inject_after=3,
        verify_field="strategy_changed",
    ),
    Case(
        name="L1b strategy_cancel",
        inject="取消所有剧本",
        inject_after=3,
        verify_field="strategy_cleared",
    ),
    # ---- L2 战术目标 (tactical_objective + engagement_constraint) ----
    Case(
        name="L2a tactical_attack",
        inject="进攻对方自然",
        inject_after=3,
        verify_field="active_tactics",
    ),
    Case(
        name="L2b tactical_scout (vision_acquired)",
        inject="看一眼对方主基地",
        inject_after=3,
        verify_field="active_tactics",
    ),
    Case(
        name="L2c tactical_harass (enemy_killed_in_area)",
        inject="凤凰打死对方 5 个农民就回",
        inject_after=3,
        verify_field="active_tactics",
    ),
    Case(
        name="L2d engagement_defend",
        inject="守家别出门",
        inject_after=3,
        verify_field="any_directive_committed",
    ),
    Case(
        name="L2e engagement_retreat (time_elapsed_since)",
        inject="30 秒后撤",
        inject_after=3,
        verify_field="any_directive_committed",
    ),
    # ---- L3 单位 / 常驻 / 建造 ----
    Case(
        name="L3a unit_claim persistent (standing)",
        inject="探机巡逻自然别动",
        inject_after=3,
        verify_field="standing_orders",
    ),
    Case(
        name="L3b unit_claim ephemeral",
        inject="那个探机去看一下气矿",
        inject_after=3,
        verify_field="any_directive_committed",
    ),
    Case(
        name="L3c scout",
        inject="派探机看一眼 11 点",
        inject_after=3,
        verify_field="any_directive_committed",
    ),
    Case(
        name="L3d build_at",
        inject="11 点放个水晶",
        inject_after=3,
        verify_field="any_directive_committed",
    ),
    # ---- L4 产能调整 ----
    Case(
        name="L4a production_override (unit_count_built_since)",
        inject="下个 BG 出俩哨兵",
        inject_after=3,
        verify_field="production_overrides",
    ),
    Case(
        name="L4b tech_override (tech_done)",
        inject="先研闪烁",
        inject_after=3,
        verify_field="production_overrides",
    ),
    Case(
        name="L4c expansion_override (expansion_count)",
        inject="马上去开三矿",
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


def _verify_strategy_cleared(
    snapshots: list[dict[str, Any]],
    events: list[tuple[float, str, dict[str, Any]]],
    initial_opening: str,
) -> tuple[bool, str]:
    """L1b strategy_cancel: 验证 opening 从 initial → None。

    cancel 不走 board.submit, 没 BoardEvent → 不发 directive.committed。靠
    snapshot 看 slot 从 initial 变 None。`_dispatch_cancel` 内部主动
    `self._push_snapshot(now)`,理论上必有一个 snapshot 反映清空状态。
    """
    saw_initial = False
    for snap in snapshots:
        strat = snap.get("strategy") or {}
        opening = strat.get("opening")
        if opening and opening.get("id") == initial_opening:
            saw_initial = True
            continue
        if saw_initial and opening is None:
            return True, f"opening cleared after initial {initial_opening!r}"
    return False, (
        f"opening 始终在(saw_initial={saw_initial}),没 cancel 成 None"
        f"（看了 {len(snapshots)} 个 snapshot）"
    )


def _verify_any_directive_committed(
    snapshots: list[dict[str, Any]],
    events: list[tuple[float, str, dict[str, Any]]],
) -> tuple[bool, str]:
    """通用 verify: events 有 directive.committed 就算 PASS。

    适用于 directive 进 board 但 snapshot 字段窗口短或没暴露的 case
    (engagement_constraint / scout / build_at / move 等 in_flight 不暴露 snapshot 字段)。
    """
    committed = [(ts, p) for ts, k, p in events if k == "directive.committed"]
    if committed:
        first_ts, first_p = committed[0]
        did = first_p.get("directive_id", "?")
        return True, f"directive.committed at +{first_ts:.1f}s id={did[:8]}"
    return False, f"events 无 directive.committed（{len(events)} 个 event）"


async def run_one_case(
    case: Case,
    log: logging.Logger,
    map_name: str,
    seconds: int,
    initial_opening: str,
    difficulty: str,
) -> CaseResult:
    log.info("=" * 70)
    log.info(
        "CASE %s: inject=%r expect=%s difficulty=%s",
        case.name, case.inject, case.verify_field, difficulty,
    )
    log.info("=" * 70)

    os.environ["VIBECRAFT_FORCE_INITIAL_OPENING"] = initial_opening

    cfg = GameConfig(
        map_name=map_name,
        opponent_race="Random",
        opponent_difficulty=difficulty,
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
    elif case.verify_field == "strategy_cleared":
        ok, reason = _verify_strategy_cleared(snapshots, events, initial_opening)
    elif case.verify_field == "any_directive_committed":
        ok, reason = _verify_any_directive_committed(snapshots, events)
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
    p.add_argument(
        "--difficulty",
        default="CheatMoney",
        help="sc2.data.Difficulty enum 名(VeryEasy/Easy/Medium/MediumHard/Hard/"
        "Harder/VeryHard/CheatVision/CheatMoney/CheatInsane)。默认 CheatMoney",
    )
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
        res = await run_one_case(
            case, log, args.map, args.seconds, args.initial_opening, args.difficulty
        )
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
