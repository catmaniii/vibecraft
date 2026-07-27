"""坑道虫突袭（Nydus Raid）真局自验：真局 vs 真对手，验 NydusRaidAct 投送管线跑通。

背景：`nydus.py` 原来"坑道虫建出来没人用、army 走正面攻"，`NydusRaidAct`
（`nydus_raid_act.py`）补上真正的"投送"——STAGE(集结待装) → TRANSIT(坑道内)
→ STRIKE(钻出打击) 状态机。本脚本验管线**真的跑通**（不只是单测/mock 内部自洽）：

  - 偷袭必须有真对手才复现（CLAUDE.md 环境纪律：sandbox 观察不到 STRIKE 打谁）。
  - 全自主（设计评审处置 #8：本 build 走"纯 plan act 自主"，不需要玩家指令/mock LLM
    注入——不像 salvage/addon selftest 那样需要 mock LLM，直接起局观察即可）。
  - non-realtime(fast)：本轮没有 mock LLM 注入，不受"mock LLM 需 realtime"限制；
    任务约定 realtime=False 先把管线跑通，后续多轮迭代如发现 fast 掩盖时序问题
    （CLAUDE.md 2026-07-04 BC 教训："反应式微操非实时自测不作数"）再切 realtime。

验证 = 读子进程日志里的 `NYDUSRAID` 事件时间线（stage/load/transit/strike/reinforce/
release/bail）+ `NydusRush: BUILD_NYDUSWORM` / `worm position locked` + telemetry
（enemy_workers_harassed 增量、己方 ROACH/ZERGLING/QUEEN supply 曲线、NYDUSCANAL
建筑计数），打印 6 维记分卡骨架（投送时机/完整性/落点/经济杀伤/兵力效率/转型）。
能算的算，算不了的标 TODO——本脚本用于后续多轮迭代，第一轮先要它能出 PASS/FAIL +
清楚的时间线。

**Round 4「声东击西」新增**（2026-07-09 用户拍板，见
Round4 精修）：额外解析
`NydusRush: window check/OPEN/timeout`（`_BuildNydusCanalAtEnemy` 的安全窗口
检测，敌方主基地矿线附近敌方战斗单位数 <= 阈值才判定"窗口开"）+
`NYDUSFEINT squad/poke/retreat`（`FeintSquadAct` 佯攻小队骚扰敌方二矿的
poke-retreat 状态机），加一段 Round4 专属输出：窗口 army_near 时间序列（验证
"佯攻是否真把敌军引离矿线"）+ 虫洞在窗口期是否存活 ≥14s（telemetry NYDUSCANAL
计数时间序列算存活区间）。PASS/FAIL 门相应扩充。

跑法：.venv/Scripts/python.exe scripts/nydus_selftest.py [--seconds 200] [--map DaybreakLE]
退出码：0 = 管线跑通（至少一次 load→transit→strike 全链路观察到）+ Round4 硬门全过，1 = FAIL。
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
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

_RE_STAGE = re.compile(r"NYDUSRAID stage n=(\d+) total_staged=(\d+)")
_RE_REINFORCE = re.compile(r"NYDUSRAID reinforce n=(\d+) total_staged=(\d+)")
_RE_LOAD = re.compile(r"NYDUSRAID load n=(\d+) wave=(\S+)(?: supply=([\d.]+))?")
_RE_TRANSIT = re.compile(r"NYDUSRAID transit tag=(\d+)")
_RE_STRIKE = re.compile(r"NYDUSRAID strike tag=(\d+) tgt=(\S+)")
_RE_RELEASE = re.compile(r"NYDUSRAID release reason=(\S+) n=(\d+)")
_RE_BAIL = re.compile(r"NYDUSRAID bail_transit n=(\d+) reason=(\S+)")
# 2026-07-26 修:这两个串在 2026-07-12 落点重构里改了前缀(NydusRush→NydusLanding,
# "worm position locked"→"worm locked"),脚本没跟着改 → 明明建了虫也报"从未发出"、判 FAIL。
# 这类"会骗人的门"比没有门更糟,发现即修(CLAUDE.md 自验纪律)。
_RE_WORM_LOCK = re.compile(r"NydusLanding: worm locked @ \(([\d.-]+), ([\d.-]+)\)")
_RE_WORM_BUILD = re.compile(r"NydusLanding: BUILD_NYDUSWORM @ \(([\d.-]+), ([\d.-]+)\) via Network")

# ── Round 4「声东击西」：窗口检测 + 佯攻小队事件 ─────────────────────────────
# 2026-07-26 修:同 worm lock/build，这三个串在 2026-07-12 落点重构后已不存在——窗口检测搬进
# NydusLandingPlanner，日志改成 `NYDUSDIAG gate: army_away=... nearby=...`（每 ~4s 一条）+
# 兜底那条单独打。旧串永远匹配不到 → 每局都报"窗口既未打开也未超时"并判 FAIL（假阴性门）。
_RE_WINDOW_CHECK = re.compile(r"NYDUSDIAG gate: army_away=(True|False) nearby=(\d+)")
# 窗口"打开" = 那一帧判定主力不在落点区（army_away=True）
_RE_WINDOW_OPEN = re.compile(r"NYDUSDIAG gate: army_away=(True) nearby=(\d+)")
# 兜底降级 = 主力赖家太久绕过 ④ 硬落
_RE_WINDOW_TIMEOUT = re.compile(r"NYDUSDIAG gate ④ 兜底触发")
_RE_FEINT_SQUAD = re.compile(r"NYDUSFEINT squad n=(\d+) poke=(\d+) retreat=(\d+)")
_RE_FEINT_POKE = re.compile(r"NYDUSFEINT poke tag=(\d+) hp=([\d.]+)")
_RE_FEINT_RETREAT = re.compile(r"NYDUSFEINT retreat tag=(\d+) hp=([\d.]+)")


def _extract_events(raw_log: str) -> dict[str, list[re.Match]]:
    events: dict[str, list[re.Match]] = {
        "stage": [],
        "reinforce": [],
        "load": [],
        "transit": [],
        "strike": [],
        "release": [],
        "bail": [],
        "worm_lock": [],
        "worm_build": [],
        "window_check": [],
        "window_open": [],
        "window_timeout": [],
        "feint_squad": [],
        "feint_poke": [],
        "feint_retreat": [],
    }
    for line in raw_log.splitlines():
        for key, pat in (
            ("stage", _RE_STAGE),
            ("reinforce", _RE_REINFORCE),
            ("load", _RE_LOAD),
            ("transit", _RE_TRANSIT),
            ("strike", _RE_STRIKE),
            ("release", _RE_RELEASE),
            ("bail", _RE_BAIL),
            ("worm_lock", _RE_WORM_LOCK),
            ("worm_build", _RE_WORM_BUILD),
            ("window_check", _RE_WINDOW_CHECK),
            ("window_open", _RE_WINDOW_OPEN),
            ("window_timeout", _RE_WINDOW_TIMEOUT),
            ("feint_squad", _RE_FEINT_SQUAD),
            ("feint_poke", _RE_FEINT_POKE),
            ("feint_retreat", _RE_FEINT_RETREAT),
        ):
            m = pat.search(line)
            if m:
                events[key].append(m)
    return events


def _latest_game_dir() -> Path | None:
    dirs = sorted((_ROOT / "logs").glob("game_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return dirs[0] if dirs else None


def _load_telemetry(game_dir: Path) -> list[dict[str, Any]]:
    path = game_dir / "telemetry.jsonl"
    if not path.exists():
        return []
    recs: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        with contextlib.suppress(_json.JSONDecodeError):
            recs.append(_json.loads(line))
    return recs


async def run(seconds: int, map_name: str, opponent_race: str, opponent_difficulty: str) -> int:
    log = logging.getLogger("nydus_selftest")
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    log_path = _ROOT / "logs" / "nydus_selftest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(log_path)
    # 全自主 build（评审处置 #8），不需要玩家指令 → 不设 mock LLM。
    os.environ.pop("VIBECRAFT_MOCK_LLM_JSON", None)

    cfg = GameConfig(
        map_name=map_name,
        my_race="Zerg",
        opponent_race=opponent_race,
        opponent_difficulty=opponent_difficulty,
        realtime=False,
        forced_opening="nydus",
        game_time_limit_s=1200,  # 20min 上限（release_after_s 硬释放兜底=900s 之内应已见分晓）
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
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(seen_playing.wait(), timeout=180)
    log.info("seen_playing, waiting up to %ds wall-clock", seconds)

    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(ended.wait(), timeout=seconds)
    if not ctask.done():
        ctask.cancel()
    with contextlib.suppress(Exception):
        await gp.stop()

    # ---------- 解析子进程日志 ----------
    raw_log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    ev = _extract_events(raw_log)

    n_stage = sum(int(m.group(1)) for m in ev["stage"])
    n_reinforce = sum(int(m.group(1)) for m in ev["reinforce"])
    n_load = sum(int(m.group(1)) for m in ev["load"])
    wave1_matches = [m for m in ev["load"] if m.group(2) == "1"]
    n_transit = len(ev["transit"])
    n_strike = len({m.group(1) for m in ev["strike"]})  # 去重 tag（每次 retarget 都打一行）
    strike_targets = [m.group(2) for m in ev["strike"]]
    n_worker_strikes = sum(1 for k in strike_targets if k == "worker")
    n_release = len(ev["release"])
    n_bail = len(ev["bail"])

    # ---------- telemetry ----------
    game_dir = _latest_game_dir()
    snaps: list[dict[str, Any]] = []
    if game_dir is not None:
        recs = _load_telemetry(game_dir)
        snaps = [r for r in recs if r.get("kind") == "snapshot"]

    canal_max = 0
    workers_harassed_max = 0
    workers_harassed_first_t: float | None = None
    supply_timeline: list[tuple[float, int]] = []
    enemy_workers_timeline: list[tuple[float, int]] = []
    # Round 4：canal 存活区间（NYDUSCANAL 计数 0→>0 开始、>0→0 结束）算最长存活时长
    canal_intervals: list[tuple[float, float]] = []
    _canal_open_since: float | None = None
    for s in snaps:
        t = s.get("t", 0.0)
        canal_n = s.get("buildings", {}).get("NYDUSCANAL", 0)
        canal_max = max(canal_max, canal_n)
        if canal_n > 0 and _canal_open_since is None:
            _canal_open_since = t
        elif canal_n == 0 and _canal_open_since is not None:
            canal_intervals.append((_canal_open_since, t))
            _canal_open_since = None
        harassed = s.get("enemy", {}).get("enemy_workers_harassed", 0)
        if harassed > 0 and workers_harassed_first_t is None:
            workers_harassed_first_t = t
        workers_harassed_max = max(workers_harassed_max, harassed)
        supply_timeline.append((t, s.get("supply_used", 0)))
        enemy_workers_timeline.append((t, s.get("enemy", {}).get("enemy_workers", 0)))
    if _canal_open_since is not None and snaps:
        # 局结束时仍存活（一直没被拆）→ 用最后一条 snapshot 的 t 收尾
        canal_intervals.append((_canal_open_since, snaps[-1].get("t", _canal_open_since)))
    canal_max_survival_s = max((b - a for a, b in canal_intervals), default=0.0)

    # ---------- 打印时间线 ----------
    worker_share = (n_worker_strikes / len(strike_targets)) if strike_targets else 0.0
    print()
    print(
        f"===== NYDUS RAID SELFTEST：事件时间线 (opponent_difficulty={opponent_difficulty}) ====="
    )
    print(f"  worm position locked : {len(ev['worm_lock'])} 次")
    print(f"  BUILD_NYDUSWORM 发令  : {len(ev['worm_build'])} 次")
    print(f"  NYDUSRAID stage       : n累计={n_stage}")
    print(f"  NYDUSRAID reinforce   : n累计={n_reinforce}")
    print(f"  NYDUSRAID load        : n累计={n_load} (wave=1 出现 {len(wave1_matches)} 次)")
    print(f"  NYDUSRAID transit     : {n_transit} 次（含重复确认）")
    print(
        f"  NYDUSRAID strike      : {n_strike} 个不同 tag 钻出（tgt 分布: worker={n_worker_strikes}/"
        f"{len(strike_targets)} retarget 事件, worker_share={worker_share:.0%}）"
    )
    print(f"  NYDUSRAID release     : {n_release} 次")
    print(f"  NYDUSRAID bail_transit: {n_bail} 次")
    print(f"  telemetry NYDUSCANAL 最大计数 : {canal_max}")
    print(
        f"  telemetry NYDUSCANAL 存活区间 : {canal_intervals}  最长存活={canal_max_survival_s:.1f}s"
    )
    print(
        f"  telemetry enemy_workers_harassed 最大 : {workers_harassed_max}"
        f"  (首次非零 t={workers_harassed_first_t})"
    )
    print()

    # ---------- Round 4「声东击西」：窗口检测 + 佯攻小队 ----------
    # 新串 `NYDUSDIAG gate: army_away=(True|False) nearby=(N)`：group1=主力是否已被引开、
    # group2=落点区附近敌方非农民数(旧串的 army_near)。旧代码按 (army_near, open, elapsed)
    # 三组解，改串后组序变了直接崩(ValueError) —— 一并对齐。
    window_checks = [(int(m.group(2)), m.group(1) == "True") for m in ev["window_check"]]
    window_opened = bool(ev["window_open"])
    window_timed_out = bool(ev["window_timeout"])
    n_feint_poke = len(ev["feint_poke"])
    n_feint_retreat = len(ev["feint_retreat"])
    squad_samples = [(int(m.group(1)), int(m.group(2)), int(m.group(3))) for m in ev["feint_squad"]]
    print("===== Round 4「声东击西」：窗口检测 + 佯攻小队 =====")
    if window_checks:
        army_near_series = [c[0] for c in window_checks]
        print(
            f"  window check          : {len(window_checks)} 次采样，army_near 序列(首→尾)="
            f"{army_near_series}"
        )
        print(
            f"      army_near: 首值={army_near_series[0]} 最小值={min(army_near_series)} "
            f"末值={army_near_series[-1]}"
        )
    else:
        print("  window check          : 未观察到（NydusNetwork 可能从未建成/未到检查阶段）")
    print(
        f"  window OPEN 事件       : {len(ev['window_open'])} 次（窗口是否曾打开={window_opened}）"
    )
    print(
        f"  window timeout 兜底    : {len(ev['window_timeout'])} 次（是否触发降级={window_timed_out}）"
    )
    if squad_samples:
        print(
            f"  NYDUSFEINT squad 采样  : {len(squad_samples)} 次，"
            f"末次 n={squad_samples[-1][0]} poke={squad_samples[-1][1]} retreat={squad_samples[-1][2]}"
        )
    else:
        print("  NYDUSFEINT squad 采样  : 未观察到（佯攻小队可能从未招募到小狗）")
    print(
        f"  NYDUSFEINT poke/retreat事件: poke={n_feint_poke} retreat={n_feint_retreat}（>=1 次 retreat 说明佯攻真的边打边撤、没有一波送光）"
    )
    print()

    # ---------- 6 维记分卡骨架 ----------
    print("===== 6 维记分卡骨架（能算的算，算不了的标 TODO，多轮迭代用）=====")

    # ① 投送时机
    if wave1_matches:
        supply_str = wave1_matches[0].group(3)
        print(
            f"  ① 投送时机     : 首波 load 触发时 supply={supply_str}（游戏内具体 t 需对照日志时间戳，TODO：脚本加时间戳解析）"
        )
    else:
        print("  ① 投送时机     : 未观察到 wave=1 事件（TODO：本局未触发首波，见下方 FAIL 原因）")

    # ② 投送完整性（不 trickle）
    if n_load and n_transit:
        print(
            f"  ② 投送完整性   : load 累计 n={n_load}，transit 确认 {n_transit} 次（粗略比：{n_transit}/{n_load}，TODO：精确核对同批次内 80% 阈值）"
        )
    else:
        print("  ② 投送完整性   : TODO（load/transit 事件不足，无法评估）")

    # ③ 落点质量（Round 4：存活时长已用 telemetry NYDUSCANAL 计数区间精确算出）
    if ev["worm_lock"]:
        lx, ly = ev["worm_lock"][0].group(1), ev["worm_lock"][0].group(2)
        print(
            f"  ③ 落点质量     : 落点锁定 @ ({lx},{ly})；NYDUSCANAL 最大计数={canal_max}，"
            f"最长存活={canal_max_survival_s:.1f}s（Round4 硬门：需 ≥14s，钻出期活下来）"
        )
    else:
        print("  ③ 落点质量     : TODO（未观察到 worm position locked 日志）")

    # ④ 经济杀伤（终态铁证）
    print(
        f"  ④ 经济杀伤     : enemy_workers_harassed 最大={workers_harassed_max}"
        f"（>0 即证明真打到过农民；精确 Δ 前后对比 TODO）"
    )

    # ⑤ 兵力效率
    print("  ⑤ 兵力效率     : TODO（需要损失 supply vs 摧毁敌方价值，本轮未实现精确核算）")

    # ⑥ 反应/转型
    if n_release or n_bail:
        print(f"  ⑥ 反应/转型    : release={n_release} bail={n_bail} 次（观察到兜底路径被触发）")
    else:
        print(
            f"  ⑥ 反应/转型    : reinforce 累计 n={n_reinforce}（无 release/bail，供应链平稳；转型判定 TODO：需比对 doctrine 切换日志）"
        )
    if supply_timeline:
        last_t, last_supply = supply_timeline[-1]
        print(
            f"       supply 曲线末点 t={last_t:.0f} supply_used={last_supply}（后劲 TODO：需比对 opening_completed 事件）"
        )
    print()

    # ---------- PASS/FAIL：管线打通 + Round4 声东击西硬门 ----------
    fails: list[str] = []
    if not ev["worm_lock"] and not ev["worm_build"]:
        fails.append("从未尝试建造/锁定坑道虫落点（BUILD_NYDUSWORM 从未发出）")
    if canal_max < 1:
        fails.append("telemetry 里 NYDUSCANAL 最大计数=0，虫洞从未真正建成")
    if not wave1_matches:
        fails.append("NYDUSRAID load wave=1 从未触发（STAGE 攒兵或 worm ready 门未满足）")
    if n_transit == 0:
        fails.append(
            "NYDUSRAID transit 从未确认（SMART 装载后从未在 passengers_tags 里出现，装载可能未真正生效）"
        )
    if n_strike == 0:
        fails.append("NYDUSRAID strike 从未触发（没有单位真正钻出敌方家）")

    # Round 4 硬门（声东击西）：佯攻真引开 + 虫洞窗口期存活 + strike 屠农民
    if not squad_samples:
        fails.append("Round4: 佯攻小队从未招募到小狗（NYDUSFEINT squad 从未采样）")
    if not window_opened and not window_timed_out:
        fails.append(
            "Round4: 窗口既未打开也未超时降级（NydusNetwork 可能从未建成，或游戏提前结束）"
        )
    if not window_opened and window_timed_out:
        fails.append("Round4: 佯攻未能把敌军引离主基地矿线，窗口从未打开——已降级到矿点背面兜底落点")
    if canal_max_survival_s < 14.0 and canal_max >= 1:
        fails.append(
            f"Round4: 虫洞最长存活仅 {canal_max_survival_s:.1f}s（<14s 钻出期门槛），"
            "落点在窗口期仍未能撑过钻出无敌期"
        )
    if n_worker_strikes == 0:
        fails.append("Round4: strike 从未打过 tgt=worker（没有屠到农民）")

    if fails:
        print("结果: FAIL")
        for f in fails:
            print(f"  - {f}")
        return 1

    print(
        "结果: PASS（管线跑通 + Round4 声东击西硬门全过：佯攻引开窗口开 → 虫洞窗口期存活"
        f" {canal_max_survival_s:.1f}s → strike 屠农民）"
    )
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser(description="坑道虫突袭真局自验（NydusRaidAct 管线）")
    ap.add_argument("--seconds", type=int, default=200, help="总 wall-clock 超时(s)")
    ap.add_argument("--map", default="DaybreakLE")
    ap.add_argument("--opponent-race", default="Terran", help="对手种族（原 build 参考 ZvT）")
    ap.add_argument(
        "--opponent",
        default="VeryEasy",
        help="对手难度（CamelCase，同 GameConfig.opponent_difficulty，如 VeryEasy/VeryHard）",
    )
    args = ap.parse_args()
    return await run(args.seconds, args.map, args.opponent_race, args.opponent)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
