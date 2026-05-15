"""神族 bot：_VoiceCraftProtossBot 继承 Aristaeus MyBot。

import 路径说明：vendor/aristaeus/ 不在标准 src layout 下，
运行时通过 sys.path 注入让 `from bot.main import MyBot` 可解析。
注入只在真正 import 本模块时发生（lazy，单测时 fake_aristaeus 先 mock 掉 sys.modules）。

设计参考：docs/plans/2026-05-16-tri-race-bots.md §S2。
"""

from __future__ import annotations

import asyncio
import logging
import queue as queue_module
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# vendor path 注入（ares 装了才跑到这里；单测先 mock sys.modules 绕开）
# -----------------------------------------------------------------------
_VENDOR_ARISTAEUS = Path(__file__).parents[5] / "vendor" / "aristaeus"


def _ensure_aristaeus_on_path() -> None:
    """把 vendor/aristaeus 加进 sys.path(幂等)。

    Aristaeus 锁定 ares-sc2 1.15.1 而 venv 装的是 3.7.2,我们直接对 vendor 代码打 patch:
    - `from ares.cython_extensions.X` → `from cython_extensions.X` (6 处,sed 批量)
    - `CombatBehavior` → `CombatIndividualBehavior` (oracle_kite_forward.py 2 处)
    无语义改动,纯 import 路径漂移。详见 vendor/aristaeus/ATTRIBUTION.md。
    """
    target = str(_VENDOR_ARISTAEUS)
    if target not in sys.path:
        sys.path.insert(0, target)


# -----------------------------------------------------------------------
# 工厂函数：返回 _VoiceCraftProtossBot 类
# 所有闭包参数与 ares_adapter.make_bot_class 一致。
# -----------------------------------------------------------------------


