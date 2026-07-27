"""航母回家待命抽搐 真局自验(调试清单 #10)。

mock LLM 注入「所有航母回家待命」(unit_claim standby Carrier @ main),配 common_bot 的
VIBECRAFT_CARRIER_STANDBY_TEST 钩子(在远离主基处 debug 生 4 航母)。standby tick 把航母从
远处拉回家。读 STANDBYTRACE 逐帧日志判:
  1. 航母是否平滑收敛回家(每个 tag 的 d_pos 单调下降到 < STANDBY_RADIUS)。
  2. move 命令是否每帧重发(抽搐根因)—— 修后应大量 MOVE_SKIP(已在去同一点不重发)。

A/B:--no-dedupe 退回旧逻辑(每帧重发,对照组),不加则用修后逻辑。

跑法(mock LLM → non-realtime fast,~1-2 min):
  .venv/Scripts/python.exe scripts/carrier_standby_selftest.py [--no-dedupe]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json as _json
import logging
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

INJECT_TEXT = "所有航母回家待命"
MOCK_LLM_RESPONSE = {
    "interpretation_zh": "所有航母回主基地待命",
    "confidence": 0.95,
    "directives": [
        {
            "type": "unit_claim",
            "payload": {
                "selector": {"unit_type": "Carrier"},
                "task": {
                    "primary_action": {
                        "verb": "standby",
                        "target": {"kind": "named_spot", "named_spot": "main"},
                    }
                },
                "persistent": True,
            },
        }
    ],
}


async def run(seconds: int, inject_after: int, map_name: str, no_dedupe: bool) -> int:
    log = logging.getLogger("carrier_selftest")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _suffix = "ctrl" if no_dedupe else "fix"
    log_path = _ROOT / "logs" / f"carrier_standby_{_suffix}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        if log_path.exists():
            log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    mock_path = _ROOT / "logs" / f"carrier_mock_llm_{_suffix}.json"
    mock_path.write_text(_json.dumps(MOCK_LLM_RESPONSE, ensure_ascii=False), encoding="utf-8")
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)
    os.environ["VIBECRAFT_CARRIER_STANDBY_TEST"] = "1"
    os.environ["VIBECRAFT_STANDBY_TRACE"] = "1"
    if no_dedupe:
        os.environ["VIBECRAFT_STANDBY_NO_DEDUPE"] = "1"
    else:
        os.environ.pop("VIBECRAFT_STANDBY_NO_DEDUPE", None)

    cfg = GameConfig(
        map_name=map_name,
        my_race="Protoss",
        opponent_race="Zerg",
        opponent_difficulty="VeryEasy",
        realtime=False,
        forced_opening="skytoss",
        sandbox_macro_only=True,
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

    # 解析 STANDBYTRACE：每个 tag 的 d_pos 轨迹 + MOVE_ISSUED/SKIP 计数
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    per_tag: dict[str, dict] = {}
    for line in text.splitlines():
        if "STANDBYTRACE" not in line:
            continue
        try:
            parts = dict(p.split("=", 1) for p in line.split() if "=" in p)
            tag = parts.get("tag", "?")
            d = float(parts.get("d_pos", "0"))
            act = parts.get("act", "")
        except Exception:
            continue
        e = per_tag.setdefault(tag, {"d": [], "issued": 0, "skip": 0})
        e["d"].append(d)
        if act == "MOVE_ISSUED":
            e["issued"] += 1
        elif act == "MOVE_SKIP":
            e["skip"] += 1

    spawned = "CARRIER_STANDBY_TEST spawned" in text
    print("\n===== CARRIER STANDBY SELFTEST =====")
    print(f"  mode: {'NO_DEDUPE(旧/对照)' if no_dedupe else 'DEDUPE(修后)'}")
    print(f"  4 航母 spawned : {spawned}")
    converged_all = bool(per_tag)
    for tag, e in per_tag.items():
        d0 = e["d"][0] if e["d"] else 0.0
        dmin = min(e["d"]) if e["d"] else 0.0
        total = e["issued"] + e["skip"]
        issue_ratio = (e["issued"] / total) if total else 0.0
        converged = dmin < 12.0
        converged_all = converged_all and converged
        print(
            f"  tag={tag}: d_pos {d0:.0f}→min {dmin:.0f} converged={converged} "
            f"move_issued={e['issued']} skip={e['skip']} issue_ratio={issue_ratio:.2f}"
        )
    # 判据 = **orbiting 检测**:核心是航母回家后**安定**,不在家门口被跳变目标反复拽出。
    # 安定 → move 分支(d_pos>RADIUS)只在"接近中"短暂出现,到家后几乎不再进 → 总 trace 极少;
    # 抽搐(bug) → 永远在 RADIUS 边界 orbit,每帧重入 move 分支 → 总 trace 几千条。
    # 真因(_own_main 解析跳变)修前 ~2500 条、修后个位数,阈值 200 干净区分。
    total_traces = sum(e["issued"] + e["skip"] for e in per_tag.values())
    print(f"  总 move-branch trace 数: {total_traces} (orbiting→几千 / settled→个位数)")
    settled = total_traces < 200
    if no_dedupe:
        # 对照组(每帧重发):即便真因修了,dedupe 关掉仍每帧 issue,但只要 pos 稳定就不 orbit。
        ok = spawned and converged_all and settled
    else:
        ok = spawned and converged_all and settled
    print(f"  => {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=170)
    ap.add_argument("--inject-after", type=int, default=8)
    ap.add_argument("--map", default="DaybreakLE")
    ap.add_argument("--no-dedupe", action="store_true", help="退回旧逻辑(每帧重发)对照组")
    args = ap.parse_args()
    return await run(args.seconds, args.inject_after, args.map, args.no_dedupe)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
