"""玩家折跃"在X刷N兵"真局自验(mock LLM + non-realtime fast)。

验"在〈地点〉刷 N 追猎"→ 折跃门兵种折跃在**离该点最近的能量场**。注入一条
production_override(Stalker×4, warp_at=主基地),抓 PLAYERWARP 日志:折满 4 个、
落点在能量场(主基地水晶)附近。用 4bg 开局(折跃研究早 ~supply 21)。

default 不在 pytest 跑(脚本)。手动:
    .venv/Scripts/python.exe scripts/player_warp_selftest.py
退出码 0=PASS,1=FAIL。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import re
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

INJECT_TEXT = "在主基地刷4追猎"

# warp_at=main:主基地一定有水晶塔(能量场),验折跃落点选最近能量场。
MOCK_LLM_RESPONSE = {
    "interpretation_zh": "在主基地折跃 4 追猎",
    "confidence": 0.95,
    "directives": [
        {
            "type": "production_override",
            "payload": {
                "items": [{"unit_type": "Stalker", "count": 4}],
                "warp_at": {"kind": "named_spot", "named_spot": "main"},
            },
        }
    ],
}

_RE_WARP = re.compile(
    r"PLAYERWARP did=\w+ warped (\d+) \w+ @ power\(([-\d.]+),([-\d.]+)\) remaining=(-?\d+)"
)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=200)
    ap.add_argument("--inject-after", type=int, default=6)  # 等 4bg 折跃研究完(~supply21)
    ap.add_argument("--map", default="DaybreakLE")
    ap.add_argument("--realtime", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    log = logging.getLogger("warp_selftest")

    log_path = _ROOT / "logs" / "player_warp_selftest_child.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    import json as _json

    mock_path = _ROOT / "logs" / "player_warp_mock_llm.json"
    mock_path.write_text(_json.dumps(MOCK_LLM_RESPONSE, ensure_ascii=False), encoding="utf-8")
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)

    cfg = GameConfig(
        map_name=args.map,
        opponent_race="Terran",
        opponent_difficulty="VeryHard",  # 局够长,等折跃研究完 + 折跃执行
        realtime=args.realtime,
        forced_opening="4bg",  # 折跃研究早
    )
    gp = GameProcess()
    gp.start(cfg)
    seen_playing = asyncio.Event()
    ended = asyncio.Event()

    async def collect() -> None:
        async for msg in gp.raw_events():
            sc2 = msg.get("sc2")
            if str(sc2) == "playing":
                seen_playing.set()
            if str(sc2) in ("crashed", "ended"):
                ended.set()
                return

    ctask = asyncio.create_task(collect())

    async def inject() -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(seen_playing.wait(), timeout=180)
        await asyncio.sleep(args.inject_after)
        log.info("INJECT %r", INJECT_TEXT)
        gp.send_command(
            {"type": "command", "text": INJECT_TEXT, "client_id": "warp", "issued_at": time.time()}
        )

    itask = asyncio.create_task(inject())
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(ended.wait(), timeout=args.seconds)
    for t in (itask, ctask):
        if not t.done():
            t.cancel()
    with contextlib.suppress(Exception):
        await gp.stop()

    total_warped = 0
    power_pts: list[tuple[float, float]] = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _RE_WARP.search(line)
            if m:
                total_warped += int(m.group(1))
                power_pts.append((float(m.group(2)), float(m.group(3))))

    print("\n" + "=" * 60)
    print(f"PLAYERWARP 折跃事件: {len(power_pts)} 条,共折 {total_warped} 个追猎")
    for pt in power_pts:
        print(f"  warped @ power source ({pt[0]:.1f},{pt[1]:.1f})")
    print("=" * 60)

    if total_warped >= 4:
        print("结果: PASS — 玩家'在X刷4追猎'折跃出 >=4 个,落在能量场附近")
        return 0
    print(
        f"结果: FAIL — 只折跃出 {total_warped} 个(期望 >=4);可能折跃研究没完成/没能量场/落点解析失败"
    )
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
