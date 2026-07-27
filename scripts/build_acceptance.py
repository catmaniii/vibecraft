"""Build order 验收 runner。

用法:
    uv run python scripts/build_acceptance.py <strategy_id>
        [--opponent veryeasy|easy|medium|hard|veryhard|cheatmoney|...] [--runs N]

流程:spawn non-realtime SC2 跑 N 局 → 每局收 telemetry.jsonl → verifier 判定 →
N 局按 check 多数票聚合 → 出报告。infra-fail(watchdog hang / SC2 崩溃)
每局自动 retry ≤3 次。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.build_acceptance.spec import AcceptanceSpec, load_spec  # noqa: E402
from vibecraft.build_acceptance.verifier import (  # noqa: E402
    Report,
    aggregate_reports,
    verify,
)
from vibecraft.build_efficiency import ScoreConfig, score_snapshots  # noqa: E402
from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

_MAX_INFRA_RETRY = 3
# 验收只需覆盖到出门攻击。non-realtime 下 600 game-sec 通常 wall-clock 几分钟，
# 给 900s wall-clock 作为宽松兜底（sub-process watchdog 120s 无消息也会先触发）。
_GAME_TIME_LIMIT_S = 600
_WALL_CLOCK_LIMIT_S = 900
_OPPONENT_DIFFICULTY = {
    "veryeasy": "VeryEasy",
    "easy": "Easy",
    "medium": "Medium",
    "mediumhard": "MediumHard",
    "hard": "Hard",
    "harder": "Harder",
    "veryhard": "VeryHard",
    "cheatvision": "CheatVision",
    "cheatmoney": "CheatMoney",
    "cheatinsane": "CheatInsane",
}


def _detect_race(strategy_id: str) -> str:
    """从 strategies/<race>/<id>.yaml 所在目录推断己方种族。

    build_acceptance 之前只跑神族，GameConfig.my_race 默认 Protoss；跑人族 /
    虫族剧本必须按 yaml 所在的 strategies/<race>/ 目录设对种族，否则 bot 用错
    种族、Terran/Zerg 剧本的 create_plan 全废。
    """
    for race in ("Protoss", "Terran", "Zerg"):
        if (_ROOT / "strategies" / race.lower() / f"{strategy_id}.yaml").exists():
            return race
    return "Protoss"


def _make_game_id() -> str:
    """生成唯一 game_id：时间戳 + pid + 随机后缀，并发安全。"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    pid = os.getpid()
    suffix = uuid.uuid4().hex[:6]
    return f"game_{ts}_{pid}_{suffix}"


def _detect_desktop_size() -> tuple[int, int]:
    """Windows 桌面工作区尺寸（物理像素，DPI-aware）。失败回退 3440×1440。"""
    try:
        import ctypes

        with contextlib.suppress(Exception):
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PerMonitor V2

        class _RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = _RECT()
        # SPI_GETWORKAREA = 0x0030：桌面减去任务栏
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return int(rect.right - rect.left), int(rect.bottom - rect.top)
    except Exception:
        pass
    return 3440, 1440


