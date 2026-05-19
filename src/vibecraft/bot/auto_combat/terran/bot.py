"""人族 bot：_VibeCraftTerranBot 继承 VibeCraftBotBase。

M6.3b：人族 bot class + plans。

本文件是人族薄壳，只保留：
  - EXCLUDE_FROM_ARMY（人族排除 SCV / MULE）
  - DEFAULT_OPENING_ID = "marine_rush"
  - create_plan()（人族 IfElse 路由树 + ScoutSCV + TerranSustain）

设计参考：docs/plans/2026-05-18-zerg-terran-bot-design.md §3.3。
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from vibecraft.bot.auto_combat.common_bot import _ensure_sharpy_on_path

logger = logging.getLogger(__name__)


def make_terran_bot_class(
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
    """工厂：返回继承 VibeCraftBotBase 的人族 bot 类。

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

    from sc2.ids.unit_typeid import UnitTypeId as _UnitTypeId

    class _VibeCraftTerranBot(VibeCraftBotBase):  # type: ignore[misc,valid-type]
        """vibecraft 人族 bot：薄壳，仅保留人族特化三处。"""

        EXCLUDE_FROM_ARMY: ClassVar[set[Any]] = {
            _UnitTypeId.SCV,
            _UnitTypeId.MULE,
        }
        DEFAULT_OPENING_ID = "marine_rush"
        active_recipe: str = "marine_rush"

        def __init__(self) -> None:
            super().__init__()

        async def create_plan(self) -> BuildOrder:
            """M6.3b：人族 IfElse 路由树 + ScoutSCV + TerranSustain 兜底。"""
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
                # 2026-05-20 bug fix:同 protoss/bot.py — PersistentDoctrine 也要进
                # IfElse 候选,否则 set_build 切持续策略时落到 sustain_plan。
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
                    from sharpy.plans.acts.terran import TerranUnit

                    return BuildOrder([TerranUnit(_U.SCV, 16)])
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

            # 两层架构（2026-05-19）：删除 TerranSustain 兜底。state_machine 不变量保证
            # active_recipe 永不为 sustain；IfElse 默认分支理论不可达。
            sustain_plan = _make_fallback_plan()

            scout_act: Any = None
            try:
                from vibecraft.bot.auto_combat.terran.plans.scout_scv import ScoutSCV

                scout_act = ScoutSCV()
            except Exception as exc:
                logger.warning("ScoutSCV 装载失败: %s — 跳过通用探路层", exc)

            def _wrap(plan: Any) -> Any:
                if scout_act is None:
                    return BuildOrder(plan)
                return BuildOrder([scout_act, plan])

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

    return _VibeCraftTerranBot
