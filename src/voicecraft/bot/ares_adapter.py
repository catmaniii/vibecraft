"""ares-sc2 / python-sc2 与 Sc2Facade 的 binding。

**仅在装了 ares-sc2 的环境才能 import**。M0b 单测全部用 FakeFacade，
本文件不被 import；只在 M0c 端到端 smoke 用。

实现按设计文档 §3.2 / §6.1 / §11.x：
- bot 继承 `ares.AresBot`
- `set_build()` → ares Build Runner
- `set_unit_role()` → `self.mediator.assign_role(tag, role)`
  （API 名按 ares 实际为准，端到端时校准）
- `move_camera()` → `self.client.move_camera(point)`

调用方式：

    from voicecraft.bot.ares_adapter import VoiceCraftBot
    VoiceCraftBot.start(...)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from voicecraft.bot.facade import BotState, UnitRole

if TYPE_CHECKING:
    # 这些 import 只在类型检查时有效；运行时 lazy import
    from ares import AresBot


class VoiceCraftBot:
    """ares-sc2 子类的薄壳。

    构造时不能直接实例化（python-sc2 通过 run 框架启动）。
    使用 `make_bot_class(director_factory)` 工厂在运行时拼装。
    """


def make_bot_class(director_factory: Any) -> type:
    """工厂：返回一个继承 AresBot 的 bot 类，把事件转给 director。

    director_factory(bot) -> Director：在 on_start 时被调用，
    传入 bot 自己，让 director 持有 facade。
    """
    try:
        from ares import AresBot
    except ImportError as e:
        raise ImportError(
            '未装 ares-sc2。`uv pip install "git+https://github.com/AresSC2/ares-sc2@main"`'
        ) from e

    class _AresFacade:
        """Sc2Facade 的 ares 实现。"""

        def __init__(self, bot: AresBot) -> None:
            self.bot = bot

        # ---- 写 -------------------------------------------------------

        def set_build(self, build_name: str) -> None:
            # ares Build Runner: 实际 API 在 ares.build_runner.BuildRunner.set_build(name)
            self.bot.build_runner.set_build(build_name)

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
            # ares unit role API：bot.mediator.assign_role(tag, role)
            self.bot.mediator.assign_role(tag=unit_tag, role=role.value)

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
            await super().on_start()
            self.facade = _AresFacade(self)
            self.director = director_factory(self.facade)

        async def on_step(self, iteration: int) -> None:
            await super().on_step(iteration)
            if self.director is not None:
                self.director.on_tick(now=float(self.time))

    return _VoiceCraftBot
