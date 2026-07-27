"""偷矿饱和真机自验（并行 N 局 + 全军防守持续 + 小窗口平铺）。

目的：验偷矿 cell 在长局里**能爬到饱和**（16 矿 + 6 气）且农民**留在偷矿基地不回主矿**
（修 tag-aware FENCE 回归后）。手段：
  - mock LLM（match 格式）：注入"全军防守"→ tactical_objective(defend, persistent=True) 让
    bot 10+ 分钟不出门（游戏不会早早打完 / 被打死），偷矿 cell 有时间长满；注入"在这偷矿"
    → stealth_mine（on_attack=hold，敌人逛到也不撤，专测饱和）。
  - 并行 N 局（non-realtime fast），**小窗口 2 列网格平铺**，互不遮挡。
  - 每局独立 game_id → logs/<game_id>/telemetry.jsonl，读 stealth_cells 序列：
    峰值 worker_count / nexus_assigned 是否跟住（OUTFLOW=农民被拉走） / DRAIN（主矿倒灌）。
  - 共享 server log grep `from_kind=stealth`（自产农民被误赶回主矿的回归信号，应≈0）。

用法：
  .venv/Scripts/python.exe scripts/stealth_saturation_selftest.py [--games 4] [--seconds 420]
退出码 0=PASS（每局 cell 进 mining 且峰值 worker_count 达标）/ 1=FAIL。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

# --- mock LLM（match 格式：按注入文本含哪个子串返回对应 directive）---
MOCK_LLM = [
    {
        "match": "防守",
        "response": {
            "interpretation_zh": "全军防守（持续姿态）",
            "confidence": 0.95,
            "directives": [
                {"type": "tactical_objective", "payload": {"verb": "defend", "persistent": True}}
            ],
        },
    },
    {
        "match": "偷矿",
        "response": {
            "interpretation_zh": "去角落偷矿（饱和测试，hold，更隐蔽）",
            "confidence": 0.95,
            "directives": [
                {
                    "type": "stealth_mine",
                    "payload": {
                        # 角落更隐蔽（远离 home↔enemy 主对角线）；snap 到最近角落 expansion。
                        # DaybreakLE：home≈(48,28)/enemy≈(127,119)，左上角 (30,140) 离对角线远。
                        "point": [30.0, 140.0],
                        "worker_target": 16,
                        "with_gas": True,
                        "on_attack": "hold",  # 饱和测试：敌人逛到也不撤，专测能不能长满
                    },
                }
            ],
        },
    },
    {
        "match": "进攻",
        "response": {
            "interpretation_zh": "全军进攻（10 分钟后，结束防守拖局）",
            "confidence": 0.95,
            "directives": [
                {
                    "type": "tactical_objective",
                    "payload": {"verb": "attack", "persistent": True, "attack_mode": "all_in"},
                }
            ],
        },
    },
]

INJECT_DEFEND = "全军防守"
INJECT_STEALTH = "在这偷矿"
INJECT_ATTACK = "全军进攻"
_ATTACK_AT_GAME_S = 600.0  # 游戏时间 10 分钟后切全军进攻（别让防守把局拖到 19 分钟）

# 小窗口 2 列网格（互不遮挡）
_WIN_W = 860
_WIN_H = 520


def _make_game_id(i: int, ts: int) -> str:
    return f"game_stealthsat_{i}_{ts}"


async def run_one(
    idx: int,
    game_id: str,
    seconds: int,
    map_name: str,
    games: int,
) -> dict:
    """跑一局：start → 等 playing → 注入 defend + stealth → 等结束/超时 → stop。返回 game_id。"""
    log = logging.getLogger("stealthsat")
    # 小窗口网格自适应：>4 局用 4 列更小窗口铺满桌面，否则 2 列。
    cols = 4 if games > 4 else 2
    win_w, win_h = (460, 420) if cols == 4 else (_WIN_W, _WIN_H)
    col, row = idx % cols, idx // cols
    cfg = GameConfig(
        map_name=map_name,
        my_race="Protoss",
        opponent_race="Terran",
        opponent_difficulty="VeryEasy",  # 防守局：让 bot 撑得久，cell 有时间长满
        realtime=False,
        window_x=col * win_w,
        window_y=row * win_h,
        window_width=win_w,
        window_height=win_h,
        forced_opening="iac_2base",
        game_id=game_id,
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

    async def do_inject() -> None:
        try:
            await asyncio.wait_for(seen_playing.wait(), timeout=180)
        except TimeoutError:
            log.warning("[g%d] 等待游戏开始超时", idx)
            return
        # 先尽早注入偷矿（非实时 fast 下游戏时间推进很快；早注入才能在 bot 开自然分矿的
        # timing 之前让偷矿处于 pending → 验"在建偷矿算进基地数、bot 延后开自己分矿"）。
        await asyncio.sleep(1)
        log.info("[g%d] INJECT 在这偷矿(早)", idx)
        gp.send_command(
            {
                "type": "command",
                "text": INJECT_STEALTH,
                "client_id": f"sat{idx}",
                "issued_at": time.time(),
            }
        )
        await asyncio.sleep(2)
        log.info("[g%d] INJECT 全军防守(持续)", idx)
        gp.send_command(
            {
                "type": "command",
                "text": INJECT_DEFEND,
                "client_id": f"sat{idx}",
                "issued_at": time.time(),
            }
        )

    async def attack_at_10min() -> None:
        """游戏时间到 _ATTACK_AT_GAME_S 时注入全军进攻（读 telemetry 的 game-time t，
        非 wall-clock —— non-realtime 下 wall≠game，且 4 并行速度不一）。"""
        tele = _ROOT / "logs" / game_id / "telemetry.jsonl"
        while not ended.is_set():
            await asyncio.sleep(5)
            try:
                if not tele.exists():
                    continue
                last_t = 0.0
                for line in tele.read_text(encoding="utf-8", errors="replace").splitlines():
                    if '"kind": "snapshot"' in line or '"kind":"snapshot"' in line:
                        with contextlib.suppress(Exception):
                            last_t = json.loads(line).get("t", last_t)
                if last_t >= _ATTACK_AT_GAME_S:
                    log.info("[g%d] game_t=%.0f → INJECT 全军进攻", idx, last_t)
                    gp.send_command(
                        {
                            "type": "command",
                            "text": INJECT_ATTACK,
                            "client_id": f"sat{idx}",
                            "issued_at": time.time(),
                        }
                    )
                    return
            except Exception:
                continue

    ctask = asyncio.create_task(collect())
    itask = asyncio.create_task(do_inject())
    atask = asyncio.create_task(attack_at_10min())
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(ended.wait(), timeout=seconds)
    for t in (itask, ctask, atask):
        if not t.done():
            t.cancel()
    with contextlib.suppress(Exception):
        await gp.stop()
    return {"idx": idx, "game_id": game_id}


def analyze(game_id: str) -> dict:
    """读 logs/<game_id>/telemetry.jsonl 的 stealth_cells 序列。"""
    path = _ROOT / "logs" / game_id / "telemetry.jsonl"
    res = {
        "game_id": game_id,
        "found": path.exists(),
        "reached_mining": False,
        "peak_wc": 0,
        "peak_gas": 0,
        "final_wc": 0,
        "final_na": -1,
        "drain_frames": 0,  # na > mineral_workers（主矿倒灌：多余采矿农民）
        "outflow_frames": 0,  # 0<=na < mineral_workers-2（自产采矿农民被拉走）
        "last_t": 0.0,
        "tail": [],
    }
    if not path.exists():
        return res
    snaps = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("kind") == "snapshot" and r.get("stealth_cells"):
            snaps.append(r)
    if snaps:
        res["last_t"] = snaps[-1]["t"]
    for s in snaps:
        c = s["stealth_cells"][0]
        wc, na = c["worker_count"], c["nexus_assigned"]
        mw = c.get(
            "mineral_workers", wc
        )  # na 是矿口引擎采矿数，只能跟采矿农民比（采气农民在 assim 不在 Nexus）
        if c["state"] == "mining":
            res["reached_mining"] = True
        res["peak_wc"] = max(res["peak_wc"], wc)
        res["peak_gas"] = max(res["peak_gas"], c.get("gas_workers", 0))
        if na > mw:
            res["drain_frames"] += 1
        if 0 <= na < mw - 2:
            res["outflow_frames"] += 1
    if snaps:
        c = snaps[-1]["stealth_cells"][0]
        res["final_wc"] = c["worker_count"]
        res["final_na"] = c["nexus_assigned"]
        # 取最后 5 个去重状态点
        prev = None
        for s in snaps:
            c = s["stealth_cells"][0]
            k = (c["state"], c["worker_count"], c["nexus_assigned"])
            if k != prev:
                res["tail"].append(
                    (round(s["t"]), c["state"], c["worker_count"], c["nexus_assigned"])
                )
                prev = k
        res["tail"] = res["tail"][-6:]
    return res


async def main() -> int:
    ap = argparse.ArgumentParser(description="偷矿饱和真机自验（并行 + 全军防守 + 小窗口）")
    ap.add_argument("--games", type=int, default=4, help="并行局数（default 4）")
    ap.add_argument("--seconds", type=int, default=420, help="每局 wall-clock 秒（default 420）")
    ap.add_argument("--map", default="DaybreakLE")
    ap.add_argument(
        "--peak-threshold",
        type=int,
        default=20,
        help="峰值 worker_count 达标线（饱和=22，default 20）",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    ts = int(time.time())
    # 共享 mock LLM + 共享 server log（并行局都写它；from_kind=stealth 按总数统计）
    mock_path = _ROOT / "logs" / f"stealthsat_mock_{ts}.json"
    mock_path.parent.mkdir(parents=True, exist_ok=True)
    mock_path.write_text(json.dumps(MOCK_LLM, ensure_ascii=False), encoding="utf-8")
    server_log = _ROOT / "logs" / f"stealthsat_server_{ts}.log"
    if server_log.exists():
        server_log.unlink()
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(server_log)

    game_ids = [_make_game_id(i, ts) for i in range(args.games)]
    print(
        f"\n##### 并行 {args.games} 局偷矿饱和测试（全军防守 + 小窗口 {_WIN_W}x{_WIN_H} 网格）#####"
    )
    # 并行跑（start 顺序发起、event loop 并发消费）
    await asyncio.gather(
        *[run_one(i, game_ids[i], args.seconds, args.map, args.games) for i in range(args.games)]
    )

    # --- 分析 ---
    print("\n" + "=" * 72)
    evict_total = 0
    if server_log.exists():
        txt = server_log.read_text(encoding="utf-8", errors="replace")
        evict_total = txt.count("from_kind=stealth")
    analyses = [analyze(gid) for gid in game_ids]
    fails: list[str] = []
    for a in analyses:
        gid = a["game_id"].replace(f"_{ts}", "")
        if not a["found"]:
            fails.append(f"{gid}: telemetry 缺失")
            print(f"  {gid}: NO TELEMETRY")
            continue
        ok = a["reached_mining"] and a["peak_wc"] >= args.peak_threshold
        flag = "PASS" if ok else "FAIL"
        if not ok:
            fails.append(
                f"{gid}: reached_mining={a['reached_mining']} peak_wc={a['peak_wc']}"
                f"(<{args.peak_threshold})"
            )
        print(
            f"  [{flag}] {gid}: mining={a['reached_mining']} peak_wc={a['peak_wc']} "
            f"peak_gas={a['peak_gas']} final wc={a['final_wc']}/na={a['final_na']} "
            f"drain={a['drain_frames']} outflow={a['outflow_frames']} last_t={a['last_t']:.0f}"
        )
        print(f"          tail={a['tail']}")

    print("\n" + "-" * 72)
    print(f"ECONTRACE from_kind=stealth（自产农民被误赶回主矿，应≈0）总计: {evict_total}")
    peaks = [a["peak_wc"] for a in analyses if a["found"]]
    if peaks:
        print(f"峰值 worker_count: min={min(peaks)} max={max(peaks)} 各局={peaks}")

    print("\n" + "=" * 72)
    if fails:
        print("结果: FAIL")
        for f in fails:
            print("  - " + f)
        return 1
    print(
        f"结果: PASS（{args.games} 局 cell 都进 mining 且峰值 worker_count ≥ {args.peak_threshold}）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
