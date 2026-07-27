"""defend "保持队形原地拉扯" 确定性复现 + 量化(systematic-debugging Phase 1 收尾)。

固定大军(60 枪兵 debug 生)+ 周期 flicker 敌(每 8s 主基附近刷 10 蟑螂)+ defend pin
(sandbox_macro_only)→ PlanZoneDefense 反复 claim 大军回防主基(enemy_center)→ 打完 release
→ PlanZoneGather 拉回前沿守点(effective_gp)→ 大军 home↔前沿 来回横跳。

量化拉扯:解析 DEFENDTRACE 的 army 中心序列,数"方向反转次数"(d_home 与 d_fwd 谁更小来回切)+
ARMYTRACE 的 Idle↔Defending role 翻转次数。修复前应见大量反转;修复后应收敛。

跑法:.venv/Scripts/python.exe scripts/defend_tug_selftest.py [--seconds 300]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402


async def run(seconds: int, map_name: str) -> int:
    logging.getLogger("defend_tug")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log_path = _ROOT / "logs" / "defend_tug.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        if log_path.exists():
            log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    os.environ["VIBECRAFT_ARMY_TRACE"] = "1"
    os.environ["VIBECRAFT_DEFEND_TRACE"] = "1"
    os.environ["VIBECRAFT_SPAWN_MARINES"] = "1"  # 固定 60 枪兵
    os.environ["VIBECRAFT_DEFEND_FLICKER"] = "1"  # 每 8s 主基刷敌
    os.environ.pop("VIBECRAFT_MOCK_LLM_JSON", None)

    cfg = GameConfig(
        map_name=map_name,
        my_race="Terran",
        opponent_race="Zerg",
        opponent_difficulty="VeryEasy",  # 弱敌:不干扰,我方不主动打(defend pin)→ 游戏跑满,只看 flicker 驱动的拉扯
        realtime=False,
        forced_opening="reaper_expand",
        sandbox_macro_only=True,  # pin intent=defend 整局
        game_time_limit_s=900,
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
    lines = text.splitlines()

    # 解析 DEFENDTRACE: army=(x,y) — army 中心轨迹。x/y 方向反转次数 = 原地拉扯量化。
    dt = [ln for ln in lines if "DEFENDTRACE" in ln]
    xs: list[int] = []
    ys: list[int] = []
    for ln in dt:
        m = re.search(r"army=\((\d+),(\d+)\)", ln)
        if m:
            xs.append(int(m.group(1)))
            ys.append(int(m.group(2)))

    def _reversals(seq: list[int]) -> int:
        rev = 0
        last_sign = 0
        for i in range(1, len(seq)):
            d = seq[i] - seq[i - 1]
            s = (d > 0) - (d < 0)
            if s != 0:
                if last_sign != 0 and s != last_sign:
                    rev += 1
                last_sign = s
        return rev

    rev_x, rev_y = _reversals(xs), _reversals(ys)

    flicker = sum(1 for ln in lines if "DEFEND_FLICKER" in ln and "fail" not in ln)
    print("\n===== DEFEND TUG SELFTEST =====")
    print(f"  flicker 生敌次数: {flicker}")
    print(f"  DEFENDTRACE 采样: {len(dt)}  有效 army 中心点: {len(xs)}")
    print(f"  >>> army 中心方向反转 x={rev_x} y={rev_y}(拉扯量化;baseline 修复前 18/16,目标 <5)")
    print("  —— DEFENDTRACE 末段(army 中心轨迹)——")
    for ln in dt[-16:]:
        seg = ln.split("] ")[-1] if "] " in ln else ln.split("- ")[-1]
        print("   " + seg.strip()[:150])
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--map", default="DaybreakLE")
    args = ap.parse_args()
    return await run(args.seconds, args.map)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
