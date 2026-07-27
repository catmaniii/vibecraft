"""地堡回收(salvage)真局自验：两段注入，验 salvage 真机路径拆掉**真实建造**的地堡。

背景：debug_create_unit 生成的建筑 SC2 不接受 SALVAGEBUNKER_SALVAGE（engine 限制）。
需要 bot 真实建造地堡，再验 salvage。

流程：
  T+5s（inject1）：structure_override 令 bot 建 1 座地堡
  T+11s（inject2）：salvage 指令（unit_type=Bunker）
  验证：
    1. telemetry 中 BUNKER 最大计数 >= 1（地堡确实建起来了）
    2. SALVAGETRACE salvage_applied salvaged>=1（director 发了 ability）
    3. telemetry 末期 BUNKER 计数 = 0（地堡真被拆了 —— bot.do(ability) 真机路径生效铁证）

mock LLM 在第 1 次注入时返回 structure_override，第 2 次注入时动态改文件返回 salvage。
non-realtime(fast)，6min 游戏 ≈ 16s wall-clock；inject1=5s / inject2=11s 均在窗口内。

跑法：.venv/Scripts/python.exe scripts/salvage_selftest.py [--seconds 50]
退出码 0=PASS，1=FAIL。
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
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

# 第 1 次注入：structure_override 建 1 座地堡（100 矿，5s wall = ~110s game，来得及）
INJECT_TEXT_1 = "建一个地堡"
# 第 2 次注入：salvage 所有地堡
INJECT_TEXT_2 = "拆掉所有地堡"

# Mock LLM 用列表 match 格式，bot 启动时一次性读入；按 user_text 含哪个 match 返回对应响应。
# 根因：MockLLMProvider 在 LLMConfig 初始化时读文件，运行中替换文件不生效（2026-06-19 真局验）。
MOCK_LLM_LIST = [
    {
        "match": "建一个地堡",
        "response": {
            "interpretation_zh": "建造 1 座地堡",
            "confidence": 0.95,
            "directives": [
                {
                    "type": "structure_override",
                    "payload": {"items": [{"structure_type": "Bunker", "delta": 1}]},
                }
            ],
        },
    },
    {
        "match": "拆掉所有地堡",
        "response": {
            "interpretation_zh": "拆掉所有地堡，回收资源",
            "confidence": 0.95,
            "directives": [
                {
                    "type": "salvage",
                    "payload": {
                        "selector": {"unit_type": "Bunker"},
                    },
                }
            ],
        },
    },
]

_RE_SALVAGETRACE = re.compile(r"SALVAGETRACE salvage_applied.*?salvaged=(\d+)")


async def run(seconds: int, inject1: int, inject2: int, map_name: str) -> int:
    log = logging.getLogger("salvage_selftest")
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    log_path = _ROOT / "logs" / "salvage_selftest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)

    mock_path = _ROOT / "logs" / "salvage_mock_llm.json"
    # 列表 match 格式：bot 启动时一次读入，按 user_text 子串匹配分发。
    mock_path.write_text(_json.dumps(MOCK_LLM_LIST, ensure_ascii=False), encoding="utf-8")
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)
    # 不用 debug 生地堡（SC2 对 debug 生建筑拒绝 salvage ability）
    os.environ.pop("VIBECRAFT_SALVAGE_TEST", None)

    # reaper_expand：有矿 + SCV，bot 能响应 structure_override 建地堡
    cfg = GameConfig(
        map_name=map_name,
        my_race="Terran",
        opponent_race="Zerg",
        opponent_difficulty="VeryEasy",
        realtime=False,
        forced_opening="reaper_expand",
        sandbox_macro_only=True,  # 不进攻，避免游戏早结束
        game_time_limit_s=360,  # 6min → fast ≈16s wall-clock
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

        # 注入 1：structure_override 建地堡
        await asyncio.sleep(inject1)
        log.info("INJECT-1 %r", INJECT_TEXT_1)
        gp.send_command(
            {
                "type": "command",
                "text": INJECT_TEXT_1,
                "client_id": "selftest",
                "issued_at": time.time(),
            }
        )

        # 注入 2：salvage（mock LLM 已在启动时按 match 格式加载，无需更新文件）
        await asyncio.sleep(inject2 - inject1)
        log.info("INJECT-2 %r", INJECT_TEXT_2)
        gp.send_command(
            {
                "type": "command",
                "text": INJECT_TEXT_2,
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

    # ---------- 解析子进程日志 ----------
    raw_log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""

    salvaged_count = 0
    for m in _RE_SALVAGETRACE.finditer(raw_log):
        salvaged_count = max(salvaged_count, int(m.group(1)))

    cast_fail = "cast_unit_ability: cast fail" in raw_log

    # 读 telemetry：BUNKER 计数时间序列
    bunker_max_seen = 0
    bunker_final = -1
    bunker_timeline: list[tuple[float, int]] = []
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
                cnt = rec.get("buildings", {}).get("BUNKER", 0)
                bunker_max_seen = max(bunker_max_seen, cnt)
                bunker_timeline.append((float(rec.get("t", 0)), cnt))
            if bunker_timeline:
                bunker_final = (
                    min(b for _, b in bunker_timeline[-3:])
                    if len(bunker_timeline) >= 3
                    else bunker_timeline[-1][1]
                )

    # ---------- 输出 ----------
    print()
    print("===== SALVAGE SELFTEST =====")
    print(f"  SALVAGETRACE salvaged 最大值     : {salvaged_count}")
    print(f"  cast_unit_ability cast fail      : {cast_fail}")
    print(f"  telemetry BUNKER 最大计数         : {bunker_max_seen}")
    print(f"  telemetry BUNKER 末期计数(末3帧)  : {bunker_final}")
    if bunker_timeline:
        # 打印关键节点
        first_nonzero = next((t for t, c in bunker_timeline if c > 0), None)
        last_nonzero = None
        for t, c in reversed(bunker_timeline):
            if c > 0:
                last_nonzero = t
                break
        print(f"  BUNKER 首次出现 t={first_nonzero}  最后非零 t={last_nonzero}")
    print()

    fails: list[str] = []

    if bunker_max_seen < 1:
        fails.append(
            "telemetry 里 BUNKER 最大计数=0，地堡没建起来"
            "（structure_override 没生效 / 矿不够 / bot 没响应）"
        )
    if salvaged_count < 1:
        fails.append(
            f"SALVAGETRACE salvaged={salvaged_count}，期望 >=1"
            "（director 没解析到地堡 / 没调 cast_unit_ability）"
        )
    if cast_fail:
        fails.append("日志出现 cast_unit_ability cast fail（ability 发送路径异常）")
    if bunker_final > 0:
        fails.append(
            f"telemetry 末期 BUNKER 计数={bunker_final}，期望 0"
            "（地堡没被真正拆掉 — bot.do(ability) 真机路径可能失效）"
        )
    elif bunker_final == -1:
        fails.append("没有读到 telemetry snapshot（游戏可能未正常运行）")

    if fails:
        print("结果: FAIL")
        for f in fails:
            print(f"  - {f}")
        return 1

    print("结果: PASS")
    print(f"  (1) [OK] 地堡建起来，BUNKER 最大={bunker_max_seen}")
    print(f"  (2) [OK] director 发 salvage ability，salvaged={salvaged_count}，无 cast fail")
    print(f"  (3) [OK] telemetry BUNKER 末期={bunker_final}（归 0）")
    print("  => salvage 真机路径 cast_unit_ability(SALVAGEEFFECT_SALVAGE) 已验证生效")
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser(description="salvage 指令真局自验（两段注入）")
    ap.add_argument("--seconds", type=int, default=50, help="总 wall-clock 超时(s)")
    ap.add_argument("--inject1", type=int, default=5, help="建地堡注入时机(seen_playing后s)")
    ap.add_argument("--inject2", type=int, default=11, help="salvage 注入时机(seen_playing后s)")
    ap.add_argument("--map", default="DaybreakLE")
    args = ap.parse_args()
    return await run(args.seconds, args.inject1, args.inject2, args.map)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
