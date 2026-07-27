"""回收**占用**地堡真局自验：建地堡 → 塞兵(占用) → 回收，验"先卸载再回收"。

背景(2026-06-19 用户重复反馈)：SC2 拒绝回收带兵地堡。SALVAGE 分支检测 has_cargo →
先 UNLOADALL_BUNKER + 入 _pending_salvage_tags，_tick_pending_salvage 等乘员清空后才
发 SALVAGEEFFECT_SALVAGE。本脚本真局验这条链真生效(地堡真被拆、兵不跟着死)。

流程(marine_rush：早期有枪兵 + 兵营 + SCV)：
  inject1：structure_override 建 1 地堡
  inject2：bunker_cargo load 往地堡塞兵(占用) —— 顺带验"进兵"指令
  inject3：salvage 回收地堡
验证：
  1. SALVAGETRACE salvage_deferred（检出 has_cargo、先卸载，不是直接 salvage）—— 占用路径铁证
  2. telemetry BUNKER 末期 = 0（地堡真被拆 —— 卸载后 pending tick 真的回收了）

non-realtime(fast) + mock LLM。跑法：.venv/Scripts/python.exe scripts/salvage_occupied_selftest.py
退出码 0=PASS，1=FAIL。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json as _json
import logging
import os
import re
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

INJECT_1 = "建一个地堡"
INJECT_2 = "往地堡塞兵"
INJECT_3 = "拆掉所有地堡"

MOCK_LLM_LIST = [
    {
        "match": "建一个地堡",
        "response": {
            "interpretation_zh": "建造 1 座地堡",
            "confidence": 0.95,
            "directives": [
                {
                    "type": "structure_override",
                    "payload": {"items": [{"structure_type": "Bunker", "delta": 1}]},
                }
            ],
        },
    },
    {
        "match": "往地堡塞兵",
        "response": {
            "interpretation_zh": "往地堡装兵",
            "confidence": 0.95,
            "directives": [
                {
                    "type": "bunker_cargo",
                    "payload": {"action": "load", "selector": {"unit_type": "Bunker"}, "count": 4},
                }
            ],
        },
    },
    {
        "match": "拆掉所有地堡",
        "response": {
            "interpretation_zh": "回收所有地堡",
            "confidence": 0.95,
            "directives": [{"type": "salvage", "payload": {"selector": {"unit_type": "Bunker"}}}],
        },
    },
]

_RE_DEFERRED = re.compile(r"SALVAGETRACE salvage_deferred.*?has_cargo")


async def run(seconds: int, inj1: int, inj2: int, inj3: int, map_name: str) -> int:
    log = logging.getLogger("salvage_occ")
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    log_path = _ROOT / "logs" / "salvage_occupied_selftest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)

    mock_path = _ROOT / "logs" / "salvage_occ_mock_llm.json"
    mock_path.write_text(_json.dumps(MOCK_LLM_LIST, ensure_ascii=False), encoding="utf-8")
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)

    cfg = GameConfig(
        map_name=map_name,
        my_race="Terran",
        opponent_race="Zerg",
        opponent_difficulty="VeryEasy",
        realtime=False,
        forced_opening="marine_rush",  # 早期有枪兵 + 兵营 + SCV
        sandbox_macro_only=True,
        game_time_limit_s=600,  # 给足 wall 余量,3 段注入(建/塞兵/回收)都在游戏结束前完成
    )

    gp = GameProcess()
    gp.start(cfg)
    seen_playing = asyncio.Event()
    ended = asyncio.Event()

    async def collect() -> None:
        async for msg in gp.raw_events():
            sc2 = str(msg.get("sc2"))
            if sc2 == "playing":
                seen_playing.set()
            if sc2 in ("crashed", "ended"):
                ended.set()
                return

    ctask = asyncio.create_task(collect())

    async def do_inject() -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(seen_playing.wait(), timeout=180)
        for delay, text in ((inj1, INJECT_1), (inj2 - inj1, INJECT_2), (inj3 - inj2, INJECT_3)):
            await asyncio.sleep(delay)
            log.info("INJECT %r", text)
            gp.send_command(
                {"type": "command", "text": text, "client_id": "selftest", "issued_at": time.time()}
            )

    itask = asyncio.create_task(do_inject())
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(ended.wait(), timeout=seconds)
    for t in (itask, ctask):
        if not t.done():
            t.cancel()
    with contextlib.suppress(Exception):
        await gp.stop()

    raw_log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    deferred = bool(_RE_DEFERRED.search(raw_log))

    bunker_max = 0
    bunker_final = -1
    with contextlib.suppress(Exception):
        dirs = sorted(
            (_ROOT / "logs").glob("game_*"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if dirs:
            tl = []
            for line in (dirs[0] / "telemetry.jsonl").read_text(encoding="utf-8").splitlines():
                try:
                    rec = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                if rec.get("kind") != "snapshot":
                    continue
                cnt = (rec.get("buildings", {}) or {}).get("BUNKER", 0)
                bunker_max = max(bunker_max, cnt)
                tl.append(cnt)
            if tl:
                bunker_final = min(tl[-3:]) if len(tl) >= 3 else tl[-1]

    print()
    print("===== SALVAGE OCCUPIED SELFTEST =====")
    print(f"  地堡建起来过(max)            : {bunker_max}")
    print(f"  SALVAGETRACE salvage_deferred : {deferred}  (检出占用→先卸载)")
    print(f"  telemetry BUNKER 末期         : {bunker_final}")
    print()

    fails = []
    if bunker_max < 1:
        fails.append("地堡没建起来（marine_rush 没响应 structure_override？）")
    if not deferred:
        fails.append(
            "没看到 salvage_deferred —— 占用地堡没走'先卸载'路径（兵没装进去/没检出 has_cargo）"
        )
    if bunker_final != 0:
        fails.append(f"BUNKER 末期={bunker_final}，期望 0（卸载后没真正回收）")

    if fails:
        print("结果: FAIL")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("结果: PASS")
    print("  (1) [OK] 地堡建起来 + 塞兵占用")
    print("  (2) [OK] 回收时检出占用、先卸载（salvage_deferred）")
    print("  (3) [OK] 卸载后真回收，BUNKER 末期=0")
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser(description="回收占用地堡真局自验")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--inj1", type=int, default=5, help="建地堡(seen_playing 后 s)")
    ap.add_argument("--inj2", type=int, default=11, help="塞兵")
    ap.add_argument("--inj3", type=int, default=18, help="回收")
    ap.add_argument("--map", default="DaybreakLE")
    args = ap.parse_args()
    return await run(args.seconds, args.inj1, args.inj2, args.inj3, args.map)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
