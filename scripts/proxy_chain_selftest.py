"""代理建造野外链真局自验(A/B 对照,mock LLM + non-realtime fast)。

跑两局 bot(mock LLM 绕开真 LLM,VIBECRAFT_MOCK_LLM_JSON 注入 canned directives):
  - 基线局:不下指令。
  - 测试局:注入"派农民去野外(forward,参考 4bg 野水晶选点)修水晶,然后修两个 VS"。
抓子进程日志的 PROXYTRACE / PROXYRESERVE_BLOCK 判定:
  (a) 野外建造:水晶 settle + 2 个不同 s_tag 的 VS 都在野外 settle + 链绑定(chain != None)。
  (b) 家里让路(问题3):测试局有"家里让路"事件(锁钱在钱紧时挡住家里出 VS),
      基线局为 0(天然 A/B 对照,证明是指令导致让路)。

用法:
  .venv/Scripts/python.exe scripts/proxy_chain_selftest.py [--seconds 150] [--no-baseline]
退出码 0=PASS,1=FAIL。
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

INJECT_TEXT = "派一个农民去我方分矿修一个水晶,然后修两个VS"

# Mock LLM 响应:固定返回这条代理建造链(natural 代理点),绕开真 LLM(无延迟,可 fast 跑)。
# 此步不测 LLM 识别,只测执行链。结构 = 真 LLM 输出过的同款。
MOCK_LLM_RESPONSE = {
    "interpretation_zh": "派农民去分矿修水晶,然后在同点修两个 VS",
    "confidence": 0.95,
    "directives": [
        {
            "type": "unit_claim",
            "payload": {
                "selector": {"unit_type": "Probe", "count": 1, "chain_id": "proxy_natural"},
                "task": {
                    "primary_action": {
                        "verb": "standby",
                        "target": {"kind": "named_spot", "named_spot": "forward"},
                    }
                },
                "persistent": True,
            },
        },
        {
            "type": "build_at",
            "payload": {
                "structure_type": "Pylon",
                "by_probe": True,
                "chain_id": "proxy_natural",
                "named_spot": "forward",
                "activate_when": {"kind": "unit_arrived", "area": "forward"},
            },
        },
        {
            "type": "build_at",
            "payload": {
                "structure_type": "Stargate",
                "by_probe": True,
                "named_spot": "forward",
                "activate_when": {"kind": "chain_structure_ready", "chain_id": "proxy_natural"},
            },
        },
        {
            "type": "build_at",
            "payload": {
                "structure_type": "Stargate",
                "by_probe": True,
                "named_spot": "forward",
                "activate_when": {"kind": "chain_structure_ready", "chain_id": "proxy_natural"},
            },
        },
    ],
}

_RE_ISSUED = re.compile(
    r"PROXYTRACE build_issued tag=(\d+) type=(\w+) near=\(([-\d.]+),([-\d.]+)\) place=\(([-\d.]+),([-\d.]+)\)"
)
_RE_SETTLED = re.compile(r"PROXYTRACE settled did=(\w+) type=(\w+) s_tag=(\d+) chain=(\S+)")
_RE_BLOCK = re.compile(r"PROXYRESERVE_BLOCK type=(\w+) 家里让路")
_RE_ASSIGN = re.compile(
    r"PROXYTRACE assign_spot did=(\w+) type=(\w+) point=\(([-\d.]+),([-\d.]+)\)"
)


async def run_one(
    inject: bool, seconds: int, inject_after: int, map_name: str, realtime: bool
) -> dict:
    """跑一局,返回解析指标。inject=False 是基线(不下指令)。"""
    log = logging.getLogger("proxy_selftest")
    import json as _json

    tag = "inject" if inject else "baseline"
    log_path = _ROOT / "logs" / f"proxy_chain_selftest_{tag}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    mock_path = _ROOT / "logs" / "proxy_mock_llm.json"
    mock_path.write_text(_json.dumps(MOCK_LLM_RESPONSE, ensure_ascii=False), encoding="utf-8")
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
        if not inject:
            return
        try:
            await asyncio.wait_for(seen_playing.wait(), timeout=180)
        except TimeoutError:
            return
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

    settled: list[tuple[str, str, str]] = []
    blocks = 0
    assigned: list[tuple[float, float]] = []  # 水晶建好刷新出的后续建筑坐标
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _RE_SETTLED.search(line)
            if m:
                settled.append((m.group(2), m.group(3), m.group(4)))
                continue
            m = _RE_ASSIGN.search(line)
            if m:
                assigned.append((float(m.group(3)), float(m.group(4))))
                continue
            if _RE_BLOCK.search(line):
                blocks += 1
    vs_distinct = {s[1] for s in settled if s[0].lower() == "stargate"}
    pylon_settled = [s for s in settled if s[0].lower() == "pylon"]
    return {
        "vs_distinct": len(vs_distinct),
        "pylon_settled": len(pylon_settled),
        "chain_bound": all(ch != "None" for _, _, ch in pylon_settled) if pylon_settled else False,
        "home_yield_events": blocks,
        "assigned_spots": len(set(assigned)),  # 刷新出的**不同**坐标数(期望 2)
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=150, help="每局 wall-clock 秒")
    ap.add_argument("--inject-after", type=int, default=3)
    ap.add_argument("--map", default="DaybreakLE")
    ap.add_argument("--realtime", action="store_true")
    ap.add_argument("--no-baseline", action="store_true", help="跳过基线(不下指令)对照局")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    base = None
    if not args.no_baseline:
        print("\n##### 基线局:不下指令 #####")
        base = await run_one(False, args.seconds, args.inject_after, args.map, args.realtime)
        print(f"  基线: 野外VS={base['vs_distinct']} 家里让路事件={base['home_yield_events']}")

    print("\n##### 测试局:下'派农民去野外修水晶+两个VS'指令 #####")
    inj = await run_one(True, args.seconds, args.inject_after, args.map, args.realtime)
    print(
        f"  测试: 野外VS settle(distinct)={inj['vs_distinct']} "
        f"水晶settle={inj['pylon_settled']} 链绑定={inj['chain_bound']} "
        f"刷新坐标数={inj['assigned_spots']} 家里让路事件={inj['home_yield_events']}"
    )

    print("\n" + "=" * 60)
    fails: list[str] = []
    # (a) 野外建造:水晶 + 2 个不同 VS 都在野外 settle、链绑定
    if inj["pylon_settled"] < 1 or not inj["chain_bound"]:
        fails.append("水晶没在野外 settle 或链没绑定")
    if inj["vs_distinct"] < 2:
        fails.append(f"两个 VS 没都建在野外(distinct={inj['vs_distinct']} 期望 2)")
    # (a') 卡片刷新:水晶建好刷新出 2 个不同坐标给两张 VS 卡
    if inj["assigned_spots"] < 2:
        fails.append(f"水晶建好没刷新出 2 个不同坐标(assigned={inj['assigned_spots']} 期望 2)")
    # (b) 家里让路:下指令时出现"家里让路"事件;不下指令时为 0(天然 A/B 对照)
    if inj["home_yield_events"] < 1:
        fails.append("没观察到'家里让路'(锁钱没在钱紧时挡住家里出 VS,问题3 未生效)")
    if base is not None and base["home_yield_events"] != 0:
        fails.append(f"基线(不下指令)不该有让路事件,却有 {base['home_yield_events']} 个")

    if fails:
        print("结果: FAIL")
        for f in fails:
            print("  - " + f)
        return 1
    print("结果: PASS")
    print(f"  (a) 野外建造:水晶 + {inj['vs_distinct']} 个 VS 都建在野外、链绑定")
    print(
        f"  (b) 家里让路:下指令后家里让路 {inj['home_yield_events']} 次"
        + (f",基线 {base['home_yield_events']} 次" if base is not None else "")
        + ""
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
