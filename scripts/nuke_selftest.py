"""核弹微操真局自验（mock LLM + non-realtime fast）。

AutoNukeAct 自主运行（不需要玩家注入指令）：ghost_nuke plan 自带 GhostAcademy 建造 + 幽灵量
产，VeryEasy 对手有固定基地作为核弹目标。让 bot 自然跑到 GhostAcademy 就绪 + 造出核弹。

断言（grep NUKETRACE）：
  - build_nuke_issued 出现过（向 GhostAcademy 下令造核弹）
  - calldown_issued   出现过（核弹真的发射）

non-realtime fast：600s 游戏时间 ≈ 27s 墙钟。ghost_nuke plan 早期 build ~8-10 分钟游戏时间后
GhostAcademy 就绪，核弹造出需 ~21s 游戏秒 → fast 下约 400-500s 游戏时间时应完成。
墙钟 timeout=120s 足够。

跑法：.venv/Scripts/python.exe scripts/nuke_selftest.py [--seconds 120] [--map DaybreakLE]
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

# 无操作 mock LLM — 只用于绕开真 LLM 调用（AutoNukeAct 完全自主，不走玩家指令）
_MOCK_LLM = {"interpretation_zh": "无操作", "confidence": 0.0, "directives": []}


async def run(seconds: int, map_name: str) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    log = logging.getLogger("nuke_selftest")

    log_path = _ROOT / "logs" / "nuke_selftest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()

    mock_path = _ROOT / "logs" / "nuke_mock_llm.json"
    mock_path.write_text(_json.dumps(_MOCK_LLM, ensure_ascii=False), encoding="utf-8")

    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)
    os.environ["VIBECRAFT_NUKE_TRACE"] = "1"

    # ghost_nuke 是 persistent_doctrine(不是 opening)→ 不能 forced_opening。
    # 走正路:一个有效 opening(reaper_expand)→ opening 完成后 auto_switch 到
    # persistent_ghost_nuke 这个 doctrine(模拟玩家切持续运营),GhostNuke.create_plan
    # 接管 → 建 GhostAcademy + 造核弹 + AutoNukeAct 跑。**不能 sandbox_macro_only**
    # (要真接敌,幽灵潜入敌方建筑才会 calldown)。VeryEasy + 10min 时限留够到核弹阶段。
    cfg = GameConfig(
        map_name=map_name,
        my_race="Terran",
        opponent_race="Zerg",
        # Medium(非 VeryEasy):VeryEasy ~9:44 就被打死,核弹(gas 重、~100 余气晚才有)还没
        # 造完游戏就结束 → 测不到 calldown。Medium 局更长,核弹有时间造完 + 幽灵接敌 → 发射。
        opponent_difficulty="Medium",
        realtime=False,
        forced_opening="reaper_expand",
        auto_switch_to="persistent_ghost_nuke",
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
    build_issued = "NUKETRACE build_nuke_issued" in text
    calldown_issued = "NUKETRACE calldown_issued" in text

    print("\n===== NUKE SELFTEST =====")
    print(f"  build_nuke_issued  : {build_issued}")
    print(f"  calldown_issued    : {calldown_issued}")

    ok = build_issued and calldown_issued
    print(f"  => {'PASS' if ok else 'FAIL'}")
    if not ok:
        log.warning("NUKETRACE lines found:")
        for line in text.splitlines():
            if "NUKETRACE" in line:
                log.warning("  %s", line)
    return 0 if ok else 1


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=120, help="墙钟超时（秒）")
    ap.add_argument("--map", default="DaybreakLE")
    args = ap.parse_args()
    return await run(args.seconds, args.map)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
