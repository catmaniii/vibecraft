"""运营按钮(开矿 N / 停农民)真机实测：验证 macro_action 到底有没有效果。

并行跑 3 局(headless 非实时 fast)，每局用固定 game_id → telemetry 落到
logs/<game_id>/telemetry.jsonl，跑完直接读文件解析 (t,bases,workers) 时间序列。

scenario:
  - baseline   ：不注入，看 bot 自然开矿到几片 + 农民自然涨(对照组)。
  - expand2    ：注入 macro_action expand=2。看基地数会不会被压在 2。
                 预期【封不住】(set_expansion_override 是 pass 空操作 +
                 PersistentMacro 自动开到 expansion_cap)。
  - workers_stop：注入 macro_action workers=stop。看农民数会不会停涨。
                 预期【有效】(act_unit vendor patch 拦截 production_blocked)。

VeryHard 对手让局更长、bot 自然多开矿，封顶行为才显形。

用法：
  .venv/Scripts/python.exe scripts/macro_action_selftest.py [--seconds 420] [--opponent VeryHard]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import sys
from pathlib import Path

# 修 Windows gbk 控制台对 ✓/✗ 报 UnicodeEncodeError：强制 stdout 走 utf-8。
with contextlib.suppress(Exception):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402


def _read_series(game_id: str) -> list[tuple[float, int, int]]:
    """读 logs/<game_id>/telemetry.jsonl 的 snapshot 记录 → (t,bases,workers)。"""
    path = _ROOT / "logs" / game_id / "telemetry.jsonl"
    series: list[tuple[float, int, int]] = []
    if not path.exists():
        return series
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        with contextlib.suppress(Exception):
            rec = json.loads(line)
            if rec.get("kind") == "snapshot":
                series.append(
                    (float(rec.get("t", 0)), int(rec.get("bases", 0)), int(rec.get("workers", 0)))
                )
    series.sort(key=lambda x: x[0])
    return series


async def run_one(
    scenario: str, seconds: int, inject_after: int, map_name: str, opp: str, opening: str
) -> dict:
    """跑一局。返回 {scenario, series, injected}。"""
    game_id = f"macro_selftest_{scenario}"
    # 清旧 telemetry，避免读到上次的
    old = _ROOT / "logs" / game_id / "telemetry.jsonl"
    if old.exists():
        old.unlink()

    log_path = _ROOT / "logs" / f"macro_selftest_{scenario}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)

    cfg = GameConfig(
        map_name=map_name,
        opponent_race="Terran",
        opponent_difficulty=opp,
        realtime=False,
        game_id=game_id,
        forced_opening=opening,
    )
    gp = GameProcess()
    gp.start(cfg)

    seen_playing = asyncio.Event()
    ended = asyncio.Event()

    async def watch() -> None:
        async for msg in gp.raw_events():
            sc2 = str(msg.get("sc2"))
            if sc2 == "playing":
                seen_playing.set()
            if sc2 in ("crashed", "ended"):
                ended.set()
                return

    wtask = asyncio.create_task(watch())

    async def do_inject() -> None:
        if scenario == "baseline":
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(seen_playing.wait(), timeout=180)
        await asyncio.sleep(inject_after)
        if scenario == "expand2":
            gp.send_command({"type": "macro_action", "dim": "expand", "value": 2})
        elif scenario == "workers_stop":
            gp.send_command({"type": "macro_action", "dim": "workers", "value": "stop"})
        elif scenario == "expand_one_more":
            # 「多开一个矿」按钮:fire-and-forget,提交一张 current+1 的扩张卡
            gp.send_command({"type": "macro_action", "dim": "expand", "value": "one_more"})
        elif scenario == "workers_max":
            # 「全力补农民」按钮:满采模式,靠 _tick_worker_saturation 每 tick 补
            gp.send_command({"type": "macro_action", "dim": "workers", "value": "max"})

    itask = asyncio.create_task(do_inject())
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(ended.wait(), timeout=seconds)
    for t in (itask, wtask):
        if not t.done():
            t.cancel()
    with contextlib.suppress(Exception):
        await gp.stop()

    injected = False
    if log_path.exists():
        txt = log_path.read_text(encoding="utf-8", errors="replace")
        injected = "macro_action expand" in txt or "macro_action workers" in txt

    return {"scenario": scenario, "series": _read_series(game_id), "injected": injected}


def _common_cut(by: dict) -> float:
    """所有局都还活着的最晚时刻 = 各局终点的最小值。"""
    ends = [r["series"][-1][0] for r in by.values() if r.get("series")]
    return min(ends) if ends else 0.0


def _at(series: list, t: float):
    """取 <= t 的最后一个快照 (t, bases, workers);没有返回 None。"""
    best = None
    for row in series:
        if row[0] <= t:
            best = row
    return best


def _cmp_at_cut(by: dict, scenario: str, cut: float, idx: int) -> tuple:
    """返回 (该场景值, baseline 值);任一局在 cut 时已被打崩(基地 0)则返回 (None, None)。"""
    a = _at(by.get(scenario, {}).get("series", []), cut)
    b = _at(by.get("baseline", {}).get("series", []), cut)
    if not a or not b:
        return (None, None)
    if a[1] == 0 or b[1] == 0:  # 基地已归零 = 局面崩了,数字不可比
        return (None, None)
    return (a[idx], b[idx])


def _summarize(r: dict) -> str:
    s = r["series"]
    if not s:
        return f"[{r['scenario']}] 无快照(telemetry 空)"
    picks = []
    last_t = -999.0
    for t, b, w in s:
        if t - last_t >= 60:
            picks.append((t, b, w))
            last_t = t
    if s[-1] not in picks:
        picks.append(s[-1])
    max_bases = max(b for _, b, _ in s)
    max_workers = max(w for _, _, w in s)
    end_t = s[-1][0]
    line = (
        f"[{r['scenario']}] inject={r['injected']} 末t={end_t:.0f}s "
        f"峰值bases={max_bases} 峰值workers={max_workers}\n"
    )
    line += "    t(s)/bases/workers: " + "  ".join(f"{t:.0f}/{b}/{w}" for t, b, w in picks)
    return line


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=420, help="每局 wall-clock 上限秒")
    ap.add_argument("--inject-after", type=int, default=3)
    ap.add_argument("--map", default="DaybreakLE")
    ap.add_argument("--opponent", default="VeryHard")
    ap.add_argument("--opening", default="", help="forced_opening 剧本 id(空=默认 4bg)")
    args = ap.parse_args()

    scenarios = ["baseline", "expand2", "workers_stop", "expand_one_more", "workers_max"]
    print(
        f"并行跑 {len(scenarios)} 局({args.opponent}, opening={args.opening or '4bg(默认)'}, "
        f"非实时 fast, 上限 {args.seconds}s/局)..."
    )
    results = await asyncio.gather(
        *[
            run_one(sc, args.seconds, args.inject_after, args.map, args.opponent, args.opening)
            for sc in scenarios
        ]
    )

    print("\n" + "=" * 70)
    by = {r["scenario"]: r for r in results}
    for sc in scenarios:
        print(_summarize(by[sc]))

    print("\n" + "=" * 70 + "\n判定：")
    base = by["baseline"]
    cut = _common_cut(by)
    print(f"\n  共同可比时刻 t={cut:.0f}s(各局终点的最小值;在此之后有的局已结束,不能横向比)")
    exp = by["expand2"]
    wrk = by["workers_stop"]

    base_peak = max((b for _, b, _ in base["series"]), default=0)
    exp_peak = max((b for _, b, _ in exp["series"]), default=0)
    if exp["injected"]:
        # 强化(2026-07-27):expand2 那局"没超过 2"只有在【baseline 同期确实超过了 2】时才算证据,
        # 否则可能只是这局太短、bot 本来也还没想开第三个 —— 那是没验到,不是封顶有效。
        exp_end = exp["series"][-1][0] if exp["series"] else 0.0
        base_at_exp_end = max((b for t, b, _ in base["series"] if t <= exp_end), default=0)
        if exp_peak <= 2 and base_at_exp_end <= 2:
            print(
                f"  [?] 开矿封顶未验到:expand=2 局峰值 {exp_peak},但 baseline 到同一时刻"
                f"(t={exp_end:.0f}s)也只有 {base_at_exp_end} 个基地 —— 封顶没被考验过"
            )
        elif exp_peak > 2:
            print(
                f"  [FAIL] 开矿封顶无效：expand=2 但基地数仍涨到 {exp_peak}"
                f"(baseline 峰值 {base_peak})"
            )
            print("     -> 印证 set_expansion_override 空操作 + PersistentMacro 自动开矿没被压住")
        else:
            print(
                f"  [OK] 开矿封顶有效:expand=2 基地数封在 {exp_peak};baseline 到同一时刻"
                f"(t={exp_end:.0f}s)已开到 {base_at_exp_end} 个"
            )
    else:
        print("  [?] expand2 注入未确认(server log 无 macro_action expand)")

    # ── 「多开一个矿」:注入后基地数应比 baseline 同期多(至少不少) ──
    om = by.get("expand_one_more")
    if om:
        if not om.get("injected"):
            print("  [?] one_more 注入未确认")
        else:
            v, b = _cmp_at_cut(by, "expand_one_more", cut, 1)
            if v is None:
                print("  [?] 多开一个矿无法判定:该局或对照局在可比时刻已被打崩(基地 0)")
            elif v > b:
                print(f"  [OK] 多开一个矿有效:t={cut:.0f}s 基地 {v} > baseline {b}")
            elif v == b:
                print(
                    f"  [?] 多开一个矿存疑:t={cut:.0f}s 基地与 baseline 同为 {v}(bot 本就会开到这么多?)"
                )
            else:
                print(f"  [FAIL] 多开一个矿反而更少:t={cut:.0f}s 基地 {v} < baseline {b}")

    # ── 「全力补农民」:注入后农民数应 >= baseline 同期 ──
    wm = by.get("workers_max")
    if wm:
        if not wm.get("injected"):
            print("  [?] workers=max 注入未确认")
        else:
            v, b = _cmp_at_cut(by, "workers_max", cut, 2)
            if v is None:
                print("  [?] 全力补农民无法判定:该局或对照局在可比时刻已被打崩(基地 0)")
            elif v >= b:
                print(f"  [OK] 全力补农民不低于 baseline:t={cut:.0f}s 农民 {v} vs {b}")
            else:
                print(f"  [FAIL] 全力补农民反而更少:t={cut:.0f}s 农民 {v} < baseline {b}")

    if wrk["injected"]:
        ws = wrk["series"]
        if len(ws) >= 4:
            mid = ws[len(ws) // 3]
            end = ws[-1]
            grew = end[2] - mid[2]
            print(
                f"  停农民：注入后农民 {mid[2]}(t={mid[0]:.0f}) -> {end[2]}(t={end[0]:.0f})，增量 {grew}"
            )
            if grew <= 2:
                print("  [OK] 停农民有效：注入后农民基本停涨")
            else:
                print("  [FAIL] 停农民无效：注入后农民仍明显增长")
        else:
            print("  [?] workers_stop 快照太少，判不了")
    else:
        print("  [?] workers_stop 注入未确认")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
