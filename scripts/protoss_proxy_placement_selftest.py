"""神族偷家落点确定性自验（隔离放置，受控局）。

验证 placement_planner 接入神族 forward_proxy 后，前线 Pylon + N 个生产建筑
（Gateway/Stargate，能量场内）能可靠规划 + probe 建成。与 SCV 存活/战斗隔离
（sandbox_macro_only + VeryEasy，无实质敌方干扰）。

判据（per-instance 黑盒终态，不是"下了 build 命令"的中间 trace）：
  - server log 出现 `FORWARDPROXY prod_settled type=... d_home=X`，X > 25（**世界真实
    终态**：前线生产建筑真的出现在 SC2 里且离家 > 25 = 确属前线，非家里补的）。
  为什么不用 telemetry 建筑计数：4bg 家里 GridBuilding(GATEWAY, 4) 会补齐总数，
  前线那个建不出时家里会顶上 → 总数 ≥4 掩盖前线失败。必须 per-target 看前线那栋。
  prod_settled 由 `_forward_prod_count` 读 `ai.structures`（真实世界）+ forward 过滤
  （d_home ≥25）产生，d_home 从真实结构坐标算 → 是终态黑盒证据。

用法：
  .venv/Scripts/python.exe scripts/protoss_proxy_placement_selftest.py \
      [--build 4bg] [--runs 3] [--min-forward 1]
  --build       神族偷家 build id（默认 4bg）
  --runs N      总跑 N 局（串行；log path 每局独占，不并行避免 race）
  --min-forward 每局至少要有几个前线生产建筑 settle（4bg=1；野2VS=2）
退出码 0=PASS，1=FAIL。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

_SETTLE_RE = re.compile(r"FORWARDPROXY prod_settled type=(\S+) tag=\d+ pos=\S+ d_home=([\-\d.]+)")


def _make_game_id() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return f"pp_ptoss_{ts}_{os.getpid()}_{uuid.uuid4().hex[:6]}"


async def _run_one(run_idx: int, build: str, min_forward: int, wall_clock_limit: int) -> dict:
    game_id = _make_game_id()
    cfg = GameConfig(
        map_name="DaybreakLE",
        my_race="Protoss",
        opponent_race="Random",
        opponent_difficulty="VeryEasy",
        realtime=False,
        forced_opening=build,
        game_id=game_id,
        sandbox_macro_only=True,
        game_time_limit_s=360,
    )

    srv_log = _ROOT / "logs" / f"{game_id}_srv.log"
    srv_log.parent.mkdir(parents=True, exist_ok=True)
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(srv_log)

    gp = GameProcess()
    gp.start(cfg)

    ended_normally = False

    async def _consume() -> None:
        nonlocal ended_normally
        async for msg in gp.raw_events():
            sc2 = msg.get("sc2")
            if sc2 == "ended":
                ended_normally = True
                return
            if sc2 == "crashed":
                return

    try:
        await asyncio.wait_for(_consume(), timeout=wall_clock_limit)
    except TimeoutError:
        print(f"[run {run_idx}] wall-clock {wall_clock_limit}s 超时 → infra-fail")
        await gp.stop()
        return {"run": run_idx, "game_id": game_id, "status": "infra_timeout", "forward": 0}
    finally:
        if not ended_normally:
            await gp.stop()

    if not ended_normally:
        return {"run": run_idx, "game_id": game_id, "status": "crashed", "forward": 0}

    # ── 解析 server log：前线生产建筑 settle（d_home > 25 = 确属前线）──
    forward_settled = 0
    path_mode = "?"
    types_seen: list[str] = []
    if srv_log.exists():
        text = srv_log.read_text(encoding="utf-8", errors="replace")
        for m in _SETTLE_RE.finditer(text):
            btype, d_home = m.group(1), float(m.group(2))
            if d_home > 25.0:
                forward_settled += 1
                types_seen.append(btype)
        if "in power field of pylon" in text:
            path_mode = "planner"
        elif "find_placement fallback" in text or "all anchors failed" in text:
            path_mode = "fallback"

    # 交叉 sanity：telemetry 里 GATEWAY+WARPGATE 峰值（不作为 per-target 判据，仅诊断）
    tpath = _ROOT / "logs" / game_id / "telemetry.jsonl"
    gw_peak = 0
    if tpath.exists():
        for line in tpath.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("kind") == "snapshot":
                b = rec.get("buildings", {})
                tot = (
                    int(b.get("GATEWAY", 0)) + int(b.get("WARPGATE", 0)) + int(b.get("STARGATE", 0))
                )
                gw_peak = max(gw_peak, tot)

    status = "pass" if forward_settled >= min_forward else "fail"
    print(
        f"[run {run_idx}] game_id={game_id} forward_settled={forward_settled}/{min_forward} "
        f"types={types_seen} path={path_mode} prod_peak={gw_peak} → {status.upper()}"
    )
    return {
        "run": run_idx,
        "game_id": game_id,
        "status": status,
        "forward": forward_settled,
        "path_mode": path_mode,
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description="神族偷家落点确定性自验")
    ap.add_argument("--build", default="4bg", help="神族偷家 build id（默认 4bg）")
    ap.add_argument("--runs", type=int, default=3, help="总跑局数（默认 3，串行）")
    ap.add_argument("--min-forward", type=int, default=1, help="每局至少前线生产建筑 settle 数")
    ap.add_argument("--seconds", type=int, default=360, help="每局 wall-clock 超时秒")
    args = ap.parse_args()

    print(
        f"\n=== protoss_proxy_placement_selftest build={args.build} "
        f"runs={args.runs} min_forward={args.min_forward} ===\n"
    )

    results: list[dict] = []
    for i in range(1, args.runs + 1):
        results.append(await _run_one(i, args.build, args.min_forward, args.seconds))

    print("\n" + "=" * 60)
    passes = [r for r in results if r["status"] == "pass"]
    fails = [r for r in results if r["status"] == "fail"]
    infras = [r for r in results if r["status"] not in ("pass", "fail")]
    for r in results:
        icon = (
            "[PASS]"
            if r["status"] == "pass"
            else "[INFRA]"
            if r["status"] in ("infra_timeout", "crashed")
            else "[FAIL]"
        )
        print(
            f"  {icon} run={r['run']} forward_settled={r['forward']} "
            f"path={r.get('path_mode', '?')} status={r['status']} game_id={r['game_id']}"
        )
    print(
        f"\n  总结: {len(passes)}/{len(results)} PASS，{len(fails)} FAIL，{len(infras)} infra-fail"
    )

    fail_msgs: list[str] = []
    for r in results:
        if r["status"] == "fail":
            fail_msgs.append(
                f"run={r['run']} forward_settled={r['forward']} < {args.min_forward}"
                f"（game_id={r['game_id']}）"
            )
        elif r["status"] not in ("pass",):
            fail_msgs.append(f"run={r['run']} infra-fail: {r['status']}")
    if fail_msgs:
        print("\n结果: FAIL")
        for m in fail_msgs:
            print(f"  - {m}")
        return 1

    print("\n结果: PASS")
    print(f"  放置确定性: {len(passes)}/{len(results)} 局前线生产建筑均建成")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
