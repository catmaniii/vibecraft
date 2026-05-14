"""M0 端到端 smoke：验证 ares Manager 是否真的 respect 一个用户定义的 role。

设计文档 §3.4 / §12.1 M0 出口标准：
- 在真实 SC2 客户端里启 bot
- bot 把若干探机置入"voicecraft 自己用的 role"（实际映射到 ares 的
  `UnitRole.CONTROL_GROUP_ONE`：ares 留给用户的空槽，无 Manager 内部引用它）
- 监测窗口期内每秒采样：
  * `mediator.get_units_from_role(role=CONTROL_GROUP_ONE, unit_type=PROBE)`
    应一直包含我们置入的所有 tag
  * 每个探机的 `orders` 应一直为空（没被 base bot 下令去做事）
  * 探机的 position 几乎不变
- 验收：30+ 秒内零异常 → "不动的叉子"，role 排除机制成立

本脚本仅在装了 ares-sc2 + 真实 SC2 客户端的 Windows 环境跑。
依赖安装：见 `docs/m0-smoke-runbook.md`。

用法：
    uv run python scripts/smoke_test.py \\
        --map "Goldenaura LE" \\
        --opponent-difficulty Easy \\
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
    p.add_argument("--map", default="Goldenaura LE", help="SC2 地图名（地图文件名去掉 .SC2Map）")
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
        help="开局后 5 秒置入受控 role 的探机数量",
    )
    p.add_argument(
        "--report-path",
        default=None,
        help="report json 输出路径；默认 logs/<game_id>/smoke_report.json",
    )
    p.add_argument(
        "--realtime",
        action="store_true",
        help="以 1x 实时速度跑（窗口正常显示，肉眼可看）；默认 False = 最高速空转，"
        "整局几秒跑完，适合 CI",
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
        from ares import AresBot
        from ares.consts import UnitRole as AresUnitRole
        from sc2.ids.unit_typeid import UnitTypeId
    except ImportError as e:
        print(
            "[smoke] 未安装 ares-sc2 / burnysc2。请先：\n"
            '  uv pip install "git+https://github.com/AresSC2/ares-sc2@main"',
            file=sys.stderr,
        )
        raise SystemExit(1) from e

    # 这就是我们的"LLM_CONTROLLED" 在 ares 里的真实身份。
    LLM_ROLE = AresUnitRole.CONTROL_GROUP_ONE

    class SmokeBot(AresBot):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            self.protected_tags: set[int] = set()
            self.initial_positions: dict[int, tuple[float, float]] = {}
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
                    payload={"smoke_started": True, "ares_role": LLM_ROLE.value},
                )
            )

        async def on_step(self, iteration: int) -> None:
            await super().on_step(iteration)
            now = float(self.time)

            # 5 秒后挑 N 个探机入受控 role
            if self.started_observation_at is None and now >= 5.0:
                self._enroll_probes(llm_controlled_count, now)
                self.started_observation_at = now

            # 监测窗口期内每 ~1 秒采一次样
            if (
                self.started_observation_at is not None
                and not self.ended
                and iteration % 22 == 0  # ~1s @ 22.4 ticks/s
            ):
                self._snapshot(now)

            # 监测窗口结束
            if (
                self.started_observation_at is not None
                and not self.ended
                and now - self.started_observation_at >= observation_seconds
            ):
                self._finalize(now)
                await self.client.leave()

        # ------------------------------------------------------------------

        def _enroll_probes(self, count: int, now: float) -> None:
            workers = list(self.workers)
            chosen = workers[:count]
            for w in chosen:
                tag = int(w.tag)
                self.protected_tags.add(tag)
                self.initial_positions[tag] = (float(w.position.x), float(w.position.y))
                try:
                    self.mediator.assign_role(tag=tag, role=LLM_ROLE)
                    # 清掉 SC2 开局默认的采矿 order：探机开局 0s 就自动采矿，
                    # 不 stop 的话残留的旧 order 会被误判成 received_orders。
                    # stop 之后再出现的任何 order，才真正意味着有 Manager 在
                    # enroll 之后主动指挥它 —— 那才是 role 隔离失效的证据。
                    w.stop()
                except Exception as e:
                    self.anomalies.append(
                        {
                            "ts": now,
                            "tag": tag,
                            "kind": "assign_role_failed",
                            "detail": f"{type(e).__name__}: {e}",
                        }
                    )

            session.log_event(
                Event(
                    ts=now,
                    kind=EventKind.UNIT_ROLE_CHANGED,
                    payload={
                        "tags": sorted(self.protected_tags),
                        "to_role": LLM_ROLE.value,
                    },
                )
            )
            print(
                f"[smoke] t={now:.1f}s: enrolled {len(chosen)} probes into "
                f"{LLM_ROLE.value} role: {sorted(self.protected_tags)}"
            )

        def _snapshot(self, now: float) -> None:
            # 反查：现在 role 池里有哪些 tag
            controlled = self.mediator.get_units_from_role(
                role=LLM_ROLE, unit_type=UnitTypeId.PROBE
            )
            controlled_tags = {int(u.tag) for u in controlled}

            snapshot: dict[str, Any] = {"ts": now, "probes": []}
            for tag in sorted(self.protected_tags):
                unit = self.units.find_by_tag(tag)
                still_in_role = tag in controlled_tags

                if unit is None:
                    snapshot["probes"].append({"tag": tag, "alive": False, "in_role": False})
                    self.anomalies.append({"ts": now, "tag": tag, "kind": "probe_died"})
                    continue

                orders = [
                    {"ability": str(o.ability.id), "target": str(o.target)}
                    for o in getattr(unit, "orders", [])
                ]
                pos = (float(unit.position.x), float(unit.position.y))
                initial = self.initial_positions.get(tag, pos)
                dx = pos[0] - initial[0]
                dy = pos[1] - initial[1]
                drift = (dx * dx + dy * dy) ** 0.5

                snapshot["probes"].append(
                    {
                        "tag": tag,
                        "alive": True,
                        "in_role": still_in_role,
                        "position": pos,
                        "drift_from_initial": round(drift, 3),
                        "order_count": len(orders),
                        "orders": orders,
                    }
                )

                if not still_in_role:
                    self.anomalies.append(
                        {
                            "ts": now,
                            "tag": tag,
                            "kind": "role_changed_away",
                            "detail": (f"tag {tag} 不再属于 {LLM_ROLE.value}；被 base bot 抢回了"),
                        }
                    )
                if orders:
                    self.anomalies.append(
                        {
                            "ts": now,
                            "tag": tag,
                            "kind": "received_orders",
                            "detail": orders,
                        }
                    )
                if drift > 3.0:
                    self.anomalies.append(
                        {
                            "ts": now,
                            "tag": tag,
                            "kind": "moved_significantly",
                            "detail": f"距初始 {drift:.2f}",
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
            print(
                f"[smoke] t={now:.1f}s: finalize verdict={verdict} anomalies={len(self.anomalies)}"
            )

    return SmokeBot


# =========================================================================
# 入口
# =========================================================================


def main() -> int:
    args = parse_args()

    try:
        from sc2 import maps
        from sc2.data import Difficulty, Race
        from sc2.main import run_game
        from sc2.player import Bot, Computer
    except ImportError:
        print(
            "[smoke] 未装 burnysc2 / python-sc2。请先：\n"
            '  uv pip install "git+https://github.com/AresSC2/ares-sc2@main"',
            file=sys.stderr,
        )
        return 1

    session = GameSession(GameSessionConfig())
    print(f"[smoke] 日志目录：{session.dir}")
    print(f"[smoke] 地图：{args.map}  对手：{args.opponent_race} {args.opponent_difficulty}")
    print(f"[smoke] 受控探机 {args.llm_controlled_probes} 个，观察窗口 {args.observation_seconds}s")

    SmokeBot = build_bot_class(
        session=session,
        llm_controlled_count=args.llm_controlled_probes,
        observation_seconds=args.observation_seconds,
    )

    bot = SmokeBot()
    try:
        run_game(
            maps.get(args.map),
            [
                Bot(Race.Protoss, bot, name="VoiceCraftSmoke"),
                Computer(Race[args.opponent_race], Difficulty[args.opponent_difficulty]),
            ],
            realtime=args.realtime,
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
        "anomalies_by_kind": _count_by(bot.anomalies, key="kind"),
        "snapshots": bot.snapshots,
        "observation_seconds": args.observation_seconds,
        "llm_controlled_probes": args.llm_controlled_probes,
        "map": args.map,
        "opponent": f"{args.opponent_race} {args.opponent_difficulty}",
        "ares_role_used": "CONTROL_GROUP_ONE",
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    session.close()

    print(f"\n[smoke] verdict: {report['verdict']}  anomalies: {report['anomaly_count']}")
    if report["anomalies_by_kind"]:
        print(f"[smoke] 异常分布：{report['anomalies_by_kind']}")
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
