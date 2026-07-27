"""人族挂件决策 P1 真局自验(真 LLM + realtime)。

注入两句玩家话,走完整 语音→真 LLM→directive→Director 路径,grep 验:
  1. "补四个兵营,两个科技两个双倍" → ADDONTRACE 应见 Barracks x4(dec=True) + BarracksTechLab x2
     + BarracksReactor x2,且**不**弹窗(addon_decided=True)。
  2. "补四个兵营"(没说挂件) → ADDONTRACE 见 Barracks x4(dec=False) + "addon 挂件未决定,弹确认"
     (弹窗触发)。

真 LLM 注入必须 realtime(CLAUDE.md)。跑法:
  .venv/Scripts/python.exe scripts/addon_decision_selftest.py [--seconds 260]
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


async def run(seconds: int, map_name: str) -> int:
    log = logging.getLogger("addon_selftest")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log_path = _ROOT / "logs" / "addon_decision.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        if log_path.exists():
            log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    os.environ["VIBECRAFT_ADDON_TRACE"] = "1"
    os.environ.pop("VIBECRAFT_MOCK_LLM_JSON", None)  # 用真 LLM

    cfg = GameConfig(
        map_name=map_name,
        my_race="Terran",
        opponent_race="Zerg",
        opponent_difficulty="VeryEasy",
        realtime=True,  # 真 LLM 注入必须 realtime
        forced_opening="bio_max",
        game_time_limit_s=400,
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
        await asyncio.sleep(110)  # 等攒出 SCV/矿(realtime ~110s 游戏时间)
        if ended.is_set():
            return
        log.info("INJECT #1: 补四个兵营,两个科技两个双倍")
        gp.send_command(
            {
                "type": "command",
                "text": "补四个兵营,两个科技两个双倍",
                "client_id": "selftest",
                "issued_at": time.time(),
            }
        )
        await asyncio.sleep(35)
        if ended.is_set():
            return
        log.info("INJECT #2: 补四个兵营")
        gp.send_command(
            {
                "type": "command",
                "text": "补四个兵营",
                "client_id": "selftest",
                "issued_at": time.time(),
            }
        )
        await asyncio.sleep(30)

    itask = asyncio.create_task(do_inject())
    ctask = asyncio.create_task(collect())
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(ended.wait(), timeout=seconds)
    for t in (itask, ctask):
        if not t.done():
            t.cancel()
    with contextlib.suppress(Exception):
        await gp.stop()

    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    lines = text.splitlines()
    addontrace = [ln for ln in lines if "ADDONTRACE" in ln]
    prompt_lines = [ln for ln in lines if "addon 挂件未决定" in ln]

    print("\n===== ADDON DECISION SELFTEST =====")
    print(f"  ADDONTRACE 行数: {len(addontrace)}")
    for ln in addontrace:
        seg = ln.split("] ")[-1] if "] " in ln else ln.split("- ")[-1]
        print("   " + seg.strip()[:160])
    print(f"  挂件弹窗触发('addon 挂件未决定'): {len(prompt_lines)} 次")
    for ln in prompt_lines:
        seg = ln.split("] ")[-1] if "] " in ln else ln.split("- ")[-1]
        print("   " + seg.strip()[:160])

    # 判定
    def _has(s: str) -> bool:
        return any(s in ln for ln in addontrace)

    mix_ok = (
        _has("Barracksx4(dec=True)") and _has("BarracksTechLabx2") and _has("BarracksReactorx2")
    )
    prompt_ok = any("Barracksx4(dec=False)" in ln for ln in addontrace) and len(prompt_lines) >= 1
    print("\n  —— 判定 ——")
    print(f"   [{'PASS' if mix_ok else 'FAIL'}] 指定挂件组合落地(4兵营+2科技+2双倍,不弹窗)")
    print(f"   [{'PASS' if prompt_ok else 'FAIL'}] '补4兵营'没说挂件 → 弹窗")
    print(f"\n  >>> 总判定: {'PASS' if (mix_ok and prompt_ok) else 'FAIL'}")
    return 0 if (mix_ok and prompt_ok) else 1


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=260)
    ap.add_argument("--map", default="DaybreakLE")
    args = ap.parse_args()
    return await run(args.seconds, args.map)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
