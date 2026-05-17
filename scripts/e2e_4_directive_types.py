"""directive 全覆盖 e2e 测试驱动。

每个 directive 类型 + 常见 verb / stance + 代表性 done_when kind 各一个 case，
挨个独立拉起 SC2 子进程，详 `docs/e2e-directive-tests.md`。

`HangWatchdog`（bot.watchdog）在子进程内监 bot.time，30s 不前进自动 kill SC2 +
子进程退出码 87。`GameProcess` 父进程层兜底 watchdog 120s 无消息也 kill。
driver 读到 sc2=crashed 把该 case 判 FAIL，继续下一个。

每个 case：fast mode + 可配置对手难度（默认 CheatMoney）+ 90s wall timeout。

verify 分两层：
  1. verify_field（原有）：snapshot 字段非空 OR events 有 directive.committed
  2. verify_log_patterns（新）：正则 grep events/snapshots 序列化 JSON（可选，增强验证）

注意：GameProcess 用 multiprocessing.Queue 做 IPC，子进程 stdout 不被 driver
捕获。verify_log_patterns 因此对 **events + snapshots** 序列化 JSON 做 regex 匹配：
- events 含 directive.queued（display 字段含中文描述）、directive.committed、
  strategy.set（strategy_id 字段）、directive.status_changed（status/reason）
- snapshots 含 active_tactics（verb 字段）、production_overrides（unit_type）等

用法::

    uv run --no-sync python scripts/e2e_4_directive_types.py
    uv run --no-sync python scripts/e2e_4_directive_types.py --only L4
    uv run --no-sync python scripts/e2e_4_directive_types.py --seconds 120
    uv run --no-sync python scripts/e2e_4_directive_types.py --difficulty VeryEasy
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vibecraft.bot.watchdog import kill_sc2_processes
from vibecraft.server.game_process import GameConfig, GameProcess


@dataclass
class Case:
    name: str
    inject: str
    inject_after: int
    verify_field: str  # "strategy_changed" | "active_tactics" | "standing_orders" | "production_overrides"
    verify_log_patterns: list[str] = field(default_factory=list)
    """每条 pattern 对 events+snapshots 序列化 JSON 做 regex 搜索；全部命中才算执行层通过。
    空 list 跳过执行层 verify（兼容现有 case，不破坏回归）。

    能匹配的字段示例（因 GameProcess 用 multiprocessing.Queue，子进程 stdout 不可 grep）：
    - events JSON：directive.queued.payload.display（如 "哨兵 × 2 已加入生产队列"）
    - events JSON：strategy.set.payload.strategy_id（如 "iac_2base"）
    - events JSON：directive.status_changed.payload.status（如 "active"）
    - snapshots JSON：active_tactics[*].verb（如 "attack" / "harass"）
    - snapshots JSON：production_overrides[*].unit_type（如 "SENTRY"）
    - snapshots JSON：strategy.opening.id（如 "sustain"）
    """


CASES: list[Case] = [
    # ---- L1 宏观策略 ----
    Case(
        name="L1a strategy_set",
        inject="切叉球一波",  # midgame 不被 _check_strategy_obsolete 拦
        inject_after=3,
        verify_field="strategy_changed",
        # strategy.set event payload.strategy_id 含 iac（叉球剧本 id 前缀）；
        # 或 snapshot strategy.midgame.id / strategy.opening.id 变化。
        verify_log_patterns=["iac"],
    ),
    Case(
        name="L1b strategy_cancel",
        inject="取消所有剧本",
        inject_after=3,
        verify_field="strategy_cleared",
        # _dispatch_cancel 调 facade.set_build("sustain")，
        # snapshot strategy 字段清空后出现 "sustain" 或 opening:null。
        verify_log_patterns=["sustain"],
    ),
    # ---- L2 战术目标 (tactical_objective + engagement_constraint) ----
    Case(
        name="L2a tactical_attack",
        inject="进攻对方自然",
        inject_after=3,
        verify_field="active_tactics",
        # directive.status_changed payload.status="active" + snapshot active_tactics 含 verb=attack
        verify_log_patterns=["attack"],
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
        # snapshot active_tactics 或 directive.status_changed 含 defend
        verify_log_patterns=["defend"],
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
        # 原 inject "那个探机去看一下气矿" LLM 解 unit_claim.verb="scout",
        # 但 Verb enum 没 scout(只有 move_to/hold_position/patrol/...)。
        # 改用 move_to 强信号:"移动到"。
        name="L3b unit_claim ephemeral",
        inject="让那个探机移动到气矿",
        inject_after=3,
        verify_field="any_directive_committed",
    ),
    Case(
        # 原 inject "派探机看一眼 11 点" LLM 把 "派探机" 解 unit_claim 而非
        # 顶层 scout directive,同样卡 Verb enum。去掉 unit 限定让 LLM 用顶层
        # scout directive(它有自己的 target 而非 task.verb)。
        name="L3c scout",
        inject="侦察一下对方主基地",
        inject_after=3,
        verify_field="any_directive_committed",
    ),
    Case(
        # 原 inject "11 点放个水晶" 是 build_at 但 LLM 不会算地图坐标,
        # 把 "11 点" 当 "11 o'clock" 字符串,point 字段 float 校验失败。
        # build_at 是 PWA UI 玩家点击坐标用,LLM 无法稳定 e2e。
        # 换成 engagement_constraint(stance=hold)覆盖第 3 个 stance。
        name="L3d engagement_hold (3rd stance)",
        inject="所有人原地待命别动",
        inject_after=3,
        verify_field="any_directive_committed",
    ),
    # ---- L4 产能调整 ----
    Case(
        name="L4a production_override (unit_count_built_since)",
        inject="下个 BG 出俩哨兵",
        inject_after=3,
        verify_field="production_overrides",
        # directive.queued event display 含 "哨兵"；
        # snapshot production_overrides[*].unit_type 含 SENTRY（大小写不限）
        verify_log_patterns=[r"(?i)sentry|哨兵"],
    ),
    Case(
        name="L4b tech_override (tech_done)",
        inject="先研闪烁",
        inject_after=3,
        verify_field="production_overrides",
        # directive.queued display 含 "BLINK" 或 "闪烁"
        verify_log_patterns=[r"(?i)blink|闪烁"],
    ),
    Case(
        name="L4c expansion_override (expansion_count)",
        inject="马上去开三矿",
        inject_after=3,
        verify_field="production_overrides",
        # directive.queued display 含 "矿" 或 "开矿"；snapshot 或事件含 expand
        verify_log_patterns=[r"矿|expand"],
    ),
    # ---- O 系列：structure_override + L2 进阶 ----
    Case(
        name="O1 structure_override 补到 8 BG",
        inject="家里补到 8 个 BG",
        inject_after=5,
        verify_field="production_overrides",
        # director.py:1402 logger.info("structure_override BUILD %s near=...", type_id, ...)
        # 该 log 走子进程 logging，不过 events 流有 directive.queued display "GATEWAY"
        # 及 snapshot production_overrides[*].structure_type="GATEWAY"
        verify_log_patterns=[r"(?i)gateway|BG|兵营"],
    ),
    Case(
        name="O2 structure_override ramp 1 cannon",
        inject="在坡道放一个炮台",
        inject_after=5,
        verify_field="production_overrides",
        # snapshot production_overrides[*].structure_type="PHOTONCANNON"
        # directive.queued display 含 "cannon" 或 "炮台"
        verify_log_patterns=[r"(?i)photoncannon|cannon|炮台"],
    ),
    Case(
        name="L2 进攻自然 done_when=None",
        inject="进攻对方自然基地",
        inject_after=3,
        verify_field="active_tactics",
        # 无 done_when → directive 不自动 done，active_tactics 持续有记录
        # snapshot active_tactics verb=attack + status=active
        verify_log_patterns=["attack"],
    ),
    Case(
        name="L2 派 5 凤凰骚扰",
        inject="派 5 个凤凰去骚扰对方主基地",
        inject_after=3,
        verify_field="active_tactics",
        # _exec_l2_squad 路径：set_unit_role LLM_CONTROLLED + TacticalSquad 创建
        # snapshot active_tactics verb=harass；events directive.status_changed status=active/on_hold
        verify_log_patterns=[r"(?i)harass|骚扰"],
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


def _verify_log_patterns(
    events: list[tuple[float, str, dict[str, Any]]],
    snapshots: list[dict[str, Any]],
    patterns: list[str],
) -> tuple[bool, str]:
    """所有 pattern 都在 events+snapshots 序列化 JSON 出现才 PASS。

    grep 目标：events 每条 frame dict JSON + snapshots 每条 dict JSON，合并成
    单个大字符串。GameProcess 用 multiprocessing.Queue IPC，子进程 stdout 不可
    直接捕获，因此用 queue 传上来的结构化数据作为 log 替代来源。
    """
    if not patterns:
        return True, "(no log verify)"

    # 构建 grep 目标字符串：所有 events payload + snapshots 的 JSON dump
    import contextlib
    corpus_parts: list[str] = []
    for _ts, _k, payload in events:
        with contextlib.suppress(Exception):
            corpus_parts.append(json.dumps(payload, ensure_ascii=False))
    for snap in snapshots:
        with contextlib.suppress(Exception):
            corpus_parts.append(json.dumps(snap, ensure_ascii=False))
    corpus = "\n".join(corpus_parts)

    missing: list[str] = []
    for p in patterns:
        if not re.search(p, corpus):
            missing.append(p)

    if missing:
        # 截前 3 条防输出过长
        return False, f"events/snapshot 缺 pattern: {missing[:3]}"
    return True, f"log patterns 全命中 ({len(patterns)})"


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
                if str(sc2) in ("crashed", "ended") and sc2_ended_at[0] is None:
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
        # 兜底 kill SC2_x64 孤儿(GameProcess.stop 内已有兜底,这里 case driver
        # 再保险一次。CheatMoney 多 case 跑容易堆积孤儿,user 实际遇到过)
        killed = kill_sc2_processes()
        if killed:
            log.info("[%s] cleanup killed %d lingering SC2 process(es)", case.name, killed)

    elapsed_total = time.time() - start_ts

    # verify — 两层：
    # 1. verify_field（原有）：snapshot 字段非空 OR events directive.committed
    # 2. verify_log_patterns（新，可选）：events+snapshots JSON grep
    if case.verify_field == "strategy_changed":
        ok1, reason1 = _verify_strategy_changed(snapshots, events, initial_opening)
    elif case.verify_field == "strategy_cleared":
        ok1, reason1 = _verify_strategy_cleared(snapshots, events, initial_opening)
    elif case.verify_field == "any_directive_committed":
        ok1, reason1 = _verify_any_directive_committed(snapshots, events)
    else:
        ok1, reason1 = _verify_field_non_empty(snapshots, events, case.verify_field)

    if ok1 and case.verify_log_patterns:
        ok2, reason2 = _verify_log_patterns(events, snapshots, case.verify_log_patterns)
        ok = ok1 and ok2
        reason = f"{reason1} | log: {reason2}"
    else:
        ok = ok1
        reason = reason1

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

    # 最终兜底:任何 case 漏掉的 SC2 孤儿
    final_killed = kill_sc2_processes()
    if final_killed:
        log.info("final cleanup killed %d residual SC2 process(es)", final_killed)

    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
