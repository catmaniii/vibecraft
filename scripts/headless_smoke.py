"""Headless 验证:spawn vibecraft bot + SC2,可选注入指令验证端到端切剧本。

不依赖 PWA / service,直接用 game_process.GameProcess。SC2 窗口会弹出
(Windows + retail SC2 不能真 headless,详见 docs/plans/2026-05-14-vibecraft-design.md:137
和本次 hidden 调研结论 — D3D9 device 在 non-interactive desktop 立刻 Lost,
ShowWindow SW_HIDE 来不及第一帧前 hide),但我们自动 kill 不需要看。

判定 bot 正常工作:
- sc2 状态进入 playing
- bot 状态进入 running
- 注入指令后 events 流里出现 strategy_set / directive_committed
- 没有 crashed / Can't find free position 等失败信号

用法:
    # 仅验 bot 能起来跑(默认 60s):
    uv run --no-sync python scripts/headless_smoke.py

    # 注入指令验切剧本:
    uv run --no-sync python scripts/headless_smoke.py --inject "切 4BG"

    # 自定指令延迟 + 总时长:
    uv run --no-sync python scripts/headless_smoke.py --inject "切 1 门 Robo" \\
        --inject-after 30 --seconds 90
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
    p.add_argument(
        "--inject", default=None, help='注入一条指令(等同手机说话),如 "切 4BG"'
    )
    p.add_argument(
        "--inject-after",
        type=int,
        default=20,
        help="启动后 N 秒注入指令(等 bot 进入 playing/running 后再发)",
    )
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
    log.info("spawning bot for %ds: map=%s inject=%r", args.seconds, args.map, args.inject)
    gp = GameProcess()
    gp.start(cfg)

    start_ts = time.time()
    seen_playing = asyncio.Event()
    injected = asyncio.Event() if args.inject else None
    interesting_events: list[tuple[float, str, dict]] = []

    async def collect() -> None:
        async for msg in gp.raw_events():
            elapsed = time.time() - start_ts
            kind = msg.get("kind")  # snapshot / event / echo / minimap
            sc2 = msg.get("sc2")
            bot = msg.get("bot")
            if sc2 or bot:
                log.info(
                    "[+%.1fs] sc2=%s bot=%s detail=%s",
                    elapsed,
                    sc2 or "?",
                    bot or "?",
                    msg.get("detail", ""),
                )
                if str(sc2) == "playing":
                    seen_playing.set()
                if str(sc2) in ("crashed", "ended"):
                    return
            elif kind == "event":
                ev_kind = msg.get("kind_name") or msg.get("event_kind") or "?"
                # event 帧:msg 本身可能 nested(取决于 _put_event 怎么塞)
                payload = msg.get("payload") or {}
                # 只 log 关键事件
                if any(k in str(msg) for k in ("strategy", "directive", "tactics")):
                    log.info("[+%.1fs] EVENT %s %s", elapsed, ev_kind, payload)
                    interesting_events.append((elapsed, ev_kind, msg))
            elif kind == "echo":
                log.info(
                    "[+%.1fs] ECHO user=%r -> %r",
                    elapsed,
                    msg.get("user_text"),
                    msg.get("interpretation"),
                )

    collect_task = asyncio.create_task(collect())

    async def inject_after_delay() -> None:
        if not args.inject:
            return
        # 先等 sc2 进 playing,再等 inject_after 秒(让 bot 进入稳定 step)
        try:
            await asyncio.wait_for(seen_playing.wait(), timeout=120)
        except TimeoutError:
            log.warning("sc2 没进 playing,放弃注入")
            return
        log.info("sc2 已 playing,等 %ds 后注入指令", args.inject_after)
        await asyncio.sleep(args.inject_after)
        cmd = {
            "type": "command",
            "text": args.inject,
            "client_id": "smoke",
            "issued_at": time.time(),
        }
        log.info("INJECTING %r", args.inject)
        gp.send_command(cmd)
        if injected:
            injected.set()

    inject_task = asyncio.create_task(inject_after_delay()) if args.inject else None

    # 等总时长 / collect 提前完成
    try:
        await asyncio.wait_for(collect_task, timeout=args.seconds)
    except TimeoutError:
        log.info("timeout %ds reached, stopping bot", args.seconds)
        if inject_task and not inject_task.done():
            inject_task.cancel()
        await gp.stop()
        try:
            await asyncio.wait_for(collect_task, timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            collect_task.cancel()

    log.info("=" * 60)
    log.info("interesting events captured: %d", len(interesting_events))
    for ts, kind, m in interesting_events[:20]:
        log.info("  +%6.1fs  %s  keys=%s", ts, kind, list(m.keys()))
    log.info("=" * 60)
    log.info("bot ran %ds. 完整日志见 logs/<latest game_id>/events.jsonl", args.seconds)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
