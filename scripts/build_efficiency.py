#!/usr/bin/env python
"""build 持续运营效率：跑纯运营沙盒局 + 打分。

用法:
  # 跑沙盒局并打分(默认 1 seed, veryeasy, 600 game-sec 提前 stop)
  python scripts/build_efficiency.py run <strategy_id> [--seeds 1 2 3] [--opponent veryeasy]
         [--seconds 600] [--no-sandbox]
  # 只对已有 telemetry 打分
  python scripts/build_efficiency.py score <path/to/telemetry.jsonl>

沙盒 = sandbox_macro_only(强制 defend, bot 只 macro)+ 固定 random_seed(变体 A/B 配对)。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))
    sys.path.insert(0, str(_ROOT))

from vibecraft.build_efficiency import ScoreConfig, score_snapshots  # noqa: E402
from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

_OPPONENT_DIFFICULTY = {
    "veryeasy": "VeryEasy",
    "easy": "Easy",
    "medium": "Medium",
    "hard": "Hard",
    "veryhard": "VeryHard",
    "cheatmoney": "CheatMoney",
}
_WALL_CLOCK_LIMIT_S = 900.0


def _detect_race(strategy_id: str) -> str:
    for race in ("protoss", "zerg", "terran"):
        if (_ROOT / "strategies" / race / f"{strategy_id}.yaml").exists():
            return race.capitalize()
    return "Protoss"


def _make_game_id(strategy_id: str, seed: int) -> str:
    import os
    import random

    return (
        f"eff_{strategy_id}_s{seed}_{int(time.time())}_{os.getpid()}_{random.randint(1000, 9999)}"
    )


async def _run_sandbox_game(
    strategy_id: str,
    seed: int,
    opponent: str,
    stop_game_time: float,
    sandbox: bool,
    auto_switch_to: str = "",
) -> Path | None:
    """跑一局沙盒(non-realtime)，到 stop_game_time(game-sec)提前 stop，返回 telemetry 路径。

    auto_switch_to 非空时：strategy_id 当开局跑，opening 完成 +10s 后自动 set_build 切到
    auto_switch_to（doctrine id，模拟玩家确认切定式）—— 用于测 persistent_doctrine 的运营效率。
    """
    race = _detect_race(strategy_id)
    tag = strategy_id if not auto_switch_to else f"{strategy_id}_TO_{auto_switch_to}"
    game_id = _make_game_id(tag, seed)
    cfg = GameConfig(
        map_name="DaybreakLE",
        my_race=race,
        opponent_race="Random",
        opponent_difficulty=_OPPONENT_DIFFICULTY[opponent],
        realtime=False,
        random_seed=seed,
        sandbox_macro_only=sandbox,
        forced_opening=strategy_id,
        auto_switch_to=auto_switch_to,
        game_id=game_id,
        # SC2 自身在 stop_game_time 处结束本局（forced-defend 下游戏不会自然停/会拖很久）。
        # +10s 余量确保评测窗口末尾有 snapshot。
        game_time_limit_s=int(stop_game_time) + 10,
        window_width=960,
        window_height=540,
    )
    logs_dir = _ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    gp = GameProcess()
    gp.start(cfg)
    start_wall = time.monotonic()
    ended = False
    stopped_at_time = False

    try:

        async def _consume() -> None:
            nonlocal ended, stopped_at_time
            async for msg in gp.raw_events():
                sc2 = msg.get("sc2")
                if sc2 == "ended":
                    ended = True
                    return
                if sc2 == "crashed":
                    return
                if msg.get("kind") == "snapshot":
                    t = msg.get("frame", {}).get("t")
                    if t is not None and float(t) >= stop_game_time:
                        stopped_at_time = True
                        print(
                            f"[eff] {strategy_id} s{seed} 到 {stop_game_time:.0f}s game-time，stop"
                        )
                        return

        await asyncio.wait_for(_consume(), timeout=_WALL_CLOCK_LIMIT_S)
    except TimeoutError:
        print(f"[eff] {strategy_id} s{seed} wall-clock 超时，强制 stop")
        await gp.stop()
        return None
    finally:
        if not ended:
            await gp.stop()

    tel = logs_dir / game_id / "telemetry.jsonl"
    if not tel.exists():
        print(f"[eff] {strategy_id} s{seed} 找不到 telemetry: {tel}")
        return None
    elapsed = time.monotonic() - start_wall
    print(
        f"[eff] {strategy_id} s{seed} 完成({elapsed:.1f}s wall, "
        f"{'stop@time' if stopped_at_time else 'ended'}): {tel}"
    )
    return tel


def _load(path: Path) -> list[dict[str, Any]]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _sandbox_held_diag(records: list[dict[str, Any]]) -> dict[str, Any]:
    """沙盒是否真的"只 macro 不出门"的诊断：看 production 出数 + opening_completed +
    army 是否一直没远征（粗略：snapshot 里有没有 production 字段、tactical.intent）。"""
    snaps = [r for r in records if r.get("kind") == "snapshot"]
    with_prod = sum(1 for s in snaps if isinstance(s.get("production"), dict))
    oc = next((s.get("opening_completed_at") for s in snaps if s.get("opening_completed_at")), None)
    intents = {
        (s.get("tactical") or {}).get("intent")
        for s in snaps
        if isinstance(s.get("tactical"), dict)
    }
    return {
        "snapshots": len(snaps),
        "snapshots_with_production": with_prod,
        "opening_completed_at": oc,
        "tactical_intents_seen": sorted(i for i in intents if i),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="build 持续运营效率：沙盒跑分")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="跑沙盒局 + 打分")
    r.add_argument("strategy_id")
    r.add_argument("--seeds", type=int, nargs="+", default=[1])
    r.add_argument("--opponent", default="veryeasy", choices=list(_OPPONENT_DIFFICULTY))
    r.add_argument("--seconds", type=float, default=600.0, help="game-time 提前 stop 秒")
    r.add_argument("--no-sandbox", action="store_true", help="关沙盒(自然打,对照用)")
    r.add_argument(
        "--auto-switch-to",
        default="",
        help="测 doctrine：strategy_id 当开局，opening 完成后自动切到此 doctrine id(如 persistent_skytoss)",
    )
    s = sub.add_parser("score", help="只对已有 telemetry 打分")
    s.add_argument("telemetry")
    s.add_argument("--from-opening", action="store_true")
    args = ap.parse_args()

    cfg = ScoreConfig()
    if args.cmd == "score":
        recs = _load(Path(args.telemetry))
        cfg.from_opening_completed = args.from_opening
        rep = score_snapshots(recs, cfg)
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
        print("沙盒诊断:", json.dumps(_sandbox_held_diag(recs), ensure_ascii=False))
        return 0

    # run
    for seed in args.seeds:
        tel = asyncio.run(
            _run_sandbox_game(
                args.strategy_id,
                seed,
                args.opponent,
                args.seconds,
                not args.no_sandbox,
                auto_switch_to=args.auto_switch_to,
            )
        )
        if tel is None:
            print(f"[eff] seed {seed} 失败")
            continue
        recs = _load(tel)
        rep = score_snapshots(recs, ScoreConfig(t_end=args.seconds))
        print(f"\n===== {args.strategy_id} seed {seed} 效率报告 =====")
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
        print("沙盒诊断:", json.dumps(_sandbox_held_diag(recs), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
