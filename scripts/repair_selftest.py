"""通用维修指令(#551)真局自验：bot 真实建地堡 → debug 打残 → 注入"派N农民修地堡" →
验 SCV 真把它修回满血（终态铁律，不只看"发了命令"）。

流程（mock LLM 两段注入 + env debug-damage 钩子）：
  T+5s（inject1）：structure_override 令 bot 建 1 座地堡
  bot 建好后：VIBECRAFT_REPAIR_SELFTEST 钩子 debug 把地堡 life 打到 50
  T+13s（inject2）：repair 指令"派 3 个农民修地堡"
  验证（终态）：
    1. telemetry BUNKER 最大计数 >= 1（地堡真建起来）
    2. REPAIR_SELFTEST damaged（地堡被打残，hp 确实掉下来）
    3. REPAIRTRACE repair_dispatched 出现且首次 dispatch 时 hp < 0.9（确认在修残血）
    4. REPAIRTRACE repair_done_all_healthy 出现（hp 回到 >=0.99 = SCV 真把它修满 = 终态）

non-realtime(fast)，6min 游戏 ≈16s wall-clock；inject 时机在窗口内。
跑法：.venv/Scripts/python.exe scripts/repair_selftest.py [--seconds 60]
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

INJECT_TEXT_1 = "建一个地堡"
INJECT_TEXT_2 = "派3个农民修地堡"

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
        "match": "修地堡",
        "response": {
            "interpretation_zh": "派 3 个农民维修地堡",
            "confidence": 0.95,
            "directives": [
                {
                    "type": "repair",
                    "payload": {
                        "selector": {"unit_type": "Bunker"},
                        "worker_count": 3,
                    },
                }
            ],
        },
    },
]

_RE_DISPATCH = re.compile(r"REPAIRTRACE repair_dispatched .*?hp=([\d.]+)")
_RE_DONE = re.compile(r"REPAIRTRACE repair_done_all_healthy")
_RE_DAMAGED = re.compile(r"REPAIR_SELFTEST damaged bunker")


async def run(seconds: int, inject1: int, inject2: int, map_name: str) -> int:
    log = logging.getLogger("repair_selftest")
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    log_path = _ROOT / "logs" / "repair_selftest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    os.environ["VIBECRAFT_REPAIR_SELFTEST"] = "1"  # 启用 debug 打残钩子

    mock_path = _ROOT / "logs" / "repair_mock_llm.json"
    mock_path.write_text(_json.dumps(MOCK_LLM_LIST, ensure_ascii=False), encoding="utf-8")
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)

    cfg = GameConfig(
        map_name=map_name,
        my_race="Terran",
        opponent_race="Zerg",
        opponent_difficulty="VeryEasy",
        realtime=False,
        forced_opening="reaper_expand",
        sandbox_macro_only=True,
        game_time_limit_s=540,  # 留足"打残窗口(120 游戏秒)+ 之后修满"的时间
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
        await asyncio.sleep(inject1)
        log.info("INJECT-1 %r", INJECT_TEXT_1)
        gp.send_command(
            {
                "type": "command",
                "text": INJECT_TEXT_1,
                "client_id": "selftest",
                "issued_at": time.time(),
            }
        )
        await asyncio.sleep(inject2 - inject1)
        log.info("INJECT-2 %r", INJECT_TEXT_2)
        gp.send_command(
            {
                "type": "command",
                "text": INJECT_TEXT_2,
                "client_id": "selftest",
                "issued_at": time.time(),
            }
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

    damaged = bool(_RE_DAMAGED.search(raw_log))
    dispatch_hps = [float(m.group(1)) for m in _RE_DISPATCH.finditer(raw_log)]
    done = bool(_RE_DONE.search(raw_log))
    first_dispatch_hp = dispatch_hps[0] if dispatch_hps else None

    bunker_max_seen = 0
    with contextlib.suppress(Exception):
        dirs = sorted(
            (_ROOT / "logs").glob("game_*"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if dirs:
            for line in (dirs[0] / "telemetry.jsonl").read_text(encoding="utf-8").splitlines():
                try:
                    rec = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                if rec.get("kind") != "snapshot":
                    continue
                bunker_max_seen = max(bunker_max_seen, rec.get("buildings", {}).get("BUNKER", 0))

    print()
    print("===== REPAIR SELFTEST =====")
    print(f"  telemetry BUNKER 最大计数        : {bunker_max_seen}")
    print(f"  REPAIR_SELFTEST damaged          : {damaged}")
    print(f"  REPAIRTRACE dispatch 次数         : {len(dispatch_hps)}")
    print(f"  首次 dispatch hp                  : {first_dispatch_hp}")
    print(f"  REPAIRTRACE repair_done_all_healthy: {done}")
    print()

    fails: list[str] = []
    if bunker_max_seen < 1:
        fails.append("telemetry BUNKER 最大计数=0，地堡没建起来（structure_override 没生效）")
    if not damaged:
        fails.append("没看到 REPAIR_SELFTEST damaged，debug 打残钩子没生效")
    if not dispatch_hps:
        fails.append("没有 REPAIRTRACE repair_dispatched，director 没派 SCV 维修")
    elif first_dispatch_hp is not None and first_dispatch_hp >= 0.95:
        fails.append(
            f"首次 dispatch hp={first_dispatch_hp} 接近满血，地堡没被真打残（验不到修残血）"
        )
    if not done:
        fails.append(
            "没有 REPAIRTRACE repair_done_all_healthy，地堡没被修回满血 "
            "（SCV.repair 真机路径可能失效 — 终态没达成）"
        )

    if fails:
        print("结果: FAIL")
        for f in fails:
            print(f"  - {f}")
        return 1

    print("结果: PASS")
    print(f"  (1) [OK] 地堡建起来，BUNKER 最大={bunker_max_seen}")
    print(f"  (2) [OK] 地堡被打残，首次维修 dispatch hp={first_dispatch_hp}")
    print("  (3) [OK] repair_done_all_healthy → SCV 真把地堡修回满血（终态）")
    print("  => 维修指令真机路径 ensure_repair(SCV.repair) 已验证生效")
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser(description="通用维修指令真局自验(#551)")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--inject1", type=int, default=5, help="建地堡注入(seen_playing后s)")
    ap.add_argument("--inject2", type=int, default=13, help="维修注入(seen_playing后s)")
    ap.add_argument("--map", default="DaybreakLE")
    args = ap.parse_args()
    return await run(args.seconds, args.inject1, args.inject2, args.map)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
