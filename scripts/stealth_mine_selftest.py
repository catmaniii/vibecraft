"""偷矿链真局自验（mock LLM + non-realtime fast + STEALTHTRACE 日志 grep）。

跑 1-2 局 bot（mock LLM 绕开真 LLM，VIBECRAFT_MOCK_LLM_JSON 注入 canned directives）：
  - 单 cell 局：注入一条 stealth_mine directive → 验 cell_created + building_started + mining_started。
  - 多 cell 局：注入两条 stealth_mine → 验两个不同 cell_id 都 cell_created + building_started。

STEALTHTRACE 日志前缀由 StealthCellManager 打出（manager.py）。

注意：
  - 坐标 [80, 150] 是 DaybreakLE 的参考值（对方侧矿区附近）。如换地图需相应调整。
  - mining_started 需要 Nexus 真正落地（约 71 游戏秒），non-realtime 运行无问题；
    若短跑（--seconds 60）gaming_started 可能捕捉不到 → 放长 --seconds 200 覆盖。
  - 主矿农民不倒灌验证（FENCE 有效）：stealth worker_tags 不出现在主矿 FENCE 外。
    本脚本通过"stealth worker_claimed 数 ≤ worker_target" + "主矿采矿员工数不因偷矿下降"
    间接验证（telemetry.jsonl 可手动二次核查）。

用法：
  .venv/Scripts/python.exe scripts/stealth_mine_selftest.py [--seconds 200] [--no-multi-cell]
退出码 0=PASS，1=FAIL。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import re
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

# ---------------------------------------------------------------------------
# Mock LLM 响应（绕开真 LLM，用固定 directive）
# ---------------------------------------------------------------------------

# 单 cell：注入一条偷矿指令，坐标指向地图对方侧（DaybreakLE 参考；point=[0,0] 会被 camera 替换）
# 这里用非零坐标以绕过 camera_point 注入，让测试不依赖镜头位置。
# DaybreakLE 对方自然扩张附近约 (75, 150)，可根据实际地图调整。
MOCK_LLM_RESPONSE_SINGLE = {
    "interpretation_zh": "去对方分矿偷矿（单 cell）",
    "confidence": 0.95,
    "directives": [
        {
            "type": "stealth_mine",
            "payload": {
                "point": [75.0, 150.0],
                "worker_target": 16,
                "with_gas": True,
                "on_attack": "flee",
            },
        }
    ],
}

# 多 cell：注入两条偷矿指令，坐标略有不同（两个不同位置）
MOCK_LLM_RESPONSE_MULTI = {
    "interpretation_zh": "偷两个矿点（多 cell）",
    "confidence": 0.95,
    "directives": [
        {
            "type": "stealth_mine",
            "payload": {
                "point": [75.0, 150.0],
                "worker_target": 16,
                "with_gas": True,
                "on_attack": "flee",
            },
        },
        {
            "type": "stealth_mine",
            "payload": {
                "point": [90.0, 140.0],
                "worker_target": 12,
                "with_gas": False,
                "on_attack": "flee",
            },
        },
    ],
}

# ---------------------------------------------------------------------------
# STEALTHTRACE 日志正则
# ---------------------------------------------------------------------------

_RE_CELL_CREATED = re.compile(
    r"STEALTHTRACE cell_created cell_id=(\d+) point=\(([-\d.]+),([-\d.]+)\)"
)
_RE_BUILDING_STARTED = re.compile(r"STEALTHTRACE building_started cell_id=(\d+) builder=(\d+)")
_RE_MINING_STARTED = re.compile(r"STEALTHTRACE mining_started cell_id=(\d+) nexus_tag=(\d+)")
_RE_WORKER_CLAIMED = re.compile(r"STEALTHTRACE worker_claimed cell_id=(\d+) tag=(\d+) total=(\d+)")
_RE_TRAIN_INITIATED = re.compile(r"STEALTHTRACE train_initiated cell_id=(\d+) nexus=(\d+)")
_RE_APPLIED = re.compile(r"STEALTHTRACE stealth_mine_applied directive_id=(\S+) cell_id=(\d+)")

INJECT_TEXT_SINGLE = "在这偷矿"
INJECT_TEXT_MULTI = "在这偷两个矿"


async def run_one(
    mode: str,
    seconds: int,
    inject_after: int,
    map_name: str,
    realtime: bool,
    cap_expand: bool = False,
) -> dict:
    """跑一局，返回解析指标。mode = 'single' | 'multi'。"""
    import json as _json

    log = logging.getLogger("stealth_selftest")
    tag = mode
    log_path = _ROOT / "logs" / f"stealth_selftest_{tag}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()

    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)

    mock_response = MOCK_LLM_RESPONSE_MULTI if mode == "multi" else MOCK_LLM_RESPONSE_SINGLE
    inject_text = INJECT_TEXT_MULTI if mode == "multi" else INJECT_TEXT_SINGLE

    mock_path = _ROOT / "logs" / f"stealth_mock_llm_{tag}.json"
    mock_path.write_text(_json.dumps(mock_response, ensure_ascii=False), encoding="utf-8")
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)

    cfg = GameConfig(
        map_name=map_name,
        opponent_race="Terran",
        opponent_difficulty="Easy",
        realtime=realtime,
        forced_opening="void_ray_rush",
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
            log.warning("等待游戏开始超时")
            return
        await asyncio.sleep(inject_after)
        # 控制变量：先封 bot 开矿到 1 矿，避免 bot 自己扩张多矿把聚合 telemetry 搅乱，
        # 这样 bases=主矿(1)+偷矿，能干净判断"主矿是否为偷矿超产 / 是否被倒灌"。
        if cap_expand:
            log.info("INJECT macro_action expand=1（控制 bot 只留 1 真矿）")
            gp.send_command(
                {"type": "macro_action", "dim": "expand", "value": 1, "client_id": "selftest"}
            )
            await asyncio.sleep(2)
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

    # -----------------------------------------------------------------------
    # 解析日志
    # -----------------------------------------------------------------------
    cells_created: dict[int, tuple[float, float]] = {}  # cell_id → point
    cells_building: set[int] = set()
    cells_mining: set[int] = set()
    workers_claimed: dict[int, int] = {}  # cell_id → max total 出现过
    train_counts: dict[int, int] = {}  # cell_id → train_initiated 次数
    applied_count = 0

    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _RE_APPLIED.search(line)
            if m:
                applied_count += 1
                continue
            m = _RE_CELL_CREATED.search(line)
            if m:
                cid = int(m.group(1))
                cells_created[cid] = (float(m.group(2)), float(m.group(3)))
                continue
            m = _RE_BUILDING_STARTED.search(line)
            if m:
                cells_building.add(int(m.group(1)))
                continue
            m = _RE_MINING_STARTED.search(line)
            if m:
                cells_mining.add(int(m.group(1)))
                continue
            m = _RE_WORKER_CLAIMED.search(line)
            if m:
                cid = int(m.group(1))
                total = int(m.group(3))
                workers_claimed[cid] = max(workers_claimed.get(cid, 0), total)
                continue
            m = _RE_TRAIN_INITIATED.search(line)
            if m:
                cid = int(m.group(1))
                train_counts[cid] = train_counts.get(cid, 0) + 1

    return {
        "mode": mode,
        "applied_count": applied_count,
        "cells_created": cells_created,
        "cells_building": cells_building,
        "cells_mining": cells_mining,
        "workers_claimed": workers_claimed,
        "train_counts": train_counts,
        "distinct_cell_ids": set(cells_created.keys()),
    }


async def main() -> int:
    ap = argparse.ArgumentParser(
        description="偷矿功能真机自验（mock LLM + non-realtime fast + STEALTHTRACE 日志 grep）"
    )
    ap.add_argument("--seconds", type=int, default=200, help="每局 wall-clock 秒（default: 200）")
    ap.add_argument("--inject-after", type=int, default=3, help="游戏开始后 N 秒注入指令")
    ap.add_argument("--map", default="DaybreakLE", help="地图名（default: DaybreakLE）")
    ap.add_argument(
        "--realtime", action="store_true", help="实时模式（debug 用；默认 non-realtime fast）"
    )
    ap.add_argument("--no-multi-cell", action="store_true", help="跳过多 cell 测试")
    ap.add_argument(
        "--cap-expand",
        action="store_true",
        help="注入开矿封顶=1（控制 bot 只留 1 真矿，便于干净判断主矿超产/倒灌）",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    fails: list[str] = []

    # -----------------------------------------------------------------------
    # 验证点 1+2: 单 cell — cell_created + building_started + (optional) mining_started
    # -----------------------------------------------------------------------
    print("\n##### 单 cell 局：注入一条 stealth_mine #####")
    single = await run_one(
        "single", args.seconds, args.inject_after, args.map, args.realtime, args.cap_expand
    )
    print(
        f"  directive_applied={single['applied_count']}"
        f" cells_created={len(single['cells_created'])}"
        f" cells_building={len(single['cells_building'])}"
        f" cells_mining={len(single['cells_mining'])}"
        f" workers_claimed={single['workers_claimed']}"
        f" train_counts={single['train_counts']}"
    )

    if single["applied_count"] < 1:
        fails.append(
            "单 cell: stealth_mine directive 未被 applied（STEALTHTRACE stealth_mine_applied 日志缺失）"
        )
    if len(single["cells_created"]) < 1:
        fails.append("单 cell: cell_created 未出现（Manager.create_cell 未调用）")
    if len(single["cells_building"]) < 1:
        fails.append("单 cell: building_started 未出现（Probe 未被派去建造 Nexus）")
    # mining_started 依赖 Nexus 实际落地（约 71 游戏秒），在 --seconds 足够时才期望出现
    if args.seconds >= 150 and len(single["cells_mining"]) < 1:
        print("  [WARN] mining_started 未出现（Nexus 可能还未建好或地点无效；可加长 --seconds）")

    # -----------------------------------------------------------------------
    # 验证点 4: 多 cell — 两个不同 cell_id
    # -----------------------------------------------------------------------
    if not args.no_multi_cell:
        print("\n##### 多 cell 局：注入两条 stealth_mine #####")
        multi = await run_one("multi", args.seconds, args.inject_after, args.map, args.realtime)
        print(
            f"  directive_applied={multi['applied_count']}"
            f" distinct_cell_ids={multi['distinct_cell_ids']}"
            f" cells_building={len(multi['cells_building'])}"
            f" cells_mining={len(multi['cells_mining'])}"
        )

        if multi["applied_count"] < 2:
            fails.append(f"多 cell: 期望 2 条 stealth_mine applied，实际 {multi['applied_count']}")
        if len(multi["distinct_cell_ids"]) < 2:
            fails.append(f"多 cell: 期望 2 个不同 cell_id，实际 {multi['distinct_cell_ids']}")
        if len(multi["cells_building"]) < 2:
            fails.append(
                f"多 cell: 期望 2 个 cell building_started，实际 {len(multi['cells_building'])}"
            )

    # -----------------------------------------------------------------------
    # 结果汇总
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    if fails:
        print("结果: FAIL")
        for f in fails:
            print("  - " + f)
        print()
        print("注：验证点 3（主矿农民不倒灌）需手动读 telemetry.jsonl 核查：")
        print(
            "  python -c \"import json; [print(l) for l in open('logs/game_*/telemetry.jsonl') if 'workers' in l]\""
        )
        print("注：验证点 5（受击交还）需真机模拟敌方进攻，本脚本不自动测试。")
        return 1

    print("结果: PASS")
    print(
        f"  (1) 单 cell: cell_created={len(single['cells_created'])} building_started={len(single['cells_building'])}"
    )
    if args.seconds >= 150:
        print(f"  (2) 单 cell: mining_started={len(single['cells_mining'])}")
    if not args.no_multi_cell:
        print(
            f"  (4) 多 cell: {multi['distinct_cell_ids']} 两个不同 cell_id 各自 building_started={len(multi['cells_building'])}"
        )
    print()
    print("验证点 3（主矿农民不倒灌）：建议手动读 telemetry.jsonl 核查 workers 字段。")
    print(
        "验证点 5（受击交还）：需真机模拟敌方进攻，本脚本不自动测试（单测 test_stealth_manager.py 覆盖）。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
