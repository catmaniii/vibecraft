"""科技单位主动技能"真触发"真局自验(P1+P2)。

debug 解锁全升级(过研究门)+ 生 caster(鬼/女妖)+ 敌人(感染虫给鬼狙、叉子给 EMP、枪兵给隐形),
caster 进 free_units → 被 plan 加进 combat group → 接敌 → 跑新写的 micro。VIBECRAFT_CASTER_TRACE=1
让每个技能真放那刻打 `CASTERTRACE <unit> <ability>`。grep 断言每个目标技能 ≥1 次触发。

跑法:.venv/Scripts/python.exe scripts/caster_ability_selftest.py [--seconds 240]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import re
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402


async def run(seconds: int, map_name: str) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log_path = _ROOT / "logs" / "caster_ability.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        if log_path.exists():
            log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    os.environ["VIBECRAFT_CASTER_SELFTEST"] = "1"  # 解锁升级 + 生 caster/敌
    os.environ["VIBECRAFT_CASTER_TRACE"] = "1"  # 技能触发打 CASTERTRACE
    os.environ.pop("VIBECRAFT_MOCK_LLM_JSON", None)

    cfg = GameConfig(
        map_name=map_name,
        my_race="Terran",
        opponent_race="Zerg",
        opponent_difficulty="VeryEasy",
        realtime=False,
        forced_opening="bio_max",
        game_time_limit_s=600,
    )
    gp = GameProcess()
    gp.start(cfg)
    ended = asyncio.Event()

    async def collect() -> None:
        async for msg in gp.raw_events():
            if str(msg.get("sc2")) in ("crashed", "ended"):
                ended.set()
                return

    ctask = asyncio.create_task(collect())
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(ended.wait(), timeout=seconds)
    if not ctask.done():
        ctask.cancel()
    with contextlib.suppress(Exception):
        await gp.stop()

    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    traces = re.findall(r"CASTERTRACE (\w+) (\w+)", text)
    counts = Counter(f"{u} {a}" for u, a in traces)
    spawned = "CASTER_SELFTEST upgrades unlocked" in text

    print("\n===== CASTER ABILITY SELFTEST =====")
    print(f"  生 caster/敌 + 解锁升级: {'OK' if spawned else '未执行(检查游戏是否跑到 30s)'}")
    print(f"  CASTERTRACE 触发统计: {dict(counts) if counts else '(无)'}")
    # 断言:用户点名的关键技能至少各触发 1 次
    targets = {
        "ghost snipe": "鬼兵狙击",
        "ghost emp": "鬼兵 EMP",
        "banshee cloak": "女妖隐形",
    }
    print("  —— 关键技能判定 ——")
    all_ok = True
    for key, label in targets.items():
        n = counts.get(key, 0)
        ok = n > 0
        all_ok = all_ok and ok
        print(f"   [{'PASS' if ok else 'FAIL'}] {label}: {n} 次")
    # 附带项(P2 / cloak):有就报,不强断言
    for key in ("ghost cloak", "viper abduct", "sentry guardianshield", "raven autoturret"):
        if counts.get(key):
            print(f"   (+) {key}: {counts[key]} 次")
    print(f"\n  >>> 关键技能总判定: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=240)
    ap.add_argument("--map", default="DaybreakLE")
    args = ap.parse_args()
    return await run(args.seconds, args.map)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
