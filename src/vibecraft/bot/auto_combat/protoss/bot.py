"""神族 bot：_VibeCraftProtossBot 继承 VibeCraftBotBase。

M6.0 重构：race-agnostic 部分（lifecycle / EventBus / 多路复用等）上提到
VibeCraftBotBase(common_bot.py)。本文件是神族薄壳，只保留：
  - EXCLUDE_FROM_ARMY（神族排除 PROBE / OBSERVER / WARPPRISM）
  - DEFAULT_OPENING_ID = "4bg"
  - create_plan()（神族 IfElse 路由树 + ScoutWorker + Sustain）

import 路径说明：vendor/sharpy/ 不在标准 src layout 下，
运行时通过 sys.path 注入让 `from sharpy.knowledges.knowledge_bot import KnowledgeBot` 可解析。
注入只在真正 import 本模块时发生（lazy，单测时 fake_sharpy 先 mock sys.modules）。

"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

# -----------------------------------------------------------------------
# 从 common_bot 重新导出（向后兼容；test_event_bus.py 等仍从此路径 import）
# -----------------------------------------------------------------------
from vibecraft.bot.auto_combat.common_bot import (  # noqa: F401
    _ensure_sharpy_on_path,
    _publish_building_complete,
    _publish_building_started,
    _publish_enemy_unit_entered_vision,
    _publish_enemy_unit_left_vision,
    _publish_unit_created,
    _publish_unit_destroyed,
    _publish_unit_took_damage,
    _publish_unit_type_changed,
    _publish_upgrade_complete,
)

logger = logging.getLogger(__name__)


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
    """工厂：返回继承 VibeCraftBotBase 的神族 bot 类。

    参数设计：把外层 make_bot_class 闭包内的对象显式传进来，
    保持与 sharpy_adapter.py 之间的解耦。
    """
    _ensure_sharpy_on_path()

    try:
        from sharpy.knowledges.knowledge_bot import KnowledgeBot as _KB  # noqa: F401
        from sharpy.plans import BuildOrder
    except ImportError as e:
        raise ImportError(
            f"无法 import sharpy.knowledges.knowledge_bot（真因: {e!r}）；"
            "确认 vendor/sharpy/ 已 clone 且 python-sc2 已装。"
        ) from e

    from vibecraft.bot.auto_combat.common_bot import (
        _make_sharpy_facade_base_class,
        _make_vibecraft_bot_base_class,
    )

    SharpyFacadeClass = _make_sharpy_facade_base_class()
    VibeCraftBotBase = _make_vibecraft_bot_base_class(
        director_factory=director_factory,
        strategy_library=strategy_library,
        status_callback=status_callback,
        down_q=down_q,
        echo_callback=echo_callback,
        snapshot_callback=snapshot_callback,
        event_callback=event_callback,
        minimap_callback=minimap_callback,
        run_command_with_echo_fn=run_command_with_echo_fn,
        SharpyFacadeClass=SharpyFacadeClass,
    )

    # UnitTypeId 在 make_protoss_bot_class 执行时已可用（_ensure_sharpy_on_path 已调）
    from sc2.ids.unit_typeid import UnitTypeId as _UnitTypeId

    class _VibeCraftProtossBot(VibeCraftBotBase):  # type: ignore[misc,valid-type]
        """vibecraft 神族 bot：薄壳，仅保留神族特化三处。"""

        EXCLUDE_FROM_ARMY: ClassVar[set[Any]] = {
            _UnitTypeId.PROBE,
            _UnitTypeId.OBSERVER,
            _UnitTypeId.WARPPRISM,
        }
        DEFAULT_OPENING_ID = "4bg"
        active_recipe: str = "4bg"

        def __init__(self) -> None:
            super().__init__()

        async def create_plan(self) -> BuildOrder:
            """M2+M3：神族 IfElse 路由树 + ScoutWorker + Sustain 兜底。"""
            import importlib
            import inspect

            from vibecraft.strategy.models import (
                LategameDoctrine,
                MidgameStance,
                OpeningBuild,
                PersistentDoctrine,
            )

            if strategy_library is None:
                logger.warning("create_plan: no strategy_library, returning empty BuildOrder")
                return BuildOrder([])

            candidates: list[tuple[str, str]] = []
            for s in strategy_library.all_strategies():
                # 2026-05-20 bug fix:之前漏了 PersistentDoctrine,导致 set_build 切到
                # persistent_skytoss 等持续策略时 IfElse 没分支,落到 sustain_plan(裸
                # ActUnit(PROBE, 14)),运营/防守/进攻全停 — 用户反馈"换策略以后追猎
                # 不进攻,运营也停了"。Skytoss plan 自身就有 DistributeWorkers /
                # PlanZoneAttack 等完整逻辑,路由进来即可。
                if (
                    isinstance(
                        s, (OpeningBuild, MidgameStance, LategameDoctrine, PersistentDoctrine)
                    )
                    and s.sharpy_dummy_class
                ):
                    candidates.append((s.id, s.sharpy_dummy_class))

            if not candidates:
                logger.warning("create_plan: 没有 sharpy_dummy_class 策略，返回空 BuildOrder")
                return BuildOrder([])

            def _make_fallback_plan() -> BuildOrder:
                from sc2.ids.unit_typeid import UnitTypeId as _U

                try:
                    from sharpy.plans.acts.act_unit import ActUnit

                    return BuildOrder([ActUnit(_U.PROBE, _U.NEXUS, 14)])
                except Exception:
                    return BuildOrder([])

            plans: dict[str, BuildOrder] = {}
            for recipe_id, dummy_spec in candidates:
                module_path, class_name = dummy_spec.rsplit(":", 1)
                try:
                    mod = importlib.import_module(module_path)
                    dummy_cls = getattr(mod, class_name)
                    dummy_inst = dummy_cls()
                    raw_plan = dummy_inst.create_plan()
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

            # 两层架构（2026-05-19）：删除 Sustain 兜底。state_machine 不变量保证
            # active_recipe 永不为 sustain；IfElse 默认分支理论不可达，但 sharpy
            # 仍需要 result 起点，用 _make_fallback_plan（最小可跑 BuildOrder）。
            sustain_plan = _make_fallback_plan()

            scout_act: Any = None
            try:
                from vibecraft.bot.auto_combat.scout_worker import ScoutWorker

                scout_act = ScoutWorker()
                # Task #352: 把 ScoutWorker 实例暴露给 Director,玩家撤回探路时可调 cancel()。
                self.scout_worker = scout_act
            except Exception as exc:
                logger.warning("ScoutWorker 装载失败: %s — 跳过通用探路层", exc)

            def _wrap(plan: Any) -> Any:
                from vibecraft.bot.auto_combat.opening_sustain_act import OpeningSustainAct
                from vibecraft.bot.auto_combat.worker_saturation_floor import make_worker_floor

                sustain_act = OpeningSustainAct(race="PROTOSS")
                # 通用农民饱和兜底（2026-07-10）：顶层 BuildOrder 直接兄弟，绝不进
                # SequentialList（否则 return False 阻塞后续）。放在 plan 之前——每帧先
                # 保证农民往饱和补 1 个、再让 plan 爆兵。恒生效（不看 sustain 任何 flag），
                # 切 persistent_doctrine 后 OpeningSustainAct 的 flag 不 fire 也照样兜底。
                worker_floor_act = make_worker_floor("PROTOSS")
                if scout_act is None:
                    return BuildOrder([worker_floor_act, plan, sustain_act])
                return BuildOrder([scout_act, worker_floor_act, plan, sustain_act])

            if not plans:
                return _wrap(sustain_plan)

            from sharpy.plans.if_else import IfElse

            recipe_ids = [rid for rid, _ in candidates]
            result: Any = sustain_plan
            for rid in reversed(recipe_ids):
                _rid = rid
                result = IfElse(
                    lambda k, r=_rid: self.active_recipe == r,
                    plans[_rid],
                    result,
                )

            return _wrap(result)

    return _VibeCraftProtossBot
