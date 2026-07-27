"""控制权链真局自验(P5/P6 规则3):编队→一队进攻→释放虚空,验"释放"连带撤销 attack claim。

mock LLM **序列**(VIBECRAFT_MOCK_LLM_JSON 列表,按 user_text 子串匹配)→ non-realtime 快跑。
注入序列(等虚空出来后):
  1. "把虚空编成一队"   → group_assign(VoidRay,1)
  2. "一队进攻对方主矿" → unit_claim(group_id:1, attack_move enemy_main, persistent)  ← 锁住虚空
  3. "释放所有虚空"     → unit_release(VoidRay)  ← 规则3:应连带撤销上面那条 attack claim

PASS 判据:inject log 出现 `CTRLTRACE cancel_controlling ... revoked>=1`(释放连带撤销了 attack 指令)。
退出码 0=PASS,1=FAIL。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json as _json
import logging
import os
import re
import time
from pathlib import Path

from vibecraft.server.game_process import GameConfig, GameProcess

_ROOT = Path(__file__).resolve().parents[1]

MOCK_SEQ = [
    {
        "match": "编成一队",
        "response": {
            "interpretation_zh": "虚空编1队",
            "confidence": 0.95,
            "directives": [
                {
                    "type": "group_assign",
                    "payload": {"group_id": 1, "selector": {"unit_type": "Probe"}},
                }
            ],
        },
    },
    {
        "match": "进攻",
        "response": {
            "interpretation_zh": "1队进攻对方主矿",
            "confidence": 0.95,
            "directives": [
                {
                    "type": "unit_claim",
                    "payload": {
                        "selector": {"group_id": 1},
                        "task": {
                            "primary_action": {
                                "verb": "attack_move",
                                "target": {"kind": "named_spot", "named_spot": "enemy_main"},
                            }
                        },
                        "persistent": True,
                    },
                }
            ],
        },
    },
    {
        "match": "释放",
        "response": {
            "interpretation_zh": "释放所有虚空",
            "confidence": 0.95,
            "directives": [
                {
                    "type": "unit_release",
                    "payload": {"selector": {"unit_type": "Probe"}, "return_to_role": "IDLE"},
                }
            ],
        },
    },
]

_RE_CANCEL = re.compile(r"CTRLTRACE cancel_controlling units=\d+ revoked=(\d+)")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=200)
    ap.add_argument(
        "--first-inject", type=int, default=8, help="第一条注入前等(wall 秒;等虚空出来)"
    )
    ap.add_argument("--gap", type=int, default=4, help="序列内每条间隔 wall 秒")
    ap.add_argument("--map", default="DaybreakLE")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    log = logging.getLogger("ctrl_selftest")

    log_path = _ROOT / "logs" / "control_chain_selftest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    mock_path = _ROOT / "logs" / "control_mock_llm.json"
    mock_path.write_text(_json.dumps(MOCK_SEQ, ensure_ascii=False), encoding="utf-8")
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)

    cfg = GameConfig(
        map_name=args.map,
        opponent_race="Terran",
        opponent_difficulty="VeryHard",
        realtime=False,
        forced_opening="void_ray_rush",
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

    ctask = asyncio.create_task(collect())

    async def do_inject() -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(seen_playing.wait(), timeout=180)
        await asyncio.sleep(args.first_inject)
        for text in ("把农民编成一队", "一队进攻对方主矿", "释放所有农民"):
            log.info("INJECT %r", text)
            gp.send_command(
                {"type": "command", "text": text, "client_id": "ctrl", "issued_at": time.time()}
            )
            await asyncio.sleep(args.gap)

    itask = asyncio.create_task(do_inject())
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(ended.wait(), timeout=args.seconds)
    for t in (itask, ctask):
        if not t.done():
            t.cancel()
    with contextlib.suppress(Exception):
        await gp.stop()

    revoked = 0
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _RE_CANCEL.search(line)
            if m:
                revoked = max(revoked, int(m.group(1)))

    print("\n" + "=" * 60)
    print(f"释放虚空连带撤销的指令数(revoked) = {revoked}")
    if revoked >= 1:
        print("结果: PASS — 规则3 生效(释放虚空连带撤销了 attack claim)")
        return 0
    print("结果: FAIL — 释放虚空没撤销 attack claim(revoked=0)")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
