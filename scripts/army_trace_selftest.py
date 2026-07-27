"""人族大军"出不了门"真局复现 + ARMY_TRACE(systematic-debugging:在命令边界打 log)。

bio vs VeryHard(真打、攒大军、supply>190 触发自动进攻)→ 开 VIBECRAFT_ARMY_TRACE →
PlanZoneAttack 每帧打各 role 兵力分布。读 "No attacking units" 那一刻的 ARMYTRACE,看大军被
攥在哪个 role(Idle/Gathering=gather 攥着 / Reserved=被 claim / Attacking 空=真没兵)。

跑法:.venv/Scripts/python.exe scripts/army_trace_selftest.py [--seconds 320]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

MOCK_ALLIN = {
    "interpretation_zh": "强制全体进攻",
    "confidence": 0.95,
    "directives": [
        {
            "type": "tactical_objective",
            "payload": {"verb": "attack", "attack_mode": "all_in", "persistent": True},
        }
    ],
}


async def run(seconds: int, map_name: str) -> int:
    log = logging.getLogger("army_trace")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log_path = _ROOT / "logs" / "army_trace.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        if log_path.exists():
            log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    os.environ["VIBECRAFT_ARMY_TRACE"] = "1"
    os.environ.pop("VIBECRAFT_SPAWN_MARINES", None)
    os.environ.pop("VIBECRAFT_MOCK_LLM_JSON", None)  # 用户配方:全程防守,不注入进攻

    # 用户复现配方:死神开矿(reaper_expand→bio_max)+ **全程全军防守**。
    # sandbox_macro_only=True 每 tick 强制 pin intent=defend(= 一直全军防守)+ bot 只防守能活久、
    # 攒出 3 矿 bio 大军。看 defend 下大军在哪 role / 是否原地抽搐(用户"保持队形"拉扯)。
    cfg = GameConfig(
        map_name=map_name,
        my_race="Terran",
        opponent_race="Zerg",
        opponent_difficulty="Hard",
        realtime=False,
        forced_opening="reaper_expand",
        sandbox_macro_only=True,
        game_time_limit_s=1800,
    )
    gp = GameProcess()
    gp.start(cfg)
    seen_playing = asyncio.Event()
    ended = asyncio.Event()

    async def collect() -> None:
        async for msg in gp.raw_events():
            if str(msg.get("sc2")) == "playing":
                seen_playing.set()
            if str(msg.get("sc2")) in ("crashed", "ended"):
                ended.set()
                return

    async def do_inject() -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(seen_playing.wait(), timeout=180)
        await asyncio.sleep(20)  # 等攒出一些兵
        # 反复注入 all_in(贴近真实:玩家见兵不动反复下进攻)
        for _ in range(20):
            if ended.is_set():
                return
            log.info("INJECT 强制全体进攻(all_in)")
            gp.send_command(
                {
                    "type": "command",
                    "text": "强制全体进攻",
                    "client_id": "selftest",
                    "issued_at": time.time(),
                }
            )
            await asyncio.sleep(15)

    itask = asyncio.create_task(do_inject())
    ctask = asyncio.create_task(collect())
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(ended.wait(), timeout=seconds)
    if not itask.done():
        itask.cancel()
    if not ctask.done():
        ctask.cancel()
    with contextlib.suppress(Exception):
        await gp.stop()

    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    lines = text.splitlines()
    no_atk = [i for i, ln in enumerate(lines) if "No attacking units" in ln]
    army = [ln for ln in lines if "ARMYTRACE" in ln]
    gather = [ln for ln in lines if "GATHERTRACE" in ln]
    # 统计 effective_gp 变化频率(CHG 次数)+ 分支分布 → 看 defend 目标点是否每帧跳
    chg = sum(1 for ln in gather if " CHG " in ln)
    from collections import Counter as _C

    branches = _C()
    for ln in gather:
        if "branch=" in ln:
            branches[ln.split("branch=")[1].split(" ")[0]] += 1
    print("\n===== GATHERTRACE(defend 实际目标点 effective_gp)=====")
    print(f"  GATHERTRACE 行数: {len(gather)}  其中 effgp 变化(CHG): {chg}")
    print(f"  分支分布: {dict(branches)}")
    print("  —— 末段 GATHERTRACE 抽样 ——")
    for ln in gather[-20:]:
        print("   " + (ln.split("] ")[-1] if "] " in ln else ln.split("- ")[-1]).strip()[:160])
    print("\n===== ARMY TRACE SELFTEST =====")
    print(f"  ARMYTRACE 行数: {len(army)}")
    print(f"  'No attacking units' 出现: {len(no_atk)} 次")
    # 打印 "No attacking units" 前后的 ARMYTRACE(看那一刻 role 分布)
    print("  —— 'No attacking units' 附近的 ARMYTRACE 抽样 ——")
    shown = 0
    for idx in no_atk:
        for j in range(max(0, idx - 3), min(len(lines), idx + 2)):
            ln = lines[j]
            if "ARMYTRACE" in ln or "No attacking units" in ln:
                print(
                    "   " + (ln.split("] ")[-1] if "] " in ln else ln.split("- ")[-1]).strip()[:160]
                )
                shown += 1
        print("   ----")
        if shown > 40:
            break
    if not no_atk:
        print("  (本局没出现 'No attacking units';抽样末段 ARMYTRACE)")
        for ln in army[-8:]:
            print("   " + (ln.split("] ")[-1] if "] " in ln else ln).strip()[:160])
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=320)
    ap.add_argument("--map", default="DaybreakLE")
    args = ap.parse_args()
    return await run(args.seconds, args.map)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
