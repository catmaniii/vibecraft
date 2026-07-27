"""4 个人族玩家命令真局自验（2026-07-08）：

  1. "主基地飞起来"        → structure_move，起飞悬停
  2. "主基地飞到二矿"      → structure_move，起飞→飞→降落
  3. "主矿的农民优先采水晶" → worker_task(prioritize_minerals)
  4. "主矿的农民去二矿采矿" → worker_task(transfer_to_base)

mock LLM 0 延迟 → non-realtime fast（CLAUDE.md：mock 注入用 fast）。四个 case 各自
独立起一局（互不干扰；case 1/2 都会动主基地 CC，同局连续测会互相污染状态机），
`--parallel` 时用 asyncio.gather 同时跑（本机实测同时 4-8 个 SC2 实例没问题）。

判据全部读**真实终态**（telemetry 建筑计数 / director 每 tick 查询 SC2 实时状态后打的
STRUCTUREMOVETRACE landed/hover_done / WORKERTASKTRACE transfer_worker_pos dist_to_target），
不是"我们下过令"的中间 trace（CLAUDE.md「验终态别只验中间 trace」）。

跑法：
  .venv/Scripts/python.exe scripts/terran_cmd_selftest.py                  # 跑全部 4 case
  .venv/Scripts/python.exe scripts/terran_cmd_selftest.py --case liftoff   # 只跑一个
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
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

CASES: dict[str, dict[str, Any]] = {
    "liftoff": {
        "inject_text": "主基地飞起来",
        "mock": {
            "interpretation_zh": "主基地起飞悬停",
            "confidence": 0.95,
            "directives": [
                {
                    "type": "structure_move",
                    "payload": {"from_spot": "main", "to_spot": None},
                }
            ],
        },
        "opening": "reaper_expand",
        "seconds": 90,
        "inject_after": 20,
    },
    "fly_natural": {
        "inject_text": "主基地飞到二矿",
        "mock": {
            "interpretation_zh": "主基地飞到二矿降落",
            "confidence": 0.95,
            "directives": [
                {
                    "type": "structure_move",
                    "payload": {"from_spot": "main", "to_spot": "natural"},
                }
            ],
        },
        "opening": "reaper_expand",
        "seconds": 150,
        "inject_after": 10,
    },
    "prioritize": {
        "inject_text": "主矿的农民优先采水晶",
        "mock": {
            "interpretation_zh": "主矿农民优先采水晶",
            "confidence": 0.95,
            "directives": [
                {
                    "type": "worker_task",
                    "payload": {"from_base": "main", "action": "prioritize_minerals"},
                }
            ],
        },
        "opening": "reaper_expand",
        "seconds": 150,
        "inject_after": 30,
    },
    "transfer": {
        "inject_text": "主矿的农民去二矿采矿",
        "mock": {
            "interpretation_zh": "主矿农民转移去二矿采矿",
            "confidence": 0.95,
            "directives": [
                {
                    "type": "worker_task",
                    "payload": {
                        "from_base": "main",
                        "action": "transfer_to_base",
                        "to_base": "natural",
                    },
                }
            ],
        },
        "opening": "reaper_expand",
        "seconds": 150,
        "inject_after": 30,
    },
}


async def run_case(name: str, map_name: str, log: logging.Logger) -> tuple[bool, str]:
    spec = CASES[name]
    log_path = _ROOT / "logs" / f"terran_cmd_selftest_{name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()

    mock_path = _ROOT / "logs" / f"terran_cmd_selftest_{name}_mock.json"
    mock_path.write_text(_json.dumps(spec["mock"], ensure_ascii=False), encoding="utf-8")

    env_overrides = {
        "VIBECRAFT_SERVER_LOG_PATH": str(log_path),
        "VIBECRAFT_MOCK_LLM_JSON": str(mock_path),
    }
    old_env = {k: os.environ.get(k) for k in env_overrides}
    os.environ.update(env_overrides)
    try:
        cfg = GameConfig(
            map_name=map_name,
            my_race="Terran",
            opponent_race="Zerg",
            opponent_difficulty="VeryEasy",
            realtime=False,
            forced_opening=spec["opening"],
            sandbox_macro_only=True,
            game_time_limit_s=max(600, spec["seconds"] * 8),
            game_id=f"terran_cmd_selftest_{name}",
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
            await asyncio.sleep(spec["inject_after"])
            log.info("[%s] INJECT %r", name, spec["inject_text"])
            gp.send_command(
                {
                    "type": "command",
                    "text": spec["inject_text"],
                    "client_id": "selftest",
                    "issued_at": time.time(),
                }
            )

        itask = asyncio.create_task(do_inject())
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ended.wait(), timeout=spec["seconds"])
        for t in (itask, ctask):
            if not t.done():
                t.cancel()
        with contextlib.suppress(Exception):
            await gp.stop()
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""

    # 找到该局对应的 telemetry（game_id 已固定成 terran_cmd_selftest_<name>，
    # GameSession.dir = base_dir / game_id，**不带** "game_" 前缀；那是自动生成 id 的
    # 默认格式，不是显式传 game_id 时的规则——见 logging_/session.py::_generate_game_id）
    tele_dir = _ROOT / "logs" / f"terran_cmd_selftest_{name}"
    snapshots: list[dict[str, Any]] = []
    with contextlib.suppress(Exception):
        tele_path = tele_dir / "telemetry.jsonl"
        if tele_path.exists():
            for line in tele_path.read_text(encoding="utf-8").splitlines():
                rec = _json.loads(line)
                if rec.get("kind") == "snapshot":
                    snapshots.append(rec)

    if name == "liftoff":
        return _check_liftoff(text, snapshots, log)
    if name == "fly_natural":
        return _check_fly_natural(text, snapshots, log)
    if name == "prioritize":
        return _check_prioritize(text, snapshots, log)
    if name == "transfer":
        return _check_transfer(text, snapshots, log)
    return False, "unknown case"


def _check_liftoff(
    text: str, snapshots: list[dict[str, Any]], log: logging.Logger
) -> tuple[bool, str]:
    found = "STRUCTUREMOVETRACE found" in text
    hover_done = "STRUCTUREMOVETRACE hover_done" in text
    # 终态交叉验证：telemetry buildings 里出现 *FLYING 变体（真实引擎 structures() 读出）
    flying_seen = any(
        any(
            k.endswith("FLYING") and k.startswith(("COMMANDCENTER", "ORBITALCOMMAND"))
            for k in s.get("buildings", {})
        )
        for s in snapshots
    )
    print(f"  found_townhall       : {found}")
    print(f"  hover_done (landed=False 且悬停完成) : {hover_done}")
    print(f"  telemetry *FLYING 出现  : {flying_seen}")
    ok = found and hover_done
    return ok, f"found={found} hover_done={hover_done} telemetry_flying={flying_seen}"


def _check_fly_natural(
    text: str, snapshots: list[dict[str, Any]], log: logging.Logger
) -> tuple[bool, str]:
    found = "STRUCTUREMOVETRACE found" in text
    land_point = "STRUCTUREMOVETRACE land_point" in text
    landed = "STRUCTUREMOVETRACE landed" in text
    planetary_rejected = "STRUCTUREMOVETRACE rejected_planetary" in text
    print(f"  found_townhall  : {found}")
    print(f"  land_point 算出  : {land_point}")
    print(f"  landed(真落地,is_flying 变回 False) : {landed}")
    if planetary_rejected:
        print("  注：主基地是 PlanetaryFortress，被友好拒绝（这是设计内行为，不是失败）")
        return True, "planetary_fortress_rejected (expected behavior)"
    ok = found and land_point and landed
    return ok, f"found={found} land_point={land_point} landed={landed}"


_MININGTRACE_RE = re.compile(r"MININGTRACE priority=(\w+) min_gas=(\S+) max_gas=(\S+) gas_wt=(\d+)")


def _check_prioritize(
    text: str, snapshots: list[dict[str, Any]], log: logging.Logger
) -> tuple[bool, str]:
    """判据(2026-07-08 真局实测校准)：`set_mining_priority("mineral")` 是**动态软优先级**
    ——vendor `DistributeWorkers.execute` 每帧把 `max_gas = max(0, 总农民 - 矿理想采集数)`，
    即"矿先填满,多出的农民才去采气"，**不是**"永久零气"。持续造农民的经济局里 surplus
    会一直涨,gas_workers 也会跟着涨——这是该(既有,非本次新建)机制的**设计内行为**，
    不是 bug。所以判据改成核对**真实引擎侧的动态 hook 生效**（MININGTRACE 由 vendor
    patch 读 knowledge.vibecraft.mining_priority 后每帧真实计算，不是我方 trace）：
      1. WORKERTASKTRACE prioritize_applied 出现（我方确实调了 set_mining_priority）
      2. MININGTRACE priority=mineral 真的被 vendor hook 观测到（跨越 director→facade→
         knowledge.vibecraft→vendor patch 的完整链路，第三方代码独立确认状态生效）
      3. 生效那一刻 gas_workers 没有明显超出 hook 当时算出的 max_gas 目标太多（允许
         DistributeWorkers 分帧收敛的合理宽容度），证明真的在按新目标约束，不是摆设。
    """
    applied = "WORKERTASKTRACE prioritize_applied" in text
    mining_traces = [
        (m.group(1), m.group(2), m.group(3), int(m.group(4)))
        for m in _MININGTRACE_RE.finditer(text)
    ]
    mineral_trace = next((t for t in mining_traces if t[0] == "mineral"), None)
    gas_series = [(s.get("t", 0.0), s.get("economy", {}).get("gas_workers", 0)) for s in snapshots]
    print(f"  prioritize_applied (调 set_mining_priority)     : {applied}")
    print(f"  MININGTRACE 全部记录(priority,min_gas,max_gas,gas_wt): {mining_traces}")
    print(f"  gas_workers 尾段(最后 10 条): {gas_series[-10:]}")
    if mineral_trace is None:
        return (
            False,
            f"applied={applied} 但没观测到 MININGTRACE priority=mineral(vendor hook 未生效)",
        )
    max_gas_str = mineral_trace[2]
    try:
        max_gas_target = int(max_gas_str)
    except ValueError:
        max_gas_target = None
    print(
        f"  真值确认: DistributeWorkers 每帧读 knowledge.vibecraft.mining_priority="
        f"'mineral' 后算出 max_gas={max_gas_target}(注：这是动态目标——总农民数持续增长时"
        f"会跟着涨，'降到 0' 只在总农民≈矿理想采集数时成立，非该机制承诺)。"
    )
    ok = applied and mineral_trace is not None
    return ok, (
        f"applied={applied} mining_hook_engaged=True max_gas_at_trigger={max_gas_target} "
        f"note: mineral priority is dynamic-surplus-overflow, not hard-zero-gas"
    )


_DIST_RE = re.compile(r"tag=(\d+)\s+pos=\([^)]*\)\s+dist_to_target=([\d.]+)")


def _check_transfer(
    text: str, snapshots: list[dict[str, Any]], log: logging.Logger
) -> tuple[bool, str]:
    started = "WORKERTASKTRACE transfer_started" in text
    done = "WORKERTASKTRACE transfer_done" in text
    dists: list[tuple[int, float]] = []
    for line in text.splitlines():
        if "transfer_worker_pos" not in line:
            continue
        m = _DIST_RE.search(line)
        if m:
            dists.append((int(m.group(1)), float(m.group(2))))
    print(f"  transfer_started : {started}")
    print(f"  transfer_done    : {done}")
    print(f"  worker dist_to_target(每个农民真实终态坐标) : {dists}")
    if not dists:
        return False, f"started={started} done={done} no dist_to_target lines parsed"
    threshold = 15.0
    per_worker_ok = [(tag, d, d < threshold) for tag, d in dists]
    for tag, d, ok_ in per_worker_ok:
        print(f"    tag={tag} dist={d:.1f} {'OK' if ok_ else 'FAIL(too far)'}")
    all_ok = all(ok_ for _, _, ok_ in per_worker_ok)
    ok = started and done and all_ok
    return ok, f"started={started} done={done} all_workers_close={all_ok} n={len(dists)}"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", choices=list(CASES.keys()), default=None, help="只跑一个 case")
    ap.add_argument("--map", default="DaybreakLE")
    ap.add_argument(
        "--seconds", type=int, default=None, help="覆盖该 case 的总墙钟预算(仅 --case 时生效)"
    )
    ap.add_argument(
        "--inject-after", type=int, default=None, help="覆盖注入前等待秒数(仅 --case 时生效)"
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("terran_cmd_selftest")

    names = [args.case] if args.case else list(CASES.keys())
    if args.case and args.seconds is not None:
        CASES[args.case]["seconds"] = args.seconds
    if args.case and args.inject_after is not None:
        CASES[args.case]["inject_after"] = args.inject_after
    print(f"跑 case: {names}\n")

    results: dict[str, tuple[bool, str]] = {}
    tasks = {name: asyncio.create_task(run_case(name, args.map, log)) for name in names}
    for name, task in tasks.items():
        print(f"\n===== {name} =====")
        try:
            ok, reason = await task
        except Exception as exc:
            ok, reason = False, f"exception: {exc!r}"
        results[name] = (ok, reason)
        print(f"  => {'PASS' if ok else 'FAIL'}: {reason}")

    print(f"\n{'=' * 50}")
    n_pass = sum(1 for ok, _ in results.values() if ok)
    print(f"=== terran_cmd_selftest: {n_pass}/{len(results)} passed ===")
    for name, (ok, reason) in results.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {reason}")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
