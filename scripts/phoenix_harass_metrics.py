"""凤凰骚扰优化指标提取器。

读一批 game telemetry,按用户 2026-07-26 定的两个核心指标聚合:
  ① 杀农民数(enemy_workers_killed，累计击杀，越多越好)
  ② 凤凰损失数(phoenix_lost，累计，越少越好)
外加 VeryHard 胜率(game_result)判优化是否真提升。

用法:
  # 指定若干 game 目录
  python scripts/phoenix_harass_metrics.py logs/game_A logs/game_B ...
  # 或自动取最近 N 局
  python scripts/phoenix_harass_metrics.py --recent 12

输出每局一行 + 末尾聚合(均值杀农民/均值损失/kills_per_loss/胜率)。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _last_snapshot(recs: list[dict]) -> dict | None:
    snaps = [r for r in recs if r.get("kind") == "snapshot"]
    return snaps[-1] if snaps else None


def _game_result(recs: list[dict]) -> str:
    for r in recs:
        if r.get("kind") == "game_result":
            # 结构可能是 {'result': 'Victory'/'Defeat'/'Tie'} 或 payload
            res = r.get("result") or r.get("payload", {}).get("result") or ""
            return str(res)
    return "?"


# 骚扰阶段截止(game-seconds)：release_after=600 + harass 卡 300s → 凤凰约此时"归队"打决战。
# 用户要优化的是"归队之前"的骚扰表现，游戏末尾累计会混入放归后团战损失，故按此窗口切。
_HARASS_CUTOFF_S: float = 620.0


def _phoenix_stats(recs: list[dict]) -> tuple[int, int, int]:
    """返回 (phoenix_produced, phoenix_lost_final, phoenix_peak)。"""
    produced = sum(
        1 for r in recs if r.get("kind") == "unit_created" and r.get("unit") == "PHOENIX"
    )
    snaps = [r for r in recs if r.get("kind") == "snapshot"]
    lost = 0
    peak = 0
    for s in snaps:
        lost = max(lost, int(s.get("phoenix_lost", 0)))
        peak = max(peak, int(s.get("units", {}).get("PHOENIX", 0)))
    return produced, lost, peak


def _at_cutoff(recs: list[dict], cutoff: float) -> tuple[int, int]:
    """骚扰阶段(t<=cutoff)结束时的 (杀农民数, 凤凰损失数)——用户"归队前"窗口。"""
    snaps = [r for r in recs if r.get("kind") == "snapshot" and float(r.get("t", 1e9)) <= cutoff]
    if not snaps:
        return 0, 0
    last = snaps[-1]
    return int(last.get("enemy", {}).get("enemy_workers_killed", 0)), int(
        last.get("phoenix_lost", 0)
    )


def analyze_game(game_dir: str) -> dict | None:
    tel = Path(game_dir) / "telemetry.jsonl"
    if not tel.exists():
        return None
    try:
        with tel.open(encoding="utf-8") as fh:
            recs = [json.loads(line) for line in fh if line.strip()]
    except Exception:
        return None
    last = _last_snapshot(recs)
    if last is None:
        return None
    enemy = last.get("enemy", {})
    killed = int(enemy.get("enemy_workers_killed", 0))
    harassed = int(enemy.get("enemy_workers_harassed", 0))
    produced, lost, peak = _phoenix_stats(recs)
    h_killed, h_lost = _at_cutoff(recs, _HARASS_CUTOFF_S)
    result = _game_result(recs)
    return {
        "game": Path(game_dir).name,
        "killed": killed,
        "harassed": harassed,
        "ph_produced": produced,
        "ph_lost": lost,
        "ph_peak": peak,
        "h_killed": h_killed,  # 骚扰阶段(归队前)杀农民
        "h_lost": h_lost,  # 骚扰阶段(归队前)凤凰损失
        "result": result,
        "t_end": round(float(last.get("t", 0)), 0),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("games", nargs="*", help="game 目录路径")
    ap.add_argument("--recent", type=int, default=0, help="自动取最近 N 局")
    ap.add_argument(
        "--loss-weight",
        type=float,
        default=1.0,
        help="骚扰得分里每损失1凤凰扣几分(得分=杀农民−权重×损失;默认1)",
    )
    args = ap.parse_args()

    dirs: list[str] = list(args.games)
    if args.recent:
        allg = sorted(Path("logs").glob("game_*"), key=lambda p: p.stat().st_mtime, reverse=True)
        dirs = [str(p) for p in allg[: args.recent]]
    if not dirs:
        print("无 game 目录。用法见 --help")
        return 1

    rows = [r for r in (analyze_game(d) for d in dirs) if r is not None]
    if not rows:
        print("没解析到有效 game")
        return 1

    hdr = (
        f"{'game':<42} {'H_kill':>6} {'H_lost':>6} | {'killed':>6} {'ph_lost':>7} "
        f"{'peak':>4} {'end':>5} {'result':>8}"
    )
    print(hdr)
    print("(H_* = 骚扰阶段/归队前 t<=620；后面是全局累计)")
    for r in rows:
        print(
            f"{r['game']:<42} {r['h_killed']:>6} {r['h_lost']:>6} | {r['killed']:>6} "
            f"{r['ph_lost']:>7} {r['ph_peak']:>4} {r['t_end']:>5.0f} {r['result']:>8}"
        )

    n = len(rows)
    # 骚扰阶段(归队前)—— 用户真正要优化的窗口:得分 = 杀农民 − 权重×损失(杀越多+存活越多越高)
    hk = sum(r["h_killed"] for r in rows)
    hl = sum(r["h_lost"] for r in rows)
    h_kpl = hk / hl if hl else float(hk)
    w = args.loss_weight
    mean_score = (hk - w * hl) / n
    wins = sum(1 for r in rows if "Vic" in r["result"])
    ties = sum(1 for r in rows if "Tie" in r["result"])
    losses = sum(1 for r in rows if "Def" in r["result"])
    print("=" * 90)
    print(
        f"*【骚扰得分】(归队前 杀农民 - {w:g}x损失)均值 = {mean_score:.1f}  "
        f"[均值杀={hk / n:.1f}  均值损失={hl / n:.1f}]"
    )
    print(f"  骚扰阶段 N={n}  kills/loss={h_kpl:.2f}  (总杀{hk}/总损{hl})")
    tk = sum(r["killed"] for r in rows)
    tl = sum(r["ph_lost"] for r in rows)
    print(
        f"【全局累计】均值杀农民={tk / n:.1f}  均值凤凰损失={tl / n:.1f}  "
        f"kills/loss={tk / tl if tl else float(tk):.2f}"
    )
    print(f"胜率: {wins}W-{ties}T-{losses}L  win_rate={wins / n * 100:.0f}%  (含Tie不算胜)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
