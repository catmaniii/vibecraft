"""Sc2Facade：bot 对 SC2 的全部需求接口。

设计原则：
1. 不暴露任何 ares-sc2 / python-sc2 类型。所有参数都用 stdlib + dataclass。
2. 设计 6 个 ares hook 点（设计文档 §3.2）能映射到这里：
   - Hook A Build Runner 切换       → `set_build`
   - Hook B OverrideMediator        → `set_production_override` / `set_tech_override`
   - Hook C Unit Role               → `set_unit_role`
   - Hook D Rationale Logger        → 由 Director 自己用 GameSession
   - Hook E ViewController          → `move_camera` / `follow_unit` / `set_camera_zoom`
   - Hook F BuildLocationOverride   → `set_build_location_override`
3. **查询**接口也走 facade：bot 内部不直接调 SC2 API，便于单测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class UnitRole(str, Enum):
    """voicecraft 内部的 unit role；运行时由 `ares_adapter` 映射到真实
    `ares.consts.UnitRole` 成员。

    **重要**：ares 的 UnitRole 是固定 StrEnum，无法动态加成员。
    `LLM_CONTROLLED` 实际映射到 ares 的 `CONTROL_GROUP_ONE`（ares
    自身注释："use for anything not specified"，且无任何 ares Manager
    内部使用它）—— 这就是设计文档 §3.4 假设的 role 排除机制的载体。
    """

    LLM_CONTROLLED = "LLM_CONTROLLED"
    IDLE = "IDLE"
    ARMY = "ARMY"
    DEFENDER = "DEFENDER"
    HARASSER = "HARASSER"
    SCOUT = "SCOUT"


# =========================================================================
# BotState：facade 暴露给上层的只读快照
# =========================================================================


@dataclass
class BotState:
    """Snapshot of in-game state at a tick.

    构造 ParseContext 时用。不包含完整单位列表（开销大），按需查询。
    """

    game_time: float = 0.0
    minerals: int = 0
    gas: int = 0
    supply_used: int = 0
    supply_cap: int = 0
    expansion_count: int = 1
    army_summary: dict[str, int] = field(default_factory=dict)
    enemy_summary: dict[str, int] = field(default_factory=dict)


# =========================================================================
# Sc2Facade Protocol
# =========================================================================


class Sc2Facade(Protocol):
    """bot 对 SC2 的全部需求。"""

    # ---- 写：剧本 / 生产 ----------------------------------------------

    def set_build(self, build_name: str) -> None: ...

    def set_production_override(
        self,
        unit_type: str,
        count: int,
        building_tag: int | None = None,
    ) -> None: ...

    def set_tech_override(
        self,
        upgrade_id: str,
        building_tag: int | None = None,
    ) -> None: ...

    def set_expansion_override(self, target_count: int) -> None: ...

    # ---- 写：单位 -----------------------------------------------------

    def set_unit_role(self, unit_tag: int, role: UnitRole) -> None: ...

    def execute_unit_action(
        self,
        unit_tag: int,
        verb: str,
        target: dict[str, object] | None = None,
        ability_id: str | None = None,
    ) -> None: ...

    # ---- 写：建造位置 / engagement -----------------------------------

    def set_build_location_override(
        self,
        structure_type: str,
        point: tuple[float, float],
    ) -> None: ...

    def set_engagement_stance(self, stance: str) -> None: ...

    # ---- 写：视野（不进 Board）---------------------------------------

    def move_camera(self, point: tuple[float, float]) -> None: ...

    def follow_unit(self, unit_tag: int) -> None: ...

    def set_camera_zoom(self, level: float) -> None: ...

    # ---- 读：游戏状态 -------------------------------------------------

    def get_state(self) -> BotState: ...

    def resolve_selector(
        self,
        unit_type: str | None = None,
        tag: int | None = None,
        tags: list[int] | None = None,
    ) -> list[int]:
        """解析 Selector 为 tag 列表。"""
        ...


# =========================================================================
# FakeFacade：单测专用，记录所有调用
# =========================================================================


@dataclass
class FacadeCall:
    method: str
    args: tuple[object, ...]
    kwargs: dict[str, object]


class FakeFacade:
    """In-memory fake：所有写操作记到 `calls`；读操作可注入 stub state。"""

    def __init__(self, state: BotState | None = None) -> None:
        self.state = state or BotState()
        self.unit_roles: dict[int, UnitRole] = {}
        self.builds: list[str] = []
        self.engagement_stances: list[str] = []
        self.camera_moves: list[tuple[float, float]] = []
        self.camera_follows: list[int] = []
        self.camera_zooms: list[float] = []
        self.production_overrides: list[tuple[str, int, int | None]] = []
        self.tech_overrides: list[tuple[str, int | None]] = []
        self.expansion_overrides: list[int] = []
        self.build_location_overrides: list[tuple[str, tuple[float, float]]] = []
        self.unit_actions: list[dict[str, object]] = []
        self.selector_lookups: list[dict[str, object]] = []
        self.calls: list[FacadeCall] = []
        # selector 解析 stub：按 unit_type 给定 tag 列表
        self.selector_stub: dict[str, list[int]] = {}

    def _record(self, method: str, *args: object, **kwargs: object) -> None:
        self.calls.append(FacadeCall(method=method, args=args, kwargs=kwargs))

    # ---- 写 -----------------------------------------------------------

    def set_build(self, build_name: str) -> None:
        self.builds.append(build_name)
        self._record("set_build", build_name)

    def set_production_override(
        self,
        unit_type: str,
        count: int,
        building_tag: int | None = None,
    ) -> None:
        self.production_overrides.append((unit_type, count, building_tag))
        self._record("set_production_override", unit_type, count, building_tag=building_tag)

    def set_tech_override(self, upgrade_id: str, building_tag: int | None = None) -> None:
        self.tech_overrides.append((upgrade_id, building_tag))
        self._record("set_tech_override", upgrade_id, building_tag=building_tag)

    def set_expansion_override(self, target_count: int) -> None:
        self.expansion_overrides.append(target_count)
        self._record("set_expansion_override", target_count)

    def set_unit_role(self, unit_tag: int, role: UnitRole) -> None:
        self.unit_roles[unit_tag] = role
        self._record("set_unit_role", unit_tag, role)

    def execute_unit_action(
        self,
        unit_tag: int,
        verb: str,
        target: dict[str, object] | None = None,
        ability_id: str | None = None,
    ) -> None:
        self.unit_actions.append(
            {"tag": unit_tag, "verb": verb, "target": target, "ability_id": ability_id}
        )
        self._record("execute_unit_action", unit_tag, verb, target=target, ability_id=ability_id)

    def set_build_location_override(
        self,
        structure_type: str,
        point: tuple[float, float],
    ) -> None:
        self.build_location_overrides.append((structure_type, point))
        self._record("set_build_location_override", structure_type, point)

    def set_engagement_stance(self, stance: str) -> None:
        self.engagement_stances.append(stance)
        self._record("set_engagement_stance", stance)

    def move_camera(self, point: tuple[float, float]) -> None:
        self.camera_moves.append(point)
        self._record("move_camera", point)

    def follow_unit(self, unit_tag: int) -> None:
        self.camera_follows.append(unit_tag)
        self._record("follow_unit", unit_tag)

    def set_camera_zoom(self, level: float) -> None:
        self.camera_zooms.append(level)
        self._record("set_camera_zoom", level)

    # ---- 读 -----------------------------------------------------------

    def get_state(self) -> BotState:
        return self.state

    def resolve_selector(
        self,
        unit_type: str | None = None,
        tag: int | None = None,
        tags: list[int] | None = None,
    ) -> list[int]:
        self.selector_lookups.append({"unit_type": unit_type, "tag": tag, "tags": tags})
        if tag is not None:
            return [tag]
        if tags:
            return list(tags)
        if unit_type is not None and unit_type in self.selector_stub:
            return list(self.selector_stub[unit_type])
        return []
