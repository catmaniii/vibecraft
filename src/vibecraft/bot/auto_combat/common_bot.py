"""VibeCraftBotBase：三族 bot 共享的 lifecycle / 多路复用 / EventBus 基类。

设计参考：docs/plans/2026-05-18-zerg-terran-bot-design.md §3.3（A 抽象基类 + B 工厂函数）。

继承层次：
    _VibeCraftProtossBot / _VibeCraftZergBot / _VibeCraftTerranBot
        → VibeCraftBotBase
        → sharpy KnowledgeBot

race-agnostic 部分（本文件）：
    - lifecycle hook 转发（11 个 _publish_xxx helper）
    - EventBus 初始化
    - down_q 消费 + camera drain + minimap 推送
    - tactics 节流 + hang watchdog
    - refresh_llm_controlled_roles / is_vibecraft_controlled
    - _SharpyFacadeBase 类

race-specific（子类实现）：
    - EXCLUDE_FROM_ARMY ClassVar（set[UnitTypeId]）
    - DEFAULT_OPENING_ID ClassVar（str）
    - create_plan() → BuildOrder
"""

from __future__ import annotations

import asyncio
import logging
import queue as queue_module
import sys
from pathlib import Path
from typing import Any, ClassVar

from vibecraft.bot.event_bus import Event, EventBus, EventKind
from vibecraft.bot.named_spot import NamedSpotRegistry
from vibecraft.bot.watchdog import HangWatchdog

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# EventBus publishing helpers（race-agnostic，三族共享）
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
            owner="own",
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
    upgrade_name = getattr(upgrade, "name", None) or str(upgrade)
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.UPGRADE_COMPLETE,
            ts=float(bot_self.time),
            payload={"upgrade_id": upgrade_name},
            owner="own",
        )
    )


def _publish_unit_took_damage(bot_self: Any, unit: Any, amount: Any) -> None:
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
            owner="own",
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


def _make_event_publisher_placeholder() -> None:
    """占位：让单测可以 from vibecraft.bot.auto_combat.protoss.bot import _make_event_publisher 不报错。

    plan P1.0b 测试 fixture 里有 _make_event_publisher 的 import，实际逻辑由各
    _publish_xxx 函数承担，此处仅作标记性导出。
    """


# -----------------------------------------------------------------------
# vendor path 注入（与 protoss/bot.py 保持一致；两文件都可独立调用）
# -----------------------------------------------------------------------
_VENDOR_SHARPY = Path(__file__).parents[4] / "vendor" / "sharpy"


def _ensure_sharpy_on_path() -> None:
    """把 vendor/sharpy 加进 sys.path + 修正 config.get_config 路径（幂等）。"""
    target = str(_VENDOR_SHARPY)
    if target not in sys.path:
        sys.path.insert(0, target)

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

    _patched_get_config._vibecraft_patched = True  # type: ignore[attr-defined]
    _sharpy_config.get_config = _patched_get_config  # type: ignore[attr-defined]


# -----------------------------------------------------------------------
# _SharpyFacadeBase：Sc2Facade 的 sharpy 实现（race-agnostic 部分）
# -----------------------------------------------------------------------


