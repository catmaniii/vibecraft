"""神族 bot：_VoiceCraftProtossBot 继承 sharpy KnowledgeBot。

import 路径说明：vendor/sharpy/ 不在标准 src layout 下，
运行时通过 sys.path 注入让 `from sharpy.knowledges.knowledge_bot import KnowledgeBot`
可解析。注入只在真正 import 本模块时发生（lazy，单测时 fake_sharpy 先 mock sys.modules）。

设计参考：docs/plans/2026-05-16-sharpy-migration.md §1-4。

继承层次：_VoiceCraftProtossBot → KnowledgeBot → SkeletonBot → BotAI
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
# vendor path 注入（单测先 mock sys.modules 绕开）
# -----------------------------------------------------------------------
_VENDOR_SHARPY = Path(__file__).parents[5] / "vendor" / "sharpy"


def _ensure_sharpy_on_path() -> None:
    """把 vendor/sharpy 加进 sys.path（幂等）。

    sharpy 所有模块（sharpy.*、config、bot_loader 等）都在 vendor/sharpy/ 下，
    直接把该目录加进 sys.path 即可解析。

    无 cython_extensions / CombatBehavior 漂移：sharpy 的 pure Python 实现
    不需要 Aristaeus 那套 patch。详见 vendor/sharpy/ATTRIBUTION.md（M1 新建）。
    """
    target = str(_VENDOR_SHARPY)
    if target not in sys.path:
        sys.path.insert(0, target)


# -----------------------------------------------------------------------
# 工厂函数：返回 _VoiceCraftProtossBot 类
# 所有闭包参数与 sharpy_adapter.make_bot_class 一致。
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
    run_command_with_echo_fn: Any,
) -> type:
    """工厂：返回继承 sharpy KnowledgeBot 的神族 bot 类。

    参数设计：把外层 make_bot_class 闭包内的对象显式传进来，
    保持 _VoiceCraftProtossBot 与 sharpy_adapter.py 之间的解耦。
    """
    _ensure_sharpy_on_path()

    try:
        from sharpy.knowledges.knowledge_bot import KnowledgeBot
        from sharpy.plans import BuildOrder
    except ImportError as e:
        raise ImportError(
            "无法 import sharpy.knowledges.knowledge_bot；"
            "确认 vendor/sharpy/ 已 clone 且 python-sc2 已装。"
        ) from e

    # facade 类（在闭包内定义，持有 bot 引用）
    from voicecraft.bot.facade import BotState, UnitRole

    # move camera done callback（ADR 0008 日志用）
    def _log_move_camera_done(task: Any) -> None:
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                logger.error("move_camera_task_failed: %s", exc, exc_info=exc)

    class _SharpyFacade:
        """Sc2Facade 的 sharpy 实现。

        camera 操作暂存模式(ADR 0008):
        move_camera / follow_unit **不直接** 发协议，只暂存最新目标点。
        on_step 末尾调 drain_pending_actions() 在 step await 链内串行发出。

        M4: LLM_CONTROLLED role 隔离
        set_unit_role(tag, LLM_CONTROLLED) 同时写入 bot._llm_controlled_tags，
        每 step 开头 refresh_llm_controlled_roles() 重新声明 Reserved role，
        防止 sharpy UnitRoleManager.update() 每帧清空 had_task_set 后丢失状态。
        """

        def __init__(self, bot: Any) -> None:
            self.bot = bot
            self._pending_camera_point: tuple[float, float] | None = None

        # ---- 写 -------------------------------------------------------

        def set_build(self, build_name: str) -> None:
            """active_recipe 切换 → IfElse 路由树在下一个 step 立即生效。

            sharpy IfElse.execute() 每 step 调 condition.check()（RequireCustom
            每次重新求值 lambda），因此只要更新 active_recipe，IfElse 就自动切换。
            """
            logger.info("set_build switched to %s", build_name)
            self.bot.active_recipe = build_name

        def set_production_override(
            self,
            unit_type: str,
            count: int,
            building_tag: int | None = None,
        ) -> None:
            # M1 noop，留 M3 接 sharpy ActUnit override
            pass

        def set_tech_override(self, upgrade_id: str, building_tag: int | None = None) -> None:
            # M1 noop
            pass

        def set_expansion_override(self, target_count: int) -> None:
            # M1 noop
            pass

        def set_unit_role(self, unit_tag: int, role: UnitRole) -> None:
            """把 voicecraft UnitRole 映射到 sharpy UnitTask 并设置。

            sharpy UnitRoleManager.set_task 接 Unit 对象（不接 tag），
            需先用 cache.by_tag 取 unit；找不到时 log warn，不崩。

            M4: LLM_CONTROLLED → 同时写入 bot._llm_controlled_tags，确保每 step
            通过 refresh_llm_controlled_roles() 持久化 Reserved 状态。
            单位死亡后 tag 不在 cache，refresh 时自动跳过（cache.by_tag 返回 None）。
            """
            try:
                from voicecraft.bot.auto_combat.common import build_role_map

                role_map = build_role_map()
                task = role_map[role]
                unit = self.bot.knowledge.unit_cache.by_tag(unit_tag)
                if unit is None:
                    logger.warning(
                        "set_unit_role: tag=%d not found in cache (role=%s)", unit_tag, role
                    )
                    return
                self.bot.knowledge.roles.set_task(task, unit)
                # M4: LLM_CONTROLLED 额外记录在 _llm_controlled_tags，
                # 每 step refresh_llm_controlled_roles() 会重新声明 Reserved，
                # 防止 UnitRoleManager.update() 清空 had_task_set 后丢失。
                if role == UnitRole.LLM_CONTROLLED:
                    self.bot._llm_controlled_tags.add(unit_tag)
                    logger.info("unit_claimed tag=%d added to _llm_controlled_tags", unit_tag)
                else:
                    # 非 LLM_CONTROLLED 角色赋值时从集合移除（e.g. release 归队）
                    self.bot._llm_controlled_tags.discard(unit_tag)
            except Exception as exc:
                logger.warning("set_unit_role failed tag=%d role=%s err=%s", unit_tag, role, exc)

        def execute_unit_action(
            self,
            unit_tag: int,
            verb: str,
            target: dict[str, object] | None = None,
            ability_id: str | None = None,
        ) -> None:
            # M1 noop。LLMControlBehavior 真实实现留 M4。
            pass

        def set_build_location_override(
            self,
            structure_type: str,
            point: tuple[float, float],
        ) -> None:
            # M1 noop
            pass

        def set_engagement_stance(self, stance: str) -> None:
            # M1 noop
            pass

        def move_camera(self, point: tuple[float, float]) -> None:
            """暂存相机目标点，真实 await 在 on_step 末尾 drain_pending_actions(ADR 0008)。

            多次调用合并为 latest——用户拖小地图节流后，只在意最终位置。
            """
            self._pending_camera_point = point

        def follow_unit(self, unit_tag: int) -> None:
            """暂存 unit 当前位置作为 camera 目标点（MVP：不持续跟随，只切一次）。"""
            unit = self.bot.units.find_by_tag(unit_tag)
            if unit is not None:
                self._pending_camera_point = (unit.position.x, unit.position.y)

        async def drain_pending_actions(self) -> None:
            """在 step await 链内串行发出暂存的 camera 调用。

            必须从 on_step 内调，且与 step 主请求串行（python-sc2 ws 协议无并发）。
            异常吞掉：相机移动失败不该让整个 bot 挂。
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
            # python-sc2 暴露 zoom 不一致，M1 noop
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
                army_summary={},  # M1 占位
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
                matched = []
                for u in self.bot.units:
                    if str(u.type_id.name).casefold() == unit_type.casefold():
                        matched.append(u.tag)
                return matched
            return []

    class _VoiceCraftProtossBot(KnowledgeBot):  # type: ignore[misc]
        """voicecraft 神族 bot：sharpy KnowledgeBot + voicecraft 指挥层。

        继承层次：_VoiceCraftProtossBot → KnowledgeBot → SkeletonBot → BotAI

        on_start 顺序：
          1. await super().on_start()（KnowledgeBot 初始化所有 Manager）
          2. 构造 _SharpyFacade / director / minimap_builder
          3. 注入 snapshot / event callback 到 director
          4. 推 set_initial_strategy（让手机 UI 一进对局就显示当前剧本）
          5. 推 status_callback

        on_step 顺序：
          1. await super().on_step(iteration)（KnowledgeBot.on_step：update + execute）
          2. M4: refresh_llm_controlled_roles()（每帧重声明 Reserved，防 had_task_set 清空）
          3. voicecraft down_q 消费 + minimap 推送 + director.on_tick
          4. decision_watcher.tick
          5. facade.drain_pending_actions（串行 camera 命令，ADR 0008）

        M4: _llm_controlled_tags
          玩家 unit_claim 后通过 set_unit_role(tag, LLM_CONTROLLED) 写入此集合。
          每 step 开头 refresh_llm_controlled_roles() 重新声明 UnitTask.Reserved，
          防止 sharpy UnitRoleManager.update() 每帧清 had_task_set 后角色丢失。

          已知未完成（文档化，留后续 M5+）：
          - GroupCombatManager 的 add_unit()/execute() 是显式传参，不自动过滤 Reserved，
            需要 zone_attack.py 里 free_units 链路保证（Reserved 不在 free_units）。
          - zone_defense.py get_defenders() 只查 Idle/Moving/Fighting/Attacking，
            Reserved 不会被拉去守基地。
          - 死亡的 LLM_CONTROLLED 单位：on_unit_destroyed 从集合移除（防内存泄漏）。
        """

        director: Any = None
        facade: _SharpyFacade | None = None
        _cmd_tasks: list[asyncio.Task[Any]]
        _minimap_tick_count: int = 0
        _minimap_builder: Any = None
        _decision_watcher: Any = None
        # 当前剧本名：IfElse 路由树每 step 检查此值；set_build 写入后下个 step 立即生效。
        # 默认 "1g_robo_immortal"（opening fallback），on_start 会根据 strategy_library 重设。
        active_recipe: str = "1g_robo_immortal"
        # M4: LLM_CONTROLLED 单位的 tag 集合（跨 step 持久化）
        _llm_controlled_tags: set[int]

        def __init__(self) -> None:
            super().__init__("VoiceCraft Protoss")
            self._cmd_tasks = []
            self._minimap_tick_count = 0
            self._minimap_builder = None
            self._decision_watcher = None
            self.active_recipe = "1g_robo_immortal"
            self._llm_controlled_tags = set()

        def is_voicecraft_controlled(self, unit: Any) -> bool:
            """M4: 判断单位是否被玩家 unit_claim 接管（不允许 sharpy manager 干预）。

            用法示例（未来 manager subclass 里 filter selection 时用）：
                units = [u for u in candidates if not bot.is_voicecraft_controlled(u)]
            """
            return unit.tag in self._llm_controlled_tags

        def _refresh_llm_controlled_roles(self) -> None:
            """M4: 每 step 重新声明 _llm_controlled_tags 里的单位为 Reserved。

            背景：sharpy UnitRoleManager.update() 在每帧末尾清空 had_task_set，
            下帧 update() 时未在 had_task_set 里的单位会被重置为 Idle/Gathering。
            解法：在 super().on_step() 之后、voicecraft 逻辑之前，把所有
            _llm_controlled_tags 重新 set_task(Reserved) + refresh_task()，
            保证 had_task_set 在当帧 update 前已登记。

            死亡单位：unit_cache.by_tag 返回 None → 跳过并从集合移除（防泄漏）。
            """
            tags: set[int] = getattr(self, "_llm_controlled_tags", set())
            if not tags:
                return
            try:
                from sharpy.managers.core.roles.unit_task import UnitTask

                dead_tags: set[int] = set()
                for tag in tags:
                    unit = self.knowledge.unit_cache.by_tag(tag)
                    if unit is None:
                        dead_tags.add(tag)
                        continue
                    # refresh_task 仅把 tag 加进 had_task_set，不改 role，
                    # 配合 set_task 使 update() 不会清掉这些单位的 Reserved role。
                    self.knowledge.roles.set_task(UnitTask.Reserved, unit)
                if dead_tags:
                    self._llm_controlled_tags -= dead_tags
                    logger.debug("llm_controlled_tags cleanup removed dead tags: %s", dead_tags)
            except Exception as exc:
                logger.warning("refresh_llm_controlled_roles failed: %s", exc)

        async def create_plan(self) -> BuildOrder:  # sharpy type; statically Any via overrides
            """M2+M3：IfElse 路由树，由 active_recipe 决定走哪个 sharpy dummy plan。

            流程：
            1. 从 strategy_library 收集所有有 sharpy_dummy_class 字段的策略
            2. 逐个 importlib.import_module + getattr 拿到 sharpy dummy class
            3. 调 dummy.create_plan()（可能 sync 或 async）拿到 BuildOrder
            4. 用 IfElse 嵌套组合：active_recipe 匹配哪个，就执行哪个 BuildOrder
            5. 任何 dummy import/实例化/create_plan 失败 → fallback 到空 BuildOrder + log warning

            lambda condition 引用 self.active_recipe，sharpy IfElse.execute() 每 step
            调 RequireCustom.check()，每次重新求值 → set_build 立即在下个 step 生效。
            """
            import importlib
            import inspect

            # --- 收集 strategies with sharpy_dummy_class ---
            # 支持 OpeningBuild / MidgameStance / LategameDoctrine（M2+M3 统一处理）
            from voicecraft.strategy.models import LategameDoctrine, MidgameStance, OpeningBuild

            if strategy_library is None:
                logger.warning("create_plan: no strategy_library, returning empty BuildOrder")
                return BuildOrder([])

            candidates: list[tuple[str, str]] = []  # [(recipe_id, "module:ClassName"), ...]
            for s in strategy_library.all_strategies():
                if (
                    isinstance(s, (OpeningBuild, MidgameStance, LategameDoctrine))
                    and s.sharpy_dummy_class
                ):
                    candidates.append((s.id, s.sharpy_dummy_class))

            if not candidates:
                logger.warning("create_plan: 没有 sharpy_dummy_class 策略，返回空 BuildOrder")
                return BuildOrder([])

            # --- 逐个 import + create_plan ---
            def _make_fallback_plan() -> BuildOrder:
                """import/实例化/create_plan 失败时的最小 fallback。"""
                from sc2.ids.unit_typeid import UnitTypeId

                try:
                    from sharpy.plans.acts.act_unit import ActUnit

                    return BuildOrder([ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14)])
                except Exception:
                    return BuildOrder([])

            # _VENDOR_SHARPY 的 dummies 子目录是 "dummies.protoss.xxx"
            # vendor/sharpy/ 已在 sys.path（_ensure_sharpy_on_path 在工厂函数入口调过）
            plans: dict[str, BuildOrder] = {}
            for recipe_id, dummy_spec in candidates:
                module_path, class_name = dummy_spec.rsplit(":", 1)
                try:
                    mod = importlib.import_module(module_path)
                    dummy_cls = getattr(mod, class_name)
                    dummy_inst = dummy_cls()
                    raw_plan = dummy_inst.create_plan()
                    # create_plan 可能是 sync（SkeletonBot）或 async（KnowledgeBot）
                    if inspect.isawaitable(raw_plan):
                        raw_plan = await raw_plan
                    plans[recipe_id] = raw_plan
                    logger.info("create_plan: loaded dummy %s for recipe %s", dummy_spec, recipe_id)
                except Exception as exc:
                    logger.warning(
                        "create_plan: failed to load dummy %s for recipe %s: %s — fallback",
                        dummy_spec,
                        recipe_id,
                        exc,
                    )
                    plans[recipe_id] = _make_fallback_plan()

            if not plans:
                return BuildOrder([])

            # --- 构建 IfElse 嵌套树 ---
            # 顺序：candidates 的顺序决定 IfElse 嵌套深度；最后一个作 else 兜底。
            # lambda 捕获 recipe_id 参数（避免 late-binding 坑：用默认参数绑定）
            from sharpy.plans.if_else import IfElse

            # 从最后一个往前折叠，构成嵌套 IfElse 树
            recipe_ids = [rid for rid, _ in candidates]
            # 兜底分支（最后一个 recipe 的 plan）
            result: Any = plans[recipe_ids[-1]]
            for rid in reversed(recipe_ids[:-1]):
                _rid = rid  # loop var capture
                result = IfElse(
                    lambda k, r=_rid: self.active_recipe == r,
                    plans[_rid],
                    result,
                )

            return BuildOrder(result)

        async def on_start(self) -> None:
            # KnowledgeBot.on_start() 初始化所有 Manager（含 roles / unit_cache 等）
            await super().on_start()

            self.facade = _SharpyFacade(self)
            self.director = director_factory(self.facade)

            # minimap builder（on_start 后 game_info / playable_area 才可访问）
            if minimap_callback is not None:
                from voicecraft.bot.minimap import MinimapBuilder

                self._minimap_builder = MinimapBuilder(self)

            # 注入 snapshot / event callback 到 director
            if snapshot_callback is not None and self.director is not None:
                self.director.set_snapshot_callback(snapshot_callback)
            if event_callback is not None and self.director is not None:
                self.director.set_event_callback(event_callback)

            # 状态 diff watcher
            if event_callback is not None:
                from voicecraft.bot.auto_combat.decision_watcher import DecisionWatcher

                self._decision_watcher = DecisionWatcher(event_callback)

            # 初始化 active_recipe + 把第一个 opening 落到 board.opening slot，
            # 让手机 UI 一进对局就显示当前宏观剧本（否则空着等玩家发指令才亮）。
            if strategy_library is not None:
                from voicecraft.strategy.models import OpeningBuild

                openings = [
                    s for s in strategy_library.all_strategies() if isinstance(s, OpeningBuild)
                ]
                if openings:
                    # 设 active_recipe 不依赖 director（create_plan() 在 KnowledgeBot.on_start
                    # 内被调用，此时 director 尚未构造）
                    self.active_recipe = openings[0].id

                    if self.director is not None:
                        from voicecraft.directives.types import StageKind

                        self.director.set_initial_strategy(
                            StageKind.OPENING, openings[0].id, float(self.time)
                        )

            if status_callback is not None:
                status_callback("in_game", "running", "")
                status_callback("playing", "running", "")

        async def on_step(self, iteration: int) -> None:
            # KnowledgeBot.on_step 跑 knowledge.update + execute（含所有 Manager tick）
            await super().on_step(iteration)

            # M4: 重新声明 LLM_CONTROLLED 单位的 Reserved role
            # 必须在 super().on_step() 后调（knowledge.unit_cache 已更新）
            self._refresh_llm_controlled_roles()

            # voicecraft down_q 消费
            if down_q is not None:
                try:
                    while True:
                        msg: dict[str, Any] = down_q.get_nowait()
                        msg_type = msg.get("type")
                        if msg_type == "command":
                            text = str(msg.get("text", ""))
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
                                self.facade.move_camera((float(target[0]), float(target[1])))
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

            # bot 自动决策 watcher（状态 diff → event 帧）
            if self._decision_watcher is not None:
                self._decision_watcher.tick(self, float(self.time))

            # step 末尾串行 await 暂存的 camera 调用（ADR 0008）
            if self.facade is not None:
                await self.facade.drain_pending_actions()

        async def on_unit_created(self, unit: Any) -> None:
            """单位创建事件。M1 无 voicecraft 逻辑，M4 加 LLM_CONTROLLED role 时再用。"""
            if hasattr(super(), "on_unit_created"):
                await super().on_unit_created(unit)

        async def on_unit_destroyed(self, unit_tag: int) -> None:
            """单位死亡事件。M4: 从 _llm_controlled_tags 移除死亡单位，防内存泄漏。"""
            # 先从 voicecraft 集合清除，再通知 KnowledgeBot（sharpy 需要 knowledge 更新）
            if unit_tag in self._llm_controlled_tags:
                self._llm_controlled_tags.discard(unit_tag)
                logger.info("unit_destroyed tag=%d removed from _llm_controlled_tags", unit_tag)
            await super().on_unit_destroyed(unit_tag)

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
