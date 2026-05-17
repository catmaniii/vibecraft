"""神族 bot：_VibeCraftProtossBot 继承 sharpy KnowledgeBot。

import 路径说明：vendor/sharpy/ 不在标准 src layout 下，
运行时通过 sys.path 注入让 `from sharpy.knowledges.knowledge_bot import KnowledgeBot`
可解析。注入只在真正 import 本模块时发生（lazy，单测时 fake_sharpy 先 mock sys.modules）。

设计参考：docs/plans/2026-05-16-sharpy-migration.md §1-4。

继承层次：_VibeCraftProtossBot → KnowledgeBot → SkeletonBot → BotAI
"""

from __future__ import annotations

import asyncio
import logging
import queue as queue_module
import sys
from pathlib import Path
from typing import Any

from vibecraft.bot.event_bus import Event, EventBus, EventKind
from vibecraft.bot.named_spot import NamedSpotRegistry
from vibecraft.bot.watchdog import HangWatchdog

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# EventBus publishing helpers (P1.0b)
# module-level 函数方便单测 mock bot_self + bus，不需要起完整 sharpy。
# 每个 helper 对应一个 python-sc2 lifecycle hook。
# -----------------------------------------------------------------------


def _publish_unit_created(bot_self: Any, unit: Any) -> None:
    owner = "own" if getattr(unit, "alliance", 0) == 1 else "enemy"
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.UNIT_CREATED,
            ts=float(bot_self.time),
            payload={"unit_tag": unit.tag, "unit_obj": unit},
            owner=owner,
            unit_tag=unit.tag,
            unit_type=str(unit.type_id),
            position=(float(unit.position.x), float(unit.position.y)),
        )
    )


def _publish_unit_destroyed(bot_self: Any, unit_tag: int) -> None:
    # 死亡时 unit 对象可能已 invalid，从 cached dicts 找（getattr 兜底：旧测试用 object.__new__ 绕过 __init__）
    enemy_dict: dict[int, Any] = getattr(bot_self, "_enemy_units_dict", {})
    own_dict: dict[int, Any] = getattr(bot_self, "_own_units_dict", {})
    unit = enemy_dict.get(unit_tag) or own_dict.get(unit_tag)
    owner: str | None = None
    unit_type: str | None = None
    position: tuple[float, float] | None = None
    area: str | None = None
    if unit is not None:
        owner = "own" if getattr(unit, "alliance", 0) == 1 else "enemy"
        unit_type = str(unit.type_id)
        position = (float(unit.position.x), float(unit.position.y))
        # P5.D: area inference via NamedSpotRegistry reverse lookup
        named_spots = getattr(bot_self, "named_spots", None)
        if named_spots is not None:
            try:
                area = named_spots.closest_named_spot(unit.position, bot_self)
            except Exception:
                area = None
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.UNIT_DESTROYED,
            ts=float(bot_self.time),
            payload={"unit_tag": unit_tag, "unit_obj": unit, "area": area},
            owner=owner,
            unit_tag=unit_tag,
            unit_type=unit_type,
            position=position,
        )
    )


def _publish_unit_type_changed(bot_self: Any, unit: Any, previous_type: Any) -> None:
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.UNIT_TYPE_CHANGED,
            ts=float(bot_self.time),
            payload={
                "unit_tag": unit.tag,
                "previous_type": str(previous_type),
                "current_type": str(unit.type_id),
            },
            owner="own" if getattr(unit, "alliance", 0) == 1 else "enemy",
            unit_tag=unit.tag,
            unit_type=str(unit.type_id),
        )
    )


def _publish_building_started(bot_self: Any, unit: Any) -> None:
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.BUILDING_STARTED,
            ts=float(bot_self.time),
            payload={"unit_tag": unit.tag, "unit_obj": unit},
            owner="own",  # 只有自方触发
            unit_tag=unit.tag,
            unit_type=str(unit.type_id),
            position=(float(unit.position.x), float(unit.position.y)),
        )
    )


def _publish_building_complete(bot_self: Any, unit: Any) -> None:
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.BUILDING_COMPLETE,
            ts=float(bot_self.time),
            payload={"unit_tag": unit.tag, "unit_obj": unit},
            owner="own",
            unit_tag=unit.tag,
            unit_type=str(unit.type_id),
            position=(float(unit.position.x), float(unit.position.y)),
        )
    )


