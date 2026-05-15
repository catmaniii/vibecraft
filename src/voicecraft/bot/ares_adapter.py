"""ares-sc2 / python-sc2 与 Sc2Facade 的 binding。

**仅在装了 ares-sc2 的环境才能 import**。M0b 单测全部用 FakeFacade，
本文件不被 import；只在 M0c 端到端 smoke 用。

实现按设计文档 §3.2 / §6.1 / §11.x：
- bot 继承 `ares.AresBot`
- `set_build()` → `bot.build_order_runner.switch_opening(name)`
  （M1.5 spike A/B 结论：AresBot 属性叫 `build_order_runner`，切换用
  `switch_opening(name)`，name 必须预先在 `bot.config["Builds"]` 里）
- voicecraft 剧本在 `on_start` 调 `super().on_start()` 之前注入
  `bot.config["Builds"]`（spike B 结论：BuildOrderRunner 在 super().on_start()
  末尾构造，必须在此之前让 config 就位）
- `set_unit_role()` → `self.mediator.assign_role(tag, role)`
- `move_camera()` → `self.client.move_camera(point)`

调用方式：

    from voicecraft.bot.ares_adapter import VoiceCraftBot
    VoiceCraftBot.start(...)
"""

from __future__ import annotations

import asyncio
import logging
import queue as queue_module
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from voicecraft.bot.auto_combat.common import _log_move_camera_done
from voicecraft.bot.build_translator import openings_to_ares_config_builds
from voicecraft.bot.facade import BotState, UnitRole
from voicecraft.strategy.library import StrategyLibrary
from voicecraft.strategy.models import OpeningBuild

if TYPE_CHECKING:
    # 这些 import 只在类型检查时有效；运行时 lazy import
    from ares import AresBot

logger = logging.getLogger(__name__)


class VoiceCraftBot:
    """ares-sc2 子类的薄壳。

    构造时不能直接实例化（python-sc2 通过 run 框架启动）。
    使用 `make_bot_class(director_factory)` 工厂在运行时拼装。
    """