def _make_sharpy_facade_base_class() -> type:
    """懒加载：在 sharpy 已注入 sys.path 后才 import BotState/UnitRole。"""
    from vibecraft.bot.facade import BotState, UnitRole

    def _log_move_camera_done(task: Any) -> None:
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                logger.error("move_camera_task_failed: %s", exc, exc_info=exc)

    class _SharpyFacadeBase:
        """Sc2Facade 的 sharpy 实现（三族共享基类）。

        camera 操作暂存模式（ADR 0008）：move_camera / follow_unit **不直接**发协议，
        只暂存最新目标点。on_step 末尾调 drain_pending_actions() 在 step await 链内串行发出。

        M4: LLM_CONTROLLED role 隔离。set_unit_role(tag, LLM_CONTROLLED) 同时写入
        bot._llm_controlled_tags，每 step 开头 refresh_llm_controlled_roles() 重新声明
        Reserved role，防止 sharpy UnitRoleManager.update() 每帧清空 had_task_set 后丢失状态。
        """

        def __init__(self, bot: Any) -> None:
            self.bot = bot
            self._pending_camera_point: tuple[float, float] | None = None

        # ---- 写 -------------------------------------------------------

        def set_build(self, build_name: str) -> None:
            logger.info("set_build switched to %s", build_name)
            self.bot.active_recipe = build_name

        def set_production_override(
            self,
            unit_type: str,
            count: int,
            building_tag: int | None = None,
        ) -> None:
            pass

        def set_tech_override(self, upgrade_id: str, building_tag: int | None = None) -> None:
            pass

        def set_expansion_override(self, target_count: int) -> None:
            pass

        def set_unit_role(self, unit_tag: int, role: UnitRole) -> None:
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
                if role == UnitRole.LLM_CONTROLLED:
                    self.bot._llm_controlled_tags.add(unit_tag)
                    logger.info("unit_claimed tag=%d added to _llm_controlled_tags", unit_tag)
                else:
                    self.bot._llm_controlled_tags.discard(unit_tag)
            except Exception as exc:
                logger.warning("set_unit_role failed tag=%d role=%s err=%s", unit_tag, role, exc)

        def _resolve_target_point(self, target: dict[str, object] | None) -> Any:
            if target is None:
                return None
            kind = target.get("kind")
            if kind == "named_spot":
                name = target.get("named_spot")
                if name:
                    registry = getattr(self.bot, "named_spots", None)
                    if registry is not None:
                        return registry.resolve(str(name), self.bot)
            elif kind == "point":
                pt = target.get("point")
                if pt:
                    from sc2.position import Point2

                    return Point2(pt)
            elif kind == "unit_tag":
                tag = target.get("unit_tag")
                if tag:
                    tag_int = int(str(tag))
                    u = self.bot.units.by_tag(tag_int)
                    if u:
                        return u.position
                    u2 = self.bot.enemy_units.by_tag(tag_int)
                    if u2:
                        return u2.position
            return None

        def execute_unit_action(
            self,
            unit_tag: int,
            verb: str,
            target: dict[str, object] | None = None,
            ability_id: str | None = None,
        ) -> None:
            target_point = self._resolve_target_point(target)
            if target_point is None:
                logger.warning(
                    "execute_unit_action: unresolvable target %r (verb=%s)", target, verb
                )
                return

            if unit_tag == 0:
                unit = None
                for u in self.bot.units:
                    if u.is_idle:
                        if str(u.type_id.name).casefold() == "probe":
                            unit = u
                            break
                        if unit is None:
                            unit = u
            else:
                unit = self.bot.units.by_tag(unit_tag)

            if unit is None:
                logger.warning("execute_unit_action: no unit tag=%d", unit_tag)
                return

            if verb in ("attack_move", "attack"):
                unit.attack(target_point)
            else:
                unit.move(target_point)

        def set_build_location_override(
            self,
            structure_type: str,
            point: tuple[float, float],
        ) -> None:
            pass

        def set_engagement_stance(self, stance: str) -> None:
            if stance == "free":
                self.bot.knowledge.vibecraft.stance_override = None
            elif stance in ("defend", "hold", "retreat"):
                self.bot.knowledge.vibecraft.stance_override = stance
            else:
                logger.warning("set_engagement_stance: unknown stance %r, no-op", stance)

        def set_attack_target_override(self, point: tuple[float, float] | None) -> None:
            self.bot.knowledge.vibecraft.attack_target_override = point

        def set_combat_intent_override(self, intent: str | None) -> None:
            self.bot.knowledge.vibecraft.combat_intent_override = intent

        def move_camera(self, point: tuple[float, float]) -> None:
            self._pending_camera_point = point

        def follow_unit(self, unit_tag: int) -> None:
            unit = self.bot.units.find_by_tag(unit_tag)
            if unit is not None:
                self._pending_camera_point = (unit.position.x, unit.position.y)

        async def drain_pending_actions(self) -> None:
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
            pass

        # ---- 读 -------------------------------------------------------

        def get_state(self) -> BotState:
            b = self.bot
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
                army_summary={},
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

    return _SharpyFacadeBase