def _publish_upgrade_complete(bot_self: Any, upgrade: Any) -> None:
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.UPGRADE_COMPLETE,
            ts=float(bot_self.time),
            payload={"upgrade_id": str(upgrade)},
            owner="own",
        )
    )


def _publish_unit_took_damage(bot_self: Any, unit: Any, amount: Any) -> None:
    # P5.D: area inference via NamedSpotRegistry reverse lookup
    area: str | None = None
    named_spots = getattr(bot_self, "named_spots", None)
    if named_spots is not None:
        try:
            area = named_spots.closest_named_spot(unit.position, bot_self)
        except Exception:
            area = None
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.UNIT_TOOK_DAMAGE,
            ts=float(bot_self.time),
            payload={"unit_tag": unit.tag, "amount": float(amount), "area": area},
            owner="own",  # python-sc2 只通知自方
            unit_tag=unit.tag,
            unit_type=str(unit.type_id),
            position=(float(unit.position.x), float(unit.position.y)),
        )
    )


def _publish_enemy_unit_entered_vision(bot_self: Any, unit: Any) -> None:
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.ENEMY_UNIT_ENTERED_VISION,
            ts=float(bot_self.time),
            payload={"unit_tag": unit.tag, "unit_obj": unit},
            owner="enemy",
            unit_tag=unit.tag,
            unit_type=str(unit.type_id),
            position=(float(unit.position.x), float(unit.position.y)),
        )
    )


def _publish_enemy_unit_left_vision(bot_self: Any, unit_tag: int) -> None:
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.ENEMY_UNIT_LEFT_VISION,
            ts=float(bot_self.time),
            payload={"unit_tag": unit_tag},
            owner="enemy",
            unit_tag=unit_tag,
        )
    )


def _make_event_publisher() -> None:
    """占位：让单测可以 from vibecraft.bot.auto_combat.protoss.bot import _make_event_publisher 不报错。

    plan P1.0b 测试 fixture 里有 _make_event_publisher 的 import，实际逻辑由各
    _publish_xxx 函数承担，此处仅作标记性导出。
    """

# -----------------------------------------------------------------------
# vendor path 注入（单测先 mock sys.modules 绕开）
# -----------------------------------------------------------------------
_VENDOR_SHARPY = Path(__file__).parents[5] / "vendor" / "sharpy"


def _ensure_sharpy_on_path() -> None:
    """把 vendor/sharpy 加进 sys.path + 修正 config.get_config 路径（幂等）。

    sharpy 所有模块（sharpy.*、config、bot_loader 等）都在 vendor/sharpy/ 下，
    直接把该目录加进 sys.path 即可解析。

    config.get_config monkey-patch：sharpy SkeletonBot.__init__ 调
    get_config()，原实现用相对路径 "config.ini"，依赖 cwd == vendor/sharpy。
    vibecraft 子进程 cwd 是 repo 根，找不到 → ValueError。
    必须在 sharpy.knowledges.* 被 import 之前 patch（那些模块用
    `from config import get_config` 把名字 bind 到 module top-level）。

    无 cython_extensions / CombatBehavior 漂移：sharpy 的 pure Python 实现
    不需要 Aristaeus 那套 patch。详见 vendor/sharpy/ATTRIBUTION.md（M1 新建）。
    """
    target = str(_VENDOR_SHARPY)
    if target not in sys.path:
        sys.path.insert(0, target)

    # monkey-patch get_config 用 vendor/sharpy 绝对路径（幂等）
    from configparser import ConfigParser

    import config as _sharpy_config

    if getattr(_sharpy_config.get_config, "_vibecraft_patched", False):  # type: ignore[attr-defined]
        return

    def _patched_get_config(local: bool = True) -> ConfigParser:
        paths = [_VENDOR_SHARPY / "config.ini"]
        if local:
            paths.append(_VENDOR_SHARPY / "config-local.ini")
        if any(p.is_file() for p in paths):
            cfg = ConfigParser()
            cfg.read([str(p) for p in paths])
            return cfg
        raise ValueError(f"sharpy config 找不到: {paths}")
        # 注:不 override game_step_size。sharpy SkeletonBot.on_step 在
        # realtime mode 下会自动设 client.game_step=1(detect 同 game_loop 被调两次)
        # 我们的 on_step 在 client.game_step=1 频率被调(~0.045s),多路复用按这个
        # 频率分发(view 高频每 step / sharpy 低频按 ratio)

    _patched_get_config._vibecraft_patched = True  # type: ignore[attr-defined]
    _sharpy_config.get_config = _patched_get_config  # type: ignore[attr-defined]


