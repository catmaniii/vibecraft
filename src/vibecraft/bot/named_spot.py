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
import math
from dataclasses import dataclass
from typing import Any  # bot is sharpy KnowledgeBot, duck-type

# 不强 import sc2.position.Point2 (lazy avoid sharpy 提前 wire)
# 实际 return 类型是 sc2.position.Point2

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DropTarget:
    """空投目标 — NamedSpotRegistry.resolve_drop_target 返回。"""

    position: Any  # Point2; 矿区已 optimize_drop_pos_to_edge
    zone_kind: str  # "mineral" | "production"
    base_index: int  # 0/1/2/3+ (enemy_main=0); clock-based 为 -1
    source_spec: str  # 原 spec(给日志/PWA 显示)


# ============================================================
# Module-level helpers (testable in isolation)
# ============================================================

_DROP_R: float = 15.0  # 矿区圆周半径(棱镜在圆周上,DT 走 R 格到矿心)
_DROP_ZONE_KINDS: frozenset[str] = frozenset({"mineral", "production", "ramp_outside", "safe_edge"})
# ramp_outside: zone.ramp.bottom_center 向外偏移 5 格(低地,DT warp 出来不被高地建筑攻击)
_RAMP_OUTSIDE_OFFSET: float = -5.0
# safe_edge: zone.center_location 沿最近地图边推到边缘,留 _EDGE_CLEARANCE 格 buffer。
# 用于棱镜 warp_pos:贴边远离 nexus 视野(corner spawn ~26-40 grid 距 nexus)。
_EDGE_CLEARANCE: float = 2.0


def _safe_edge_from_nexus(nexus: Any, playable: Any) -> Any:
    """sun: nexus 沿最近地图边推到边缘(留 _EDGE_CLEARANCE 格 clearance)。

    2026-05-24 新增。棱镜 warp_pos 用 —— 贴边远离 enemy nexus 视野。
    corner spawn 典型距 nexus 26-40 grid(超出建筑 + 移动单位视野)。
    """
    from sc2.position import Point2

    dl = nexus.x - playable.x
    dr = playable.x + playable.width - nexus.x
    dt_ = playable.y + playable.height - nexus.y
    db = nexus.y - playable.y
    min_d = min(dl, dr, dt_, db)
    eps = 1e-6
    if abs(min_d - dl) < eps:
        return Point2((playable.x + _EDGE_CLEARANCE, nexus.y))
    if abs(min_d - dr) < eps:
        return Point2((playable.x + playable.width - _EDGE_CLEARANCE, nexus.y))
    if abs(min_d - dt_) < eps:
        return Point2((nexus.x, playable.y + playable.height - _EDGE_CLEARANCE))
    return Point2((nexus.x, playable.y + _EDGE_CLEARANCE))


def _optimize_drop_pos_to_edge(M: Any, R: float, playable: Any) -> Any:
    """矿区 drop_pos = M 到最近地图边缘方向,距 M = R 的圆周点。

    棱镜在地图边缘 = 远离敌方主力,DT 卸下走 R 格到矿区。
    """
    from sc2.position import Point2

    dl = M.x - playable.x
    dr = playable.x + playable.width - M.x
    dt_ = playable.y + playable.height - M.y
    db = M.y - playable.y
    min_dist = min(dl, dr, dt_, db)
    eps = 1e-6
    if abs(min_dist - dl) < eps:
        return Point2((M.x - R, M.y))
    if abs(min_dist - dr) < eps:
        return Point2((M.x + R, M.y))
    if abs(min_dist - dt_) < eps:
        return Point2((M.x, M.y + R))
    return Point2((M.x, M.y - R))


def _clock_at_expansion(clock: int, bot: Any, anchor: Any = None) -> Any | None:
    """钟点方向最近的 expansion (12点=正上,3点=正右,6点=正下,9点=正左)。

    Args:
        clock: 1-12 钟点数
        bot: sharpy KnowledgeBot
        anchor: 锚点 Point2；None 则用 bot.game_info.map_center（向后兼容）
    """
    if anchor is None:
        anchor = bot.game_info.map_center
    # 12 点 = π/2(正上); 3 点 = 0(正右); clock 每格减 30°
    target_angle = (math.pi / 2) - ((clock % 12) * math.pi / 6)
    best = None
    best_d = float("inf")
    for p in bot.expansion_locations_list:
        angle = math.atan2(p.y - anchor.y, p.x - anchor.x)
        diff = abs((angle - target_angle + math.pi) % (2 * math.pi) - math.pi)
        if diff < best_d:
            best_d = diff
            best = p
    return best


