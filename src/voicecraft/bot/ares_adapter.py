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

from typing import TYPE_CHECKING, Any

from voicecraft.bot.build_translator import openings_to_ares_config_builds
from voicecraft.bot.facade import BotState, UnitRole
from voicecraft.strategy.library import StrategyLibrary
from voicecraft.strategy.models import OpeningBuild

if TYPE_CHECKING:
    # 这些 import 只在类型检查时有效；运行时 lazy import
    from ares import AresBot


class VoiceCraftBot:
    """ares-sc2 子类的薄壳。

    构造时不能直接实例化（python-sc2 通过 run 框架启动）。
    使用 `make_bot_class(director_factory)` 工厂在运行时拼装。
    """


def make_bot_class(director_factory: Any, strategy_library: StrategyLibrary | None = None) -> type:
    """工厂：返回一个继承 AresBot 的 bot 类，把事件转给 director。

    director_factory(bot) -> Director：在 on_start 时被调用，
    传入 bot 自己，让 director 持有 facade。

    strategy_library：可选；传入后会把其中所有 OpeningBuild 在 on_start 时注入
    `bot.config["Builds"]`（spike B：必须在 super().on_start() 之前完成，
    因为 BuildOrderRunner 在 super().on_start() 末尾构造时读 config）。
    """
    try:
        from ares import AresBot
        from ares.consts import UnitRole as AresUnitRole
    except ImportError as e:
        raise ImportError(
            '未装 ares-sc2。`uv pip install "git+https://github.com/AresSC2/ares-sc2@main"`'
        ) from e

    # 把 voicecraft 的 UnitRole 映射到 ares 真实成员。
    # LLM_CONTROLLED → CONTROL_GROUP_ONE：ares 留给用户的空槽，
    # ares 源码里没有任何 Manager 使用它，正是 §3.4 想要的"排除单元"载体。
    role_map = {
        UnitRole.LLM_CONTROLLED: AresUnitRole.CONTROL_GROUP_ONE,
        UnitRole.IDLE: AresUnitRole.IDLE,
        UnitRole.ARMY: AresUnitRole.ATTACKING,
        UnitRole.DEFENDER: AresUnitRole.DEFENDING,
        UnitRole.HARASSER: AresUnitRole.HARASSING,
        UnitRole.SCOUT: AresUnitRole.SCOUTING,
    }

    class _AresFacade:
        """Sc2Facade 的 ares 实现。"""

        def __init__(self, bot: AresBot) -> None:
            self.bot = bot

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
            from sc2.position import Point2

            self.bot.client.move_camera(Point2(point))

        def follow_unit(self, unit_tag: int) -> None:
            unit = self.bot.units.find_by_tag(unit_tag)
            if unit is not None:
                self.bot.client.move_camera(unit.position)

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

    class _VoiceCraftBot(AresBot):  # type: ignore[misc]
        director = None
        facade: _AresFacade | None = None

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

            await super().on_start()
            self.facade = _AresFacade(self)
            self.director = director_factory(self.facade)

        async def on_step(self, iteration: int) -> None:
            await super().on_step(iteration)
            if self.director is not None:
                self.director.on_tick(now=float(self.time))

    return _VoiceCraftBot
