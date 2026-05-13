"""M0 端到端 smoke：验证 ares Manager 是否真的 respect LLM_CONTROLLED role。

设计文档 §3.4 / §12.1 M0 出口标准：
- 在真实 SC2 客户端里启 bot
- bot 把若干探机置入 LLM_CONTROLLED role
- 持续记录这些探机的 role / 行动指令
- 验收：30 秒内 base bot 的 ArmyManager / OffensiveManager / DefensiveManager
  / ProductionManager 都不主动改它们的 role，也不下达行动指令
- demo 看点："不动的叉子"

本脚本仅在装了 ares-sc2 + 真实 SC2 客户端的 Windows 环境跑。
依赖安装：见 `docs/m0-smoke-runbook.md`。

使用：
    uv run python scripts/smoke_test.py \
        --map "Goldenaura LE" \
        --opponent-difficulty Easy \
        --observation-seconds 60

输出落到 `logs/<game_id>/smoke_report.json` + `events.jsonl`。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from voicecraft.bot.facade import UnitRole
from voicecraft.logging_ import (
    Event,
    EventKind,
    GameSession,
    GameSessionConfig,
    LogStream,
)

# =========================================================================
# 配置
# =========================================================================


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="VoiceCraft M0 smoke test")
    p.add_argument("--map", default="Goldenaura LE", help="SC2 地图名")
    p.add_argument(
        "--opponent-difficulty",
        default="Easy",
        choices=["VeryEasy", "Easy", "Medium", "Hard", "Harder", "VeryHard", "CheatVision"],
    )
    p.add_argument(
        "--opponent-race",
        default="Random",
        choices=["Protoss", "Terran", "Zerg", "Random"],
    )
    p.add_argument(
        "--observation-seconds",
        type=float,
        default=60.0,
        help="LLM_CONTROLLED 探机被监测多长时间（游戏秒）",
    )
    p.add_argument(
        "--llm-controlled-probes",
        type=int,
        default=2,
        help="开局后 5 秒置入 LLM_CONTROLLED 的探机数量",
    )
    p.add_argument(
        "--report-path",
        default=None,
        help="report json 输出路径；默认 logs/<game_id>/smoke_report.json",
    )
    return p.parse_args()


# =========================================================================
# Bot 实现
# =========================================================================


def build_bot_class(
    session: GameSession,
    llm_controlled_count: int,
    observation_seconds: float,
) -> type:
    """工厂：在 import ares 之后构造 bot 类。"""
    try:
        from ares import AresBot  # type: ignore[import-untyped]
    except ImportError as e:
        print(
            "[smoke] 未安装 ares-sc2。请先：\n"
            '  uv pip install "git+https://github.com/AresSC2/ares-sc2@main"',
            file=sys.stderr,
        )
        raise SystemExit(1) from e

    class SmokeBot(AresBot):  # type: ignore[misc,valid-type]
        def __init__(self) -> None:
            super().__init__()
            self.protected_tags: set[int] = set()
            self.snapshots: list[dict[str, Any]] = []
            self.anomalies: list[dict[str, Any]] = []
            self.started_observation_at: float | None = None
            self.ended: bool = False

        async def on_start(self) -> None:
            await super().on_start()
            session.log_event(
                Event(
                    ts=float(self.time),
                    kind=EventKind.STRATEGY_SET,
                    payload={"smoke_started": True},
                )
            )

        async def on_step(self, iteration: int) -> None:
            await super().on_step(iteration)
            now = float(self.time)

            # 5 秒后挑 N 个探机入 LLM_CONTROLLED
            if self.started_observation_at is None and now >= 5.0:
                self._enroll_probes(llm_controlled_count, now)
                self.started_observation_at = now

            # 监测窗口期内每 ~1 秒采一次样
            if (
                self.started_observation_at is not None
                and not self.ended
                and iteration % 22 == 0  # ~1s @ 22 ticks/s
            ):
                self._snapshot(now)

            # 监测窗口结束
            if (
                self.started_observation_at is not None
                and not self.ended
                and now - self.started_observation_at >= observation_seconds
            ):
                self._finalize(now)
                # 用 leave 退出（python-sc2 提供）
                await self.client.leave()

        # ------------------------------------------------------------------

        def _enroll_probes(self, count: int, now: float) -> None:
            workers = list(self.workers)  # type: ignore[attr-defined]
            chosen = workers[:count]
            for w in chosen:
                self.protected_tags.add(int(w.tag))
                try:
                    self.mediator.assign_role(  # type: ignore[attr-defined]
                        tag=int(w.tag), role=UnitRole.LLM_CONTROLLED.value
                    )
                except Exception as e:
                    self.anomalies.append(
                        {
                            "ts": now,
                            "tag": int(w.tag),
                            "kind": "assign_role_failed",
                            "detail": f"{type(e).__name__}: {e}",
                        }
                    )

            session.log_event(
                Event(
                    ts=now,
                    kind=EventKind.UNIT_ROLE_CHANGED,
                    payload={
                        "tags": [int(w.tag) for w in chosen],
                        "to_role": "LLM_CONTROLLED",
                    },
                )
            )

        def _snapshot(self, now: float) -> None:
            snapshot: dict[str, Any] = {"ts": now, "probes": []}
            for tag in list(self.protected_tags):
                unit = self.units.find_by_tag(tag)  # type: ignore[attr-defined]
                if unit is None:
                    snapshot["probes"].append({"tag": tag, "alive": False})
                    self.anomalies.append({"ts": now, "tag": tag, "kind": "probe_died"})
                    continue

                # 当前 role
                try:
                    current_role = self.mediator.get_unit_role(tag=tag)  # type: ignore[attr-defined]
                except Exception:
                    current_role = "?"

                # 当前是否有 active orders
                orders = [
                    {"ability": str(o.ability.id), "target": str(o.target)}
                    for o in getattr(unit, "orders", [])
                ]
                snapshot["probes"].append(
                    {
                        "tag": tag,
                        "alive": True,
                        "role": str(current_role),
                        "position": [unit.position.x, unit.position.y],
                        "order_count": len(orders),
                        "orders": orders,
                    }
                )

                # 异常：role 不再是 LLM_CONTROLLED
                if str(current_role) != UnitRole.LLM_CONTROLLED.value:
                    self.anomalies.append(
                        {
                            "ts": now,
                            "tag": tag,
                            "kind": "role_changed_away",
                            "detail": f"role 变为 {current_role!r}",
                        }
                    )
                # 异常：base bot 给了 orders
                if orders:
                    self.anomalies.append(
                        {
                            "ts": now,
                            "tag": tag,
                            "kind": "received_orders",
                            "detail": orders,
                        }
                    )

            self.snapshots.append(snapshot)

        def _finalize(self, now: float) -> None:
            self.ended = True
            verdict = "pass" if not self.anomalies else "fail"
            session.log_event(
                Event(
                    ts=now,
                    kind=EventKind.STRATEGY_PHASE_CHANGE,
                    payload={
                        "smoke_finalized": True,
                        "verdict": verdict,
                        "anomaly_count": len(self.anomalies),
                    },
                )
            )

    return SmokeBot


# =========================================================================
# 入口
# =========================================================================


def main() -> int:
    args = parse_args()

    try:
        from sc2.data import Difficulty, Race  # type: ignore[import-untyped]
        from sc2.main import run_game  # type: ignore[import-untyped]
        from sc2.player import Bot, Computer  # type: ignore[import-untyped]
    except ImportError:
        print(
            "[smoke] 未装 burnysc2 / python-sc2。请先：\n"
            '  uv pip install "git+https://github.com/AresSC2/ares-sc2@main"',
            file=sys.stderr,
        )
        return 1

    session = GameSession(GameSessionConfig())
    print(f"[smoke] 日志目录：{session.dir}")

    SmokeBot = build_bot_class(
        session=session,
        llm_controlled_count=args.llm_controlled_probes,
        observation_seconds=args.observation_seconds,
    )

    try:
        bot = SmokeBot()
        run_game(
            args.map,
            [
                Bot(Race.Protoss, bot, name="VoiceCraftSmoke"),
                Computer(Race[args.opponent_race], Difficulty[args.opponent_difficulty]),
            ],
            realtime=False,
        )
    except Exception as e:
        print(f"[smoke] 对局失败：{type(e).__name__}: {e}", file=sys.stderr)
        session.log_event(
            Event(
                ts=0.0,
                kind=EventKind.DIRECTIVE_FAILED,
                payload={"smoke_runtime_error": f"{type(e).__name__}: {e}"},
                priority="high",
            )
        )
        session.close()
        return 2

    # 落 report
    report_path = Path(args.report_path) if args.report_path else session.dir / "smoke_report.json"
    report = {
        "verdict": "pass" if not bot.anomalies else "fail",
        "anomaly_count": len(bot.anomalies),
        "anomalies": bot.anomalies,
        "snapshots": bot.snapshots,
        "anomalies_by_kind": _count_by(bot.anomalies, key="kind"),
        "observation_seconds": args.observation_seconds,
        "llm_controlled_probes": args.llm_controlled_probes,
        "map": args.map,
        "opponent": f"{args.opponent_race} {args.opponent_difficulty}",
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    session.close()

    print(f"[smoke] verdict: {report['verdict']}  anomalies: {report['anomaly_count']}")
    print(f"[smoke] 报告：{report_path}")
    print(f"[smoke] 事件流：{session.dir / LogStream.EVENTS.value}.jsonl")
    return 0 if report["verdict"] == "pass" else 3


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for x in items:
        out[str(x.get(key, "?"))] += 1
    return dict(out)


if __name__ == "__main__":
    raise SystemExit(main())
