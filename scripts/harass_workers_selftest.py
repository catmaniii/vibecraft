"""harass_workers 通用骚扰执行器真局自验。

验"玩家说『派死神去骚扰对方农民』→ 被 claim 的死神真的飞到敌方矿区打农民"整条链在
**真局**里跑通(修复前 bug：死神被 claim 但杵着不动，无人命令)。

隔离设计(干净归因，避免假阳性)：
  - forced_opening=非骚扰开局(bio_stim，无 HarassWorkerLineAct) + sandbox_macro_only=True
    (bot 自身强制 defend、不出门) → bot 自身绝不会去打敌方农民。
  - VIBECRAFT_WHARASS_SELFTEST=1：debug 生 3 个死神在主基。
  - mock LLM 注入 harass_workers unit_claim(Reaper×3) → 死神 Reserved，**唯一**能驱动它们的
    就是 director 每 tick 的 _execute_worker_harass_micro。所以死神动 = player_claim 路径生效。

终态铁律(不是只看"发了命令")：
  1. WHARASSTRACE 每 tick 记被 claim 死神到敌主基距离 → min dist < 15
     (SC2 真把死神飞到敌矿；修复前它杵在主基 dist 恒 ~120)。
  2. telemetry enemy_workers_harassed(累计被我方打伤/打死的不同敌方农民) > 0
     (sandbox 下 bot 不出门 → 这个上升只可能来自被 claim 的死神)。

non-realtime(fast) + mock LLM(0 延迟)。跑法：
  .venv/Scripts/python.exe scripts/harass_workers_selftest.py [--seconds 200]
退出码 0=PASS，1=FAIL。
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
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

INJECT_TEXT = "派死神去骚扰对方农民"

# Mock LLM 响应：固定返回 harass_workers unit_claim(Reaper×3，target None=auto 找有农民的敌矿)。
# 绕开真 LLM(0 延迟，可 fast 跑)。结构 = 真 LLM 输出过的同款(few_shot 例 19c)。
MOCK_LLM_RESPONSE = {
    "interpretation_zh": "派 3 个死神去骚扰对方农民",
    "confidence": 0.95,
    "directives": [
        {
            "type": "unit_claim",
            "payload": {
                "selector": {"unit_type": "Reaper", "count": 6},
                "task": {"primary_action": {"verb": "harass_workers"}},
                "persistent": True,
            },
        }
    ],
}

_RE_POS = re.compile(r"WHARASSTRACE pos tag=(\d+) dist=([\d.]+) hp=([\d.]+)")
_RE_SPAWN = re.compile(r"WHARASS_SELFTEST spawned \d+ REAPER")


async def run(seconds: int, inject_after: int, map_name: str) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    log = logging.getLogger("wharass_selftest")

    log_path = _ROOT / "logs" / "harass_workers_selftest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    os.environ["VIBECRAFT_WHARASS_SELFTEST"] = "1"
    os.environ["VIBECRAFT_WHARASS_TRACE"] = "1"
    mock_path = _ROOT / "logs" / "wharass_mock_llm.json"
    mock_path.write_text(json.dumps(MOCK_LLM_RESPONSE, ensure_ascii=False), encoding="utf-8")
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)

    # 记录起跑时刻，之后据此挑本局新生成的 telemetry 目录
    start_wall = time.time()

    cfg = GameConfig(
        map_name=map_name,
        my_race="Terran",
        opponent_race="Zerg",
        opponent_difficulty="VeryEasy",
        realtime=False,
        forced_opening="bio_stim",  # 非骚扰开局(无 HarassWorkerLineAct)
        sandbox_macro_only=True,  # bot 自身强制 defend、不出门 → 干净归因
        game_time_limit_s=600,
    )

    gp = GameProcess()
    gp.start(cfg)
    seen_playing = asyncio.Event()
    ended = asyncio.Event()

    async def collect() -> None:
        async for msg in gp.raw_events():
            sc2 = str(msg.get("sc2"))
            if sc2 == "playing":
                seen_playing.set()
            if sc2 in ("crashed", "ended"):
                ended.set()
                return

    ctask = asyncio.create_task(collect())

    async def do_inject() -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(seen_playing.wait(), timeout=180)
        await asyncio.sleep(inject_after)
        log.info("INJECT %r", INJECT_TEXT)
        gp.send_command(
            {
                "type": "command",
                "text": INJECT_TEXT,
                "client_id": "selftest",
                "issued_at": time.time(),
            }
        )

    itask = asyncio.create_task(do_inject())
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(ended.wait(), timeout=seconds)
    for t in (itask, ctask):
        if not t.done():
            t.cancel()
    with contextlib.suppress(Exception):
        await gp.stop()

    raw = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""

    # ── 指标：WHARASSTRACE 到敌主基距离 ──────────────────────────────────
    spawned = bool(_RE_SPAWN.search(raw))
    dist_by_tag: dict[str, list[float]] = {}
    for tag, dist, _hp in _RE_POS.findall(raw):
        dist_by_tag.setdefault(tag, []).append(float(dist))
    min_dist = min((min(v) for v in dist_by_tag.values()), default=9999.0)
    max_start = max((v[0] for v in dist_by_tag.values()), default=0.0)
    driven_tags = len(dist_by_tag)

    # ── 指标：telemetry enemy_workers_harassed(累计) ─────────────────────
    # 挑本局(起跑后新建)的 game_ 目录里的 telemetry.jsonl
    harassed_max = 0
    tel_files = sorted(
        _ROOT.glob("logs/game_*/telemetry.jsonl"),
        key=lambda p: p.stat().st_mtime,
    )
    tel_used = None
    for p in reversed(tel_files):
        if p.stat().st_mtime >= start_wall - 5:
            tel_used = p
            break
    if tel_used is not None:
        for line in tel_used.read_text(encoding="utf-8", errors="replace").splitlines():
            with contextlib.suppress(Exception):
                rec = json.loads(line)
                v = rec.get("enemy_workers_harassed")
                if v is None:
                    # 嵌套在 enemy block 里
                    v = (rec.get("enemy") or {}).get("enemy_workers_harassed")
                if isinstance(v, int) and v > harassed_max:
                    harassed_max = v

    # ── 报告 ─────────────────────────────────────────────────────────────
    _REACH = 15.0
    print()
    print("===== HARASS_WORKERS SELFTEST (通用骚扰执行器) =====")
    print(f"  debug 生死神                            : {spawned}")
    print(f"  被 claim 死神被驱动(有 WHARASSTRACE)数  : {driven_tags}")
    print(f"  死神初始到敌主基距离(最大)              : {max_start:.1f}")
    print(f"  死神到敌主基最近距离 min_dist           : {min_dist:.1f}  (需 < {_REACH})")
    print(f"  telemetry enemy_workers_harassed(累计)  : {harassed_max}  (需 > 0)")
    print(f"  telemetry 文件                          : {tel_used}")
    print()

    fails: list[str] = []
    if not spawned:
        fails.append("没 debug 生出死神(VIBECRAFT_WHARASS_SELFTEST 钩子没触发)")
    if driven_tags < 1:
        fails.append("没有 WHARASSTRACE —— player_claim 骚扰微操没驱动任何死神(claim 没发布 tags?)")
    if min_dist > _REACH:
        fails.append(
            f"死神到敌主基最近={min_dist:.1f} > {_REACH} —— 死神没真飞到敌矿(可能杵着没动)"
        )
    if harassed_max < 1:
        fails.append("enemy_workers_harassed=0 —— 死神没打到敌方农民(没造成外部终态)")

    if fails:
        print("结果: FAIL")
        for f in fails:
            print("  - " + f)
        return 1

    print("结果: PASS")
    print(f"  (1) [OK] mock LLM 注入 harass_workers claim → {driven_tags} 个死神被驱动")
    print(f"  (2) [OK] 终态: 死神从 dist~{max_start:.0f} 真飞到敌矿(min={min_dist:.1f} < {_REACH})")
    print(f"  (3) [OK] 终态: 敌方农民被打(enemy_workers_harassed={harassed_max} > 0)")
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser(description="harass_workers 通用骚扰执行器真局自验")
    ap.add_argument("--seconds", type=int, default=200, help="wall-clock 秒(fast)")
    ap.add_argument("--inject-after", type=int, default=3)
    ap.add_argument("--map", default="DaybreakLE")
    args = ap.parse_args()
    return await run(args.seconds, args.inject_after, args.map)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
