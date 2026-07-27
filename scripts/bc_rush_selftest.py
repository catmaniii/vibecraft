"""BC Rush 真局自验（mock LLM + non-realtime fast）。

BcRaidSquadAct 自主运行（不需要玩家注入指令）：
bc_rush plan 自带 FusionCore + Starport TechLab → BC 产出后骚扰逻辑自动触发。
Medium 难度对手让游戏时间够长（BC 出场 ~4:20，骚扰期需要更多时间）。

断言（grep BCRAIDTRACE）：
  - flyout         出现过（BC 飞向敌矿骚扰）
  - jump_home      出现过（BC 残血 Tactical Jump 回家）
  - healing_hold   出现过（BC 进入回血 hold 状态）

non-realtime fast：900s 游戏时间 ≈ 40s 墙钟（fast ~22x）。
BC 出场 ~4:20 (260s)，之后骚扰并取得足够伤害需要更多游戏时间。
墙钟 timeout=180s 足够。

跑法：.venv/Scripts/python.exe scripts/bc_rush_selftest.py [--seconds 180] [--map DaybreakLE]
退出码 0=PASS，1=FAIL。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json as _json
import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

# 无操作 mock LLM — BcRaidSquadAct 完全自主运行，不需要玩家指令
_MOCK_LLM = {"interpretation_zh": "无操作", "confidence": 0.0, "directives": []}


async def run(seconds: int, map_name: str) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    log = logging.getLogger("bc_rush_selftest")

    log_path = _ROOT / "logs" / "bc_rush_selftest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()

    mock_path = _ROOT / "logs" / "bc_rush_mock_llm.json"
    mock_path.write_text(_json.dumps(_MOCK_LLM, ensure_ascii=False), encoding="utf-8")

    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)
    os.environ["VIBECRAFT_BCRAID_TRACE"] = "1"

    # bc_rush 是 opening_build（不是 persistent_doctrine），可以直接 forced_opening。
    # BcRaidSquadAct 在 BC 出场后自动开始骚扰，无需玩家指令。
    # Medium + Zerg 对手：Hydralisk / Spore Crawler 会对飞行单位造成伤害，
    # 触发 jump_home (hp<40%) 条件。game_time_limit_s=900 确保 BC 出场后有足够的骚扰时间。
    cfg = GameConfig(
        map_name=map_name,
        my_race="Terran",
        opponent_race="Zerg",
        opponent_difficulty="Medium",
        realtime=False,
        forced_opening="bc_rush",
        sandbox_macro_only=False,
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
    flyout_found = "BCRAIDTRACE flyout" in text
    jump_home_found = "BCRAIDTRACE jump_home" in text
    healing_hold_found = "BCRAIDTRACE healing_hold" in text

    print("\n===== BC RUSH SELFTEST =====")
    print(f"  flyout       : {flyout_found}")
    print(f"  jump_home    : {jump_home_found}")
    print(f"  healing_hold : {healing_hold_found}")

    ok = flyout_found and jump_home_found and healing_hold_found
    print(f"  => {'PASS' if ok else 'FAIL'}")

    if not ok:
        log.warning("BCRAIDTRACE lines found:")
        for line in text.splitlines():
            if "BCRAIDTRACE" in line:
                log.warning("  %s", line)
        if not text:
            log.warning("  (log file empty or not found: %s)", log_path)

    return 0 if ok else 1


async def main() -> int:
    ap = argparse.ArgumentParser(description="BC Rush BcRaidSquadAct 真局自验")
    ap.add_argument("--seconds", type=int, default=180, help="墙钟超时（秒）")
    ap.add_argument("--map", default="DaybreakLE", help="地图名称")
    args = ap.parse_args()
    return await run(args.seconds, args.map)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
