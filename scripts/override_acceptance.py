"""Player override 验收 runner (Task #311 player override e2e)。

用法:
    .venv/Scripts/python.exe scripts/override_acceptance.py <case_id>
        [--opponent veryeasy|...] [--runs N] [--parallel N]

case_id 对应 tests/override_acceptance/<case_id>.yaml,典型命名:
    4bg__retreat, macro_hatch__retreat, phoenix_2base__defend ...

跟 build_acceptance 的区别:
- spec 含 player_actions(玩家时间线)+ army_after_player_action check
- spawn 时 GameConfig.player_actions 注入,子进程 Director 到点自动 fire 模拟
  玩家按 UI 战术按钮(retreat/attack/defend)
- 报告输出到 logs/override_acceptance/

复用 build_acceptance 的:窗口网格平铺 / 多 strategy 并发 / infra-fail retry /
opponent / 经济曲线 score / Report.summary。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))  # 让 `scripts.build_acceptance` 可 import

# 复用 build_acceptance 的 SC2 spawn / window grid / retry / load_telemetry helpers。
# 仅 override _run_one_game(加 player_actions 注入)+ 改 spec 路径。
from scripts.build_acceptance import (  # noqa: E402
    _OPPONENT_DIFFICULTY,
    _WALL_CLOCK_LIMIT_S,
    _detect_desktop_size,
    _detect_race,
    _load_telemetry,
    _make_game_id,
)

from vibecraft.build_acceptance.spec import AcceptanceSpec, load_spec  # noqa: E402
from vibecraft.build_acceptance.verifier import (  # noqa: E402
    Report,
    aggregate_reports,
    verify,
)
from vibecraft.server.game_process import GameConfig, GameProcess  # noqa: E402

# spec 目录跟 build_acceptance 平行;case 命名 <strategy_id>__<verb>.yaml
_SPEC_DIR = _ROOT / "tests" / "override_acceptance"

# 2026-06-02 早停余量(game-time 秒):跑到最后检查点 + 余量就停,不死等游戏自然结束。
# 根因:fire 完 retreat/defend 后军队持久防守 → 跟对手僵持 → 游戏永不结束 → 900s
# wall 超时。检查是 post-hoc 从 telemetry 读(army_after_player_action),游戏只需跑到
# 最后检查点之后一点点。余量确保最后检查点附近有 snapshot 供 verifier ±tol 命中。
_STOP_MARGIN_S = 30.0


def _compute_stop_game_time(spec: AcceptanceSpec) -> float | None:
    """最后一个检查点的 game-time(+余量);无可算检查点 → None(回退等自然结束)。

    army_after_player_action check 时机 = player_actions[action_idx].at_s + after_s。
    其余 check 用 check.at_s。
    """
    times: list[float] = []
    for c in spec.checks:
        idx = getattr(c, "action_idx", None)
        after = getattr(c, "after_s", None)
        if idx is not None and after is not None:
            if 0 <= idx < len(spec.player_actions):
                base = spec.player_actions[idx].at_s
                if base is not None:
                    times.append(float(base) + float(after))
        else:
            t = c.at_s
            if t is not None:
                times.append(float(t))
    if not times:
        return None
    return max(times) + _STOP_MARGIN_S


async def _run_one_override_game(
    case_id: str,
    spec: AcceptanceSpec,
    opponent: str,
    window_x: int = 0,
    window_y: int = 0,
    window_w: int = 800,
    window_h: int = 600,
) -> Path | None:
    """跑一局,返回 telemetry.jsonl 路径;infra-fail 返回 None。

    跟 build_acceptance._run_one_game 几乎一致,差别:
    1. forced_opening = spec.strategy_id(从 spec 字段拿,不是 case_id)
    2. GameConfig.player_actions 注入 spec.player_actions 的序列化版本
    """
    game_id = _make_game_id()

    cfg = GameConfig(
        map_name="DaybreakLE",
        my_race=_detect_race(spec.strategy_id),
        opponent_race="Random",
        opponent_difficulty=_OPPONENT_DIFFICULTY[opponent],
        realtime=False,
        window_x=window_x,
        window_y=window_y,
        window_width=window_w,
        window_height=window_h,
        forced_opening=spec.strategy_id,
        game_id=game_id,
        # Task #311: 把 spec.player_actions 序列化成 picklable list[dict]
        # 子进程入口 _build_bot_class 闭包到 director_factory,Director.on_tick
        # 到点 _fire_scheduled_action 模拟玩家按 UI 战术按钮。
        player_actions=[
            {
                "at_s": a.at_s,
                "verb": a.verb,
                "mode": a.mode,
                "target_area": a.target_area,
            }
            for a in spec.player_actions
        ],
    )

    start_wall = time.monotonic()
    logs_dir = _ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    gp = GameProcess()
    gp.start(cfg)

    ended_normally = False
    crashed = False
    stopped_after_checks = False
    stop_game_time = _compute_stop_game_time(spec)

    try:

        async def _consume() -> None:
            nonlocal ended_normally, crashed, stopped_after_checks
            async for msg in gp.raw_events():
                sc2 = msg.get("sc2")
                if sc2 == "ended":
                    ended_normally = True
                    return
                if sc2 == "crashed":
                    crashed = True
                    return
                # 早停:telemetry 跑到最后检查点 + 余量即可,不死等游戏自然结束
                # (fire 完 retreat/defend 后军队持久防守会僵持到 900s 超时)。
                if stop_game_time is not None and msg.get("kind") == "snapshot":
                    t = msg.get("frame", {}).get("t")
                    if t is not None and float(t) >= stop_game_time:
                        stopped_after_checks = True
                        print(
                            f"[override-runner] {case_id} 跑到检查点时刻 "
                            f"{stop_game_time:.0f}s(game-time),提前 stop 验证"
                        )
                        return

        await asyncio.wait_for(_consume(), timeout=_WALL_CLOCK_LIMIT_S)
    except TimeoutError:
        print(f"[override-runner] wall-clock {_WALL_CLOCK_LIMIT_S}s 超时,强制 stop(infra-fail)")
        await gp.stop()
        return None
    finally:
        # ended_normally=自然结束(子进程已退);stopped_after_checks=早停(需 stop 子进程)
        if not ended_normally:
            await gp.stop()

    # 自然结束 或 早停(telemetry 已覆盖到最后检查点)→ 都可验证
    if not ended_normally and not stopped_after_checks:
        return None

    telemetry_path = logs_dir / game_id / "telemetry.jsonl"
    if not telemetry_path.exists():
        print(f"[override-runner] 游戏结束但找不到 telemetry.jsonl: {telemetry_path}")
        return None

    elapsed = time.monotonic() - start_wall
    print(
        f"[override-runner] {case_id} 结束({elapsed:.1f}s wall-clock),telemetry: {telemetry_path}"
    )
    return telemetry_path


_MAX_INFRA_RETRY = 3


async def _run_with_retry(
    case_id: str,
    spec: AcceptanceSpec,
    opponent: str,
    label: str = "",
    window_x: int = 0,
    window_y: int = 0,
    window_w: int = 800,
    window_h: int = 600,
) -> Path | None:
    """infra-fail 自动 retry ≤ _MAX_INFRA_RETRY 次;全失败返 None。"""
    tag = label or f"{case_id} vs {opponent}"
    for attempt in range(1, _MAX_INFRA_RETRY + 1):
        print(f"[override-runner] {tag} — infra 尝试 {attempt}")
        telemetry_path = await _run_one_override_game(
            case_id,
            spec,
            opponent,
            window_x=window_x,
            window_y=window_y,
            window_w=window_w,
            window_h=window_h,
        )
        if telemetry_path is not None:
            return telemetry_path
        print(f"[override-runner] {tag} infra-fail(第 {attempt} 次),retry...")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Player override 验收 runner: 子进程 Director 自动按 spec.player_actions "
            "时间线模拟玩家按 UI 战术按钮,验单位真服从。"
        )
    )
    ap.add_argument(
        "case_ids",
        nargs="+",
        help="case id,如 4bg__retreat;对应 tests/override_acceptance/<id>.yaml",
    )
    ap.add_argument(
        "--opponent",
        default="veryeasy",
        choices=sorted(_OPPONENT_DIFFICULTY),
        help="对手难度(default veryeasy;override 验收时机优先用低难度排除外因)",
    )
    ap.add_argument(
        "--runs",
        type=int,
        default=1,
        help="每个 case 跑几局取多数票(default 1;3 消除随机性)",
    )
    ap.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="并发度(default 1)",
    )
    ap.add_argument(
        "--window-w",
        type=int,
        default=800,
        help="单 SC2 窗口宽(default 800)",
    )
    ap.add_argument(
        "--window-h",
        type=int,
        default=600,
        help="单 SC2 窗口高(default 600)",
    )
    args = ap.parse_args()

    if args.runs < 1:
        print("ERROR: --runs 必须 >= 1")
        return 2
    if args.parallel < 1:
        print("ERROR: --parallel 必须 >= 1")
        return 2

    # 校验 spec 文件 + 至少有 1 个 player_action(否则用 build_acceptance 就够)
    specs: dict[str, AcceptanceSpec] = {}
    for cid in args.case_ids:
        spec_path = _SPEC_DIR / f"{cid}.yaml"
        if not spec_path.exists():
            print(f"ERROR: 没有 override spec: {spec_path}")
            return 2
        spec = load_spec(spec_path)
        if not spec.player_actions:
            print(f"ERROR: {cid}.yaml 没有 player_actions,用 build_acceptance.py 验普通 build 即可")
            return 2
        specs[cid] = spec

    desktop_w, desktop_h = _detect_desktop_size()
    grid_cols = min(4, args.parallel)
    grid_rows = max(1, (args.parallel + grid_cols - 1) // grid_cols)
    cell_w = desktop_w // grid_cols
    cell_h = desktop_h // grid_rows
    win_w = args.window_w
    win_h = args.window_h

    jobs: list[tuple[str, int, int]] = []
    slot = 0
    for cid in args.case_ids:
        for run_idx in range(1, args.runs + 1):
            jobs.append((cid, run_idx, slot % args.parallel))
            slot += 1

    total_jobs = len(jobs)
    reports_by_case: dict[str, list[Report]] = {cid: [] for cid in args.case_ids}

    async def _run_job(cid: str, run_idx: int, gslot: int) -> tuple[str, int, Path | None]:
        label = f"{cid} run {run_idx}/{args.runs}"
        col, row = gslot % grid_cols, gslot // grid_cols
        path = await _run_with_retry(
            cid,
            specs[cid],
            args.opponent,
            label=label,
            window_x=col * cell_w,
            window_y=row * cell_h,
            window_w=win_w,
            window_h=win_h,
        )
        return cid, run_idx, path

    async def _run_all() -> None:
        sem = asyncio.Semaphore(args.parallel)

        async def _guarded(cid: str, run_idx: int, gslot: int):  # type: ignore[no-untyped-def]
            async with sem:
                return await _run_job(cid, run_idx, gslot)

        tasks = [asyncio.create_task(_guarded(cid, run_idx, gslot)) for cid, run_idx, gslot in jobs]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        for cid, run_idx, tpath in results:
            label = f"{cid} run {run_idx}/{args.runs}"
            if tpath is None:
                print(f"[override-runner] {label} 连续 infra-fail,跳过")
                continue
            reports_by_case[cid].append(
                verify(_load_telemetry(tpath), specs[cid], opponent=args.opponent)
            )

    print(
        f"[override-runner] 共 {total_jobs} 局,--parallel {args.parallel},"
        f"窗口网格 {grid_cols}x{grid_rows} cell {cell_w}x{cell_h} "
        f"窗口 {win_w}x{win_h},cases: {', '.join(args.case_ids)}"
    )
    asyncio.run(_run_all())

    ts = time.strftime("%Y%m%d_%H%M%S")
    rep_dir = _ROOT / "logs" / "override_acceptance"
    rep_dir.mkdir(parents=True, exist_ok=True)

    all_passed = True
    for cid in args.case_ids:
        rpts = reports_by_case[cid]
        if not rpts:
            print(f"INFRA BROKEN [{cid}]: 所有 run 都 infra-fail")
            all_passed = False
            continue
        agg = aggregate_reports(rpts)
        out = "\n".join(
            [
                f"=== Override Acceptance: {cid} vs {args.opponent} "
                f"({len(rpts)}/{args.runs} runs) ===",
                agg.summary(),
            ]
        )
        print(out)
        rep_file = rep_dir / f"{cid}_{args.opponent}_{ts}.txt"
        rep_file.write_text(out, encoding="utf-8")
        print(f"[override-runner] 报告: {rep_file}")
        if not agg.passed:
            all_passed = False

    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