# ============================================================
# Clock / direction spot constants
# ============================================================

# 无前缀 clock_X：锚点 = map_center
_CLOCK_BASE_SPOTS: frozenset[str] = frozenset(f"clock_{i}" for i in range(1, 13))
# 自方锚点 = own_main（enemy_start_locations[0] 对面 expansion）
_OWN_CLOCK_SPOTS: frozenset[str] = frozenset(f"own_clock_{i}" for i in range(1, 13))
# 敌方锚点 = enemy_main（enemy_start_locations[0]）
_ENEMY_CLOCK_SPOTS: frozenset[str] = frozenset(f"enemy_clock_{i}" for i in range(1, 13))

# 方位 alias → clock 数字映射（LLM 把"上面/下面/左/右"翻译为 top/bottom/left/right）
_DIRECTION_TO_CLOCK: dict[str, int] = {
    "top": 12,
    "bottom": 6,
    "left": 9,
    "right": 3,
    "top_left": 11,
    "top_right": 1,
    "bottom_left": 8,
    "bottom_right": 5,
}
_OWN_DIRECTION_SPOTS: frozenset[str] = frozenset(f"own_{d}" for d in _DIRECTION_TO_CLOCK)
_ENEMY_DIRECTION_SPOTS: frozenset[str] = frozenset(f"enemy_{d}" for d in _DIRECTION_TO_CLOCK)

# 建 townhall 时必须 snap 到这些预算好的"贴矿最优位"——见 closest_expansion_location。
# 基础 townhall（建造出来的）；升级体(ORBITAL/PLANETARY/LAIR/HIVE)不会被 build 直接造,
# 但收进来无害(防御性)。
TOWNHALL_TYPE_NAMES: frozenset[str] = frozenset(
    {
        "NEXUS",
        "COMMANDCENTER",
        "ORBITALCOMMAND",
        "PLANETARYFORTRESS",
        "HATCHERY",
        "LAIR",
        "HIVE",
    }
)


# 建 townhall 落点三档(2026-06-09 用户):按指定点离最近 expansion 的距离分档。
#  ≤ 8 格(SNAP):离最近矿很近 → 直接 snap 到贴矿最优位(就是想要那片矿、只点偏一点),静默。
#  8 ~ 13 格(CONFIRM):偏了但不算太离谱 → 弹确认让玩家选"修正到矿区"还是"就在原地"。
#  > 13 格:明显故意造偏(挡路/卡口/proxy) → 直接按玩家指定位建,不问。
# 8 ≈ 一屏(~24 格)的 1/3(框矿区镜头中心一般在此内);13 = 攻城坦克 siege 射程(Liquipedia),
# 超此基本不可能是"想框那片矿点偏了"。两常量都可单独调。
TOWNHALL_SNAP_MAX_DIST: float = 8.0
TOWNHALL_CONFIRM_MAX_DIST: float = 13.0


def closest_expansion_location(point: Any, bot: Any) -> Any | None:
    """返回离 point 最近的 expansion 落点（贴矿最优 townhall 位）；无 expansion 数据返 None。

    2026-06-09 修"在这里造基地造歪了"：build_at by_probe 造 Nexus 原来直接对镜头点
    find_placement，只找"最近能放下的点"，不贴矿 → 基地歪在矿区旁、农民没近矿可采 →
    新基地农民全 idle（DistributeWorkers 没法分配）。修法：建 townhall 前先把目标点
    snap 到这里返回的最优位（= bot 自己开矿用的 `expansion_locations_list` /
    sharpy `zone_manager.expansion_zones[i].center_location`），再 find_placement。

    优先 sharpy zone_manager（更准），fallback python-sc2 expansion_locations_list。
    """
    from sc2.position import Point2

    candidates: list[Any] = []
    zone_mgr = getattr(getattr(bot, "knowledge", None), "zone_manager", None)
    if zone_mgr is not None and hasattr(zone_mgr, "expansion_zones"):
        for z in zone_mgr.expansion_zones or []:
            c = getattr(z, "center_location", None)
            if c is not None:
                candidates.append(c)
    if not candidates:
        exp_list = getattr(bot, "expansion_locations_list", None)
        if exp_list:
            candidates = list(exp_list)
    if not candidates:
        return None
    p = Point2(point)
    return min(candidates, key=lambda c: c.distance_to(p))