def make_bot_class(
    director_factory: Any,
    strategy_library: StrategyLibrary | None = None,
    status_callback: Callable[[str, str, str], None] | None = None,
    down_q: Any | None = None,
    echo_callback: Callable[[str, str], None] | None = None,
    snapshot_callback: Callable[[dict[str, Any]], None] | None = None,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    minimap_callback: Callable[[dict[str, Any]], None] | None = None,
    race: str = "Protoss",
) -> type:
    """工厂：返回一个继承 AresBot 的 bot 类，把事件转给 director。

    director_factory(facade) -> Director：在 on_start 时被调用，
    传入 bot 自己的 facade，让 director 持有 facade。

    strategy_library：可选；传入后会把其中所有 OpeningBuild 在 on_start 时注入
    `bot.config["Builds"]`（spike B：必须在 super().on_start() 之前完成，
    因为 BuildOrderRunner 在 super().on_start() 末尾构造时读 config）。

    status_callback：可选；签名 (sc2_state, bot_state, detail) -> None，
    在 on_start/on_step/on_end 时推状态给父进程（Gap 5）。
    None 时忽略（向后兼容 M0c smoke）。

    down_q：可选；multiprocessing.Queue 或 queue.Queue，
    bot 的 on_step 里非阻塞消费（Gap 2）。None 时忽略。

    echo_callback：可选；签名 (user_text, interpretation) -> None，
    on_player_command 完成后推基础 echo 给父进程（设计文档 §9.3 基础 echo）。
    None 时忽略。

    snapshot_callback：可选；签名 (snapshot_dict) -> None，
    Director 每次需要推 snapshot 时调用（P0）。None 时忽略。

    event_callback：可选；签名 (event_dict) -> None，
    Director 推 event 帧时调用（P1）。None 时忽略。

    minimap_callback：可选；签名 (minimap_dict) -> None，
    on_step 每 N tick 调用一次（minimap 帧，5Hz 下行流）。None 时忽略。
    """
    try:
        from ares import AresBot
        from ares.behaviors.macro import (
            AutoSupply,
            BuildWorkers,
            ExpansionController,
            GasBuildingController,
            Mining,
            ProductionController,
            SpawnController,
        )
        from sc2.ids.unit_typeid import UnitTypeId
    except ImportError as e:
        raise ImportError(
            '未装 ares-sc2。`uv pip install "git+https://github.com/AresSC2/ares-sc2@main"`'
        ) from e

    # 把 voicecraft 的 UnitRole 映射到 ares 真实成员（抽到 common.py）。
    # LLM_CONTROLLED → CONTROL_GROUP_ONE：ares 留给用户的空槽，
    # ares 源码里没有任何 Manager 使用它，正是 §3.4 想要的"排除单元"载体。
    from voicecraft.bot.auto_combat.common import build_role_map

    role_map = build_role_map()

    # === AUTO-PILOT === 通用神族军队组合（追猎为主 + 不朽 + 叉子，普通电脑级别）。
    # SpawnController / ProductionController 共用：proportion 之和必须 == 1.0，
    # priority 0 最高。详见 docs/plans/2026-05-15-auto-pilot.md。
    generic_army: dict[Any, dict[str, Any]] = {
        UnitTypeId.IMMORTAL: {"proportion": 0.25, "priority": 0},
        UnitTypeId.STALKER: {"proportion": 0.55, "priority": 1},
        UnitTypeId.ZEALOT: {"proportion": 0.20, "priority": 2},
    }
    target_worker_count = 66  # 约 3-4 基地饱和
    target_base_count = 4  # 普通电脑级别 3-4 矿够

    class _AresFacade:
        """Sc2Facade 的 ares 实现。

        camera 操作暂存模式(ADR 0008):
        move_camera / follow_unit **不直接** 发协议,只暂存最新目标点。
        on_step 末尾调 drain_pending_actions() 在 step await 链内串行发出。
        python-sc2 的 ws 协议是单 socket 一发一收(无 request id),
        fire-and-forget 的 create_task 会和 step 并发写 socket 导致
        帧交织,SC2 客户端协议解析失败直接崩溃。
        """

        def __init__(self, bot: AresBot) -> None:
            self.bot = bot
            self._pending_camera_point: tuple[float, float] | None = None

        # ---- 写 -------------------------------------------------------

        def set_build(self, build_name: str) -> None:
            # spike A/B 结论：
            #   - AresBot 属性是 `build_order_runner`（非 `build_runner`）
            #   - 切换 API：`switch_opening(opening_name)`
            #   - `opening_name` 必须预先在 `bot.config["Builds"]` 里
            #     （注入发生在 on_start 调 super() 之前，见 _VoiceCraftBot.on_start）
            self.bot.build_order_runner.switch_opening(build_name)

        def set_production_override(
            self,
            unit_type: str,
            count: int,
            building_tag: int | None = None,
        ) -> None:
            # M0：仅 log，留 M1 接 OverrideMediator
            pass

        def set_tech_override(self, upgrade_id: str, building_tag: int | None = None) -> None:
            pass

        def set_expansion_override(self, target_count: int) -> None:
            pass

        def set_unit_role(self, unit_tag: int, role: UnitRole) -> None:
            ares_role = role_map[role]
            self.bot.mediator.assign_role(tag=unit_tag, role=ares_role)

        def execute_unit_action(
            self,
            unit_tag: int,
            verb: str,
            target: dict[str, object] | None = None,
            ability_id: str | None = None,
        ) -> None:
            # M0：仅 log。LLMControlBehavior 真实实现留 M1。
            pass

        def set_build_location_override(
            self,
            structure_type: str,
            point: tuple[float, float],
        ) -> None:
            # Hook F：M1 拦截 mediator.request_building_placement
            pass

        def set_engagement_stance(self, stance: str) -> None:
            pass

        def move_camera(self, point: tuple[float, float]) -> None:
            """暂存相机目标点,真实 await 在 on_step 末尾 drain_pending_actions(ADR 0008)。

            多次调用合并为 latest——用户拖小地图节流后,只在意最终位置。
            """
            self._pending_camera_point = point

        def follow_unit(self, unit_tag: int) -> None:
            """暂存 unit 当前位置作为 camera 目标点(MVP:不持续跟随,只切一次)。"""
            unit = self.bot.units.find_by_tag(unit_tag)
            if unit is not None:
                self._pending_camera_point = (unit.position.x, unit.position.y)

        async def drain_pending_actions(self) -> None:
            """在 step await 链内串行发出暂存的 camera 调用。

            必须从 on_step 内调,且与 step 主请求串行(python-sc2 ws 协议无并发)。
            异常吞掉:相机移动失败不该让整个 bot 挂。
            """
            if self._pending_camera_point is None:
                return
            from sc2.position import Point2

            pt = self._pending_camera_point
            self._pending_camera_point = None
            try:
                await self.bot.client.move_camera(Point2(pt))
            except Exception as exc:
                logger.warning("move_camera_failed point=%s err=%s", pt, exc)

        def set_camera_zoom(self, level: float) -> None:
            # python-sc2 暴露 zoom 不一致，M0 noop
            pass

        # ---- 读 -------------------------------------------------------

        def get_state(self) -> BotState:
            b = self.bot
            return BotState(
                game_time=float(b.time),
                minerals=int(b.minerals),
                gas=int(b.vespene),
                supply_used=int(b.supply_used),
                supply_cap=int(b.supply_cap),
                expansion_count=len(b.townhalls),
                army_summary={},  # M0 占位
                enemy_summary={},
            )

        def resolve_selector(
            self,
            unit_type: str | None = None,
            tag: int | None = None,
            tags: list[int] | None = None,
        ) -> list[int]:
            if tag is not None:
                return [tag]
            if tags:
                return list(tags)
            if unit_type is not None:
                # M0：朴素遍历 own units 按 type name 匹配
                matched = []
                for u in self.bot.units:
                    if str(u.type_id.name).casefold() == unit_type.casefold():
                        matched.append(u.tag)
                return matched
            return []

    # -----------------------------------------------------------------------
    # 基础 echo 辅助协程（在 make_bot_class 闭包内，捕获 echo_callback）
    # -----------------------------------------------------------------------

    async def _run_command_with_echo(director: Any, text: str, now: float) -> None:
        """调 director.on_player_command，完成后推基础 echo（§9.3）。

        薄包装：把闭包里的 echo_callback 传给 common.run_command_with_echo。
        """
        from voicecraft.bot.auto_combat.common import run_command_with_echo

        await run_command_with_echo(director, text, now, echo_callback)

    class _VoiceCraftBot(AresBot):  # type: ignore[misc]
        director = None
        facade: _AresFacade | None = None
        # in-flight async task 列表：防止 GC 消掉后台任务（Gap 3）
        _cmd_tasks: list[asyncio.Task[Any]]
        # P1-2：auto-pilot 阶段二边沿检测（只在 false→true 那一 tick 推一次 event）
        _autopilot_started: bool = False
        # minimap：tick 计数（每 N tick 推一次）
        _minimap_tick_count: int = 0
        _minimap_builder: Any = None  # MinimapBuilder 实例，on_start 后构造

        def __init__(self) -> None:
            super().__init__()
            self._cmd_tasks = []
            self._autopilot_started = False
            self._minimap_tick_count = 0
            self._minimap_builder = None

        async def on_start(self) -> None:
            # spike B：BuildOrderRunner 在 super().on_start() 末尾构造，
            # 因此必须在调 super() 之前把 voicecraft 剧本注入 config["Builds"]。
            # 若 strategy_library 未传入，则跳过注入（向后兼容 M0c smoke）。
            if strategy_library is not None:
                openings: list[OpeningBuild] = [
                    s for s in strategy_library.all_strategies() if isinstance(s, OpeningBuild)
                ]
                if openings:
                    builds_cfg: dict[str, object] = openings_to_ares_config_builds(openings)
                    if "Builds" not in self.config:
                        self.config["Builds"] = {}
                    self.config["Builds"].update(builds_cfg)
                    # ares data_manager.initialise() 只在 config 里有 BuildChoices
                    # 时才设 chosen_opening；否则 BuildOrderRunner 拿到空 opening
                    # 名直接 assert 崩。用第一个 opening_build 作各种族默认初始
                    # opening，玩家之后用语音 set_build 切。
                    cycle_cfg = {"Cycle": [openings[0].id]}
                    self.config["BuildChoices"] = {
                        "Terran": cycle_cfg,
                        "Zerg": cycle_cfg,
                        "Protoss": cycle_cfg,
                        "Random": cycle_cfg,
                    }

            await super().on_start()
            self.facade = _AresFacade(self)
            self.director = director_factory(self.facade)

            # minimap builder（on_start 后 game_info / playable_area 才可访问）
            if minimap_callback is not None:
                from voicecraft.bot.minimap import MinimapBuilder

                self._minimap_builder = MinimapBuilder(self)

            # P0-3：注入 snapshot / event callback 到 director
            if snapshot_callback is not None and self.director is not None:
                self.director.set_snapshot_callback(snapshot_callback)
            if event_callback is not None and self.director is not None:
                self.director.set_event_callback(event_callback)

            # Gap 5：推 in_game → playing 给父进程
            if status_callback is not None:
                status_callback("in_game", "running", "")
                status_callback("playing", "running", "")

        async def on_step(self, iteration: int) -> None:
            if hasattr(super(), "on_step"):
                await super().on_step(iteration)

            # === AUTO-PILOT === 每 tick 重注册通用运营 behavior
            # （behavior_executioner 在 _after_step 执行后会清空注册列表）。
            self._register_auto_pilot()

            # Gap 2：非阻塞消费下行队列
            if down_q is not None:
                try:
                    while True:
                        msg: dict[str, Any] = down_q.get_nowait()
                        msg_type = msg.get("type")
                        if msg_type == "command":
                            text = str(msg.get("text", ""))
                            issued_at = float(msg.get("issued_at", float(self.time)))
                            if self.director is not None:
                                # Gap 3：fire-and-forget，不 await（LLM 调用可能几秒）
                                task = asyncio.create_task(
                                    _run_command_with_echo(self.director, text, issued_at),
                                    name=f"cmd-{issued_at:.3f}",
                                )
                                self._cmd_tasks.append(task)
                                # 绑定回调：task 完成后从列表移除 + 捕获异常日志
                                task.add_done_callback(self._on_cmd_task_done)
                        elif msg_type == "view_move":
                            # minimap 拖拽 → 切 SC2 大屏视野（ADR 0007：async fire-and-forget）
                            target = msg.get("target_point", [0.0, 0.0])
                            if self.facade is not None:
                                self.facade.move_camera((float(target[0]), float(target[1])))
                        elif msg_type == "leave":
                            logger.info("bot 收到 leave 信号，等待 on_end")
                except queue_module.Empty:
                    pass

            # minimap：每 N tick 推一帧（§1.3，N=5 ≈ 4.5Hz）
            if minimap_callback is not None and self._minimap_builder is not None:
                self._minimap_tick_count += 1
                if self._minimap_tick_count >= 5:
                    self._minimap_tick_count = 0
                    try:
                        frame = self._minimap_builder.build(float(self.time))
                        minimap_callback(frame)
                    except Exception as exc:
                        logger.warning("minimap_build_failed: %s", exc)

            # 每 tick 让 director 处理 committed directives
            if self.director is not None:
                self.director.on_tick(now=float(self.time))

        def _register_auto_pilot(self) -> None:
            """注册通用 auto-pilot behavior（设计文档 §6 基础 bot 能力标定）。

            两阶段：
            - 阶段一（opening 未跑完）：只跑不和 build_order_runner 抢资源的
              Mining / AutoSupply
            - 阶段二（build_completed）：追加会主动造建筑 / 出兵的 controller

            隔离保证：这些 behavior 选 worker 都走 mediator.select_worker（只取
            UnitRole.GATHERING），出兵只操作生产建筑 —— 不碰 CONTROL_GROUP_ONE
            （voicecraft 的 LLM 接管特种兵）。详见 docs/plans/2026-05-15-auto-pilot.md §4。

            behavior_executioner 每个 _after_step 执行后会清空注册列表，故必须
            每个 on_step 重新注册。
            """
            runner = getattr(self, "build_order_runner", None)
            if runner is None:
                return

            # 阶段一：全程开（不和 build_order_runner 抢资源）
            self.register_behavior(Mining())
            self.register_behavior(AutoSupply(self.start_location))

            # 阶段二：opening 跑完才开（会主动造建筑 / 出兵，opening 期间和 BO 冲突）
            if runner.build_completed:
                # P1-2：边沿检测 false→true，只推一次 autopilot_phase event
                if not self._autopilot_started:
                    self._autopilot_started = True
                    if event_callback is not None:
                        event_callback(
                            {
                                "type": "event",
                                "kind": "decision.autopilot_phase",
                                "ts": round(float(self.time), 3),
                                "payload": {
                                    "phase": "macro",
                                    "message": "开局 build 跑完，转入自动运营（造兵/扩张/开矿）",
                                },
                            }
                        )
                self.register_behavior(BuildWorkers(to_count=target_worker_count))
                self.register_behavior(GasBuildingController(to_count=len(self.townhalls) * 2))
                self.register_behavior(
                    ExpansionController(to_count=target_base_count, max_pending=1)
                )
                self.register_behavior(ProductionController(generic_army, self.start_location))
                self.register_behavior(
                    SpawnController(generic_army, spawn_target=self.start_location)
                )

        def _on_cmd_task_done(self, task: asyncio.Task[Any]) -> None:
            """后台 cmd task 完成回调：移除引用，异常时 log 不静默丢。"""
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
            """游戏结束时：等待所有 in-flight cmd task，再推 ended 状态。"""
            # 等待所有后台 cmd task（不再接新 command）
            if self._cmd_tasks:
                await asyncio.gather(*self._cmd_tasks, return_exceptions=True)
                self._cmd_tasks.clear()

            # Gap 5：推 ended 给父进程
            if status_callback is not None:
                status_callback("ended", "idle", "")

    # dispatch by race
    if race == "Protoss":
        from voicecraft.bot.auto_combat.common import run_command_with_echo
        from voicecraft.bot.auto_combat.protoss.bot import make_protoss_bot_class

        return make_protoss_bot_class(
            director_factory=director_factory,
            strategy_library=strategy_library,
            status_callback=status_callback,
            down_q=down_q,
            echo_callback=echo_callback,
            snapshot_callback=snapshot_callback,
            event_callback=event_callback,
            minimap_callback=minimap_callback,
            facade_class=_AresFacade,
            run_command_with_echo_fn=run_command_with_echo,
            log_move_camera_done_fn=_log_move_camera_done,
            openings_to_ares_config_builds_fn=openings_to_ares_config_builds,
            opening_build_class=OpeningBuild,
        )
    raise NotImplementedError(f"race={race!r} 暂未实现（Terran/Zerg 留 M3+）")