def make_protoss_bot_class(
    director_factory: Any,
    strategy_library: Any,
    status_callback: Any,
    down_q: Any,
    echo_callback: Any,
    snapshot_callback: Any,
    event_callback: Any,
    minimap_callback: Any,
    # 闭包引用：由 ares_adapter 传入（运行时已构造好）
    facade_class: Any,
    run_command_with_echo_fn: Any,
    log_move_camera_done_fn: Any,
    openings_to_ares_config_builds_fn: Any,
    opening_build_class: Any,
) -> type:
    """工厂：返回继承 Aristaeus MyBot 的神族 bot 类。

    参数设计：把外层 make_bot_class 闭包内的对象显式传进来，
    保持 _VoiceCraftProtossBot 与 ares_adapter.py 之间的解耦。
    """
    _ensure_aristaeus_on_path()

    try:
        from bot.main import MyBot
    except ImportError as e:
        raise ImportError(
            "无法 import vendor/aristaeus/bot/main.py；"
            "确认 vendor/aristaeus/ 已 clone 且 ares-sc2 已装。"
        ) from e

    class _VoiceCraftProtossBot(MyBot):  # type: ignore[misc]
        """voicecraft 神族 bot：Aristaeus MyBot + voicecraft 指挥层。

        继承层次：_VoiceCraftProtossBot → MyBot → AresBot → ...

        on_start 顺序（spike B 结论保持）：
          1. 注入 config["Builds"]（必须在 super().on_start() 之前）
          2. await super().on_start()（Aristaeus + AresBot 完成初始化）
          3. 构造 facade / director / minimap builder
          4. 注入 snapshot / event callback

        on_step 顺序：
          1. await super().on_step(iteration)（Aristaeus 自身：Mining + ProductionManager）
          2. voicecraft down_q 消费 + minimap 推送 + director.on_tick
        """

        director: Any = None
        facade: Any = None
        _cmd_tasks: list[asyncio.Task[Any]]
        _minimap_tick_count: int = 0
        _minimap_builder: Any = None
        _decision_watcher: Any = None

        def __init__(self) -> None:
            super().__init__()
            self._cmd_tasks = []
            self._minimap_tick_count = 0
            self._minimap_builder = None
            self._decision_watcher = None

        async def on_start(self) -> None:
            # spike B：config["Builds"] 必须在 super().on_start() 之前注入
            if strategy_library is not None:
                openings = [
                    s
                    for s in strategy_library.all_strategies()
                    if isinstance(s, opening_build_class)
                ]
                if openings:
                    builds_cfg = openings_to_ares_config_builds_fn(openings)
                    if "Builds" not in self.config:
                        self.config["Builds"] = {}
                    self.config["Builds"].update(builds_cfg)
                    cycle_cfg = {"Cycle": [openings[0].id]}
                    self.config["BuildChoices"] = {
                        "Terran": cycle_cfg,
                        "Zerg": cycle_cfg,
                        "Protoss": cycle_cfg,
                        "Random": cycle_cfg,
                    }

            await super().on_start()
            self.facade = facade_class(self)
            self.director = director_factory(self.facade)

            if minimap_callback is not None:
                from voicecraft.bot.minimap import MinimapBuilder

                self._minimap_builder = MinimapBuilder(self)

            if snapshot_callback is not None and self.director is not None:
                self.director.set_snapshot_callback(snapshot_callback)
            if event_callback is not None and self.director is not None:
                self.director.set_event_callback(event_callback)

            # 状态 diff watcher:bot 自动决策(造建筑/扩张/升级/build 完成)推 event
            if event_callback is not None:
                from voicecraft.bot.auto_combat.decision_watcher import DecisionWatcher

                self._decision_watcher = DecisionWatcher(event_callback)

            # 把 ares 选中的默认 opening 立刻落到 board.opening slot,
            # 让手机 UI 一进对局就显示当前宏观脚本(否则空着等玩家发指令才亮)。
            if (
                strategy_library is not None
                and self.director is not None
                and "BuildChoices" in self.config
            ):
                from voicecraft.directives.types import StageKind

                cycle = self.config["BuildChoices"].get("Protoss", {}).get("Cycle", [])
                if cycle:
                    self.director.set_initial_strategy(
                        StageKind.OPENING, cycle[0], float(self.time)
                    )

            if status_callback is not None:
                status_callback("in_game", "running", "")
                status_callback("playing", "running", "")

        async def on_step(self, iteration: int) -> None:
            # 先让 Aristaeus（Mining + ProductionManager）跑
            await super().on_step(iteration)

            # voicecraft down_q 消费
            if down_q is not None:
                try:
                    while True:
                        msg: dict[str, Any] = down_q.get_nowait()
                        msg_type = msg.get("type")
                        if msg_type == "command":
                            text = str(msg.get("text", ""))
                            # WS 消息里的 issued_at 是 unix timestamp(time.time()),
                            # 但 Director / Board 内部所有 now 都是 game_time(bot.time)。
                            # 混用会让 effective_at = unix + 1.5 永远 > game_time,
                            # directive 卡 pending 永不 commit(剧本切换无响应)。
                            game_now = float(self.time)
                            if self.director is not None:
                                task = asyncio.create_task(
                                    run_command_with_echo_fn(
                                        self.director, text, game_now, echo_callback
                                    ),
                                    name=f"cmd-{game_now:.3f}",
                                )
                                self._cmd_tasks.append(task)
                                task.add_done_callback(self._on_cmd_task_done)
                        elif msg_type == "view_move":
                            target = msg.get("target_point", [0.0, 0.0])
                            if self.facade is not None:
                                self.facade.move_camera(
                                    (float(target[0]), float(target[1]))
                                )
                        elif msg_type == "leave":
                            logger.info("bot 收到 leave 信号，等待 on_end")
                except queue_module.Empty:
                    pass

            # minimap 推送（§1.3，N=5 ≈ 4.5Hz）
            if minimap_callback is not None and self._minimap_builder is not None:
                self._minimap_tick_count += 1
                if self._minimap_tick_count >= 5:
                    self._minimap_tick_count = 0
                    try:
                        frame = self._minimap_builder.build(float(self.time))
                        minimap_callback(frame)
                    except Exception as exc:
                        logger.warning("minimap_build_failed: %s", exc)

            # director tick
            if self.director is not None:
                self.director.on_tick(now=float(self.time))

            # bot 自动决策 watcher(状态 diff → event 帧)
            if self._decision_watcher is not None:
                self._decision_watcher.tick(self, float(self.time))

            # step 末尾串行 await 暂存的 camera 调用(ADR 0008:避免与 step 主请求并发写 ws)
            if self.facade is not None:
                await self.facade.drain_pending_actions()

        def _on_cmd_task_done(self, task: asyncio.Task[Any]) -> None:
            import contextlib

            with contextlib.suppress(ValueError):
                self._cmd_tasks.remove(task)
            if not task.cancelled():
                exc = task.exception()
                if exc is not None:
                    logger.error(
                        "cmd_task_failed %s: %s",
                        task.get_name(),
                        exc,
                        exc_info=exc,
                    )

        async def on_end(self, game_result: Any) -> None:
            if self._cmd_tasks:
                await asyncio.gather(*self._cmd_tasks, return_exceptions=True)
                self._cmd_tasks.clear()
            if status_callback is not None:
                status_callback("ended", "idle", "")

    return _VoiceCraftProtossBot