async def _run_one_game(
    strategy_id: str,
    opponent: str,
    window_x: int = 0,
    window_y: int = 0,
    window_w: int = 860,
    window_h: int = 720,
) -> Path | None:
    """跑一局，返回 telemetry.jsonl 路径；infra-fail 返回 None。

    阻塞点 A 修复：每局生成唯一 game_id，通过 VIBECRAFT_GAME_ID 环境变量传给
    子进程，子进程的 GameSession 用它作 logs/<game_id>/ 目录名。父进程直接读
    logs/<game_id>/telemetry.jsonl，不再扫 mtime，并发安全。

    infra-fail 判定（return None）：
    - raw_events() 中收到 sc2="crashed"
    - 子进程非零退出（raw_events() 结束后 exitcode != 0 且未 ended）
    - wall-clock 超时（_WALL_CLOCK_LIMIT_S）
    """
    # 生成本局唯一 id；通过 GameConfig 字段传(picklable,跨子进程独立)。
    # 2026-05-23 修并发 race:之前用 os.environ 父进程共享 → 并行多 strategy
    # 后写覆盖前写 → 所有子进程 active_recipe / game_id 都漂到最后一个。
    # 改成 GameConfig.{forced_opening, game_id} 字段,每个子进程 spawn 时拷贝
    # 自己那份,子进程入口写到自己 os.environ,完全消除 race。
    game_id = _make_game_id()

    # 从 spec 读 auto_switch 字段（Task #350: persistent_doctrine 验收用）
    spec_path = _ROOT / "tests" / "build_acceptance" / f"{strategy_id}.yaml"
    _auto_switch_to: str = ""
    _auto_switch_delay_s: float = 10.0
    # 默认 forced_opening = CLI strategy_id;但 auto_switch_to spec 用 spec.strategy_id
    # 作起步 opening (跟 CLI arg = spec 文件名可能不同,e.g.
    # persistent_lurker_hydra.yaml 内 strategy_id=macro_hatch)。
    _initial_opening = strategy_id
    _initial_race = _detect_race(strategy_id)
    if spec_path.exists():
        try:
            _spec = load_spec(spec_path)
            _auto_switch_to = _spec.auto_switch_to or ""
            _auto_switch_delay_s = _spec.auto_switch_delay_s
            # 总是优先用 spec.strategy_id 作 forced_opening(CLI arg 是 spec
            # 文件名,可能跟 strategy library id 不同 e.g. lurker_hydra_diag.yaml
            # 内 strategy_id=persistent_lurker_hydra)。
            _initial_opening = _spec.strategy_id
            _initial_race = _spec.my_race
        except Exception:
            pass

    cfg = GameConfig(
        map_name="DaybreakLE",
        my_race=_initial_race,
        opponent_race=os.environ.get("VIBECRAFT_OPPONENT_RACE", "Random"),
        opponent_difficulty=_OPPONENT_DIFFICULTY[opponent],
        realtime=False,
        # 并发时窗口网格平铺：runner 算好每局的 x/y/尺寸，铺满桌面互不遮挡。
        window_x=window_x,
        window_y=window_y,
        window_width=window_w,
        window_height=window_h,
        forced_opening=_initial_opening,
        game_id=game_id,
        # Task #350: persistent_doctrine 验收 — spec.auto_switch_to 非空时透传
        auto_switch_to=_auto_switch_to,
        auto_switch_delay_s=_auto_switch_delay_s,
    )

    start_wall = time.monotonic()

    logs_dir = _ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    gp = GameProcess()
    gp.start(cfg)

    ended_normally = False
    crashed = False

    try:
        # 用 asyncio.wait_for 包住整个 raw_events() 消费循环，实现 wall-clock 超时
        async def _consume() -> None:
            nonlocal ended_normally, crashed
            async for msg in gp.raw_events():
                sc2 = msg.get("sc2")
                if sc2 == "ended":
                    ended_normally = True
                    return  # 正常结束，跳出循环
                if sc2 == "crashed":
                    crashed = True
                    return  # infra-fail，跳出循环

        await asyncio.wait_for(_consume(), timeout=_WALL_CLOCK_LIMIT_S)
    except TimeoutError:
        # wall-clock 超时：视为 infra-fail，强制 stop
        print(f"[runner] wall-clock {_WALL_CLOCK_LIMIT_S}s 超时，强制 stop（infra-fail）")
        await gp.stop()
        return None
    finally:
        # 确保子进程被清理（ended_normally 时子进程已自然退出，stop() 是幂等的）
        if not ended_normally:
            await gp.stop()

    if not ended_normally:
        # crashed 或其他异常退出
        return None

    # 游戏结束，直接按 game_id 定位 telemetry.jsonl（阻塞点 A 修复核心）
    telemetry_path = logs_dir / game_id / "telemetry.jsonl"
    if not telemetry_path.exists():
        print(f"[runner] 游戏结束但找不到 telemetry.jsonl: {telemetry_path}")
        return None

    elapsed = time.monotonic() - start_wall
    print(f"[runner] 游戏结束（{elapsed:.1f}s wall-clock），telemetry: {telemetry_path}")
    return telemetry_path


async def _run_with_retry(
    strategy_id: str,
    opponent: str,
    label: str = "",
    window_x: int = 0,
    window_y: int = 0,
    window_w: int = 860,
    window_h: int = 720,
) -> Path | None:
    """跑一局 + infra-fail 自动 retry ≤ _MAX_INFRA_RETRY 次；全失败返回 None。

    改为 async，供并发场景用 asyncio.gather 调度（阻塞点 C）。
    label 用于并发时区分日志输出（如 "strategy_id run 2/3"）。
    window_x/y/w/h：并发时该局 SC2 窗口的网格平铺位置与尺寸。
    """
    tag = label or f"{strategy_id} vs {opponent}"
    for attempt in range(1, _MAX_INFRA_RETRY + 1):
        print(f"[runner] {tag} — infra 尝试 {attempt}")
        telemetry_path = await _run_one_game(
            strategy_id,
            opponent,
            window_x=window_x,
            window_y=window_y,
            window_w=window_w,
            window_h=window_h,
        )
        if telemetry_path is not None:
            return telemetry_path
        print(f"[runner] {tag} infra-fail（第 {attempt} 次），retry...")
    return None


