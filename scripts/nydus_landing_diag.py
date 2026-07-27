"""坑道虫落地诊断 runner(2026-07-12 目标落地率 90%+)。

用户真机观察:兵有 / 坑道网络有 / 视野也有,坑道虫就是不放 → 疑「能不能放」判定 bug。
本 runner 起真 Zerg 局(non-realtime fast,可并行),把 planner 每 ~4s 打的 `NYDUSDIAG`
per-tile 判定分解(vis/place/bl/threat/dOL)+ ④ 主力门 + `BUILD_NYDUSWORM`(含 worm_available
/ ActionResult 语义) + `BLOCKED` 原因全 grep 出来,定位到底卡在哪一步。

跑完每局汇总:
  - canal 落地没(telemetry NYDUSCANAL 峰值 ≥1)
  - 出现过多少次 gate(army_away True/False)
  - 关键失配:vis=True 但 place=False 的格数(能不能放 bug 铁证);place=True 但 vis=False
    的格数(视野与落点解耦);vis=True∧place=True 却仍没放(逻辑漏)
  - BUILD_NYDUSWORM 发了几次 / worm_available 真假

用法:
  .venv/Scripts/python.exe scripts/nydus_landing_diag.py --games 4 --parallel 4 \
      --difficulty veryhard --seconds 500
退出码始终 0(诊断工具,不判 PASS/FAIL)。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

_DIFF = {
    "veryeasy": "VeryEasy",
    "easy": "Easy",
    "medium": "Medium",
    "hard": "Hard",
    "veryhard": "VeryHard",
}

# 自动测试窗口布局**写死在脚本里**(2026-07-12 用户:更小、3440×1440 能同时显示 8 个互不遮挡、别让
# 模型每次定)。**4 列 × 2 行网格**:4×850=3400<3440、2×700=1400<1440 → 8 个 848×696 窗口铺满不重叠
# (实际常并行 6 个也一样每个占独立格)。848 略低于 SC2 名义最小 1024×768,windowed 下实测可接受/自适应。
_WIN_COLS: int = 4  # 网格列数(3440 宽塞 4 列)
_WIN_W: int = 848  # 单窗宽(4×850 铺满 ultrawide)
_WIN_H: int = 696  # 单窗高(2×700 放两行)
_WIN_X_STEP: int = 850  # 列间距(留 2px 缝)
_WIN_Y_STEP: int = 704  # 行间距(窗高 + 标题栏余量)

# NYDUSDIAG gate: army_away=True nearby=3 anchor=(x,y) OL=2 dOL_center=[..] commit=False center=(x,y)
_RE_GATE = re.compile(r"NYDUSDIAG gate: army_away=(\w+) nearby=(\d+) .* OL=(\d+)")
# NYDUSDIAG tile[0]edge (x,y) vis=True place=False bl=False threat=0 dOL=12.3
_RE_TILE = re.compile(
    r"NYDUSDIAG tile\[(\d+)\](\w+) \(([-\d.]+),([-\d.]+)\) vis=(\w+) place=(\w+) bl=(\w+) threat=(\d+) dOL=([\d.]+)"
)
_RE_LOCK = re.compile(r"NydusLanding: worm locked @ \(([-\d.]+), ([-\d.]+)\) commit=(\w+)")
_RE_BUILD = re.compile(r"BUILD_NYDUSWORM @ \(([-\d.]+), ([-\d.]+)\).* worm_available=(\w+)")
_RE_BLOCK = re.compile(r"NydusLanding BLOCKED: (.+)")


def _analyze_log(log_path: Path) -> dict:
    gate_true = gate_false = 0
    ol_seen_max = 0
    vis_true_place_false = 0  # 有视野却放不了 = 能不能放 bug 铁证
    place_true_vis_false = 0  # 可放却没视野 = 视野与落点解耦
    both_true = 0  # vis∧place 都真(该落却没落 = 上层逻辑漏)
    tile_samples: list[str] = []
    locks = 0
    builds = 0
    worm_avail_true = worm_avail_false = 0
    blocks: dict[str, int] = {}
    if not log_path.exists():
        return {"error": "no log"}
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _RE_GATE.search(line)
        if m:
            if m.group(1) == "True":
                gate_true += 1
            else:
                gate_false += 1
            ol_seen_max = max(ol_seen_max, int(m.group(3)))
            continue
        m = _RE_TILE.search(line)
        if m:
            vis = m.group(5) == "True"
            place = m.group(6) == "True"
            if vis and not place:
                vis_true_place_false += 1
            if place and not vis:
                place_true_vis_false += 1
            if vis and place:
                both_true += 1
            if len(tile_samples) < 30:
                tile_samples.append(line.split("NYDUSDIAG ", 1)[-1].strip())
            continue
        if _RE_LOCK.search(line):
            locks += 1
            continue
        m = _RE_BUILD.search(line)
        if m:
            builds += 1
            if m.group(3) == "True":
                worm_avail_true += 1
            elif m.group(3) == "False":
                worm_avail_false += 1
            continue
        m = _RE_BLOCK.search(line)
        if m:
            key = m.group(1).split("(")[0].strip()[:40]
            blocks[key] = blocks.get(key, 0) + 1
    return {
        "gate_true": gate_true,
        "gate_false": gate_false,
        "ol_seen_max": ol_seen_max,
        "vis_true_place_false": vis_true_place_false,
        "place_true_vis_false": place_true_vis_false,
        "both_true_never_locked": both_true if locks == 0 else 0,
        "both_true": both_true,
        "locks": locks,
        "builds": builds,
        "worm_avail_true": worm_avail_true,
        "worm_avail_false": worm_avail_false,
        "blocks": blocks,
        "tile_samples": tile_samples,
    }


def _canal_landed(game_id: str) -> tuple[int, int, str]:
    """读 telemetry 判 canal 落地。返回 (building_started 次数, snapshot buildings 峰值, result)。

    2026-07-12 修:NYDUSCANAL 是 **building**,在 snapshot 的 `buildings` 子字典 + 独立
    `building_started`/`building_complete` 事件里,**不在** `units` 字典(旧读 units 恒 0 = 假阴性)。
    building_started(distinct tag)= 真落地次数(最权威);buildings 峰值 = 同时存活数。
    """
    tel = _ROOT / "logs" / game_id / "telemetry.jsonl"
    started_tags: set = set()
    peak = 0
    result = "?"
    if tel.exists():
        for line in tel.read_text(encoding="utf-8", errors="replace").splitlines():
            with contextlib.suppress(Exception):
                r = json.loads(line)
                k = r.get("kind")
                if k == "building_started" and r.get("unit") == "NYDUSCANAL":
                    started_tags.add(r.get("tag"))
                elif k == "snapshot":
                    peak = max(peak, int(r.get("buildings", {}).get("NYDUSCANAL", 0)))
                if "result" in r:
                    result = r["result"]
    return len(started_tags), peak, result


async def run_one(idx: int, difficulty: str, seconds: int, map_name: str) -> dict:
    game_id = f"nydusdiag_{idx}_{uuid.uuid4().hex[:6]}"
    log_path = _ROOT / "logs" / f"{game_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # 设 env 后立即 start(同步 spawn,子进程拿到本局 log_path 快照);start 返回后才动下一局。
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    cfg = GameConfig(
        map_name=map_name,
        my_race="Zerg",
        opponent_race="Random",
        opponent_difficulty=_DIFF[difficulty],
        realtime=False,
        forced_opening="nydus",
        game_id=game_id,
        # 小窗口网格平铺(用户 2026-07-12):3 列铺满 ultrawide,边界相接不重叠。
        window_width=_WIN_W,
        window_height=_WIN_H,
        window_x=(idx % _WIN_COLS) * _WIN_X_STEP,
        window_y=(idx // _WIN_COLS) * _WIN_Y_STEP,
    )
    gp = GameProcess()
    gp.start(cfg)  # 同步 spawn
    ended = asyncio.Event()

    async def collect() -> None:
        async for msg in gp.raw_events():
            if str(msg.get("sc2")) in ("crashed", "ended"):
                ended.set()
                return

    ctask = asyncio.create_task(collect())
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(ended.wait(), timeout=seconds)
    if not ctask.done():
        ctask.cancel()
    with contextlib.suppress(Exception):
        await gp.stop()

    stats = _analyze_log(log_path)
    started, peak, result = _canal_landed(game_id)
    stats["canal_started"] = started  # building_started distinct tag 数 = 真落地次数
    stats["canal_peak"] = peak  # 同时存活峰值
    stats["result"] = result
    stats["game_id"] = game_id
    return stats


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=4)
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--difficulty", default="veryhard", choices=list(_DIFF))
    ap.add_argument("--seconds", type=int, default=500)
    ap.add_argument("--map", default="DaybreakLE")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    results: list[dict] = []
    for batch_start in range(0, args.games, args.parallel):
        batch = list(range(batch_start, min(batch_start + args.parallel, args.games)))
        print(f"\n##### 批次 {batch} (parallel={len(batch)}, {args.difficulty}) #####")
        # 逐个 start(同步 spawn 各拿自己 log_path),再并发 collect
        tasks = [run_one(i, args.difficulty, args.seconds, args.map) for i in batch]
        batch_res = await asyncio.gather(*tasks, return_exceptions=True)
        for r in batch_res:
            if isinstance(r, Exception):
                print(f"  局异常: {r}")
            else:
                results.append(r)

    print("\n" + "=" * 70)
    print(f"════ {len(results)} 局落地诊断汇总 ════")
    landed = sum(1 for r in results if r.get("canal_started", 0) >= 1)
    print(
        f"canal 落地(building_started≥1): {landed} / {len(results)} = "
        f"{landed * 100 // max(1, len(results))}%"
    )
    for r in results:
        print(
            f"\n[{r.get('game_id', '?')}] canal落地次数={r.get('canal_started')} "
            f"存活峰值={r.get('canal_peak')} result={r.get('result')}"
        )
        print(
            f"  gate: away_True={r.get('gate_true')} away_False={r.get('gate_false')} OLmax={r.get('ol_seen_max')}"
        )
        print(
            f"  失配: vis但放不了={r.get('vis_true_place_false')}  可放但没视野={r.get('place_true_vis_false')}  "
            f"vis∧place却没锁={r.get('both_true_never_locked')}(both={r.get('both_true')})"
        )
        print(
            f"  lock={r.get('locks')} build={r.get('builds')} "
            f"worm_avail(T/F)={r.get('worm_avail_true')}/{r.get('worm_avail_false')}  blocks={r.get('blocks')}"
        )
        for s in (r.get("tile_samples") or [])[:6]:
            print(f"    · {s}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
