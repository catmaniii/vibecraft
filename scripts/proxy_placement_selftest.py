"""proxy_4rax 落点确定性自验（隔离放置，受控局）。

验证目的：证明 placement_planner 接入后，3 个野兵营落点能可靠规划 + SCV 能建成。
与 SCV 存活/战斗完全隔离（sandbox_macro_only + VeryEasy，无实质敌方干扰）。

判据（per-instance 黑盒终态）：
  - telemetry 快照中 buildings.BARRACKS 峰值 ≥ 4（1 家 BB + 3 野 BB 全建成）
  - 注：只有 SCV 到达且 can_place 通过才会建兵营；telemetry 里的 buildings 包含在建建筑

用法：
  .venv/Scripts/python.exe scripts/proxy_placement_selftest.py [--runs 3] [--parallel 1]
  --parallel N  同时跑 N 局（节省 wall-clock）
  --runs N      总跑 N 局
  --seconds N   每局 wall-clock 超时（默认 240s，fast 模式约 3-5 分钟游戏时间）
退出码 0=PASS，1=FAIL。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402


def _make_game_id() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return f"pp_selftest_{ts}_{os.getpid()}_{uuid.uuid4().hex[:6]}"


async def _run_one(run_idx: int, wall_clock_limit: int) -> dict:
    """跑一局 proxy_4rax（sandbox_macro_only + VeryEasy + fast），返回指标字典。"""
    game_id = _make_game_id()
    cfg = GameConfig(
        map_name="DaybreakLE",
        my_race="Terran",
        opponent_race="Random",
        opponent_difficulty="VeryEasy",
        realtime=False,  # fast 模式（比 1x 快 10-100x，不需要真实时间）
        forced_opening="proxy_4rax",
        game_id=game_id,
        sandbox_macro_only=True,  # bot 只 macro 不主动出门（隔离战斗损耗）
        game_time_limit_s=300,  # 游戏内 300s（~5 min）就结束，防止跑太长
    )

    # 镜像子进程日志到文件，用于确认走了 planner 路径（而非 fallback）。
    # 注：VIBECRAFT_SERVER_LOG_PATH 走父进程 env → 子进程 spawn 时快照，
    # 并行多局会 race（后写覆盖），仅串行（--parallel 1）时可靠。
    srv_log = _ROOT / "logs" / f"{game_id}_srv.log"
    srv_log.parent.mkdir(parents=True, exist_ok=True)
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(srv_log)

    gp = GameProcess()
    gp.start(cfg)

    ended_normally = False
    crashed = False

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
        await asyncio.wait_for(_consume(), timeout=wall_clock_limit)
    except TimeoutError:
        print(f"[run {run_idx}] wall-clock {wall_clock_limit}s 超时 → infra-fail")
        await gp.stop()
        return {"run": run_idx, "game_id": game_id, "status": "infra_timeout", "barracks_peak": 0}
    finally:
        if not ended_normally:
            await gp.stop()

    if not ended_normally:
        print(f"[run {run_idx}] SC2 崩溃 → infra-fail")
        return {"run": run_idx, "game_id": game_id, "status": "crashed", "barracks_peak": 0}

    # ── 读 telemetry ──────────────────────────────────────────────────────
    tpath = _ROOT / "logs" / game_id / "telemetry.jsonl"
    if not tpath.exists():
        print(f"[run {run_idx}] telemetry 不存在: {tpath}")
        return {"run": run_idx, "game_id": game_id, "status": "no_telemetry", "barracks_peak": 0}

    barracks_peak = 0
    for line in tpath.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("kind") == "snapshot":
            b = rec.get("buildings", {}).get("BARRACKS", 0)
            if b > barracks_peak:
                barracks_peak = b

    # 扫子进程 server log（诊断：确认走了 planner 路径而非 fallback）
    path_mode = "?"
    if srv_log.exists():
        text = srv_log.read_text(encoding="utf-8", errors="replace")
        planner_used = "ProxyRax planner: proxy=" in text or "planner locked" in text
        fallback_used = "all anchors failed" in text or "switching to fallback" in text
        if planner_used and not fallback_used:
            path_mode = "planner"
        elif fallback_used:
            path_mode = "fallback"
        elif planner_used:
            path_mode = "planner+fallback"

    status = "pass" if barracks_peak >= 4 else "fail"
    print(
        f"[run {run_idx}] game_id={game_id} BARRACKS_peak={barracks_peak} "
        f"path={path_mode} → {status.upper()}"
    )
    return {
        "run": run_idx,
        "game_id": game_id,
        "status": status,
        "barracks_peak": barracks_peak,
        "path_mode": path_mode,
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description="proxy_4rax 落点确定性自验")
    ap.add_argument("--runs", type=int, default=3, help="总跑局数（默认 3）")
    ap.add_argument("--parallel", type=int, default=1, help="同时跑 N 局（默认 1 串行）")
    ap.add_argument("--seconds", type=int, default=240, help="每局 wall-clock 超时秒（默认 240）")
    args = ap.parse_args()

    print(f"\n=== proxy_placement_selftest: {args.runs} 局，并行度={args.parallel} ===\n")

    results: list[dict] = []
    run_indices = list(range(1, args.runs + 1))

    # 分批并行跑
    for batch_start in range(0, len(run_indices), args.parallel):
        batch = run_indices[batch_start : batch_start + args.parallel]
        batch_results = await asyncio.gather(*[_run_one(i, args.seconds) for i in batch])
        results.extend(batch_results)

    # ── 统计 ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    passes = [r for r in results if r["status"] == "pass"]
    fails = [r for r in results if r["status"] == "fail"]
    infras = [r for r in results if r["status"] not in ("pass", "fail")]

    for r in results:
        icon = (
            "[PASS]"
            if r["status"] == "pass"
            else "[INFRA]"
            if r["status"] in ("infra_timeout", "crashed", "no_telemetry")
            else "[FAIL]"
        )
        print(
            f"  {icon} run={r['run']} BARRACKS_peak={r['barracks_peak']:2d} "
            f"path={r.get('path_mode', '?')} status={r['status']} game_id={r['game_id']}"
        )

    print(
        f"\n  总结: {len(passes)}/{len(results)} PASS，{len(fails)} FAIL，{len(infras)} infra-fail"
    )

    # per-instance 断言（每局都要 ≥ 4 兵营）
    fail_msgs: list[str] = []
    for r in results:
        if r["status"] == "fail":
            fail_msgs.append(
                f"run={r['run']} BARRACKS_peak={r['barracks_peak']} < 4（game_id={r['game_id']}）"
            )
        elif r["status"] not in ("pass",):
            fail_msgs.append(f"run={r['run']} infra-fail: {r['status']}")

    if fail_msgs:
        print("\n结果: FAIL")
        for m in fail_msgs:
            print(f"  - {m}")
        print("\n诊断提示：")
        print("  1. 若 BARRACKS_peak=1（只有家里那个），说明 proxy SCV 没出发或全死了")
        print("  2. 若 BARRACKS_peak=2~3，说明部分 proxy 兵营建不起来（落点互相封路？）")
        print("  3. 若 infra-fail，说明 SC2 崩溃/超时，不算放置失败")
        print("  4. 看 logs/<game_id>/telemetry.jsonl 的 buildings 字段时间线")
        return 1

    print("\n结果: PASS")
    print(f"  放置确定性: {len(passes)}/{len(results)} 局全部建成 4 兵营（1 家 + 3 野）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
