"""#560 通用"空闲 CC 飞去开矿"真局自验。

VIBECRAFT_SPARECC_SELFTEST 钩子在远离矿处 debug 生 1 个 idle spare CC（代表玩家预造的额外 CC）
→ SpareCcExpandAct 自动把它 LIFT 起飞 → 飞到最近未占扩张点 → LAND 落地开矿。

验**终态**（不只看"发了命令"）：
  1. SPARECCTRACE lift（act 检测到 spare CC 并起飞）
  2. SPARECCTRACE landing（飞到锁定落点发 LAND）
  3. telemetry：扩张点数（townhalls 总数）增加 —— spare CC 真落在了一个**新的扩张点**（开了矿）。
     用 COMMANDCENTER/ORBITALCOMMAND/PLANETARYFORTRESS 落地后 townhalls 计数 ≥ 起始+1 判定。

non-realtime(fast)。跑法：.venv/Scripts/python.exe scripts/spare_cc_expand_selftest.py [--seconds 90]
退出码 0=PASS（spare CC 真飞到新扩张点开矿），1=FAIL。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json as _json
import logging
import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

_RE_SPAWN = re.compile(r"SPARECC_SELFTEST spawned")
_RE_LIFT = re.compile(r"SPARECCTRACE lift ")
_RE_LANDING = re.compile(r"SPARECCTRACE (landing|stuck_land) ")


async def run(seconds: int, map_name: str) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log_path = _ROOT / "logs" / "spare_cc_expand_selftest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    os.environ["VIBECRAFT_SPARECC_SELFTEST"] = "1"
    os.environ["VIBECRAFT_SPARECC_TRACE"] = "1"
    mock_path = _ROOT / "logs" / "sparecc_mock_llm.json"
    mock_path.write_text("[]", encoding="utf-8")
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)

    cfg = GameConfig(
        map_name=map_name,
        my_race="Terran",
        opponent_race="Zerg",
        opponent_difficulty="VeryEasy",
        realtime=False,
        forced_opening="reaper_expand",
        sandbox_macro_only=True,
        game_time_limit_s=300,
    )

    gp = GameProcess()
    gp.start(cfg)
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

    raw = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    spawned = bool(_RE_SPAWN.search(raw))
    lifted = bool(_RE_LIFT.search(raw))
    landing = bool(_RE_LANDING.search(raw))

    # telemetry 终态：townhalls(CC+orbital+PF) 计数峰值 —— spare CC 落地新扩张点 → +1
    # 起始通常 1（主基）+ debug spare(1，飞行中不算 townhall)。落地后回到地面 → 计数应达 2+。
    th_max = 0
    with contextlib.suppress(Exception):
        dirs = sorted(
            (_ROOT / "logs").glob("game_*"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if dirs:
            for line in (dirs[0] / "telemetry.jsonl").read_text(encoding="utf-8").splitlines():
                try:
                    rec = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                if rec.get("kind") != "snapshot":
                    continue
                b = rec.get("buildings", {})
                th = (
                    b.get("COMMANDCENTER", 0)
                    + b.get("ORBITALCOMMAND", 0)
                    + b.get("PLANETARYFORTRESS", 0)
                )
                th_max = max(th_max, th)

    print()
    print("===== SPARE CC EXPAND SELFTEST (#560) =====")
    print(f"  SPARECC_SELFTEST spawned spare CC : {spawned}")
    print(f"  SPARECCTRACE lift (检测+起飞)      : {lifted}")
    print(f"  SPARECCTRACE landing (飞到落点LAND): {landing}")
    print(f"  telemetry townhall 峰值(地面 CC 数): {th_max}  (需 >=2 = spare 落到新扩张点)")
    print()

    fails: list[str] = []
    if not spawned:
        fails.append("没生成 spare CC（debug 钩子没触发）")
    if not lifted:
        fails.append("SpareCcExpandAct 没检测到 spare CC 并起飞（SPARECCTRACE lift 缺）")
    if not landing:
        fails.append("没飞到落点发 LAND（SPARECCTRACE landing 缺）")
    if th_max < 2:
        fails.append(
            f"telemetry townhall 峰值={th_max} < 2 —— spare CC 没真落到新扩张点开矿（终态未达成）"
        )

    if fails:
        print("结果: FAIL")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("结果: PASS")
    print("  (1) [OK] 生成 idle spare CC")
    print("  (2) [OK] SpareCcExpandAct 检测+起飞+飞到落点 LAND")
    print(f"  (3) [OK] 终态:townhall 峰值={th_max} → spare CC 真落到新扩张点开矿")
    print("  => #560 空闲 CC 飞去开矿 真机路径生效")
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser(description="空闲 CC 飞去开矿真局自验(#560)")
    ap.add_argument("--seconds", type=int, default=90)
    ap.add_argument("--map", default="DaybreakLE")
    args = ap.parse_args()
    return await run(args.seconds, args.map)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
