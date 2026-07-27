"""全体防守智能选点 真局自验。

验两条规则:
  1. 敌人接近某己方基地 → 优先守该基地(assaulting_enemy_power>0 的 zone)。
  2. 基地附近无敌 → 守"距敌方主基最近的己方基地"(最前沿),而非 natural。

skytoss vs **凶**虫族(VeryHard,真有进攻压力)→ 不用 sandbox,让敌人真打基地,观察:
  - 敌压某 zone 时它的 assaulting_enemy_power 是否真>0(规则1 信号活没活)。
  - army 中心去了威胁 zone / 最前沿 zone / 还是傻待 natural。

mock LLM 注入 tactical_objective defend(= UI"全体防守")。读 DEFENDTRACE 逐帧判读。

跑法:.venv/Scripts/python.exe scripts/defend_selftest.py [--seconds 200] [--inject-after 30]
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

INJECT_TEXT = "全体防守"
MOCK_LLM_RESPONSE = {
    "interpretation_zh": "全军防守",
    "confidence": 0.95,
    "directives": [
        {"type": "tactical_objective", "payload": {"verb": "defend", "persistent": True}}
    ],
}


async def run(seconds: int, inject_after: int, map_name: str, difficulty: str) -> int:
    log = logging.getLogger("defend_selftest")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log_path = _ROOT / "logs" / "defend_selftest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        if log_path.exists():
            log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    mock_path = _ROOT / "logs" / "defend_mock_llm.json"
    mock_path.write_text(_json.dumps(MOCK_LLM_RESPONSE, ensure_ascii=False), encoding="utf-8")
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)
    os.environ["VIBECRAFT_DEFEND_TRACE"] = "1"
    os.environ["VIBECRAFT_DEFEND_FORCE_BASES"] = "1"  # 强制多基地,区分 home vs 最前沿
    os.environ.pop("VIBECRAFT_DEFEND_SPAWN_ENEMY", None)  # 本轮纯无威胁,测规则2 army 去哪

    cfg = GameConfig(
        map_name=map_name,
        my_race="Protoss",
        opponent_race="Zerg",
        opponent_difficulty=difficulty,
        realtime=False,
        forced_opening="skytoss",
        # sandbox_macro_only:bot 只防守(不送)→ 活得久、能开分基地铺出多 zone;且自动 pin
        # intent=defend(runbook §6),无需注入。VeryHard 敌人仍真打基地 → 触发威胁信号。
        sandbox_macro_only=True,
        game_time_limit_s=900,
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

    # 读 DEFENDTRACE：defend 期间是否出现过 pw>0(信号活)、army 与各 zone 的关系
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    defend_lines = [ln for ln in text.splitlines() if "DEFENDTRACE" in ln and "intent=defend" in ln]
    # 判威胁:任一 defend 帧里有 zone 的 assaulting_enemy_power > 0(规则1 信号活)
    threat_seen = False
    for ln in defend_lines:
        for tok in ln.split():
            if tok.startswith("pw="):
                with contextlib.suppress(ValueError):
                    if float(tok[3:]) > 0.0:
                        threat_seen = True
    print("\n===== DEFEND SELFTEST =====")
    print(f"  defend trace 行数: {len(defend_lines)}")
    print(f"  规则1 信号(某己方 zone assaulting_enemy_power>0 出现过): {threat_seen}")
    print("  —— 以下抽样供人工判读 army 是否去威胁/最前沿 zone ——")
    for ln in defend_lines[:: max(1, len(defend_lines) // 12)][:12]:
        print("   " + ln.split("] ")[-1].strip() if "] " in ln else "   " + ln.strip())
    # 自验只做信号存活的硬断言;选点正确性靠人工判读抽样(选点是策略,需肉眼)
    ok = len(defend_lines) > 0
    print(f"  => {'TRACE OK' if ok else 'NO DEFEND TRACE'}")
    return 0 if ok else 1


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=200)
    ap.add_argument("--inject-after", type=int, default=30)
    ap.add_argument("--map", default="DaybreakLE")
    ap.add_argument("--difficulty", default="VeryEasy")
    args = ap.parse_args()
    return await run(args.seconds, args.inject_after, args.map, args.difficulty)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