# -----------------------------------------------------------------------
# VibeCraftBotBase 工厂
# -----------------------------------------------------------------------


def _make_vibecraft_bot_base_class(
    director_factory: Any,
    strategy_library: Any,
    status_callback: Any,
    down_q: Any,
    echo_callback: Any,
    snapshot_callback: Any,
    event_callback: Any,
    minimap_callback: Any,
    run_command_with_echo_fn: Any,
    SharpyFacadeClass: type,
) -> type:
    """返回 VibeCraftBotBase 类（闭包持有所有回调）。

    子类只需继承此类并实现：
      - EXCLUDE_FROM_ARMY: ClassVar[set]
      - DEFAULT_OPENING_ID: ClassVar[str]
      - create_plan() -> BuildOrder
    """
    from sharpy.knowledges.knowledge_bot import KnowledgeBot

    class VibeCraftBotBase(KnowledgeBot):  # type: ignore[misc]
        """vibecraft 三族 bot 基类：sharpy KnowledgeBot + vibecraft 指挥层。

        race-agnostic 部分（lifecycle / EventBus / 多路复用 / LLM_CONTROLLED 隔离）
        全部在本基类实现。各族子类只填 EXCLUDE_FROM_ARMY / DEFAULT_OPENING_ID / create_plan。
        """

        # 子类必须覆盖
        EXCLUDE_FROM_ARMY: ClassVar[set[Any]] = set()
        DEFAULT_OPENING_ID: ClassVar[str] = ""

        director: Any = None
        facade: Any = None
        _cmd_tasks: list[asyncio.Task[Any]]
        _minimap_tick_count: int = 0
        _minimap_builder: Any = None
        _decision_watcher: Any = None
        _hang_watchdog: HangWatchdog | None = None
        active_recipe: str = ""
        _llm_controlled_tags: set[int]
        _tactics_last_s: float = 0.0
        _voice_step_count: int = 0
        _sharpy_iteration: int = 0
        _SHARPY_STEP_RATIO: int = 5

        def __init__(self) -> None:
            # 用 DEFAULT_OPENING_ID 作为 bot 名后缀
            race_name = type(self).__name__
            super().__init__(f"VibeCraft {race_name}")
            self._cmd_tasks = []
            self._minimap_tick_count = 0
            self._minimap_builder = None
            self._decision_watcher = None
            self._hang_watchdog = None
            self.active_recipe = self.__class__.DEFAULT_OPENING_ID or ""
            self._llm_controlled_tags = set()
            self._tactics_last_s = 0.0
            self._voice_step_count = 0
            self._sharpy_iteration = 0
            self.event_bus = EventBus()
            self._enemy_units_dict: dict[int, Any] = {}
            self._own_units_dict: dict[int, Any] = {}
            self.named_spots = NamedSpotRegistry()

        def _update_tactics_throttled(self, now: float) -> None:
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
            """返回 (stance, label, reason)。子类可覆盖以适配不同种族单位类型。"""
            townhalls = self.townhalls
            home = townhalls.first.position if townhalls else self.start_location

            # expanding：有正在建造的基地
            try:
                # 各族主基地类型（Hatchery/CommandCenter/Nexus 都叫 townhalls）
                pending_th = self.townhalls.not_ready.amount
                if pending_th > 0:
                    return (
                        "expanding",
                        f"开矿中(+{pending_th} 矿)",
                        f"已有 {townhalls.amount} 矿,扩 {townhalls.amount + pending_th} 矿",
                    )
            except Exception:
                pass

            # army（排除种族工人 / 非战斗单位）
            try:
                exclude = self.__class__.EXCLUDE_FROM_ARMY
                if exclude:
                    army = self.units.exclude_type(exclude)
                else:
                    army = self.units
            except Exception:
                army = self.units

            # defending
            for th in townhalls:
                enemies_near = self.enemy_units.closer_than(25.0, th)
                if enemies_near.amount >= 2:
                    return (
                        "defending",
                        f"守家({enemies_near.amount} 敌单位逼近)",
                        f"{th.type_id.name} 附近 {enemies_near.amount} 敌单位",
                    )

            # attacking
            if army.amount >= 6:
                center = army.center
                dist = center.distance_to(home)
                if dist > 25.0:
                    return (
                        "attacking",
                        f"进攻中({army.amount} 单位出门)",
                        f"军队重心离家 {int(dist)} 距离",
                    )

            return ("sustaining", "运营中", f"{townhalls.amount} 矿, {army.amount} 兵")

        def is_vibecraft_controlled(self, unit: Any) -> bool:
            return unit.tag in self._llm_controlled_tags

        def _refresh_llm_controlled_roles(self) -> None:
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
                    self.knowledge.roles.set_task(UnitTask.Reserved, unit)
                if dead_tags:
                    self._llm_controlled_tags -= dead_tags
                    logger.debug("llm_controlled_tags cleanup removed dead tags: %s", dead_tags)
            except Exception as exc:
                logger.warning("refresh_llm_controlled_roles failed: %s", exc)

        async def create_plan(self) -> Any:
            raise NotImplementedError("子类必须实现 create_plan()")

        async def on_start(self) -> None:
            await super().on_start()

            from types import SimpleNamespace as _SNS

            self.knowledge.vibecraft = _SNS(
                attack_target_override=None,
                combat_intent_override=None,
                stance_override=None,
            )

            self.facade = SharpyFacadeClass(self)
            self.director = director_factory(self.facade)

            if self.director is not None:
                self.director._bot = self

            if self.director is not None and hasattr(self, "event_bus"):
                self.director.setup_task_monitor(self.event_bus)

            if minimap_callback is not None:
                from vibecraft.bot.minimap import MinimapBuilder

                self._minimap_builder = MinimapBuilder(self)

            if snapshot_callback is not None and self.director is not None:
                self.director.set_snapshot_callback(snapshot_callback)
            if event_callback is not None and self.director is not None:
                self.director.set_event_callback(event_callback)

            if event_callback is not None:
                from vibecraft.bot.auto_combat.decision_watcher import DecisionWatcher

                self._decision_watcher = DecisionWatcher(event_callback)

            if strategy_library is not None:
                import os
                import random

                from vibecraft.strategy.models import OpeningBuild

                _DEFAULT_OPENING_ID = self.__class__.DEFAULT_OPENING_ID or ""
                openings = [
                    s for s in strategy_library.all_strategies() if isinstance(s, OpeningBuild)
                ]
                if openings:
                    forced_id = os.environ.get("VIBECRAFT_FORCE_INITIAL_OPENING")
                    chosen = None
                    if forced_id:
                        chosen = next((o for o in openings if o.id == forced_id), None)
                        if chosen is None:
                            logger.warning(
                                "forced initial opening %r 不在 catalog,回退默认 %s",
                                forced_id,
                                _DEFAULT_OPENING_ID,
                            )
                    if chosen is None:
                        chosen = next(
                            (o for o in openings if o.id == _DEFAULT_OPENING_ID),
                            None,
                        )
                        if chosen is None:
                            logger.warning(
                                "default opening %r 不在 catalog,回退 random",
                                _DEFAULT_OPENING_ID,
                            )
                            chosen = random.choice(openings)
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

            import os as _os

            if not _os.environ.get("VIBECRAFT_DISABLE_HANG_WATCHDOG"):

                def _on_hang() -> None:
                    if status_callback is not None:
                        status_callback("crashed", "error", "hang_watchdog: bot.time stuck")

                self._hang_watchdog = HangWatchdog(
                    get_bot_time=lambda: float(self.time),
                    on_hang=_on_hang,
                )
                self._hang_watchdog.start()

        async def on_step(self, iteration: int) -> None:
            self._voice_step_count += 1
            now_s = float(self.time)

            await self._tick_view_channel(now_s)

            if self._voice_step_count % self._SHARPY_STEP_RATIO == 0:
                await self._tick_bot_channel(iteration, now_s)

        async def _tick_view_channel(self, now_s: float) -> None:
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
                            directive_id = msg.get("directive_id")
                            if directive_id and self.director is not None:
                                self.director.revoke_directive(directive_id, now_s)
                        elif msg_type == "leave":
                            logger.info("bot 收到 leave 信号，等待 on_end")
                except queue_module.Empty:
                    pass

            if minimap_callback is not None and self._minimap_builder is not None:
                self._minimap_tick_count += 1
                if self._minimap_tick_count >= 2:
                    self._minimap_tick_count = 0
                    try:
                        frame = self._minimap_builder.build(now_s)
                        minimap_callback(frame)
                    except Exception as exc:
                        logger.warning("minimap_build_failed: %s", exc)

            if self.facade is not None:
                await self.facade.drain_pending_actions()

            self._update_tactics_throttled(now_s)
            if self.director is not None:
                self.director.on_tick(now=now_s)
            if self._decision_watcher is not None:
                self._decision_watcher.tick(self, now_s)

        async def _tick_bot_channel(self, py_sc2_iteration: int, now_s: float) -> None:
            if self.director is not None:
                try:
                    await self.director.execute_overrides_step(now_s)
                except Exception as exc:
                    logger.warning("execute_overrides_step fail: %s", exc)
                try:
                    await self.director.execute_tactics_step(now_s)
                except Exception as exc:
                    logger.warning("execute_tactics_step fail: %s", exc)
            await super().on_step(self._sharpy_iteration)
            self._sharpy_iteration += 1
            self._refresh_llm_controlled_roles()

        async def on_unit_created(self, unit: Any) -> None:
            _publish_unit_created(self, unit)
            if getattr(unit, "alliance", 0) == 1:
                self._own_units_dict[unit.tag] = unit
            else:
                self._enemy_units_dict[unit.tag] = unit
            if hasattr(super(), "on_unit_created"):
                await super().on_unit_created(unit)

        async def on_unit_destroyed(self, unit_tag: int) -> None:
            if hasattr(self, "event_bus"):
                _publish_unit_destroyed(self, unit_tag)
            if unit_tag in self._llm_controlled_tags:
                self._llm_controlled_tags.discard(unit_tag)
                logger.info("unit_destroyed tag=%d removed from _llm_controlled_tags", unit_tag)
            if hasattr(self, "_own_units_dict"):
                self._own_units_dict.pop(unit_tag, None)
            if hasattr(self, "_enemy_units_dict"):
                self._enemy_units_dict.pop(unit_tag, None)
            await super().on_unit_destroyed(unit_tag)

        async def on_unit_type_changed(self, unit: Any, previous_type: Any) -> None:
            _publish_unit_type_changed(self, unit, previous_type)
            if hasattr(super(), "on_unit_type_changed"):
                await super().on_unit_type_changed(unit, previous_type)

        async def on_building_construction_started(self, unit: Any) -> None:
            _publish_building_started(self, unit)
            if hasattr(super(), "on_building_construction_started"):
                await super().on_building_construction_started(unit)

        async def on_building_construction_complete(self, unit: Any) -> None:
            _publish_building_complete(self, unit)
            if hasattr(super(), "on_building_construction_complete"):
                await super().on_building_construction_complete(unit)

        async def on_upgrade_complete(self, upgrade: Any) -> None:
            _publish_upgrade_complete(self, upgrade)
            if hasattr(super(), "on_upgrade_complete"):
                await super().on_upgrade_complete(upgrade)

        async def on_unit_took_damage(self, unit: Any, amount_damage_taken: Any) -> None:
            _publish_unit_took_damage(self, unit, amount_damage_taken)
            if hasattr(super(), "on_unit_took_damage"):
                await super().on_unit_took_damage(unit, amount_damage_taken)

        async def on_enemy_unit_entered_vision(self, unit: Any) -> None:
            _publish_enemy_unit_entered_vision(self, unit)
            self._enemy_units_dict[unit.tag] = unit
            if hasattr(super(), "on_enemy_unit_entered_vision"):
                await super().on_enemy_unit_entered_vision(unit)

        async def on_enemy_unit_left_vision(self, unit_tag: int) -> None:
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

    return VibeCraftBotBase
