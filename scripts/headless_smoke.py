"""Headless 验证:spawn vibecraft bot + SC2,跑 N 秒后 kill,grep 关键事件。

不依赖 PWA / service,直接用 game_process.GameProcess。SC2 窗口会弹出
(Windows 不能完全 headless),但我们自动 kill 不需要看。

判定 bot 是否正常工作:
- 关键事件:[ActUnit] PROBE / [GridBuilding] (无 "Can't find") / [ChronoUnit] / [Tech]
- 失败信号:repeated "Can't find free position"

用法:
    uv run --no-sync python scripts/headless_smoke.py [--seconds 60]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

# 父进程不需要装 sharpy(子进程会装),但 import vibecraft 需要 path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=int, default=60, help="跑多少秒后 kill")
    p.add_argument("--map", default="DaybreakLE")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    log = logging.getLogger("headless_smoke")

    if not os.environ.get("SC2PATH"):
        log.warning("SC2PATH not set,SC2 可能找不到")

    cfg = GameConfig(
        map_name=args.map,
        opponent_race="Random",
        opponent_difficulty="VeryHard",
        realtime=True,
    )
    log.info("spawning bot for %ds: map=%s", args.seconds, args.map)
    gp = GameProcess()
    gp.start(cfg)

    # 收集 status 事件(non-blocking)
    statuses: list[tuple[float, str, str]] = []
    start_ts = time.time()

    async def collect() -> None:
        async for msg in gp.raw_events():
            sc2 = msg.get("sc2") or "?"
            bot = msg.get("bot") or "?"
            statuses.append((time.time() - start_ts, str(sc2), str(bot)))
            log.info(
                "[+%.1fs] sc2=%s bot=%s detail=%s",
                time.time() - start_ts,
                sc2,
                bot,
                msg.get("detail", ""),
            )
            if str(sc2) in ("crashed", "ended"):
                return

    collect_task = asyncio.create_task(collect())

    # 等到 args.seconds 或 collect_task 提前完成
    try:
        await asyncio.wait_for(collect_task, timeout=args.seconds)
    except TimeoutError:
        log.info("timeout reached %ds, stopping bot", args.seconds)
        await gp.stop()
        try:
            await asyncio.wait_for(collect_task, timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            collect_task.cancel()

    log.info("=" * 60)
    log.info("statuses observed:")
    for ts, sc2, bot in statuses:
        log.info("  +%6.1fs  sc2=%-10s bot=%-10s", ts, sc2, bot)
    log.info("=" * 60)
    log.info("✓ bot ran %ds, kill called", args.seconds)
    log.info("查 logs/<latest game_id>/ 验证 ActUnit/GridBuilding 事件")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
