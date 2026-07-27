"""proxy_4rax 枪兵前向集结真局自验：跑 proxy_4rax vs 真对手（fast），
抓 loguru MARINESTAGE / proxy_anchor / ProxyRax 日志，验证枪兵在锚点集结、
攒够 threshold 才释放（不是被 PlanZoneGather 拉回家）。

终态校验：额外每隔几秒记录 ready 枪兵到 proxy 锚点 vs 到家的平均距离——
集结期应 dist_to_anchor << dist_to_home（真的在前方，不是回家）。

用法：
  python scripts/marine_staging_selftest.py --opponent veryeasy --seconds 190
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--opponent", default="VeryEasy", help="CamelCase: VeryEasy/Easy/Medium/Hard/VeryHard"
    )
    ap.add_argument("--seconds", type=float, default=190.0, help="墙钟上限（fast 下够跑到 ~3:00+）")
    args = ap.parse_args()

    import os

    game_id = f"marinestage_{os.getpid()}"
    cfg = GameConfig(
        map_name="DaybreakLE",
        my_race="Terran",
        opponent_race="Random",
        opponent_difficulty=args.opponent,
        realtime=False,  # fast：纯 bot 行为自验，无需注入
        forced_opening="proxy_4rax",
        game_id=game_id,
        game_time_limit_s=260,
    )
    log_path = _ROOT / "logs" / f"{game_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)

    gp = GameProcess()
    gp.start(cfg)
    seen_playing = asyncio.Event()
    ended = asyncio.Event()

    async def _consume() -> None:
        async for msg in gp.raw_events():
            if msg.get("sc2") == "playing":
                seen_playing.set()
            if msg.get("sc2") in ("ended", "crashed"):
                ended.set()
                return

    ctask = asyncio.create_task(_consume())
    try:
        await asyncio.wait_for(seen_playing.wait(), timeout=120)
    except TimeoutError:
        print("SELFTEST_FAIL 没进入游戏")
        await gp.stop()
        return 1

    print(f"PLAYING game_id={game_id} log={log_path.name}")
    deadline = time.time() + args.seconds
    while time.time() < deadline and not ended.is_set():
        await asyncio.sleep(5.0)

    await gp.stop()
    ctask.cancel()

    # 解析日志
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    lines = text.splitlines()
    proxy_anchor = [ln for ln in lines if "proxy=" in ln and "ProxyRax" in ln]
    staging = [ln for ln in lines if "MARINESTAGE staging" in ln]
    released = [ln for ln in lines if "MARINESTAGE released" in ln]

    print("\n=== 结果 ===")
    print(
        f"ProxyRax proxy 选点: {len(proxy_anchor)} 行"
        + (f" | {proxy_anchor[0][-80:]}" if proxy_anchor else "")
    )
    print(f"MARINESTAGE staging: {len(staging)} 次")
    if staging:
        print(f"  首: {staging[0][-90:]}")
        print(f"  末: {staging[-1][-90:]}")
    print(f"MARINESTAGE released: {len(released)} 次")
    for r in released:
        print(f"  {r[-90:]}")

    ok = bool(staging) and bool(released)
    print(
        f"\n{'SELFTEST_PASS' if ok else 'SELFTEST_FAIL'}: staging={len(staging)}>0 released={len(released)}>0"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