def _load_telemetry(path: Path) -> list[dict[str, Any]]:
    """读 telemetry.jsonl → record 列表。"""
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


# ── 执行质量六维度 ────────────────────────────────────────────────────────────

# 属于"科技链 timing"的 check 类型（⑤维度用）
_TECH_CHECK_TYPES = frozenset(
    {"building_started", "building_complete", "upgrade_complete", "building_count"}
)
# 采气建筑（判定"有楼无人采气"）
_GAS_BUILDINGS = frozenset({"ASSIMILATOR", "REFINERY", "EXTRACTOR"})


def _compute_per_run_quality(
    records: list[dict[str, Any]],
    report: Report,
    check_type_map: dict[str, str],
) -> dict[str, Any]:
    """单局六维原始指标（纯内部辅助函数）。

    Args:
        records:        单局 telemetry record 列表。
        report:         该局 verify Report。
        check_type_map: check_id → check.type（由 spec.checks 生成）。
    """
    # ① 农民不闲置 — 早期窗 t < 300
    early_snaps = [r for r in records if r.get("kind") == "snapshot" and float(r.get("t", 0)) < 300]
    # 注意:idle_workers/gas_workers/mineral_workers 嵌在 snapshot 的 `economy` 子字段里,
    # **不是** top-level(top-level 永远取不到 → criterion ① 永远 false-clean,正是要堵的洞)。
    idle_vals = [float((s.get("economy") or {}).get("idle_workers", 0)) for s in early_snaps]
    idle_mean = sum(idle_vals) / len(idle_vals) if idle_vals else 0.0
    idle_peak = max(idle_vals) if idle_vals else 0.0

    gas_no_worker_s = 0.0
    for i in range(len(early_snaps) - 1):
        s = early_snaps[i]
        dt = float(early_snaps[i + 1].get("t", 0)) - float(s.get("t", 0))
        if dt <= 0:
            continue
        bldgs: dict[str, Any] = s.get("buildings", {}) or {}
        has_gas = any(bldgs.get(k, 0) > 0 for k in _GAS_BUILDINGS)
        if has_gas and int((s.get("economy") or {}).get("gas_workers", 0)) == 0:
            gas_no_worker_s += dt

    # ②③④ — score_snapshots 计算余钱积分 / 产能 / 卡人口
    try:
        eff = score_snapshots(records)
        avg_excess_bank = eff.avg_excess_bank
        prod_util = eff.prod_util
        avg_larva_idle = eff.avg_larva_idle
        supply_block_time = eff.supply_block_time
        race = eff.race
    except Exception:
        avg_excess_bank = 0.0
        prod_util = None
        avg_larva_idle = None
        supply_block_time = 0.0
        race = "UNKNOWN"

    # ⑤ 科技链 timing — building/upgrade 类 check 通过率
    tech_results = [
        r
        for r in report.results
        if check_type_map.get(r.check_id) in _TECH_CHECK_TYPES and not r.skipped
    ]
    tech_total = len(tech_results)
    tech_pass = sum(1 for r in tech_results if r.ok)

    # ⑥ 后劲 — 峰值 supply + 后 1/3 是否明显回落
    all_snaps = sorted(
        (r for r in records if r.get("kind") == "snapshot"),
        key=lambda r: float(r.get("t", 0)),
    )
    supply_vals = [float(s.get("supply_used", 0)) for s in all_snaps]
    peak_supply = max(supply_vals) if supply_vals else 0.0
    late_drop = False
    if len(supply_vals) >= 4:
        cutoff = len(supply_vals) * 2 // 3
        late_max = max(supply_vals[cutoff:])
        if peak_supply > 50 and late_max < peak_supply * 0.7:
            late_drop = True

    return {
        "race": race,
        "idle_mean": idle_mean,
        "idle_peak": idle_peak,
        "gas_no_worker_s": gas_no_worker_s,
        "avg_excess_bank": avg_excess_bank,
        "prod_util": prod_util,
        "avg_larva_idle": avg_larva_idle,
        "supply_block_time": supply_block_time,
        "tech_pass": tech_pass,
        "tech_total": tech_total,
        "peak_supply": peak_supply,
        "late_drop": late_drop,
    }