# -----------------------------------------------------------------------
# 工厂函数：返回 _VibeCraftProtossBot 类
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
    保持 _VibeCraftProtossBot 与 sharpy_adapter.py 之间的解耦。
    """
    _ensure_sharpy_on_path()

    try:
        from sharpy.knowledges.knowledge_bot import KnowledgeBot
        from sharpy.plans import BuildOrder
    except ImportError as e:
        # 带原因 + 重复 repr：log 默认只打 str(e),容易把真因丢了
        raise ImportError(
            f"无法 import sharpy.knowledges.knowledge_bot（真因: {e!r}）；"
            "确认 vendor/sharpy/ 已 clone 且 python-sc2 已装。"
        ) from e

    # facade 类（在闭包内定义，持有 bot 引用）
    from vibecraft.bot.facade import BotState, UnitRole

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
            """把 vibecraft UnitRole 映射到 sharpy UnitTask 并设置。

            sharpy UnitRoleManager.set_task 接 Unit 对象（不接 tag），
            需先用 cache.by_tag 取 unit；找不到时 log warn，不崩。

            M4: LLM_CONTROLLED → 同时写入 bot._llm_controlled_tags，确保每 step
            通过 refresh_llm_controlled_roles() 持久化 Reserved 状态。
            单位死亡后 tag 不在 cache，refresh 时自动跳过（cache.by_tag 返回 None）。
            """
            try:
                from vibecraft.bot.auto_combat.common import build_role_map

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
            # 已造 + 在造的所有建筑名(大写,与 SC2 UnitTypeId.name 一致)
            try:
                built = frozenset(str(s.type_id.name).upper() for s in b.structures)
            except Exception:
                built = frozenset()
            return BotState(
                game_time=float(b.time),
                minerals=int(b.minerals),
                gas=int(b.vespene),
                supply_used=int(b.supply_used),
                supply_cap=int(b.supply_cap),
                expansion_count=len(b.townhalls),
                army_summary={},  # M1 占位
                enemy_summary={},
                structures_built=built,
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

    class _VibeCraftProtossBot(KnowledgeBot):  # type: ignore[misc]
        """vibecraft 神族 bot：sharpy KnowledgeBot + vibecraft 指挥层。

        继承层次：_VibeCraftProtossBot → KnowledgeBot → SkeletonBot → BotAI

        on_start 顺序：
          1. await super().on_start()（KnowledgeBot 初始化所有 Manager）
          2. 构造 _SharpyFacade / director / minimap_builder
          3. 注入 snapshot / event callback 到 director
          4. 推 set_initial_strategy（让手机 UI 一进对局就显示当前剧本）
          5. 推 status_callback

        on_step 顺序：
          1. await super().on_step(iteration)（KnowledgeBot.on_step：update + execute）
          2. M4: refresh_llm_controlled_roles()（每帧重声明 Reserved，防 had_task_set 清空）
          3. vibecraft down_q 消费 + minimap 推送 + director.on_tick
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
        _hang_watchdog: HangWatchdog | None = None
        # 当前剧本名：IfElse 路由树每 step 检查此值；set_build 写入后下个 step 立即生效。
        # 默认 "1g_robo_immortal"（opening fallback），on_start 会根据 strategy_library 重设。
        active_recipe: str = "1g_robo_immortal"
        # M4: LLM_CONTROLLED 单位的 tag 集合（跨 step 持久化）
        _llm_controlled_tags: set[int]
        # tactics 节流
        _tactics_last_s: float = 0.0

        # ============================================================
        # 多路复用 step 设计(view 与 bot 通信通过独立 channel 解耦)
        # ============================================================
        # 物理约束:python-sc2 client 是单 socket ws,所有调用必须在 on_step 内串行
        # sharpy 在 realtime mode 自动设 client.game_step=1 → on_step ~0.045s/次
        #
        # 两个逻辑 channel,共享同一个 on_step 物理执行点:
        #
        # 1. ViewChannel (每 on_step 触发,~0.045s):
        #    - 消费 down_q 里的 view_move 帧 → facade.move_camera 暂存
        #    - 末尾 await facade.drain_pending_actions() → 真正发 SC2 client.move_camera
        #    - minimap 帧推送(自带节流计数)
        #
        # 2. BotChannel (每 _SHARPY_STEP_RATIO 次触发,~0.225s 一次):
        #    - super().on_step(remapped_iter):sharpy manager update + plan execute
        #    - 关键:传 remap 的 iter(从 0 开始),让 sharpy 看到自己 namespace
        #      (BuildingSolver `if iteration==0` 等条件依赖)
        #    - _refresh_llm_controlled_roles / director / tactics 等下层逻辑
        #
        # 扩展:加新 channel(如 metric 推送 / event ack)只需在 on_step 加分支
        # ============================================================
        _voice_step_count: int = 0
        _sharpy_iteration: int = 0
        _SHARPY_STEP_RATIO: int = 5

        def __init__(self) -> None:
            super().__init__("VibeCraft Protoss")
            self._cmd_tasks = []
            self._minimap_tick_count = 0
            self._minimap_builder = None
            self._decision_watcher = None
            self._hang_watchdog = None
            self.active_recipe = "1g_robo_immortal"
            self._llm_controlled_tags = set()
            self._tactics_last_s = 0.0
            self._voice_step_count = 0
            self._sharpy_iteration = 0
            # P1.0b: EventBus — vibecraft 内部 pub/sub，lifecycle hook → subscriber 分发
            self.event_bus = EventBus()
            # P1.0b: 单位状态缓存（on_unit_destroyed 时 unit 对象可能已 invalid，
            # 从这里取 owner/type。sharpy knowledge.unit_cache 是权威源，这是补充 lookup。）
            self._enemy_units_dict: dict[int, Any] = {}
            self._own_units_dict: dict[int, Any] = {}
            # P5.C: named_spot registry — task_monitor vision checker 通过 bot.named_spots.resolve() 解析
            self.named_spots = NamedSpotRegistry()

        def _update_tactics_throttled(self, now: float) -> None:
            """每 ~1s 算一次 stance,写到 director._tactics。

            stance 优先级(高到低):
              - expanding: 有 Nexus pending(正在 warp in)
              - attacking: 我方军队 > 6 单位且重心离最近基地 > 25 距离(在敌方半场)
              - defending: 敌方单位在我方任意基地 < 20 距离内可见
              - scouting: 有 probe 在主基地 > 50 距离
              - sustaining: 都没匹配

            label/reason 直接给 PWA 一行展示。
            """
            if now - self._tactics_last_s < 1.0:
                return
            self._tactics_last_s = now
            if self.director is None:
                return
            try:
                from vibecraft.bot.director import Tactics

                stance, label, reason = self._compute_stance()
                self.director._tactics = Tactics(stance=stance, label=label, reason=reason)
            except Exception as exc:
                logger.debug("tactics_compute_failed: %s", exc)

        def _compute_stance(self) -> tuple[str, str, str]:
            """返回 (stance, label, reason)。纯 rule-based,看 sharpy 已有状态。

            没有"意图"概念 → 用观察到的事实倒推:army 在哪 / 谁打谁 / 有没有 Nexus pending。
            """
            from sc2.ids.unit_typeid import UnitTypeId

            townhalls = self.townhalls
            home = townhalls.first.position if townhalls else self.start_location

            # expanding
            pending_nexus = self.structures(UnitTypeId.NEXUS).not_ready.amount
            if pending_nexus > 0:
                return (
                    "expanding",
                    f"🏗️ 开矿中(+{pending_nexus} 矿)",
                    f"已有 {townhalls.amount} 矿,扩 {townhalls.amount + pending_nexus} 矿",
                )

            # 自家 army(不含探机/Observer)
            army = self.units.exclude_type(
                {UnitTypeId.PROBE, UnitTypeId.OBSERVER, UnitTypeId.WARPPRISM}
            )

            # defending:敌人 < 25 距离我方任何基地
            for th in townhalls:
                enemies_near = self.enemy_units.closer_than(25.0, th)
                if enemies_near.amount >= 2:
                    return (
                        "defending",
                        f"🛡️ 守家({enemies_near.amount} 敌单位逼近)",
                        f"{th.type_id.name} 附近 {enemies_near.amount} 敌单位",
                    )

            # attacking:army > 6 且重心离家 > 25
            if army.amount >= 6:
                center = army.center
                dist = center.distance_to(home)
                if dist > 25.0:
                    return (
                        "attacking",
                        f"⚔️ 进攻中({army.amount} 单位出门)",
                        f"军队重心离家 {int(dist)} 距离",
                    )

            # scouting:有 probe 离家 > 50
            scouts = [p for p in self.units(UnitTypeId.PROBE) if p.distance_to(home) > 50.0]
            if scouts:
                return (
                    "scouting",
                    f"🔍 探路({len(scouts)} 探机外出)",
                    "探机在敌方区域",
                )

            # sustaining
            return ("sustaining", "⚙️ 运营中", f"{townhalls.amount} 矿, {army.amount} 兵")

        def is_vibecraft_controlled(self, unit: Any) -> bool:
            """M4: 判断单位是否被玩家 unit_claim 接管（不允许 sharpy manager 干预）。

            用法示例（未来 manager subclass 里 filter selection 时用）：
                units = [u for u in candidates if not bot.is_vibecraft_controlled(u)]
            """
            return unit.tag in self._llm_controlled_tags

        def _refresh_llm_controlled_roles(self) -> None:
            """M4: 每 step 重新声明 _llm_controlled_tags 里的单位为 Reserved。

            背景：sharpy UnitRoleManager.update() 在每帧末尾清空 had_task_set，
            下帧 update() 时未在 had_task_set 里的单位会被重置为 Idle/Gathering。
            解法：在 super().on_step() 之后、vibecraft 逻辑之前，把所有
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
            from vibecraft.strategy.models import LategameDoctrine, MidgameStance, OpeningBuild

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

            # --- Sustain 兜底 plan(active_recipe="sustain"或没人匹配时 fallback)---
            # vibecraft 自带,不是 yaml 剧本(玩家不能 voice 切"sustain"),
            # 由 cancel_strategy directive / voice 取消时由 facade.set_build 切到这里
            try:
                from vibecraft.bot.auto_combat.protoss.plans.sustain import Sustain

                sustain_inst = Sustain()
                sustain_plan = await sustain_inst.create_plan()
                logger.info("create_plan: Sustain 兜底 plan 装载成功")
            except Exception as exc:
                logger.warning("create_plan: Sustain 装载失败,用空 BuildOrder 兜底: %s", exc)
                sustain_plan = _make_fallback_plan()

            # --- 通用层:所有 recipe 共享的行为 ---
            # ScoutWorker:派 1 农民探路,保命优先,与 IfElse 路由独立(任何 plan 都跑)
            # 容错:单测里 sharpy 是 fake,import 会失败,fallback 为 None
            scout_act: Any = None
            try:
                from vibecraft.bot.auto_combat.protoss.plans.scout_worker import (
                    ScoutWorker,
                )

                scout_act = ScoutWorker()
            except Exception as exc:
                logger.warning("ScoutWorker 装载失败: %s — 跳过通用探路层", exc)

            def _wrap(plan: Any) -> Any:
                """把 scout_act + plan 包成 BuildOrder list;scout_act 为 None 时只返 plan。"""
                if scout_act is None:
                    return BuildOrder(plan)
                return BuildOrder([scout_act, plan])

            if not plans:
                return _wrap(sustain_plan)

            # --- 构建 IfElse 嵌套树 ---
            # 顺序：candidates 的顺序决定 IfElse 嵌套深度；
            # 最深 else 是 sustain_plan(active_recipe 都不匹配时降级 → 不主动出门)
            # lambda 捕获 recipe_id 参数（避免 late-binding 坑：用默认参数绑定）
            from sharpy.plans.if_else import IfElse

            recipe_ids = [rid for rid, _ in candidates]
            # 兜底分支:Sustain plan(玩家 cancel 后 / active_recipe="sustain" 时)
            result: Any = sustain_plan
            for rid in reversed(recipe_ids):
                _rid = rid  # loop var capture
                result = IfElse(
                    lambda k, r=_rid: self.active_recipe == r,
                    plans[_rid],
                    result,
                )

            # 顶层 BuildOrder:ScoutWorker(通用) + IfElse 路由(具体 plan)
            return _wrap(result)

        async def on_start(self) -> None:
            # KnowledgeBot.on_start() 初始化所有 Manager（含 roles / unit_cache 等）
            await super().on_start()

            self.facade = _SharpyFacade(self)
            self.director = director_factory(self.facade)

            # P5.C: bot backref — director 通过 _bot 把 self 传给 task_monitor.tick
            if self.director is not None:
                self.director._bot = self

            # P3.2: 把 event_bus 注入 director，启动 task_monitor
            if self.director is not None and hasattr(self, "event_bus"):
                self.director.setup_task_monitor(self.event_bus)

            # minimap builder（on_start 后 game_info / playable_area 才可访问）
            if minimap_callback is not None:
                from vibecraft.bot.minimap import MinimapBuilder

                self._minimap_builder = MinimapBuilder(self)

            # 注入 snapshot / event callback 到 director
            if snapshot_callback is not None and self.director is not None:
                self.director.set_snapshot_callback(snapshot_callback)
            if event_callback is not None and self.director is not None:
                self.director.set_event_callback(event_callback)

            # 状态 diff watcher
            if event_callback is not None:
                from vibecraft.bot.auto_combat.decision_watcher import DecisionWatcher

                self._decision_watcher = DecisionWatcher(event_callback)

            # 初始化 active_recipe:bot 从所有 opening 剧本里随机挑一个,
            # set_by=BOT_INTERNAL → PWA badge 显示 "⚙️ bot 默认"。
            # 玩家随时可 voice 切其他剧本(VOICE > BOT_INTERNAL 优先级)。
            if strategy_library is not None:
                import os
                import random

                from vibecraft.strategy.models import OpeningBuild

                openings = [
                    s for s in strategy_library.all_strategies() if isinstance(s, OpeningBuild)
                ]
                if openings:
                    # env VIBECRAFT_FORCE_INITIAL_OPENING=<id> 强制 default(测试用)
                    forced_id = os.environ.get("VIBECRAFT_FORCE_INITIAL_OPENING")
                    chosen = None
                    if forced_id:
                        chosen = next((o for o in openings if o.id == forced_id), None)
                        if chosen is None:
                            logger.warning(
                                "forced initial opening %r 不在 catalog,回退 random", forced_id
                            )
                    if chosen is None:
                        chosen = random.choice(openings)
                    # active_recipe 不依赖 director(create_plan() 在 KnowledgeBot.on_start
                    # 内被调用,此时 director 尚未构造,所以 active_recipe 是 plan 路由的真理源)
                    self.active_recipe = chosen.id
                    logger.info("bot 选定开局剧本: %s (%s)", chosen.id, chosen.display_name_zh)

                    if self.director is not None:
                        from vibecraft.directives.types import StageKind

                        self.director.set_initial_strategy(
                            StageKind.OPENING, chosen.id, float(self.time)
                        )

            if status_callback is not None:
                status_callback("in_game", "running", "")
                status_callback("playing", "running", "")

            # 启 hang watchdog（VIBECRAFT_DISABLE_HANG_WATCHDOG=1 关掉，e.g. 调试时）
            import os as _os

            if not _os.environ.get("VIBECRAFT_DISABLE_HANG_WATCHDOG"):
                def _on_hang() -> None:
                    if status_callback is not None:
                        status_callback(
                            "crashed", "error", "hang_watchdog: bot.time stuck"
                        )

                self._hang_watchdog = HangWatchdog(
                    get_bot_time=lambda: float(self.time),
                    on_hang=_on_hang,
                )
                self._hang_watchdog.start()

        async def on_step(self, iteration: int) -> None:
            """多路复用 step:view channel(高频)+ bot channel(低频,remap iteration)。

            sharpy realtime mode 自动设 game_step=1 → on_step ~0.045s/次。
            我们在这个频率上做逻辑多路复用,bot channel 节流到原 sharpy 节奏。

            关键设计:sharpy 看到的 iteration 是 vibecraft 重映射的(_sharpy_iteration),
            从 0 开始每次 sharpy beat +1。否则 sharpy 内部 `if iteration == 0` 类
            一次性 init 条件(如 BuildingSolver.solve_grid)永远不触发 → bot 不造水晶。

            扩展:加新通道(metric/event push 等)按延迟需求挂高频或低频。
            """
            self._voice_step_count += 1
            now_s = float(self.time)

            # ---- ViewChannel(每 step,~0.045s)----
            # 只处理与视角强相关的消息(view_move),其它消息(command/confirm)等
            # bot channel 时再批处理,避免在 sharpy state 未更新时误调 director
            await self._tick_view_channel(now_s)

            # ---- BotChannel(每 _SHARPY_STEP_RATIO step,~0.225s)----
            if self._voice_step_count % self._SHARPY_STEP_RATIO == 0:
                await self._tick_bot_channel(iteration, now_s)

        async def _tick_view_channel(self, now_s: float) -> None:
            """高频通道(每 step,~0.045s):input 消费 + view 反馈。

            包含:
            - 所有 down_q 消息(view_move 立即暂存 / command 创 async task / confirm 调 director)
            - minimap 帧推送(节流 N=2 → ~0.09s/帧 ≈ 11Hz 接近实时)
            - 末尾 await facade.drain_pending_actions(view_move 真正发到 SC2,延迟 ≤ 0.045s)

            消息处理本身都是轻量(create_task / 暂存 / director 接口调用),不阻塞 step。
            director 内部 board 仲裁基于 wall-time,不依赖 sharpy state。
            """
            if down_q is not None:
                try:
                    while True:
                        msg: dict[str, Any] = down_q.get_nowait()
                        msg_type = msg.get("type")
                        if msg_type == "command":
                            text = str(msg.get("text", ""))
                            if self.director is not None:
                                task = asyncio.create_task(
                                    run_command_with_echo_fn(
                                        self.director, text, now_s, echo_callback
                                    ),
                                    name=f"cmd-{now_s:.3f}",
                                )
                                self._cmd_tasks.append(task)
                                task.add_done_callback(self._on_cmd_task_done)
                        elif msg_type == "view_move":
                            target = msg.get("target_point", [0.0, 0.0])
                            if self.facade is not None:
                                self.facade.move_camera((float(target[0]), float(target[1])))
                        elif msg_type == "confirm_recommendation":
                            if self.director is not None:
                                self.director.confirm_recommendation(now_s)
                        elif msg_type == "dismiss_recommendation":
                            if self.director is not None:
                                self.director.dismiss_recommendation()
                        elif msg_type == "confirm_force_strategy":
                            if self.director is not None:
                                self.director.confirm_force_strategy(now_s)
                        elif msg_type == "cancel_force_strategy":
                            if self.director is not None:
                                self.director.cancel_force_strategy()
                        elif msg_type == "revoke_directive":
                            # P2: 玩家撤销 standing order 或 production override（统一入口）
                            directive_id = msg.get("directive_id")
                            if directive_id and self.director is not None:
                                self.director.revoke_directive(directive_id, now_s)
                        elif msg_type == "leave":
                            logger.info("bot 收到 leave 信号，等待 on_end")
                except queue_module.Empty:
                    pass

            # minimap 帧推送(节流 N=2 → ~0.09s/帧 ≈ 11Hz,接近实时)
            if minimap_callback is not None and self._minimap_builder is not None:
                self._minimap_tick_count += 1
                if self._minimap_tick_count >= 2:
                    self._minimap_tick_count = 0
                    try:
                        frame = self._minimap_builder.build(now_s)
                        minimap_callback(frame)
                    except Exception as exc:
                        logger.warning("minimap_build_failed: %s", exc)

            # drain camera(ADR 0008):view_move 延迟 ≤ ~0.045s 接近实时
            if self.facade is not None:
                await self.facade.drain_pending_actions()

            # vibecraft 慢逻辑放这(本身已 throttle,每 step 调只多 if check):
            # director 决定 board commit / push snapshot,需要每 step 推进 wall-time
            self._update_tactics_throttled(now_s)
            if self.director is not None:
                self.director.on_tick(now=now_s)
            if self._decision_watcher is not None:
                self._decision_watcher.tick(self, now_s)

        async def _tick_bot_channel(self, py_sc2_iteration: int, now_s: float) -> None:
            """低频通道(每 _SHARPY_STEP_RATIO step,~0.225s):只跑 sharpy 主流程。

            sharpy super().on_step 调 knowledge.update(iteration) + execute(plan)。
            传 remap iter 让 sharpy 看到自己 namespace(0,1,2,...),
            保证 BuildingSolver 的 `if iteration == 0` 首次 init 条件命中。
            """
            await super().on_step(self._sharpy_iteration)
            self._sharpy_iteration += 1
            self._refresh_llm_controlled_roles()

        async def on_unit_created(self, unit: Any) -> None:
            """单位创建事件。P1.0b: publish UNIT_CREATED event 到 EventBus。"""
            _publish_unit_created(self, unit)
            # 更新 own/enemy 单位缓存（供 on_unit_destroyed lookup 用）
            if getattr(unit, "alliance", 0) == 1:
                self._own_units_dict[unit.tag] = unit
            else:
                self._enemy_units_dict[unit.tag] = unit
            if hasattr(super(), "on_unit_created"):
                await super().on_unit_created(unit)

        async def on_unit_destroyed(self, unit_tag: int) -> None:
            """单位死亡事件。P1.0b: publish UNIT_DESTROYED + M4 _llm_controlled_tags 清理。"""
            # publish 在前（此时 cached dict 还有这个 unit）
            # guard: 旧测试 object.__new__ 绕过 __init__，event_bus 可能未初始化
            if hasattr(self, "event_bus"):
                _publish_unit_destroyed(self, unit_tag)
            # M4: 从 _llm_controlled_tags 移除死亡单位，防内存泄漏（保留已有逻辑）
            if unit_tag in self._llm_controlled_tags:
                self._llm_controlled_tags.discard(unit_tag)
                logger.info("unit_destroyed tag=%d removed from _llm_controlled_tags", unit_tag)
            # 清理单位缓存
            if hasattr(self, "_own_units_dict"):
                self._own_units_dict.pop(unit_tag, None)
            if hasattr(self, "_enemy_units_dict"):
                self._enemy_units_dict.pop(unit_tag, None)
            await super().on_unit_destroyed(unit_tag)

        async def on_unit_type_changed(self, unit: Any, previous_type: Any) -> None:
            """单位类型变化（如 Gateway → Warpgate）。P1.0b: publish UNIT_TYPE_CHANGED。"""
            _publish_unit_type_changed(self, unit, previous_type)
            if hasattr(super(), "on_unit_type_changed"):
                await super().on_unit_type_changed(unit, previous_type)

        async def on_building_construction_started(self, unit: Any) -> None:
            """建筑开始建造。P1.0b: publish BUILDING_STARTED。"""
            _publish_building_started(self, unit)
            if hasattr(super(), "on_building_construction_started"):
                await super().on_building_construction_started(unit)

        async def on_building_construction_complete(self, unit: Any) -> None:
            """建筑建造完成。P1.0b: publish BUILDING_COMPLETE。"""
            _publish_building_complete(self, unit)
            if hasattr(super(), "on_building_construction_complete"):
                await super().on_building_construction_complete(unit)

        async def on_upgrade_complete(self, upgrade: Any) -> None:
            """科技研究完成。P1.0b: publish UPGRADE_COMPLETE。"""
            _publish_upgrade_complete(self, upgrade)
            if hasattr(super(), "on_upgrade_complete"):
                await super().on_upgrade_complete(upgrade)

        async def on_unit_took_damage(self, unit: Any, amount_damage_taken: Any) -> None:
            """自方单位受伤。P1.0b: publish UNIT_TOOK_DAMAGE。"""
            _publish_unit_took_damage(self, unit, amount_damage_taken)
            if hasattr(super(), "on_unit_took_damage"):
                await super().on_unit_took_damage(unit, amount_damage_taken)

        async def on_enemy_unit_entered_vision(self, unit: Any) -> None:
            """敌方单位进入视野。P1.0b: publish ENEMY_UNIT_ENTERED_VISION。"""
            _publish_enemy_unit_entered_vision(self, unit)
            # 加入 enemy 缓存
            self._enemy_units_dict[unit.tag] = unit
            if hasattr(super(), "on_enemy_unit_entered_vision"):
                await super().on_enemy_unit_entered_vision(unit)

        async def on_enemy_unit_left_vision(self, unit_tag: int) -> None:
            """敌方单位离开视野。P1.0b: publish ENEMY_UNIT_LEFT_VISION。"""
            _publish_enemy_unit_left_vision(self, unit_tag)
            if hasattr(super(), "on_enemy_unit_left_vision"):
                await super().on_enemy_unit_left_vision(unit_tag)

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
            if self._hang_watchdog is not None:
                self._hang_watchdog.stop()
                self._hang_watchdog = None
            if self._cmd_tasks:
                await asyncio.gather(*self._cmd_tasks, return_exceptions=True)
                self._cmd_tasks.clear()
            if status_callback is not None:
                status_callback("ended", "idle", "")

    return _VibeCraftProtossBot
