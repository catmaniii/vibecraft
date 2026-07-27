"""挂件(addon)真局自验:mock LLM 注入「出两个坦克」,验证

  1. 重工(Factory)没挂 TechLab 时自动补挂件（auto_addon emit FACTORYTECHLAB）
  2. 挂件真的挂上去（BUILD ADDON FACTORYTECHLAB / 出现 FACTORYTECHLAB 结构）
  3. 坦克真的出来（SIEGETANK trained）

forced_opening=reaper_expand：它建 Factory 但不挂 FactoryTechLab → 出坦克会触发自动补挂件。
mock LLM 0 延迟 → non-realtime fast（CLAUDE.md：mock 注入用 fast）。

跑法：.venv/Scripts/python.exe scripts/addon_selftest.py [--seconds 160] [--inject-after 35]
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

# 普通测(#542 自动补挂件)：出坦克 → 自动补 FACTORYTECHLAB。
INJECT_TEXT = "出两个坦克"
MOCK_LLM_RESPONSE = {
    "interpretation_zh": "出 2 个攻城坦克",
    "confidence": 0.95,
    "directives": [
        {
            "type": "production_override",
            "payload": {"items": [{"unit_type": "SiegeTank", "count": 2}]},
        }
    ],
}
# block-addon 测(#543 起飞挪位)：直接「重工下科技挂件」structure_override —— 不走出坦克
# (出坦克会 auto-build 一座真重工干扰)，让 debug 生在开阔地那座被堵的重工是唯一的 → 必须挪。
INJECT_TEXT_BLOCK = "重工下科技挂件"
MOCK_LLM_RESPONSE_BLOCK = {
    "interpretation_zh": "给重工挂科技实验室",
    "confidence": 0.95,
    "directives": [
        {
            "type": "structure_override",
            "payload": {"items": [{"structure_type": "FactoryTechLab", "delta": 1}]},
        }
    ],
}


async def run(seconds: int, inject_after: int, map_name: str, block_addon: bool = False) -> int:
    log = logging.getLogger("addon_selftest")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log_path = _ROOT / "logs" / "addon_selftest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    mock = MOCK_LLM_RESPONSE_BLOCK if block_addon else MOCK_LLM_RESPONSE
    inject_text = INJECT_TEXT_BLOCK if block_addon else INJECT_TEXT
    mock_path = _ROOT / "logs" / "addon_mock_llm.json"
    mock_path.write_text(_json.dumps(mock, ensure_ascii=False), encoding="utf-8")
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)
    # #543 起飞挪位验证：堵住重工挂件位，强制走 LIFT→LAND→build。
    if block_addon:
        os.environ["VIBECRAFT_ADDON_BLOCK_TEST"] = "1"
    else:
        os.environ.pop("VIBECRAFT_ADDON_BLOCK_TEST", None)

    # block-addon(#543 起飞挪位)用 marine_rush(5 兵营**无重工**)→ debug 生的重工是
    # **唯一**重工、挂件位被堵，director 只能挪它 → 触发 LIFT。普通测用 reaper_expand(有重工)。
    opening = "marine_rush" if block_addon else "reaper_expand"
    cfg = GameConfig(
        map_name=map_name,
        my_race="Terran",
        opponent_race="Zerg",
        opponent_difficulty="VeryEasy",
        realtime=False,
        forced_opening=opening,
        # sandbox：bot 只 macro 不速胜，游戏不早结束 → 注入有窗口（fast 下游戏会跑到限时）。
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
        log.info("INJECT %r", inject_text)
        gp.send_command(
            {
                "type": "command",
                "text": inject_text,
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

    # 解析 server log（挂件下令路径）
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    auto_addon = "auto_addon emit FACTORYTECHLAB" in text
    build_addon = "BUILD ADDON" in text and "FACTORYTECHLAB" in text
    # 坦克/挂件是否真出现 → 读 telemetry（坦克走 production_override bot.train，不在 sharpy 日志里）
    techlab_built = False
    tank_trained = False
    with contextlib.suppress(Exception):
        dirs = sorted(
            (_ROOT / "logs").glob("game_*"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if dirs:
            for line in (dirs[0] / "telemetry.jsonl").read_text(encoding="utf-8").splitlines():
                rec = _json.loads(line)
                if rec.get("kind") != "snapshot":
                    continue
                b = rec.get("buildings", {})
                u = rec.get("units", {})
                if b.get("FACTORYTECHLAB", 0) > 0:
                    techlab_built = True
                if u.get("SIEGETANK", 0) + u.get("SIEGETANKSIEGED", 0) >= 2:
                    tank_trained = True
    # #543 起飞挪位专项：堵住挂件位后应出现 spawned blocker + LIFT + LAND
    blocked = "ADDON_BLOCK_TEST spawned" in text
    lifted = "addon relocate: LIFT" in text
    landed = "addon relocate: LAND" in text
    print("\n===== ADDON SELFTEST =====")
    print(f"  auto_addon emit FACTORYTECHLAB : {auto_addon}")
    print(f"  BUILD ADDON FACTORYTECHLAB     : {build_addon}")
    print(f"  FACTORYTECHLAB 真挂上(telemetry) : {techlab_built}")
    print(f"  SIEGETANK>=2 (telemetry)        : {tank_trained}")
    if block_addon:
        print(f"  [#543] 堵挂件位 spawned          : {blocked}")
        print(f"  [#543] 起飞 LIFT                 : {lifted}")
        print(f"  [#543] 落下 LAND                 : {landed}")
        # 挂件位被堵 → 必须起飞挪位(LIFT+LAND)+ 最终挂上
        ok = blocked and lifted and landed and techlab_built
    else:
        ok = auto_addon and tank_trained
    print(f"  => {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=160)
    ap.add_argument("--inject-after", type=int, default=35)
    ap.add_argument("--map", default="DaybreakLE")
    ap.add_argument("--block-addon", action="store_true", help="#543: 堵挂件位验起飞挪位")
    args = ap.parse_args()
    return await run(args.seconds, args.inject_after, args.map, args.block_addon)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
