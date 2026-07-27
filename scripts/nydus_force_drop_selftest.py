"""玩家喊「放坑道虫」真局自验（2026-07-26 真局 bug 修复的验证）。

真局现象：玩家语音"放坑道虫" → LLM 把它解析成 `cast_ability`，且 ability 名是**编出来的**
`NYDUSWORMLOCATION_NYDUSNETWORK`（SC2 里没这个枚举）→ 旧路径只打一条 unknown ability 警告、
`cast 0 times`，玩家看着毫无反应（logs/server_20260726_192600 后续那局）。

修法：facade `cast_ability_on_units` 认出"这是在指放坑道虫"（关键词宽松匹配，不指望 LLM 拼对
枚举名），翻译成**玩家强制投放意图** `nydus_force_drop_until`；`_BuildNydusCanalAtEnemy` 读到后
无视拉黑 + 按 COMMIT 放宽窗口阈值去投。

本脚本用 **mock LLM 原样重放那条出错的 directive**（不测 LLM 识别，只测执行链接不接得住），
判定看子进程日志：

  PASS 门 ①：出现 `玩家强制投放坑道虫`（facade 认出来了，不再是 unknown ability）
  PASS 门 ②：**不再**出现 `unknown ability=NYDUSWORMLOCATION`（旧的静默失效路径没走）

跑法（mock LLM 无延迟 → 用 non-realtime fast）：
  .venv/Scripts/python.exe scripts/nydus_force_drop_selftest.py [--seconds 150]
退出码 0=PASS，1=FAIL。
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

INJECT_TEXT = "放坑道虫"

# 原样重放真局里 LLM 吐出的那条(ability 名是它自己编的,SC2 无此枚举)。
MOCK_LLM_RESPONSE = {
    "interpretation_zh": "在敌方基地放坑道虫",
    "confidence": 0.9,
    # 形状照 few_shot 例 27b:cast_ability 是 unit_claim 里的 primary_action verb,不是顶层 type
    # (真局里 LLM 输出的就是这个形状 —— 日志能看到它 claim 了一只单位)。
    "directives": [
        {
            "type": "unit_claim",
            "payload": {
                "selector": {"unit_type": "NydusNetwork", "count": 1},
                "task": {
                    "primary_action": {
                        "verb": "cast_ability",
                        "ability_id": "NYDUSWORMLOCATION_NYDUSNETWORK",
                    }
                },
                "persistent": False,
            },
        }
    ],
}


async def run(seconds: int, map_name: str, inject_after: float) -> int:
    log = logging.getLogger("nydus_force_drop_selftest")
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    log_path = _ROOT / "logs" / "nydus_force_drop_selftest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    mock_path = _ROOT / "logs" / "nydus_force_drop_mock_llm.json"
    mock_path.write_text(_json.dumps(MOCK_LLM_RESPONSE, ensure_ascii=False), encoding="utf-8")
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)

    cfg = GameConfig(
        map_name=map_name,
        my_race="Zerg",
        opponent_race="Terran",
        opponent_difficulty="VeryEasy",
        realtime=False,
        forced_opening="nydus",
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
            await asyncio.wait_for(seen_playing.wait(), timeout=120)
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
    await asyncio.sleep(1.0)

    raw = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    n_force = raw.count("玩家强制投放坑道虫")
    n_unknown = raw.count("unknown ability=NYDUSWORMLOCATION")
    print("\n===== 玩家「放坑道虫」指令链自验 =====")
    print(f"  facade 认出并转成强制投放 : {n_force} 次")
    print(f"  旧的 unknown ability 路径 : {n_unknown} 次（应为 0）")
    ok = n_force >= 1 and n_unknown == 0
    print(f"\n结果: {'PASS' if ok else 'FAIL'}")
    if not ok:
        if n_force == 0:
            print("  - facade 没认出这条 ability 名（`_is_nydus_worm_ability` 匹配漏了？）")
        if n_unknown:
            print("  - 仍走了旧的 unknown ability 静默失效路径")
    return 0 if ok else 1


async def main() -> int:
    ap = argparse.ArgumentParser(description="玩家「放坑道虫」指令链自验")
    ap.add_argument("--seconds", type=int, default=150)
    ap.add_argument("--map", default="DaybreakLE")
    ap.add_argument(
        "--inject-after", type=float, default=6.0, help="看到 playing 后多久注入(墙钟秒)"
    )
    args = ap.parse_args()
    return await run(args.seconds, args.map, args.inject_after)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
