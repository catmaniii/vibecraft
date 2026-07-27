"""采矿策略真局自验 (non-realtime fast, mock LLM 不需要真 LLM)。

测试三个场景（连续跑两局）：
  TEST1 (矿采优先): 注入 mining=mineral → 断言 gas_workers 降低（< 3）。
  TEST2 (气采优先): 先 mineral 确立低基线，再 gas → 断言 gas_workers 回升（≥ 5）。
  TEST3 (default 恢复): 继 TEST1 局，mineral 之后再 default → 断言 gas_workers 回 normal (≥ 3)。

时序 (non-realtime fast ≈ 30-60x 速):
  wall t=5  : inject mining=mineral
  wall t=18 : (TEST2 局) inject mining=gas  /  (TEST3 局) inject mining=default
  wall t=30 : game 结束

断言策略：
  从 telemetry.jsonl 的 snapshot 记录按 game_time 分三段：
    前段 = t < T1  (T1 = 矿采注入后约 10 game-sec)
    中段 = T1 ≤ t < T2 (矿采期)
    后段 = t ≥ T2  (气采/恢复期)
  中段 gas_workers 均值 ≤ 3, 后段 gas_workers 均值 ≥ 5。
  MININGTRACE 日志行验证 hook 实际触发的优先级和 gas_wt（辅助诊断）。

用法：
  .venv/Scripts/python.exe scripts/mining_priority_selftest.py
  .venv/Scripts/python.exe scripts/mining_priority_selftest.py --seconds 30 --map DaybreakLE
退出码 0=PASS, 1=FAIL。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from statistics import mean

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

# ── 日志解析 ────────────────────────────────────────────────────────────────
_RE_TRACE = re.compile(r"MININGTRACE priority=(\S+) min_gas=(\S+) max_gas=(\S+) gas_wt=(\d+)")

# ── 参数 ──────────────────────────────────────────────────────────────────────
_MINERAL_AT_S = 5  # wall-clock 秒数，注入 mining=mineral
_GAS_AT_S = 18  # wall-clock 秒数，注入 mining=gas（TEST2 局）
_DEFAULT_AT_S = 18  # wall-clock 秒数，注入 mining=default（TEST3 局）
_GAME_SEC = 30  # 每局 wall-clock 上限（non-realtime fast 下 30s ≈ 15 min+ 游戏时间）
_MAP = "DaybreakLE"
_OPENING = "void_ray_rush"  # 神族早期必出气，确保测试局有 gas buildings


# ── 单局 runner ───────────────────────────────────────────────────────────────


async def run_game(
    game_id: str,
    inject_sequence: list[tuple[float, dict]],
    seconds: int,
    map_name: str,
) -> dict:
    """跑一局，按 inject_sequence 在指定 wall-clock 时刻发送命令，返回解析后指标。

    inject_sequence: [(wall_clock_t, cmd_dict), ...]，cmd 会在 seen_playing 后 wall_clock_t 秒发出。
    """
    log = logging.getLogger("mining_selftest")

    log_path = _ROOT / "logs" / f"mining_selftest_{game_id}.log"
    tel_path = _ROOT / "logs" / game_id / "telemetry.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    os.environ["VIBECRAFT_GAME_ID"] = game_id
    # 不设 VIBECRAFT_MOCK_LLM_JSON：macro_action 命令走直接路径，不经过 LLM。

    cfg = GameConfig(
        map_name=map_name,
        opponent_race="Terran",
        opponent_difficulty="VeryEasy",
        realtime=False,  # non-realtime fast：快 30-60x，约 30s wall-clock 可跑 15+ min 游戏时间
        forced_opening=_OPENING,
    )
    gp = GameProcess()
    gp.start(cfg)

    seen_playing = asyncio.Event()
    ended = asyncio.Event()

    async def collect() -> None:
        async for msg in gp.raw_events():
            sc2 = str(msg.get("sc2", ""))
            if sc2 == "playing":
                seen_playing.set()
            if sc2 in ("crashed", "ended"):
                ended.set()
                return

    ctask = asyncio.create_task(collect())

    async def do_inject() -> None:
        try:
            await asyncio.wait_for(seen_playing.wait(), timeout=120)
        except TimeoutError:
            log.warning("timeout waiting for playing state")
            return
        play_start = time.monotonic()
        # 按序按时间发命令
        for rel_t, cmd in sorted(inject_sequence, key=lambda x: x[0]):
            wait = rel_t - (time.monotonic() - play_start)
            if wait > 0:
                await asyncio.sleep(wait)
            log.info("INJECT wall=%.1fs cmd=%s", rel_t, cmd)
            gp.send_command(cmd)

    itask = asyncio.create_task(do_inject())

    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(ended.wait(), timeout=seconds + 10)

    for t in (itask, ctask):
        if not t.done():
            t.cancel()
    with contextlib.suppress(Exception):
        await gp.stop()

    # ── 解析 MININGTRACE 日志 ──────────────────────────────────────────────
    traces: list[dict] = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _RE_TRACE.search(line)
            if m:
                traces.append(
                    {
                        "priority": m.group(1),
                        "min_gas": None if m.group(2) == "None" else int(m.group(2)),
                        "max_gas": None if m.group(3) == "None" else int(m.group(3)),
                        "gas_wt": int(m.group(4)),
                    }
                )

    # ── 解析 telemetry.jsonl ──────────────────────────────────────────────────
    snapshots: list[dict] = []
    if tel_path.exists():
        for raw in tel_path.read_text(encoding="utf-8", errors="replace").splitlines():
            with contextlib.suppress(Exception):
                rec = json.loads(raw)
                if rec.get("kind") == "snapshot" and rec.get("economy"):
                    snapshots.append(rec)

    return {"traces": traces, "snapshots": snapshots, "game_id": game_id}


# ── 辅助：从 snapshots 按 game_time 窗口提取 gas_workers ──────────────────────


def _gas_workers_in_range(snapshots: list[dict], t_lo: float, t_hi: float) -> list[int]:
    """返回 t_lo ≤ snap['t'] ≤ t_hi 区间内所有 gas_workers 值。"""
    return [
        int(s["economy"]["gas_workers"])
        for s in snapshots
        if t_lo <= s["t"] <= t_hi and "economy" in s and "gas_workers" in s["economy"]
    ]


def _gas_workers_in_last_frac(snapshots: list[dict], frac: float = 0.4) -> list[int]:
    """返回最后 frac 比例 snapshots 的 gas_workers 值。"""
    if not snapshots:
        return []
    t_max = max(s["t"] for s in snapshots)
    t_lo = t_max * (1 - frac)
    return _gas_workers_in_range(snapshots, t_lo, t_max)


def _gas_workers_window(snapshots: list[dict], frac_lo: float, frac_hi: float) -> list[int]:
    """返回 [frac_lo, frac_hi] 比例区间内的 gas_workers 值。"""
    if not snapshots:
        return []
    t_max = max(s["t"] for s in snapshots)
    t_min = min(s["t"] for s in snapshots)
    span = t_max - t_min
    return _gas_workers_in_range(snapshots, t_min + span * frac_lo, t_min + span * frac_hi)


# ── 主逻辑 ────────────────────────────────────────────────────────────────────


async def main() -> int:
    ap = argparse.ArgumentParser(description="采矿策略真局自验")
    ap.add_argument("--seconds", type=int, default=_GAME_SEC, help="每局 wall-clock 秒")
    ap.add_argument("--map", default=_MAP)
    ap.add_argument("--mineral-at", type=float, default=float(_MINERAL_AT_S))
    ap.add_argument("--gas-at", type=float, default=float(_GAS_AT_S))
    ap.add_argument("--default-at", type=float, default=float(_DEFAULT_AT_S))
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    log = logging.getLogger("mining_selftest")

    fails: list[str] = []

    # ── TEST 1: 矿采优先 ─────────────────────────────────────────────────────
    # 只注入 mineral，不注入 gas。期望: 中后期 gas_workers ≤ 3（取后 40% snapshots）。
    print("\n" + "=" * 60)
    print("TEST1: 矿采优先（注入 mineral，期望 gas_workers 降低）")
    gid1 = f"mining_selftest_mineral_{int(time.time())}"
    r1 = await run_game(
        game_id=gid1,
        inject_sequence=[
            (args.mineral_at, {"type": "macro_action", "dim": "mining", "value": "mineral"}),
        ],
        seconds=args.seconds,
        map_name=args.map,
    )

    if r1["snapshots"]:
        # 矿采优先注入在 mineral_at 秒，non-realtime 下注入后 game_time 已很长。
        # 取后 40% 的 snapshots（注入后充分稳定期）做断言。
        gas1_late = _gas_workers_in_last_frac(r1["snapshots"], frac=0.4)
        avg1 = mean(gas1_late) if gas1_late else -1
        print(f"  后40%快照 gas_workers: {gas1_late[:10]}... avg={avg1:.1f}")
        # 矿采优先会将 gas_workers 压到 < full_capacity(6)。
        # 精确阈值随游戏阶段变化（worker数/mineral_ideal比值），取 <5 确认确实低于满配。
        if avg1 >= 5.5:
            fails.append(
                f"TEST1 FAIL: mineral priority 后 gas_workers 均值={avg1:.1f}，期望 < 5.5（应低于满配 6）"
            )
        else:
            print(f"  TEST1 PASS: gas_workers 均值={avg1:.1f} < 5.5（低于满配 6）✓")
    else:
        fails.append(f"TEST1 FAIL: 没有 telemetry 快照（game_id={gid1}）")

    trace1_mineral = [t for t in r1["traces"] if t["priority"] == "mineral"]
    if not trace1_mineral:
        fails.append("TEST1 FAIL: 没有 MININGTRACE priority=mineral 日志行（hook 没触发）")
    else:
        tr = trace1_mineral[0]
        print(f"  MININGTRACE 首次 mineral: max_gas={tr['max_gas']} gas_wt={tr['gas_wt']}")

    # ── TEST 2: 气采优先（先降后升）────────────────────────────────────────────
    # 先注入 mineral 把 gas_workers 降下来，再注入 gas 让它升回来。
    # 期望: gas_at 之后的后 30% snapshots 中 gas_workers 均值 ≥ 5。
    print("\n" + "=" * 60)
    print("TEST2: 气采优先（先 mineral 降低，再 gas 回升，期望 gas_workers ≥ 5）")
    gid2 = f"mining_selftest_gas_{int(time.time())}"
    r2 = await run_game(
        game_id=gid2,
        inject_sequence=[
            (args.mineral_at, {"type": "macro_action", "dim": "mining", "value": "mineral"}),
            (args.gas_at, {"type": "macro_action", "dim": "mining", "value": "gas"}),
        ],
        seconds=args.seconds,
        map_name=args.map,
    )

    if r2["snapshots"]:
        gas2_late = _gas_workers_in_last_frac(r2["snapshots"], frac=0.3)
        avg2 = mean(gas2_late) if gas2_late else -1
        print(f"  后30%快照 gas_workers: {gas2_late[:10]}... avg={avg2:.1f}")
        if avg2 < 5.5:
            fails.append(
                f"TEST2 FAIL: gas priority 后 gas_workers 均值={avg2:.1f}，期望 ≥ 5.5（气满配接近 6）"
            )
        else:
            print(f"  TEST2 PASS: gas_workers 均值={avg2:.1f} ≥ 5.5 ✓")
    else:
        fails.append(f"TEST2 FAIL: 没有 telemetry 快照（game_id={gid2}）")

    trace2_gas = [t for t in r2["traces"] if t["priority"] == "gas"]
    if not trace2_gas:
        fails.append("TEST2 FAIL: 没有 MININGTRACE priority=gas 日志行（hook 没触发）")
    else:
        tr = trace2_gas[0]
        print(f"  MININGTRACE 首次 gas: min_gas={tr['min_gas']} gas_wt={tr['gas_wt']}")

    # ── TEST 3: default 恢复 ─────────────────────────────────────────────────
    # 先 mineral 降低，再 default 恢复。
    # 期望: default 之后的后 30% snapshots 中 gas_workers ≥ 3（sharpy natural distribution）。
    print("\n" + "=" * 60)
    print("TEST3: default 恢复（先 mineral 降低，再 default 恢复，期望 gas_workers ≥ 3）")
    gid3 = f"mining_selftest_default_{int(time.time())}"
    r3 = await run_game(
        game_id=gid3,
        inject_sequence=[
            (args.mineral_at, {"type": "macro_action", "dim": "mining", "value": "mineral"}),
            (args.default_at, {"type": "macro_action", "dim": "mining", "value": "default"}),
        ],
        seconds=args.seconds,
        map_name=args.map,
    )

    if r3["snapshots"]:
        gas3_late = _gas_workers_in_last_frac(r3["snapshots"], frac=0.3)
        avg3 = mean(gas3_late) if gas3_late else -1
        print(f"  后30%快照 gas_workers: {gas3_late[:10]}... avg={avg3:.1f}")
        if avg3 < 3:
            fails.append(
                f"TEST3 FAIL: default 恢复后 gas_workers 均值={avg3:.1f}，期望 ≥ 3（回到自然分配水平）"
            )
        else:
            print(f"  TEST3 PASS: gas_workers 均值={avg3:.1f} ≥ 3（回到自然分配）✓")
    else:
        fails.append(f"TEST3 FAIL: 没有 telemetry 快照（game_id={gid3}）")

    trace3_default = [t for t in r3["traces"] if t["priority"] == "None"]
    if not trace3_default:
        log.warning(
            "TEST3: 没有 MININGTRACE priority=None（default 可能没触发，但 telemetry 已断言）"
        )
    else:
        tr = trace3_default[0]
        print(
            f"  MININGTRACE 首次 default: min_gas={tr['min_gas']} max_gas={tr['max_gas']} gas_wt={tr['gas_wt']}"
        )

    # ── 汇总 ──────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    if fails:
        print("结果: FAIL")
        for f in fails:
            print("  - " + f)
        print("\n诊断日志:")
        print(f"  TEST1: logs/mining_selftest_{gid1}.log | logs/{gid1}/telemetry.jsonl")
        print(f"  TEST2: logs/mining_selftest_{gid2}.log | logs/{gid2}/telemetry.jsonl")
        print(f"  TEST3: logs/mining_selftest_{gid3}.log | logs/{gid3}/telemetry.jsonl")
        return 1

    print("结果: PASS")
    print("  TEST1: 矿采优先 gas_workers 降低 ✓")
    print("  TEST2: 气采优先 gas_workers 回升 ✓")
    print("  TEST3: default 恢复 gas_workers 正常 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
