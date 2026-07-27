"""偷家 build 落点截图探针：realtime + sandbox 起局跑指定 build，收集偷家建筑落点，
把镜头移到落点中心并持续保持，打印 SCREENSHOT_READY 后 hold 一段时间供外部截 SC2 窗口。

用法：
  python scripts/proxy_screenshot_probe.py --opening proxy_4rax --race Terran \
      --pos-regex "proxy=\\(([-0-9.]+),([-0-9.]+)\\)" --min-pos 1 --hold 90
  python scripts/proxy_screenshot_probe.py --opening 4bg --race Protoss \
      --pos-regex "prod_settled type=\\w+ tag=\\d+ pos=\\(([-0-9.]+),([-0-9.]+)\\)" --min-pos 2 --hold 90
  python scripts/proxy_screenshot_probe.py --opening void_ray_rush --race Protoss \
      --inject "派一个农民去我方分矿修一个水晶,然后在同点下两个VS" --inject-after 10 \
      --pos-regex "PROXYTRACE settled did=\\w+ type=\\w+ s_tag=\\d+ chain=\\S+ spos=\\(([-0-9.]+),([-0-9.]+)\\)" \
      --min-pos 2 --hold 100
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402


def _make_game_id(opening: str) -> str:
    return f"shot_{opening}_{os.getpid()}"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--opening", required=True)
    ap.add_argument("--race", default="Protoss")
    ap.add_argument("--pos-regex", required=True, help="捕获两个组 (x,y) 的正则")
    ap.add_argument("--min-pos", type=int, default=1, help="收集到几个落点就移镜头")
    ap.add_argument("--inject", default=None, help="可选：注入的玩家命令文本")
    ap.add_argument("--inject-after", type=float, default=10.0)
    ap.add_argument("--hold", type=float, default=90.0, help="移镜头后 hold 多少秒供截图")
    ap.add_argument("--max-wait", type=float, default=420.0, help="等落点/建成的最长墙钟")
    ap.add_argument("--built-type", default=None, help="可选：telemetry 里等这个建筑建成")
    ap.add_argument("--built-count", type=int, default=1, help="建成阈值")
    ap.add_argument(
        "--shot-prefix", default=None, help="截图输出前缀（探针自己在 READY 时截 SC2 窗口）"
    )
    ap.add_argument("--n-shots", type=int, default=3)
    args = ap.parse_args()

    game_id = _make_game_id(args.opening)
    cfg = GameConfig(
        map_name="DaybreakLE",
        my_race=args.race,
        opponent_race="Random",
        opponent_difficulty="VeryEasy",
        realtime=True,  # 渲染画面供截图
        forced_opening=args.opening,
        game_id=game_id,
        sandbox_macro_only=True,  # 无敌方，建筑稳留
        game_time_limit_s=900,
    )
    srv_log = _ROOT / "logs" / f"{game_id}_srv.log"
    srv_log.parent.mkdir(parents=True, exist_ok=True)
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(srv_log)

    pos_re = re.compile(args.pos_regex)
    gp = GameProcess()
    gp.start(cfg)

    seen_playing = asyncio.Event()
    ended = asyncio.Event()

    async def _consume() -> None:
        async for msg in gp.raw_events():
            sc2 = msg.get("sc2")
            if sc2 == "playing":
                seen_playing.set()
            if sc2 in ("ended", "crashed"):
                ended.set()
                return

    ctask = asyncio.create_task(_consume())

    # 等进入游戏
    try:
        await asyncio.wait_for(seen_playing.wait(), timeout=120)
    except TimeoutError:
        print("PROBE_FAIL 没进入游戏")
        await gp.stop()
        return 1

    # 可选注入玩家命令
    if args.inject:
        await asyncio.sleep(args.inject_after)
        print(f"INJECT {args.inject!r}")
        gp.send_command(
            {
                "type": "command",
                "text": args.inject,
                "client_id": "shotprobe",
                "issued_at": time.time(),
            }
        )

    # 轮询子进程日志收集落点
    positions: list[tuple[float, float]] = []
    seen_lines = 0
    deadline = time.time() + args.max_wait
    while time.time() < deadline and not ended.is_set():
        if srv_log.exists():
            lines = srv_log.read_text(encoding="utf-8", errors="replace").splitlines()
            for ln in lines[seen_lines:]:
                m = pos_re.search(ln)
                if m:
                    try:
                        p = (float(m.group(1)), float(m.group(2)))
                        if p not in positions:
                            positions.append(p)
                    except (ValueError, IndexError):
                        pass
            seen_lines = len(lines)
        if len(positions) >= args.min_pos:
            break
        await asyncio.sleep(2.0)

    if len(positions) < args.min_pos:
        print(f"PROBE_FAIL 只收集到 {len(positions)} 个落点(需 {args.min_pos}): {positions}")
        await gp.stop()
        return 1

    # 可选：等 telemetry 里建筑真建成（Terran 的 planner locked 是"计划"，要等真建好）
    if args.built_type:
        tpath = _ROOT / "logs" / game_id / "telemetry.jsonl"
        while time.time() < deadline and not ended.is_set():
            peak = 0
            if tpath.exists():
                for line in tpath.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("kind") == "snapshot":
                        peak = max(peak, rec.get("buildings", {}).get(args.built_type, 0))
            if peak >= args.built_count:
                print(f"BUILT_CONFIRMED {args.built_type}={peak}")
                break
            await asyncio.sleep(3.0)

    cx = sum(p[0] for p in positions) / len(positions)
    cy = sum(p[1] for p in positions) / len(positions)
    print(f"POSITIONS {positions}")
    print(f"SCREENSHOT_READY center=({cx:.1f},{cy:.1f}) game_id={game_id}")

    # 先把镜头钉到落点中心并稳定几秒
    for _ in range(6):
        gp.send_command({"type": "view_move", "target_point": [cx, cy]})
        await asyncio.sleep(1.0)

    # 探针自己在此刻截 SC2 窗口（时间精确，不跟外部竞争）
    if args.shot_prefix:
        cap_ps1 = str(_ROOT / "scripts" / "_capture_sc2.ps1")
        for i in range(1, args.n_shots + 1):
            gp.send_command({"type": "view_move", "target_point": [cx, cy]})
            out = str(Path(f"{args.shot_prefix}_{i}.png"))  # 规范化成反斜杠 Windows 路径
            _env = {**os.environ, "SC2_SHOT_OUT": out}  # 显式传 env（-File 参数绑不上，用 env var）
            try:
                r = subprocess.run(  # noqa: ASYNC221 (截图阻塞可接受，游戏在 hold)
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", cap_ps1],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=_env,
                )
                print(f"SHOT_{i} {r.stdout.strip()} {r.stderr.strip()[:80]}")
            except Exception as exc:
                print(f"SHOT_{i}_FAIL {exc}")
            await asyncio.sleep(3.0)

    # 再 hold 一小段（保持镜头，供人工/外部补截）
    hold_end = time.time() + max(0.0, args.hold)
    while time.time() < hold_end and not ended.is_set():
        gp.send_command({"type": "view_move", "target_point": [cx, cy]})
        await asyncio.sleep(1.5)

    print("HOLD_DONE")
    await gp.stop()
    ctask.cancel()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