def snap_townhall_point(
    point: Any, bot: Any, max_distance: float = TOWNHALL_SNAP_MAX_DIST
) -> tuple[Any, bool]:
    """建 townhall 落点策略：近矿则 snap 到贴矿最优位，偏太多则尊重玩家指定位。

    2026-06-09 用户：挡路/卡口基地是真实战术（人虫神都有），故意造偏的基地要允许。
    所以只有玩家指定点**离最近 expansion ≤ max_distance**（说明就是想要那片矿、只是点偏了）
    才 snap；偏太多 → 原样返回，按玩家指定位置附近建（find_placement 兜底找合法点）。

    返回 (落点 Point2, 是否 snap 了)。无 expansion 数据 / 偏太远 → (原始点, False)。
    """
    from sc2.position import Point2

    p = Point2(point)
    nearest = closest_expansion_location(point, bot)
    if nearest is None:
        return p, False
    if p.distance_to(nearest) <= max_distance:
        return Point2((float(nearest.x), float(nearest.y))), True
    return p, False


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
            # 2026-05-25 用户:Xel'Naga 瞭望塔(地图战略点,通常 2 个对称分布)
            # 玩家说"左边/右边瞭望塔" → 按 x 坐标分;只 1 个就 watchtower_left
            # 和 watchtower_right 都返回同一个;0 个返 None。
            "watchtower",  # 任一(取第一个,fallback)
            "watchtower_left",  # 按 x 坐标最小
            "watchtower_right",  # 按 x 坐标最大
            # 2026-05-27 用户:"前线"= 我方推进点(玩家"在前线造水晶折跃追猎")。
            # 多矿:取已建 nexus 中距 enemy_main 最近者(自方占领的最前沿);
            # 单矿:fallback main_ramp.bottom_center(主斜坡外侧低地);
            # 再 fallback:own_main 向 enemy_main 推 5 格。
            "forward",
            # *_gas 变种
            "natural_gas",
            "third_gas",
            "main_gas",
            "enemy_main_gas",
            "enemy_natural_gas",
            "enemy_third_gas",
        }
        | _CLOCK_BASE_SPOTS
        | _OWN_CLOCK_SPOTS
        | _ENEMY_CLOCK_SPOTS
        | _OWN_DIRECTION_SPOTS
        | _ENEMY_DIRECTION_SPOTS
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
        if name == "watchtower":
            return self._watchtower(bot, side=None)
        if name == "watchtower_left":
            return self._watchtower(bot, side="left")
        if name == "watchtower_right":
            return self._watchtower(bot, side="right")
        if name == "forward":
            return self._forward(bot)
        # ---- clock_X / own_clock_X / enemy_clock_X dispatch ----
        import re

        m = re.match(r"^(enemy_|own_)?clock_(1[0-2]|[1-9])$", name)
        if m:
            prefix = m.group(1)  # "enemy_" | "own_" | None
            clock = int(m.group(2))
            if prefix == "enemy_":
                anchor = self._enemy_main(bot)
                if anchor is None:
                    return None
            elif prefix == "own_":
                anchor = self._own_main(bot)
                if anchor is None:
                    return None
            else:
                anchor = None  # will use map_center inside _clock_at_expansion
            try:
                return _clock_at_expansion(clock, bot, anchor=anchor)
            except AttributeError:
                # bot 不具备 game_info.map_center（mock / 单测场景）
                return None

        # ---- 方位 alias dispatch (own_top / enemy_bottom_right 等) ----
        for _prefix in ("enemy_", "own_"):
            if name.startswith(_prefix):
                direction = name[len(_prefix) :]
                if direction in _DIRECTION_TO_CLOCK:
                    clock_name = f"{_prefix}clock_{_DIRECTION_TO_CLOCK[direction]}"
                    return self.resolve(clock_name, bot)

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
        """自方主基地 = 距 start_location 最近的 townhall。

        ⚠️ **不能用 `townhalls.first`**(2026-06-17 真局坐实):`bot.townhalls` 是 Units,
        开了分基地后**帧间顺序不保证稳定** → `.first` 每帧可能返回不同 Nexus → "main" 解析
        在多个基地点之间跳变。下游每帧拿它当目标(如 standby"回家")→ 目标点跳 → 单位抽搐
        (用户「目标坐标锁定」规则的上游:连解析本身都得确定性)。改取**距 start_location 最近**
        的 townhall(就是真正的主基地),帧间稳定。
        """
        townhalls = getattr(bot, "townhalls", None)
        if townhalls is None or not townhalls:
            return None
        try:
            start = getattr(bot, "start_location", None)
            if start is not None:
                return townhalls.closest_to(start).position
            return townhalls.first.position
        except (AttributeError, IndexError, ValueError):
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
            return getattr(ramp, "top_center", None) or getattr(ramp, "depot_in_middle", None)
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

    def _watchtower(self, bot: Any, side: str | None) -> Any | None:
        """Xel'Naga 瞭望塔(neutral structure)。side=left/right 按 x 坐标分;
        None 取第一个;塔不在/被推时返 None。

        SC2 Xel'Naga Tower 是 UnitTypeId.XELNAGATOWER,通常每张地图 2 个对称分布。
        """
        try:
            from sc2.ids.unit_typeid import UnitTypeId
        except ImportError:
            return None
        # neutral structures 在 bot.all_units 里(不在 bot.structures - 它是己方专)
        all_units = getattr(bot, "all_units", None)
        if all_units is None:
            return None
        try:
            towers = list(all_units(UnitTypeId.XELNAGATOWER))
        except Exception:
            return None
        if not towers:
            return None
        if side is None:
            return towers[0].position
        # 按 x 坐标排序:left = 最小 x, right = 最大 x
        towers_sorted = sorted(towers, key=lambda t: t.position.x)
        if side == "left":
            return towers_sorted[0].position
        if side == "right":
            return towers_sorted[-1].position
        return towers[0].position

    def _forward(self, bot: Any) -> Any | None:
        """ "前线" = 我方推进点(2026-05-27 用户)。

        语义优先级:
        1. 多矿:取已建 nexus(bot.townhalls)中距 enemy_main 最近者
           (自方占领的最前沿矿)。
        2. 单矿:fallback main_ramp.bottom_center(主斜坡外侧低地,折跃水晶
           典型位置)。
        3. 再 fallback:own_main 向 enemy_main 推 5 格。

        典型场景:玩家"在前线造水晶方便折跃追猎"。
        """
        enemy = self._enemy_main(bot)
        if enemy is None:
            return None
        townhalls = getattr(bot, "townhalls", None)
        if townhalls is not None:
            try:
                count = len(townhalls)
            except TypeError:
                count = 0
            if count > 1:
                try:
                    return townhalls.closest_to(enemy).position
                except (AttributeError, IndexError):
                    pass
        # 单矿(或 closest_to 失败):主斜坡下方
        ramp = getattr(bot, "main_base_ramp", None)
        if ramp is not None:
            bottom = getattr(ramp, "bottom_center", None)
            if bottom is not None:
                return bottom
            top = getattr(ramp, "top_center", None)
            if top is not None:
                return top
        # 终极 fallback:own_main 朝 enemy 走 5 格
        if townhalls is not None:
            try:
                from sc2.position import Point2

                home = townhalls.first.position
                return Point2((float(home.x), float(home.y))).towards(
                    Point2((float(enemy.x), float(enemy.y))),
                    5.0,
                )
            except (AttributeError, IndexError, TypeError):
                pass
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

    def resolve_drop_target(self, spec: str, bot: Any) -> DropTarget | None:
        """解析 drop spec → DropTarget。

        spec 格式: <base_ref>:<zone_kind>
          base_ref: enemy_main | enemy_natural | enemy_third | clock_{0..11} | map_center
          zone_kind: mineral | production | ramp_outside
            production 仅 enemy_main/natural/third 有效;
            clock_X / map_center 只 mineral;
            ramp_outside 仅 enemy_main/natural/third 有效:
              zone.ramp.bottom_center 向外 (远离 zone.center) 偏移 _RAMP_OUTSIDE_OFFSET 格。
              设计 §5: DT warp 在此点不被高地建筑直接射击。
        """
        if ":" not in spec:
            return None
        base_ref, _, zone_kind = spec.partition(":")
        if zone_kind not in _DROP_ZONE_KINDS:
            return None

        # 找 zone + base_index
        zone = None
        base_index = -1
        try:
            zones = bot.knowledge.zone_manager.enemy_expansion_zones
        except AttributeError:
            zones = []

        if base_ref == "enemy_main" and len(zones) > 0:
            zone, base_index = zones[0], 0
        elif base_ref == "enemy_natural" and len(zones) > 1:
            zone, base_index = zones[1], 1
        elif base_ref == "enemy_third" and len(zones) > 2:
            zone, base_index = zones[2], 2
        elif base_ref.startswith("clock_"):
            # clock_X 只支持 mineral
            if zone_kind != "mineral":
                return None
            try:
                clock = int(base_ref.removeprefix("clock_"))
            except ValueError:
                return None
            exp_pos = _clock_at_expansion(clock, bot)
            if exp_pos is None:
                return None
            playable = bot.game_info.playable_area
            drop_pos = _optimize_drop_pos_to_edge(exp_pos, _DROP_R, playable)
            return DropTarget(
                position=drop_pos,
                zone_kind="mineral",
                base_index=-1,
                source_spec=spec,
            )
        elif base_ref == "map_center":
            if zone_kind != "mineral":
                return None
            center = bot.game_info.map_center
            best = None
            best_d = float("inf")
            for p in bot.expansion_locations_list:
                d = ((p.x - center.x) ** 2 + (p.y - center.y) ** 2) ** 0.5
                if d < best_d:
                    best_d = d
                    best = p
            if best is None:
                return None
            playable = bot.game_info.playable_area
            drop_pos = _optimize_drop_pos_to_edge(best, _DROP_R, playable)
            return DropTarget(
                position=drop_pos,
                zone_kind="mineral",
                base_index=-1,
                source_spec=spec,
            )
        else:
            return None

        # enemy_main/natural/third 分支: zone 不为 None
        if zone is None:
            return None
        if zone_kind == "mineral":
            M = zone.behind_mineral_position_center
            playable = bot.game_info.playable_area
            drop_pos = _optimize_drop_pos_to_edge(M, _DROP_R, playable)
            return DropTarget(
                position=drop_pos,
                zone_kind="mineral",
                base_index=base_index,
                source_spec=spec,
            )
        if zone_kind == "safe_edge":
            # 2026-05-24 新增:zone.center_location 沿最近地图边推到边缘
            # (留 _EDGE_CLEARANCE 格 buffer)。棱镜 warp_pos 用:贴边远离
            # nexus 视野。corner spawn 典型距 nexus 26-40 grid。
            try:
                playable = bot.game_info.playable_area
                pos = _safe_edge_from_nexus(zone.center_location, playable)
            except Exception as exc:
                logger.warning("resolve_drop_target safe_edge failed: %s", exc)
                return None
            return DropTarget(
                position=pos,
                zone_kind="safe_edge",
                base_index=base_index,
                source_spec=spec,
            )
        if zone_kind == "ramp_outside":
            # zone.ramp.bottom_center 向外(远离 zone center)偏移 _RAMP_OUTSIDE_OFFSET 格
            # 设计 §5: DT warp 在斜坡外低地,不被高地建筑直接攻击
            try:
                from sc2.position import Point2

                ramp = zone.ramp
                bottom = ramp.bottom_center
                center = zone.center_location
                # towards(target, distance): distance < 0 → 反向(远离 center)
                ramp_pos = Point2((float(bottom.x), float(bottom.y))).towards(
                    Point2((float(center.x), float(center.y))),
                    _RAMP_OUTSIDE_OFFSET,
                )
            except Exception as exc:
                logger.warning("resolve_drop_target ramp_outside failed: %s", exc)
                return None
            return DropTarget(
                position=ramp_pos,
                zone_kind="ramp_outside",
                base_index=base_index,
                source_spec=spec,
            )
        # production
        return DropTarget(
            position=zone.center_location,
            zone_kind="production",
            base_index=base_index,
            source_spec=spec,
        )

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
        # forward 是合成 spot(自方推进点),反向查找无意义 — 玩家想知道
        # "我兵到 natural 了"而不是"到 forward 了",前者更精确。
        non_gas = [s for s in self.KNOWN_SPOTS if not s.endswith("_gas") and s != "forward"]
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
