"""#560 linchpin 真机核对：CommandCenter LIFT/LAND ability 是否真能用。

VIBECRAFT_CCLIFT_PROBE 钩子自驱：等主基 CC ready → get_available_abilities 核对 LIFT →
起飞 → 飞到最近未占 expansion → 落地。验**终态**（CC 真飞到目标落地），不只看"发了命令"。

无需注入指令（钩子自驱）。non-realtime(fast)。
跑法：.venv/Scripts/python.exe scripts/cclift_probe.py [--seconds 90]
退出码 0=ability 可用且 CC 真飞到落地，1=不可用/没落地。
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

_RE_START = re.compile(r"CCLIFTPROBE start .*has_lift=(\w+)")
_RE_LIFTED = re.compile(r"CCLIFTPROBE lifted_ok")
_RE_LANDED = re.compile(r"CCLIFTPROBE landed_ok")
_RE_FAIL = re.compile(r"CCLIFTPROBE FAIL")


async def run(seconds: int, map_name: str) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log_path = _ROOT / "logs" / "cclift_probe.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    os.environ["VIBECRAFT_CCLIFT_PROBE"] = "1"
    mock_path = _ROOT / "logs" / "cclift_mock_llm.json"
    mock_path.write_text("[]", encoding="utf-8")
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)

    cfg = GameConfig(
        map_name=map_name,
        my_race="Terran",
        opponent_race="Zerg",
        opponent_difficulty="VeryEasy",
        realtime=False,
        forced_opening="reaper_expand",  # 有 CC + 农民
        sandbox_macro_only=True,
        game_time_limit_s=300,
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

    raw = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    m = _RE_START.search(raw)
    has_lift = m.group(1) if m else "?"
    lifted = bool(_RE_LIFTED.search(raw))
    landed = bool(_RE_LANDED.search(raw))
    failed = bool(_RE_FAIL.search(raw))

    print()
    print("===== CC LIFT/LAND PROBE (#560 linchpin) =====")
    print(f"  has_lift (get_available_abilities) : {has_lift}")
    print(f"  lifted_ok (COMMANDCENTERFLYING 出现) : {lifted}")
    print(f"  landed_ok (CC 飞到目标落地，终态)    : {landed}")
    print(f"  FAIL marker                         : {failed}")
    print()

    if has_lift == "True" and lifted and landed and not failed:
        print("结果: PASS —— CC LIFT/LAND ability 真机可用，CC 真飞到目标落地")
        print("  => #560 'spare CC 飞到扩张点' 技术可行，可继续设计实现")
        return 0
    print("结果: FAIL —— CC 起降未跑通")
    if has_lift != "True":
        print("  - LIFT_COMMANDCENTER 不在 available abilities（enum 存在≠真能用）")
    if not lifted:
        print("  - CC 没起飞成 COMMANDCENTERFLYING")
    if not landed:
        print("  - CC 没飞到目标落地（终态未达成）")
    return 1


async def main() -> int:
    ap = argparse.ArgumentParser(description="CC LIFT/LAND 真机核对(#560)")
    ap.add_argument("--seconds", type=int, default=90)
    ap.add_argument("--map", default="DaybreakLE")
    args = ap.parse_args()
    return await run(args.seconds, args.map)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
