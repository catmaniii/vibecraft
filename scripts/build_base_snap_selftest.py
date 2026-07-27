"""真局自验:"在这里造基地" → townhall 落点策略(2026-06-09)。

两种模式(--mode):
  near  指定点靠近某 expansion(≤15 格) → 应 snap 到贴矿最优位
        PASS:日志出现 `townhall Nexus snap 镜头点 ... → 贴矿最优位 (EX,EY)`
  far   指定点离任何 expansion 都 > 15 格(故意造偏的挡路/卡口基地) → 应**不** snap
        PASS:日志出现 `townhall Nexus 指定点 ... 按玩家指定位建`(尊重玩家位)

mock LLM 注入 build_at(Nexus, by_probe, point=<按 mode 取>)。修前:农民直接对该点
find_placement,造歪在矿区旁;修后:_drain_probe_builds 按 snap_townhall_point 决策。

mock LLM 0 延迟 → non-realtime 快跑。退出码 0=PASS,1=FAIL。
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

# DaybreakLE 下方出生(实测 expansion:主 48.5,28.5 / 旁 54.5,34.5 / 自然 30.5,49.5):
#  near 点贴近主矿(≤8)→ 应 snap;confirm 点离主矿 ~9(8-13)→ 应弹确认;far 角落 → 原地建。
_POINT_NEAR = [54.0, 32.0]
_POINT_CONFIRM = [48.5, 19.5]
_POINT_FAR = [1.0, 1.0]


def _point_for_mode(mode: str) -> list[float]:
    return {"near": _POINT_NEAR, "confirm": _POINT_CONFIRM, "far": _POINT_FAR}[mode]


def _mock_seq(point: list[float]) -> list:
    return [
        {
            "match": "基地",
            "response": {
                "interpretation_zh": "派一个农民在这里造一个基地",
                "confidence": 0.95,
                "directives": [
                    {
                        "type": "build_at",
                        "payload": {
                            "structure_type": "Nexus",
                            "point": point,
                            "by_probe": True,
                        },
                    }
                ],
            },
        }
    ]


# order_probe_build: townhall Nexus snap 镜头点 (56.0, 36.0) → 贴矿最优位 (54.5, 34.5)
_RE_SNAP = re.compile(
    r"townhall \S+ snap 镜头点 \(([-\d.]+), ([-\d.]+)\) → 贴矿最优位 \(([-\d.]+), ([-\d.]+)\)"
)
# order_probe_build: townhall Nexus 指定点 (1.0, 1.0) 离最近矿 > 13 格,按玩家指定位建...
_RE_NOSNAP = re.compile(r"townhall \S+ 指定点 \(([-\d.]+), ([-\d.]+)\) 离最近矿 > .*按玩家指定位建")
# townhall 落点模糊,弹确认: 这里离最近矿区约 9 格 ...
_RE_CONFIRM = re.compile(r"townhall 落点模糊,弹确认")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--mode",
        choices=["near", "confirm", "far"],
        default="near",
        help="near=应 snap / confirm=应弹确认(8-13) / far=应原地建(>13)",
    )
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument(
        "--first-inject", type=int, default=5, help="注入前等(wall 秒;等 expansion 数据 ready)"
    )
    ap.add_argument("--map", default="DaybreakLE")
    ap.add_argument("--opponent", default="VeryEasy")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    log = logging.getLogger("snap_selftest")

    inject_point = _point_for_mode(args.mode)

    log_path = _ROOT / "logs" / f"build_base_snap_selftest_{args.mode}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    mock_path = _ROOT / "logs" / f"snap_mock_llm_{args.mode}.json"
    mock_path.write_text(_json.dumps(_mock_seq(inject_point), ensure_ascii=False), encoding="utf-8")
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)

    cfg = GameConfig(
        map_name=args.map,
        opponent_race="Terran",
        opponent_difficulty=args.opponent,
        realtime=False,
        forced_opening="1g_robo_immortal",
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
        log.info("INJECT 在这里造基地 mode=%s (point=%s)", args.mode, inject_point)
        gp.send_command(
            {
                "type": "command",
                "text": "派一个农民在这里造一个基地",
                "client_id": "snap",
                "issued_at": time.time(),
            }
        )

    itask = asyncio.create_task(do_inject())
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(ended.wait(), timeout=args.seconds)
    for t in (itask, ctask):
        if not t.done():
            t.cancel()
    with contextlib.suppress(Exception):
        await gp.stop()

    snap_hit: tuple[float, float, float, float] | None = None
    nosnap_hit: tuple[float, float] | None = None
    confirm_hit = False
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            ms = _RE_SNAP.search(line)
            if ms and snap_hit is None:
                snap_hit = (
                    float(ms.group(1)),
                    float(ms.group(2)),
                    float(ms.group(3)),
                    float(ms.group(4)),
                )
            mn = _RE_NOSNAP.search(line)
            if mn and nosnap_hit is None:
                nosnap_hit = (float(mn.group(1)), float(mn.group(2)))
            if _RE_CONFIRM.search(line):
                confirm_hit = True

    print("\n" + "=" * 60)
    print(f"mode={args.mode}  注入点={inject_point}")
    if args.mode == "near":
        if snap_hit is None:
            print("结果: FAIL — near 应 snap,但没看到 snap 日志")
            return 1
        ix, iy, ex, ey = snap_hit
        print(f"snap 后落点 ({ex}, {ey})  [贴矿最优 expansion 位]")
        if (ix, iy) != (ex, ey):
            print("结果: PASS — 近矿点已 snap 到真实 expansion 位")
            return 0
        print("结果: FAIL — snap 后落点等于注入点")
        return 1
    if args.mode == "confirm":
        if confirm_hit and snap_hit is None and nosnap_hit is None:
            print("结果: PASS — 8-13 格模糊点弹了确认(director hold,未直接 snap/原地建)")
            return 0
        print(
            f"结果: FAIL — confirm 应弹确认(confirm={confirm_hit} snap={snap_hit} nosnap={nosnap_hit})"
        )
        return 1
    # far
    if nosnap_hit is not None and snap_hit is None and not confirm_hit:
        print(f"按玩家指定位建 ({nosnap_hit[0]}, {nosnap_hit[1]})  [尊重故意造偏的挡路基地]")
        print("结果: PASS — 偏太远的点未被 snap,按玩家指定位建")
        return 0
    print(
        f"结果: FAIL — far 应'按玩家指定位建'(confirm={confirm_hit} snap={snap_hit} nosnap={nosnap_hit})"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