def compute_exec_quality(
    all_records: list[list[dict[str, Any]]],
    all_reports: list[Report],
    spec: AcceptanceSpec,
) -> dict[str, Any]:
    """跨 N 局聚合六维执行质量（纯函数，可单测）。

    Args:
        all_records: 每局 telemetry records 列表（per-run）。
        all_reports: 每局 verify Report（per-run）。
        spec:        验收 spec（用于提取 check_id→type 映射）。

    Returns:
        字段：n_runs / race / dim1..dim6（各含 warn bool）/ worst_dims。
        若无数据则返回含 "error" 键的 dict。
    """
    if not all_records or not all_reports:
        return {"n_runs": 0, "error": "无 records"}

    check_type_map: dict[str, str] = {c.id: c.type for c in spec.checks}

    per_run: list[dict[str, Any]] = []
    for records, report in zip(all_records, all_reports, strict=False):
        with contextlib.suppress(Exception):
            per_run.append(_compute_per_run_quality(records, report, check_type_map))

    if not per_run:
        return {"n_runs": len(all_records), "error": "所有 run 质量计算失败"}

    n = len(per_run)
    race: str = str(per_run[0].get("race", "UNKNOWN"))

    def _avg(key: str) -> float:
        vals = [float(q[key]) for q in per_run if isinstance(q.get(key), (int, float))]
        return sum(vals) / len(vals) if vals else 0.0

    def _avg_opt(key: str) -> float | None:
        vals = [float(q[key]) for q in per_run if q.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    idle_mean = _avg("idle_mean")
    idle_peak = _avg("idle_peak")
    gas_no_worker_s = _avg("gas_no_worker_s")
    avg_excess_bank = _avg("avg_excess_bank")
    prod_util = _avg_opt("prod_util")
    avg_larva_idle = _avg_opt("avg_larva_idle")
    supply_block_time = _avg("supply_block_time")
    tech_pass_total = sum(int(q["tech_pass"]) for q in per_run)
    tech_total_sum = sum(int(q["tech_total"]) for q in per_run)
    peak_supply = _avg("peak_supply")
    late_drop_count = sum(1 for q in per_run if q.get("late_drop"))

    cfg = ScoreConfig()

    warn1 = idle_mean > 1.5
    warn2 = avg_excess_bank > cfg.diag_bank_bad
    if race == "ZERG":
        warn3 = avg_larva_idle is not None and avg_larva_idle > cfg.larva_floor
    else:
        warn3 = prod_util is not None and prod_util < cfg.diag_prod_bad
    warn4 = supply_block_time > cfg.diag_supply_bad
    warn5 = tech_total_sum > 0 and tech_pass_total < tech_total_sum
    warn6 = peak_supply < 150 or late_drop_count * 2 > n

    worst_dims: list[str] = []
    if warn1:
        worst_dims.append("①农民闲置")
    if warn2:
        worst_dims.append("②资源堆积")
    if warn3:
        worst_dims.append("③产能")
    if warn4:
        worst_dims.append("④人口")
    if warn5:
        worst_dims.append("⑤科技链")
    if warn6:
        worst_dims.append("⑥后劲")

    return {
        "n_runs": n,
        "race": race,
        "dim1": {
            "idle_mean": idle_mean,
            "idle_peak": idle_peak,
            "gas_no_worker_s": gas_no_worker_s,
            "warn": warn1,
        },
        "dim2": {"avg_excess_bank": avg_excess_bank, "warn": warn2},
        "dim3": {
            "prod_util": prod_util,
            "avg_larva_idle": avg_larva_idle,
            "warn": warn3,
        },
        "dim4": {"supply_block_time": supply_block_time, "warn": warn4},
        "dim5": {
            "pass_count": tech_pass_total,
            "total": tech_total_sum,
            "warn": warn5,
        },
        "dim6": {
            "peak_supply": peak_supply,
            "late_drop": late_drop_count > 0,
            "warn": warn6,
        },
        "worst_dims": worst_dims,
    }


def format_exec_quality_block(sid: str, quality: dict[str, Any]) -> str:
    """把 compute_exec_quality 返回的 dict 格式化成可读文本块。"""
    if quality.get("error"):
        n = quality.get("n_runs", 0)
        return f"\n=== 执行质量自检（{sid}，{n} 局）: {quality['error']} ===\n"

    n = quality["n_runs"]
    race: str = str(quality.get("race", "UNKNOWN"))
    lines = [f"\n=== 执行质量自检（{sid}，{n} 局，{race}）==="]

    def _mark(warn: bool) -> str:
        # ASCII only —— ⚠️/emoji 会让 Windows GBK 控制台 print() 崩 UnicodeEncodeError
        # (同 addon_selftest 的 ↔ 坑)。用纯 ASCII 标记。
        return "WARN" if warn else " OK "

    # ①
    d1: dict[str, Any] = quality["dim1"]
    gas_note = (
        f"  气矿有楼但无采气 {d1['gas_no_worker_s']:.0f}s" if d1["gas_no_worker_s"] > 5 else ""
    )
    lines.append(
        f"[{_mark(d1['warn'])}] ① 农民闲置:"
        f" 早期idle均值={d1['idle_mean']:.1f} 峰值={d1['idle_peak']:.0f}{gas_note}"
    )

    # ②
    d2: dict[str, Any] = quality["dim2"]
    lines.append(f"[{_mark(d2['warn'])}] ② 资源堆积: avg_excess_bank={d2['avg_excess_bank']:.0f}")

    # ③
    d3: dict[str, Any] = quality["dim3"]
    if race == "ZERG":
        if d3["avg_larva_idle"] is not None:
            prod_val = f"avg_larva_idle={d3['avg_larva_idle']:.1f}"
        else:
            prod_val = "larva数据缺"
    else:
        if d3["prod_util"] is not None:
            prod_val = f"prod_util={d3['prod_util']:.2f}"
        else:
            prod_val = "产能数据缺"
    lines.append(f"[{_mark(d3['warn'])}] ③ 产能利用率: {prod_val}")

    # ④
    d4: dict[str, Any] = quality["dim4"]
    lines.append(f"[{_mark(d4['warn'])}] ④ 卡人口: supply_block={d4['supply_block_time']:.0f}s")

    # ⑤
    d5: dict[str, Any] = quality["dim5"]
    rate_str = f"{d5['pass_count']}/{d5['total']} PASS" if d5["total"] > 0 else "无科技类check"
    lines.append(f"[{_mark(d5['warn'])}] ⑤ 科技链timing: {rate_str}")

    # ⑥
    d6: dict[str, Any] = quality["dim6"]
    drop_note = " 后期回落" if d6["late_drop"] else ""
    lines.append(f"[{_mark(d6['warn'])}] ⑥ 后劲: 峰值supply={d6['peak_supply']:.0f}{drop_note}")

    if quality["worst_dims"]:
        worst: list[str] = quality["worst_dims"]
        lines.append(f"最差维度: {' / '.join(worst)}")
    else:
        lines.append("最差维度: 全部 OK")

    lines.append("=" * 52)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Build order 验收 runner：spawn non-realtime SC2 跑 N 局，多数票出验收报告。\n"
            "支持多个 strategy_id 同时验收，可加 --parallel 并发跑。"
        )
    )
    ap.add_argument(
        "strategy_ids",
        nargs="+",
        help="验收目标剧本 id，如 1g_robo_immortal（可指定多个）",
    )
    ap.add_argument(
        "--opponent",
        default="veryeasy",
        choices=sorted(_OPPONENT_DIFFICULTY),
        help="对手难度（default: veryeasy）",
    )
    ap.add_argument(
        "--runs",
        type=int,
        default=1,
        help="每个剧本跑几局取多数票（default 1；建议 3 消除随机性）",
    )
    ap.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="最多同时并发跑几局（default 1 = 串行；>1 时各局独立 game_id，互不干扰）",
    )
    ap.add_argument(
        "--window-offset",
        type=int,
        default=0,
        help="（已废弃，保留兼容）窗口现按网格平铺，此参数被忽略",
    )
    ap.add_argument(
        "--window-w",
        type=int,
        default=800,
        help="单个 SC2 窗口宽度(default 800,用户偏好:单测固定 800x600)",
    )
    ap.add_argument(
        "--window-h",
        type=int,
        default=600,
        help="单个 SC2 窗口高度(default 600,用户偏好:单测固定 800x600)",
    )
    args = ap.parse_args()

    if args.runs < 1:
        print("ERROR: --runs 必须 >= 1")
        return 2
    if args.parallel < 1:
        print("ERROR: --parallel 必须 >= 1")
        return 2

    # 校验所有 spec 文件存在
    specs: dict[str, Any] = {}
    for sid in args.strategy_ids:
        spec_path = _ROOT / "tests" / "build_acceptance" / f"{sid}.yaml"
        if not spec_path.exists():
            print(f"ERROR: 没有 acceptance spec: {spec_path}")
            return 2
        specs[sid] = load_spec(spec_path)

    # 窗口网格平铺：cols 列 × rows 行铺满桌面，并发的 SC2 窗口互不遮挡。
    # cols 最多 4（左半 2 列 + 右半 2 列）；窗口数 > cols 时往下铺行。
    desktop_w, desktop_h = _detect_desktop_size()
    grid_cols = min(4, args.parallel)
    grid_rows = max(1, (args.parallel + grid_cols - 1) // grid_cols)
    cell_w = desktop_w // grid_cols
    cell_h = desktop_h // grid_rows
    # 窗口尺寸(用户偏好 2026-05-23):NonRealtime 单测固定 800x600。
    # offset 还是按 cell 算(保持平铺位置),窗口本身用固定 800x600。
    win_w = args.window_w
    win_h = args.window_h

    # 展开 job 列表：(strategy_id, run_idx, parallel_slot)
    jobs: list[tuple[str, int, int]] = []
    slot = 0
    for sid in args.strategy_ids:
        for run_idx in range(1, args.runs + 1):
            jobs.append((sid, run_idx, slot % args.parallel))
            slot += 1

    total_jobs = len(jobs)

    # 按 strategy_id 聚合报告 + 原始 records（供六维质量自检用）
    reports_by_strategy: dict[str, list[Report]] = {sid: [] for sid in args.strategy_ids}
    records_by_strategy: dict[str, list[list[dict[str, Any]]]] = {
        sid: [] for sid in args.strategy_ids
    }

    async def _run_job(sid: str, run_idx: int, gslot: int) -> tuple[str, int, Path | None]:
        label = f"{sid} run {run_idx}/{args.runs}"
        col, row = gslot % grid_cols, gslot // grid_cols
        path = await _run_with_retry(
            sid,
            args.opponent,
            label=label,
            window_x=col * cell_w,
            window_y=row * cell_h,
            window_w=win_w,
            window_h=win_h,
        )
        return sid, run_idx, path

    async def _run_all() -> None:
        # semaphore 限制并发度（阻塞点 C）
        sem = asyncio.Semaphore(args.parallel)

        async def _guarded(sid: str, run_idx: int, gslot: int) -> tuple[str, int, Path | None]:
            async with sem:
                return await _run_job(sid, run_idx, gslot)

        tasks = [asyncio.create_task(_guarded(sid, run_idx, gslot)) for sid, run_idx, gslot in jobs]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        for sid, run_idx, tpath in results:
            label = f"{sid} run {run_idx}/{args.runs}"
            if tpath is None:
                print(f"[runner] {label} 连续 infra-fail，跳过此局")
                continue
            records = _load_telemetry(tpath)
            records_by_strategy[sid].append(records)
            reports_by_strategy[sid].append(verify(records, specs[sid], opponent=args.opponent))

    print(
        f"[runner] 共 {total_jobs} 局，--parallel {args.parallel}，"
        f"窗口网格 {grid_cols}×{grid_rows} cell {cell_w}×{cell_h} "
        f"窗口 {win_w}×{win_h}，"
        f"策略: {', '.join(args.strategy_ids)}"
    )
    asyncio.run(_run_all())

    # 输出各策略验收报告
    ts = time.strftime("%Y%m%d_%H%M%S")
    rep_dir = _ROOT / "logs" / "build_acceptance"
    rep_dir.mkdir(parents=True, exist_ok=True)

    all_passed = True
    for sid in args.strategy_ids:
        rpts = reports_by_strategy[sid]
        if not rpts:
            print(f"INFRA BROKEN [{sid}]: 所有 run 都基础设施失败，无法验收。需人工排查。")
            all_passed = False
            continue

        agg = aggregate_reports(rpts)
        out = "\n".join(
            [
                f"=== Build Acceptance: {sid} vs {args.opponent} "
                f"({len(rpts)}/{args.runs} runs) ===",
                agg.summary(),
            ]
        )
        out += format_exec_quality_block(
            sid,
            compute_exec_quality(records_by_strategy[sid], rpts, specs[sid]),
        )
        print(out)
        rep_file = rep_dir / f"{sid}_{args.opponent}_{ts}.txt"
        rep_file.write_text(out, encoding="utf-8")
        print(f"[runner] 报告已写入: {rep_file}")
        if not agg.passed:
            all_passed = False

    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
