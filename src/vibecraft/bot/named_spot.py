"""NamedSpot registry - 把人类可读的位置名（"enemy_main_gas"）映射到 SC2 坐标。

LLM IntentParser 输出 target/area 用 named_spot 字符串，task_monitor checker 调
NamedSpotRegistry.resolve(name, bot) 得到 Point2 坐标。

支持的 spot:
- natural / third / main —— 自方扩张点
- enemy_main / enemy_natural / enemy_third —— 敌方对应
- main_ramp / natural_ramp —— 自方斜坡
- enemy_main_ramp —— 敌方斜坡
- <X>_gas —— 任何 X 的气矿点（X ∈ above）

resolve 返回 Point2 | None。None 表示该 spot 在当前 game state 不能解析（如 enemy_third
还没侦察到）。
"""

from __future__ import annotations

import logging
from typing import Any  # bot is sharpy KnowledgeBot, duck-type

# 不强 import sc2.position.Point2 (lazy avoid sharpy 提前 wire)
# 实际 return 类型是 sc2.position.Point2

logger = logging.getLogger(__name__)


class NamedSpotRegistry:
    """名字到 SC2 坐标的运行时解析。"""

    # 已知 spot 白名单 (helps validation + completion in PWA)
    KNOWN_SPOTS: frozenset[str] = frozenset(
        {
            "natural",
            "third",
            "main",
            "enemy_main",
            "enemy_natural",
            "enemy_third",
            "main_ramp",
            "natural_ramp",
            "enemy_main_ramp",
            # *_gas 变种
            "natural_gas",
            "third_gas",
            "main_gas",
            "enemy_main_gas",
            "enemy_natural_gas",
            "enemy_third_gas",
        }
    )

    def resolve(self, name: str, bot: Any) -> Any | None:
        """解析 name → Point2 | None。

        Args:
            name: spot 名字 (KNOWN_SPOTS 之一)
            bot: sharpy KnowledgeBot 实例，用其 zone_manager / expansion_locations 等

        Returns:
            Point2 或 None (spot 不可解析)
        """
        if name not in self.KNOWN_SPOTS:
            logger.warning("named_spot_unknown: %s (not in KNOWN_SPOTS)", name)
            return None

        # 按 spot 名 dispatch
        if name == "natural":
            return self._own_natural(bot)
        if name == "third":
            return self._own_third(bot)
        if name == "main":
            return self._own_main(bot)
        if name == "enemy_main":
            return self._enemy_main(bot)
        if name == "enemy_natural":
            return self._enemy_natural(bot)
        if name == "enemy_third":
            return self._enemy_third(bot)
        if name == "main_ramp":
            return self._main_ramp(bot)
        if name == "natural_ramp":
            return self._natural_ramp(bot)
        if name == "enemy_main_ramp":
            return self._enemy_main_ramp(bot)
        if name.endswith("_gas"):
            base = name[: -len("_gas")]
            base_pos = self.resolve(base, bot)
            if base_pos is None:
                return None
            return self._closest_gas_to(base_pos, bot)
        return None  # pragma: no cover

    # === Implementation helpers - access sharpy / python-sc2 API ===
    # 大量 hasattr / getattr 兜底，因为 bot 可能是 mock（单测）

    def _own_main(self, bot: Any) -> Any | None:
        """自方主基地 = bot.townhalls[0] (开局唯一 nexus)。"""
        townhalls = getattr(bot, "townhalls", None)
        if townhalls is None or not townhalls:
            return None
        # bot.townhalls 是 Units (类 list)，取第一个 .position
        try:
            return townhalls.first.position
        except (AttributeError, IndexError):
            return None

    def _own_natural(self, bot: Any) -> Any | None:
        """自方 natural - 用 sharpy zone_manager.expansion_zones 第 2 个 (index 1) 或
        bot.expansion_locations_list 第 2 个。"""
        # 优先用 sharpy zone_manager
        zone_mgr = getattr(getattr(bot, "knowledge", None), "zone_manager", None)
        if zone_mgr and hasattr(zone_mgr, "expansion_zones"):
            zones = zone_mgr.expansion_zones
            if zones and len(zones) > 1:
                return zones[1].center_location  # natural
        # fallback python-sc2 expansion_locations_list (已按距 main 排序)
        exp_list = getattr(bot, "expansion_locations_list", None)
        if exp_list and len(exp_list) > 1:
            return exp_list[1]
        return None

    def _own_third(self, bot: Any) -> Any | None:
        zone_mgr = getattr(getattr(bot, "knowledge", None), "zone_manager", None)
        if zone_mgr and hasattr(zone_mgr, "expansion_zones"):
            zones = zone_mgr.expansion_zones
            if zones and len(zones) > 2:
                return zones[2].center_location
        exp_list = getattr(bot, "expansion_locations_list", None)
        if exp_list and len(exp_list) > 2:
            return exp_list[2]
        return None

    def _enemy_main(self, bot: Any) -> Any | None:
        """敌方主基地 = enemy_start_locations[0]。"""
        enemy_starts = getattr(bot, "enemy_start_locations", None)
        if enemy_starts and len(enemy_starts) > 0:
            return enemy_starts[0]
        return None

    def _enemy_natural(self, bot: Any) -> Any | None:
        """敌方 natural - sharpy zone_manager 提供敌方扩张顺序。"""
        zone_mgr = getattr(getattr(bot, "knowledge", None), "zone_manager", None)
        if zone_mgr and hasattr(zone_mgr, "enemy_expansion_zones"):
            zones = zone_mgr.enemy_expansion_zones
            if zones and len(zones) > 1:
                return zones[1].center_location
        return None

    def _enemy_third(self, bot: Any) -> Any | None:
        zone_mgr = getattr(getattr(bot, "knowledge", None), "zone_manager", None)
        if zone_mgr and hasattr(zone_mgr, "enemy_expansion_zones"):
            zones = zone_mgr.enemy_expansion_zones
            if zones and len(zones) > 2:
                return zones[2].center_location
        return None

    def _main_ramp(self, bot: Any) -> Any | None:
        """自方主斜坡 - python-sc2 main_base_ramp.top_center。"""
        ramp = getattr(bot, "main_base_ramp", None)
        if ramp is not None:
            return getattr(ramp, "top_center", None) or getattr(
                ramp, "depot_in_middle", None
            )
        return None

    def _natural_ramp(self, bot: Any) -> Any | None:
        """自方 natural 斜坡 - sharpy zone_manager 提供。"""
        zone_mgr = getattr(getattr(bot, "knowledge", None), "zone_manager", None)
        if zone_mgr and hasattr(zone_mgr, "expansion_zones"):
            zones = zone_mgr.expansion_zones
            if zones and len(zones) > 1:
                ramp = getattr(zones[1], "ramp", None)
                if ramp:
                    return getattr(ramp, "top_center", None)
        return None

    def _enemy_main_ramp(self, bot: Any) -> Any | None:
        zone_mgr = getattr(getattr(bot, "knowledge", None), "zone_manager", None)
        if zone_mgr and hasattr(zone_mgr, "enemy_expansion_zones"):
            zones = zone_mgr.enemy_expansion_zones
            if zones:
                ramp = getattr(zones[0], "ramp", None)
                if ramp:
                    return getattr(ramp, "top_center", None)
        return None

    def closest_named_spot(
        self,
        point: Any,
        bot: Any,
        max_distance: float = 15.0,
    ) -> str | None:
        """反向查找：point 附近 max_distance 内最近的 named spot 名字。

        只遍历 KNOWN_SPOTS 中非 _gas 变种（避免双重解析噪音）。

        Args:
            point: SC2 Point2 或有 .x/.y 的对象
            bot: sharpy KnowledgeBot，传给 resolve()
            max_distance: 距离阈值（game units，默认 15）

        Returns:
            spot 名字（如 "enemy_natural"）或 None（没有匹配的 spot 在范围内）
        """
        non_gas = [s for s in self.KNOWN_SPOTS if not s.endswith("_gas")]
        best_name: str | None = None
        best_dist: float = max_distance  # 只取 < max_distance 的

        try:
            px = float(point.x)
            py = float(point.y)
        except (AttributeError, TypeError):
            return None

        for name in non_gas:
            spot_pos = self.resolve(name, bot)
            if spot_pos is None:
                continue
            try:
                sx = float(spot_pos.x)
                sy = float(spot_pos.y)
            except (AttributeError, TypeError):
                continue
            dist = ((px - sx) ** 2 + (py - sy) ** 2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_name = name

        return best_name

    def _closest_gas_to(self, base_pos: Any, bot: Any) -> Any | None:
        """找最近的 vespene geyser 到 base_pos。

        bot.vespene_geyser (python-sc2 Units) 是地图全部气矿点(neutral)。
        """
        geysers = getattr(bot, "vespene_geyser", None)
        if not geysers:
            return None
        try:
            return geysers.closest_to(base_pos).position
        except (AttributeError, IndexError):
            return None
