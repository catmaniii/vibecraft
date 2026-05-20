"""Build order 验收 runner。

用法:
    uv run python scripts/build_acceptance.py <strategy_id>
        [--opponent veryeasy|cheatmoney] [--runs N]

流程:spawn non-realtime SC2 跑 N 局 → 每局收 telemetry.jsonl → verifier 判定 →
N 局按 check 多数票聚合 → 出报告。infra-fail(watchdog hang / SC2 崩溃)
每局自动 retry ≤3 次。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.build_acceptance.spec import load_spec  # noqa: E402
from vibecraft.build_acceptance.verifier import (  # noqa: E402
    Report,
    aggregate_reports,
    verify,
)
from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

_MAX_INFRA_RETRY = 3
# 验收只需覆盖到出门攻击。non-realtime 下 600 game-sec 通常 wall-clock 几分钟，
# 给 900s wall-clock 作为宽松兜底（sub-process watchdog 120s 无消息也会先触发）。
_GAME_TIME_LIMIT_S = 600
_WALL_CLOCK_LIMIT_S = 900
_OPPONENT_DIFFICULTY = {"veryeasy": "VeryEasy", "cheatmoney": "CheatMoney"}


async def _run_one_game(strategy_id: str, opponent: str) -> Path | None:
    """跑一局，返回 telemetry.jsonl 路径；infra-fail 返回 None。

    子进程在自己的 GameSession 里决定 logs/<game_id>/ 目录，父进程拿不到
    game_id。对策：记下 start 前 logs/ 里 telemetry.jsonl 的最新 mtime 基准，
    游戏结束后扫 logs/ 取 mtime 最新的 telemetry.jsonl（必然是本局产生的）。

    infra-fail 判定（return None）：
    - raw_events() 中收到 sc2="crashed"
    - 子进程非零退出（raw_events() 结束后 exitcode != 0 且未 ended）
    - wall-clock 超时（_WALL_CLOCK_LIMIT_S）
    """
    os.environ["VIBECRAFT_FORCE_INITIAL_OPENING"] = strategy_id

    cfg = GameConfig(
        map_name="DaybreakLE",
        opponent_race="Random",
        opponent_difficulty=_OPPONENT_DIFFICULTY[opponent],
        realtime=False,
    )

    # 记下本局 start 时间基准（用于扫最新 telemetry.jsonl）
    start_wall = time.monotonic()
    start_ts = time.time()

    logs_dir = _ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    gp = GameProcess()
    gp.start(cfg)

    ended_normally = False
    crashed = False

    try:
        # 用 asyncio.wait_for 包住整个 raw_events() 消费循环，实现 wall-clock 超时
        async def _consume() -> None:
            nonlocal ended_normally, crashed
            async for msg in gp.raw_events():
                sc2 = msg.get("sc2")
                if sc2 == "ended":
                    ended_normally = True
                    return  # 正常结束，跳出循环
                if sc2 == "crashed":
                    crashed = True
                    return  # infra-fail，跳出循环

        await asyncio.wait_for(_consume(), timeout=_WALL_CLOCK_LIMIT_S)
    except TimeoutError:
        # wall-clock 超时：视为 infra-fail，强制 stop
        print(f"[runner] wall-clock {_WALL_CLOCK_LIMIT_S}s 超时，强制 stop（infra-fail）")
        await gp.stop()
        return None
    finally:
        # 确保子进程被清理（ended_normally 时子进程已自然退出，stop() 是幂等的）
        if not ended_normally:
            await gp.stop()

    if not ended_normally:
        # crashed 或其他异常退出
        return None

    # 游戏结束，定位本局的 telemetry.jsonl
    # 扫 logs/game_*/ 目录，取 mtime 晚于 start_ts 的最新 telemetry.jsonl
    candidates: list[tuple[float, Path]] = []
    for game_dir in logs_dir.glob("game_*/"):
        tele = game_dir / "telemetry.jsonl"
        if tele.exists():
            mtime = tele.stat().st_mtime
            if mtime >= start_ts - 5:  # 允许 5s 误差（NTP / 时钟偏移）
                candidates.append((mtime, tele))

    if not candidates:
        print("[runner] 游戏结束但找不到 telemetry.jsonl（logs/game_*/telemetry.jsonl）")
        return None

    # 取 mtime 最新的（本局产生的）
    candidates.sort(key=lambda x: x[0], reverse=True)
    telemetry_path = candidates[0][1]
    elapsed = time.monotonic() - start_wall
    print(f"[runner] 游戏结束（{elapsed:.1f}s wall-clock），telemetry: {telemetry_path}")
    return telemetry_path


def _run_with_retry(strategy_id: str, opponent: str) -> Path | None:
    """跑一局 + infra-fail 自动 retry ≤ _MAX_INFRA_RETRY 次；全失败返回 None。"""
    for attempt in range(1, _MAX_INFRA_RETRY + 1):
        print(f"[runner] {strategy_id} vs {opponent} — infra 尝试 {attempt}")
        telemetry_path = asyncio.run(_run_one_game(strategy_id, opponent))
        if telemetry_path is not None:
            return telemetry_path
        print(f"[runner] infra-fail（第 {attempt} 次），retry...")
    return None


def _load_telemetry(path: Path) -> list[dict[str, Any]]:
    """读 telemetry.jsonl → record 列表。"""
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build order 验收 runner：spawn non-realtime SC2 跑 N 局，多数票出验收报告。"
    )
    ap.add_argument("strategy_id", help="验收目标剧本 id，如 1g_robo_immortal")
    ap.add_argument(
        "--opponent",
        default="veryeasy",
        choices=["veryeasy", "cheatmoney"],
        help="对手难度（default: veryeasy）",
    )
    ap.add_argument(
        "--runs",
        type=int,
        default=1,
        help="跑几局取多数票（default 1；加固验收建议 3，消除单跑随机性）",
    )
    args = ap.parse_args()

    if args.runs < 1:
        print("ERROR: --runs 必须 >= 1")
        return 2

    spec_path = _ROOT / "tests" / "build_acceptance" / f"{args.strategy_id}.yaml"
    if not spec_path.exists():
        print(f"ERROR: 没有 acceptance spec: {spec_path}")
        return 2
    spec = load_spec(spec_path)

    reports: list[Report] = []
    for run_idx in range(1, args.runs + 1):
        print(f"[runner] ===== {args.strategy_id} run {run_idx}/{args.runs} =====")
        telemetry_path = _run_with_retry(args.strategy_id, args.opponent)
        if telemetry_path is None:
            print(f"[runner] run {run_idx} 连续 infra-fail，跳过此局")
            continue
        reports.append(verify(_load_telemetry(telemetry_path), spec, opponent=args.opponent))

    if not reports:
        print("INFRA BROKEN: 所有 run 都基础设施失败，无法验收。需人工排查。")
        return 3

    agg = aggregate_reports(reports)
    out = "\n".join(
        [
            f"=== Build Acceptance: {args.strategy_id} vs {args.opponent} "
            f"({len(reports)}/{args.runs} runs) ===",
            agg.summary(),
        ]
    )
    print(out)
    ts = time.strftime("%Y%m%d_%H%M%S")
    rep_dir = _ROOT / "logs" / "build_acceptance"
    rep_dir.mkdir(parents=True, exist_ok=True)
    rep_file = rep_dir / f"{args.strategy_id}_{args.opponent}_{ts}.txt"
    rep_file.write_text(out, encoding="utf-8")
    print(f"[runner] 报告已写入: {rep_file}")
    return 0 if agg.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
