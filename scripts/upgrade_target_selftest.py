"""攻防升级目标等级封顶门 真局自验（baseline vs capped 对照）。

验证 vendor/sharpy/sharpy/plans/acts/tech.py::Tech.execute 的封顶门在**真局**里
真的生效：玩家把某条攻防升级线 target 设为 0 → 终局 telemetry.jsonl 里该 family
**完全没有** upgrade_complete 事件（SC2 引擎 on_upgrade_complete 回调确认的真实
终态，不是 bot 自己打的"我跳过了"中间 trace —— salvage 纪律硬要求）。

两个 case（design doc 要求 per-family 断言，别聚合）：
  A. 神族地面攻 PROTOSSGROUNDWEAPONS —— forced_opening=iac_2base（该 build 早期
     supply 28 就 research 地面攻击1/防御1），不需要 LLM，只发 macro_action。
  C. 虫族空攻 ZERGFLYERWEAPONS（评审强调最易错线，旧代码错名 ZERGFLYERATTACK）——
     forced_opening=12pool(默认) + mock LLM 注入 strategy_set 切
     persistent_brood_corruptor（Spire ready 后立即 research 空攻1；Lair 前置链
     完整，跟 muta_ling_bane 缺 Lair 步骤的已知 build 顺序问题不同），再发
     macro_action 封顶。

判据（每 case）：
  baseline（不封顶）该 family 终态等级 > 0 —— 证明这条链真的会研究它，否则
  baseline 恰好是 0 也没有对照意义。
  capped（target=0）终态恒 0（没有任何该 family 的 upgrade_complete 记录）。

macro_action 走 director.apply_macro_action，不经过 LLM（真人局面板按钮走的就是
这条路径），bypass 掉真 LLM 延迟；只有 case C 的 strategy_set 需要 mock LLM
（VIBECRAFT_MOCK_LLM_JSON）。跟 proxy_chain_selftest.py 一样用 non-realtime fast，
game_id 走 GameConfig 字段（不用 os.environ，并发安全），telemetry.jsonl 直接按
game_id 定位（同 build_acceptance.py 的阻塞点 A 修复模式）。

用法：
  .venv/Scripts/python.exe scripts/upgrade_target_selftest.py [--seconds 200] [--repeat 2]
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
import uuid
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

logger = logging.getLogger("upgrade_target_selftest")

_LEVEL_RE = re.compile(r"^(.*)LEVEL([123])$")

# mock LLM 响应：只用于虫族 case（切到 persistent_brood_corruptor 才会研
# ZergFlyerWeapons；神族 case 直接 forced_opening=iac_2base，不需要 LLM）。
_ZERG_SWITCH_MOCK: dict[str, Any] = {
    "interpretation_zh": "切换到巢虫腐化后期运营",
    "confidence": 0.95,
    "directives": [
        {
            "type": "strategy_set",
            "payload": {"stage": "lategame", "strategy_id": "persistent_brood_corruptor"},
        }
    ],
}


def _make_game_id(tag: str) -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return f"game_{ts}_{os.getpid()}_{tag}_{uuid.uuid4().hex[:6]}"


def _load_telemetry(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        with contextlib.suppress(Exception):
            out.append(json.loads(line))
    return out


def _max_level_for_family(records: list[dict[str, Any]], family: str) -> int:
    """扫 kind=='upgrade_complete' 记录，返回该 family 出现过的最高等级。

    这是 SC2 引擎 on_upgrade_complete 回调确认的**真实终态**（common_bot.py
    _tel_event_upgrade 写入），不是 bot 自己打的中间 trace。
    0 = 该 family 完全没有 upgrade_complete 记录（未研究 / 被封顶门拦住）。
    """
    best = 0
    for rec in records:
        if rec.get("kind") != "upgrade_complete":
            continue
        upg = str(rec.get("upgrade", ""))
        m = _LEVEL_RE.match(upg)
        if not m:
            continue
        fam, lvl = m.group(1), int(m.group(2))
        if fam == family:
            best = max(best, lvl)
    return best


async def run_one(
    *,
    tag: str,
    my_race: str,
    forced_opening: str,
    cap_family: str | None,
    cap_level: int,
    switch_to_zerg_doctrine: bool,
    seconds: int,
    inject_after: float,
    map_name: str,
    window_x: int = 0,
    window_y: int = 0,
) -> dict[str, Any]:
    """跑一局，返回 {"telemetry_path", "records", "max_level"(若 cap_family 给了)}。"""
    game_id = _make_game_id(tag)

    # 只有虫族 case 需要 mock LLM（注入 strategy_set 切 doctrine）；神族 case
    # 显式 pop 掉，避免跨局残留污染（env 是进程级共享状态，同一父进程连续起
    # 多局时上一局设的必须显式清）。
    if switch_to_zerg_doctrine:
        mock_path = _ROOT / "logs" / f"upgrade_target_mock_llm_{tag}.json"
        mock_path.parent.mkdir(parents=True, exist_ok=True)
        mock_path.write_text(json.dumps(_ZERG_SWITCH_MOCK, ensure_ascii=False), encoding="utf-8")
        os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)
    else:
        os.environ.pop("VIBECRAFT_MOCK_LLM_JSON", None)

    cfg = GameConfig(
        map_name=map_name,
        my_race=my_race,
        opponent_race="Terran",
        opponent_difficulty="Easy",
        realtime=False,
        forced_opening=forced_opening,
        game_id=game_id,
        window_x=window_x,
        window_y=window_y,
        window_width=860,
        window_height=720,
    )
    gp = GameProcess()
    gp.start(cfg)

    seen_playing = asyncio.Event()
    ended = asyncio.Event()

    async def collect() -> None:
        async for msg in gp.raw_events():
            sc2 = msg.get("sc2")
            if str(sc2) == "playing":
                seen_playing.set()
            if str(sc2) in ("crashed", "ended"):
                ended.set()
                return

    ctask = asyncio.create_task(collect())

    async def do_inject() -> None:
        try:
            await asyncio.wait_for(seen_playing.wait(), timeout=180)
        except TimeoutError:
            logger.warning("[%s] 等 playing 超时,跳过注入", tag)
            return
        await asyncio.sleep(inject_after)
        if switch_to_zerg_doctrine:
            logger.info("[%s] INJECT strategy_set -> persistent_brood_corruptor", tag)
            gp.send_command(
                {
                    "type": "command",
                    "text": "切换到巢虫腐化后期运营",
                    "client_id": "selftest",
                    "issued_at": time.time(),
                }
            )
            # 给 mock LLM(近乎 0 延迟) + director 一点时间处理完 strategy_set,
            # 避免跟下面的 upgrade_target 同帧竞态。
            await asyncio.sleep(1.5)
        if cap_family is not None:
            logger.info(
                "[%s] INJECT macro_action upgrade_target family=%s level=%d",
                tag,
                cap_family,
                cap_level,
            )
            gp.send_command(
                {
                    "type": "macro_action",
                    "dim": "upgrade_target",
                    "value": {"family": cap_family, "level": cap_level},
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

    telemetry_path = _ROOT / "logs" / game_id / "telemetry.jsonl"
    records = _load_telemetry(telemetry_path)
    result: dict[str, Any] = {"telemetry_path": telemetry_path, "records": records, "tag": tag}
    return result


async def run_case(
    *,
    case_name: str,
    family: str,
    my_race: str,
    forced_opening: str,
    switch_to_zerg_doctrine: bool,
    seconds: int,
    inject_after: float,
    map_name: str,
    base_x: int,
    base_y: int,
) -> dict[str, Any]:
    """baseline + capped 并发跑一对，返回两边的 family 终态等级。"""
    baseline_task = run_one(
        tag=f"{case_name}_baseline",
        my_race=my_race,
        forced_opening=forced_opening,
        cap_family=None,
        cap_level=0,
        switch_to_zerg_doctrine=switch_to_zerg_doctrine,
        seconds=seconds,
        inject_after=inject_after,
        map_name=map_name,
        window_x=base_x,
        window_y=base_y,
    )
    capped_task = run_one(
        tag=f"{case_name}_capped",
        my_race=my_race,
        forced_opening=forced_opening,
        cap_family=family,
        cap_level=0,
        switch_to_zerg_doctrine=switch_to_zerg_doctrine,
        seconds=seconds,
        inject_after=inject_after,
        map_name=map_name,
        window_x=base_x + 900,
        window_y=base_y,
    )
    baseline, capped = await asyncio.gather(baseline_task, capped_task)
    baseline_level = _max_level_for_family(baseline["records"], family)
    capped_level = _max_level_for_family(capped["records"], family)
    return {
        "case_name": case_name,
        "family": family,
        "baseline_level": baseline_level,
        "capped_level": capped_level,
        "baseline_telemetry": str(baseline["telemetry_path"]),
        "capped_telemetry": str(capped["telemetry_path"]),
        "baseline_upgrade_events": [
            r for r in baseline["records"] if r.get("kind") == "upgrade_complete"
        ],
        "capped_upgrade_events": [
            r for r in capped["records"] if r.get("kind") == "upgrade_complete"
        ],
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--seconds", type=int, default=200, help="每局 wall-clock 秒(non-realtime fast)"
    )
    ap.add_argument("--inject-after", type=float, default=5.0)
    ap.add_argument("--map", default="DaybreakLE")
    ap.add_argument("--repeat", type=int, default=2, help="重复整套 case 跑几轮(防方差)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    all_fails: list[str] = []
    for rep in range(1, args.repeat + 1):
        print(f"\n{'#' * 70}\n# 第 {rep}/{args.repeat} 轮\n{'#' * 70}")

        case_a_task = run_case(
            case_name=f"caseA_r{rep}",
            family="PROTOSSGROUNDWEAPONS",
            my_race="Protoss",
            forced_opening="iac_2base",
            switch_to_zerg_doctrine=False,
            seconds=args.seconds,
            inject_after=args.inject_after,
            map_name=args.map,
            base_x=0,
            base_y=0,
        )
        case_c_task = run_case(
            case_name=f"caseC_r{rep}",
            family="ZERGFLYERWEAPONS",
            my_race="Zerg",
            forced_opening="12pool",
            switch_to_zerg_doctrine=True,
            seconds=args.seconds,
            inject_after=args.inject_after,
            map_name=args.map,
            base_x=0,
            base_y=780,
        )
        result_a, result_c = await asyncio.gather(case_a_task, case_c_task)

        for r in (result_a, result_c):
            print(f"\n--- case {r['case_name']} (family={r['family']}) ---")
            print(
                f"  baseline 终态等级 = {r['baseline_level']}  (events={r['baseline_upgrade_events']})"
            )
            print(
                f"  capped(target=0) 终态等级 = {r['capped_level']}  (events={r['capped_upgrade_events']})"
            )
            print(f"  baseline telemetry: {r['baseline_telemetry']}")
            print(f"  capped   telemetry: {r['capped_telemetry']}")

            if r["baseline_level"] < 1:
                all_fails.append(
                    f"[r{rep}][{r['case_name']}] baseline 终态 {r['family']} 等级={r['baseline_level']},"
                    f" 期望>0(该 build 本该会研究它,否则无对照意义 —— 换 build 或延长 --seconds)"
                )
            if r["capped_level"] != 0:
                all_fails.append(
                    f"[r{rep}][{r['case_name']}] capped(target=0) 终态 {r['family']} 等级="
                    f"{r['capped_level']},期望恒 0(封顶门在真局未生效!)"
                )

    print("\n" + "=" * 70)
    if all_fails:
        print("结果: FAIL")
        for f in all_fails:
            print("  - " + f)
        return 1
    print(f"结果: PASS（{args.repeat} 轮全过，baseline>0 且 capped==0，per-family 断言）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
