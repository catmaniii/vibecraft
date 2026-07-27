"""炮塔速攻前线 proxy：派探机贴边走到隐蔽点建 Pylon + PhotonCannon。

设计要点（2026-05-20 deep research + 用户战术细化；v7 多探机 / 多水晶）
======================================================================
cannon rush 成败的**第一要素是光炮能不能活到建完** —— warp 中的光炮极脆，
被发现就被工人 / 近战清掉。所以：选点隐蔽 + 探机贴边赶路（不暴露意图），
被发现后靠多水晶互相掩护 + 多探机并行抢建活下来。

- **Forge 在家建**（不前出）：proxy 探机只管 proxy Pylon + 光炮。光炮需
  psi 供电（proxy Pylon）+ Forge 科技 —— Forge 科技前置由家里那个 Forge
  满足。家里 Forge 一好，ForwardCannonProxy 就在 proxy Pylon 供电内拍光炮。
- **打击目标随机** natural / main，两种打法不同：
  - main（怼主矿）：探机**开局立刻出发**，从敌方 ramp 斜坡上高地。proxy 选在
    高地**空间更充足的那一侧**贴边（避开狭窄角 —— 狭窄侧探机容易被堵死、
    水晶 / 光炮没地方铺开），避开主矿矿区。先让光炮活下来、建成。
  - natural（卡二矿）：可稍晚一点点，proxy 选在敌方 natural **背后**贴边，
    目标是第一个光炮在对方二矿建好前 / 刚好时完成，限制二矿采矿。
- **Pylon 必须先建**：光炮要 psi 供电，proxy 点没 Pylon → find_placement
  永远 None。
- **蛙跳推进**（v8 用户战术）：第一波光炮**不**追求打到敌方建筑 —— 直接架在
  矿区 / 建筑旁，warp 没好就被工人 / 部队清掉。第一波只求藏好、活着建成；
  起来后用它们当**火力掩护**，朝敌方推进下一波 Pylon + 光炮（新一波落在前波
  光炮射程内 warp，被打有炮还击），一波波蛙跳爬向敌方建筑。
  - 第一波 Pylon / 光炮选点用 `_predicted_enemy_vision` 硬过滤 —— 敌方 Nexus /
    气矿 / 矿线建好后会照亮的范围全部排除（开局看不见但位置已知）。
  - `_pylon_target`：下一波 Pylon 从当前最前就绪光炮朝敌方推进 _WAVE_ADVANCE_STEP。
  - `_cannon_anchor`：光炮落点带"靠近敌方目标"偏置 + "在就绪光炮射程内"掩护约束。
- **多探机并行抢建**（v7 用户战术）：主探机血量低 / 被发现 → 再派 1 个探机
  （上限 2）。两个探机各自独立赶路、并行建 Pylon / 光炮 —— 降低被单点焊死、
  提高容错。每个探机一次只发一个 build 命令（per-worker `pending_build`）。
- **濒死光炮 cancel**：warp 中光炮被打、血量掉破阈值 → cancel 退 75% 矿。
- 探机保命：血量低撤退到 hide 点，恢复后继续。

完成判定（OR）
==============
A. 已建（含在建）≥ _TARGET_CANNONS 个 forward PhotonCannon —— 前线花钱到位
B. 超时 300s
C. 探机累计死亡 ≥ 4 次
D. 主力已出门压制
"""

from __future__ import annotations

import math
import random
from typing import Any

from loguru import logger
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

# 自家 Nexus 这个距离内的建筑视为"家里"
_MIN_HOME_DIST: float = 25.0
# proxy 点离打击目标锚点的硬上限（太远光炮够不到，cannon rush 无意义）
_MAX_ANCHOR_DIST: float = 22.0
# 任务超时（秒，游戏内）—— 赶路 + Pylon + 3 Cannon，给到 300s 兜底
_TASK_TIMEOUT_S: float = 300.0
# 单个 build 命令的耐心（秒）：发出后等这么久建筑还没出现 → 视为失败、重试
_BUILD_PATIENCE_S: float = 75.0
# 探机累计死亡次数上限（cannon proxy 贴敌方近，多砸几个探机换炮塔值得）
_MAX_WORKER_DEATHS: int = 4
# HP+shield 撤退/复出阈值
_RETREAT_RATIO: float = 0.5
_REENGAGE_RATIO: float = 0.9
# 目标 / 上限 forward PhotonCannon 数量（用户战术：前线尽量多花钱、多修光炮）
_TARGET_CANNONS: int = 6
# warp 中光炮：被攻击且 (health+shield) 掉破此值 → cancel 退 75% 矿
_CANNON_CANCEL_HP: float = 80.0
# 单个赶路航点的到达耐心（秒）：到不了（航点不可寻路）就跳过下一个
_WP_TIMEOUT_S: float = 30.0
# 赶路总放弃时限（秒）：超时还没到 proxy → 放弃航点、直接建（worker.build 自寻路）
_TRAVEL_GIVEUP_S: float = 90.0
# Pylon psi 供电半径（SC2 实测 6.5）—— 光炮选点要落在该范围内
_PYLON_POWER_R: float = 6.5
# proxy 探机上限（主 + 1 后补，再多浪费经济）
_MAX_PROXY_WORKERS: int = 2
# 主探机 HP+shield 比例掉破此值 → 补一个后补探机
_SECOND_WORKER_HP: float = 0.55
# 覆盖某光炮选点的 Pylon 血量（health+shield）低于此值视为"濒死供电"
_LOW_PYLON_HP: float = 120.0
# 一个 Pylon 供电范围内塞几个光炮就该往前推进下一波（蛙跳节奏）
_CANNONS_PER_PYLON: int = 2
# forward Pylon 数上限（蛙跳波数上限）
_MAX_PYLONS: int = 4
# 光炮射程（蛙跳掩护判定：新光炮要落在已有就绪光炮射程内）
_CANNON_RANGE: float = 7.0
# 蛙跳推进步长：下一波 Pylon 从当前最前光炮朝敌方推进的距离
# （< 光炮射程 → 新一波 Pylon / 光炮在前波火力掩护内 warp）
_WAVE_ADVANCE_STEP: float = 5.0


class _ProxyWorker:
    """单个 proxy 探机的状态（每个探机独立赶路 + 独立 build 命令跟踪）。"""

    def __init__(self, tag: int, now: float) -> None:
        self.tag: int = tag
        # 在途 build 命令：(建筑类型, 发出时刻, 目标落点)；用落点匹配判定起建
        self.pending_build: tuple[UnitTypeId, float, Point2] | None = None
        self.reached_proxy: bool = False
        self.route_idx: int = 0
        self.wp_since: float = now  # 当前航点开始时刻（到达耐心计时）
        self.assigned_at: float = now  # 指派时刻（赶路总放弃计时）
        self.retreating: bool = False


class ForwardCannonProxy(ActBase):  # type: ignore[misc]
    """炮塔速攻前线 proxy：探机赶到隐蔽点建 Pylon + 一片 PhotonCannon。

    序列：赶路（main 走 ramp 上高地）→ 隐蔽点第一波 Pylon + 光炮（求活着建成）
    → 用前波光炮当掩护朝敌方蛙跳推进后续波次，建满 _TARGET_CANNONS 个光炮。
    Forge 在家建（科技前置），不前出。打击目标每局随机 natural / main。
    多探机并行抢建、提高容错。
    """

    def __init__(self) -> None:
        super().__init__()
        self.proxy_location: Point2 | None = None
        self.hide_location: Point2 | None = None
        self._completed: bool = False

        # 探机死亡跟踪（累计，跨所有 proxy 探机）
        self._worker_death_count: int = 0
        # 任务起始 ts
        self._start_time: float | None = None
        # 见过的 forward cannon 数量峰值（含在建）—— 完成判定用
        self._max_cannons: int = 0
        # 打击目标：natural（卡二矿）或 main（怼主矿）—— 开局随机
        self._target_mode: str = random.choice(["natural", "main"])
        # warp 中光炮血量跟踪（濒死 cancel 用）：tag → 上一 tick 的 health+shield
        self._cannon_hp: dict[int, float] = {}
        # proxy 是否已暴露（被敌方看到 / forward 建筑或探机挨打）—— 一旦置位不复位
        self.discovered: bool = False
        # 当前 proxy 探机：tag → _ProxyWorker
        self._workers: dict[int, _ProxyWorker] = {}
        # 赶路航点（home → proxy，所有探机共用同一条路；route_idx 各自独立）
        self._route: list[Point2] = []
        # 敌方静态视野源缓存（Nexus / 矿 / 气矿点 —— 全程不变，算一次）
        self._enemy_vision_cache: list[tuple[Point2, float]] | None = None

    async def start(self, knowledge: Any) -> None:
        await super().start(knowledge)
        self.proxy_location = None
        self.hide_location = None

    # ------------------------------------------------------------------
    # 打击目标锚点 + 敌方 ramp
    # ------------------------------------------------------------------

    def _enemy_main_ramp(self) -> Any:
        """敌方主基地 ramp（top_center 离敌方主基地最近的那个）。"""
        try:
            enemy_main = self.ai.enemy_start_locations[0]
            ramps = self.ai.game_info.map_ramps
        except Exception:
            return None
        if not ramps:
            return None
        try:
            return min(ramps, key=lambda r: r.top_center.distance_to(enemy_main))
        except Exception:
            return None

    def _target_anchor(self) -> Point2 | None:
        """打击目标锚点：main = 敌方 ramp 顶上高地（避开矿区），natural = 敌方 natural。"""
        try:
            enemy_main: Point2 = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return None
        if self._target_mode == "main":
            ramp = self._enemy_main_ramp()
            if ramp is not None:
                try:
                    # ramp 顶往主基地里挪一点 = 稳在高地台子上（但不到矿区）
                    return ramp.top_center.towards(enemy_main, 4)
                except Exception:
                    return enemy_main
            return enemy_main
        nat = self._enemy_natural(enemy_main)
        return nat if nat is not None else enemy_main

    def _enemy_natural(self, enemy_main: Point2) -> Point2 | None:
        """敌方 natural = 离敌方主基地最近的扩张点（排除主基地本身）。"""
        try:
            exps = [e for e in self.ai.expansion_locations_list if e.distance_to(enemy_main) > 1.0]
        except Exception:
            return None
        if not exps:
            return None
        return min(exps, key=lambda e: e.distance_to(enemy_main))

    # ------------------------------------------------------------------
    # proxy 选点（隐蔽优先 + 目标差异化）
    # ------------------------------------------------------------------

    def _pick_proxy_location(self) -> Point2 | None:
        """选点策略：环绕打击目标锚点，隐蔽优先（natural 取背后 / main 取空间充足侧）。"""
        candidates = self._generate_cannon_candidates()
        if not candidates:
            return self._sharpy_fallback_proxy()

        scored = [(p, self._score_cannon_pos(p)) for p in candidates]
        scored = [(p, s) for p, s in scored if s > 0]
        if not scored:
            logger.info(f"ForwardCannon[{self._target_mode}]: 0 valid candidates → sharpy fallback")
            return self._sharpy_fallback_proxy()

        scored.sort(key=lambda x: x[1], reverse=True)
        top_n = scored[: min(3, len(scored))]
        chosen, score = random.choice(top_n)
        logger.info(
            f"ForwardCannon[{self._target_mode}] picked proxy={chosen} "
            f"(score={score:.1f}, {len(scored)}/{len(candidates)} valid)"
        )
        return chosen

    def _sharpy_fallback_proxy(self) -> Point2 | None:
        """兜底：打击目标锚点稍微偏地图中心一点（保证能建）。"""
        anchor = self._target_anchor()
        if anchor is None:
            return None
        try:
            return anchor.towards(self.ai.game_info.map_center, 8)
        except Exception:
            return anchor

    def _generate_cannon_candidates(self) -> list[Point2]:
        """生成候选点：环绕打击目标锚点不同角度 / 半径。"""
        anchor = self._target_anchor()
        if anchor is None:
            return []
        candidates: list[Point2] = []
        # 半径 8-20、每 20° 一个：密一点撒网，给视野外的隐蔽点更多候选
        for dist in (8, 11, 14, 17, 20):
            for angle_deg in range(0, 360, 20):
                angle_rad = math.radians(angle_deg)
                candidates.append(
                    Point2(
                        (
                            anchor.x + dist * math.cos(angle_rad),
                            anchor.y + dist * math.sin(angle_rad),
                        )
                    )
                )
        return candidates

    def _score_cannon_pos(self, pos: Point2) -> float:
        """评分 proxy 位置：隐蔽优先 + 目标差异化。

        - 公共：贴地图边（藏视野盲区）。
        - natural：proxy 选在 natural 背后（远离敌主基地那侧）。
        - main：proxy 选在高地**空间充足侧** + 贴边 + 远离敌方主矿矿区。
          狭窄侧（贴悬崖、可建格少）探机容易被堵死、水晶 / 光炮铺不开 ——
          `_openness` 衡量周围可建格密度，空间充足侧得分高。
        """
        anchor = self._target_anchor()
        if anchor is None:
            return -1.0
        if not self._in_map_bounds(pos):
            return -1.0
        # 第一波必须藏好 —— 排除敌方预测视野（含开局看不见的气矿 / 矿线）
        if self._predicted_enemy_vision(pos):
            return -1.0
        try:
            if not self.ai.in_placement_grid(pos):
                return -1.0
        except Exception:
            return -1.0
        d_anchor = pos.distance_to(anchor)
        if d_anchor > _MAX_ANCHOR_DIST:
            return -1.0

        s: float = 100.0
        # 公共：贴地图边（cannon rush proxy 藏视野盲区的近似）
        edge_d = self._edge_distance(pos)
        s += max(0.0, 45.0 - edge_d * 3.0)

        if self._target_mode == "natural":
            # 卡二矿：proxy 选在 natural "背后"（远离敌方主基地那一侧）
            try:
                enemy_main = self.ai.enemy_start_locations[0]
                vpx, vpy = pos.x - anchor.x, pos.y - anchor.y
                vax, vay = anchor.x - enemy_main.x, anchor.y - enemy_main.y
                ln = math.hypot(vax, vay)
                if ln > 1e-3:
                    behind = (vpx * vax + vpy * vay) / ln
                    s += max(0.0, behind) * 4.0  # 在 natural 背后加分
            except Exception:
                pass
            s -= d_anchor * 1.5  # 够得到二矿
        else:
            # 怼主矿：高地空间充足侧 + 贴边 + 远离主矿矿区（不在矿线晃悠）
            s += self._openness(pos) * 45.0  # 空间充足侧（可建格密度高）大幅加分
            corner_d = self._nearest_corner_dist(pos)
            s += max(0.0, 40.0 - corner_d * 1.2)
            try:
                d_nexus = pos.distance_to(self.ai.enemy_start_locations[0])
                s += min(d_nexus, 22.0) * 2.0  # 离敌方 nexus 越远越好（封顶 22）
            except Exception:
                pass
            s -= d_anchor * 0.5
        return s

    def _openness(self, pos: Point2) -> float:
        """pos 周围可建格密度（0-1）—— 高 = 空间充足侧，低 = 贴悬崖的狭窄侧。"""
        ok = 0
        total = 0
        for dx in (-6.0, -3.0, 0.0, 3.0, 6.0):
            for dy in (-6.0, -3.0, 0.0, 3.0, 6.0):
                total += 1
                cand = Point2((pos.x + dx, pos.y + dy))
                try:
                    if self.ai.in_placement_grid(cand):
                        ok += 1
                except Exception:
                    pass
        return ok / total if total else 0.0

    def _edge_distance(self, pos: Point2) -> float:
        """pos 到 playable_area 最近一条边的距离。"""
        try:
            area = self.ai.game_info.playable_area
            return float(
                min(
                    pos.x - area.x,
                    area.x + area.width - pos.x,
                    pos.y - area.y,
                    area.y + area.height - pos.y,
                )
            )
        except Exception:
            return 100.0

    def _nearest_corner_dist(self, pos: Point2) -> float:
        """pos 到最近地图角的距离。"""
        corners = self._map_corners()
        if not corners:
            return 100.0
        return float(min(pos.distance_to(c) for c in corners))

    def _map_corners(self) -> list[Point2]:
        """playable_area 四角（已内缩 8）。"""
        try:
            area = self.ai.game_info.playable_area
        except Exception:
            return []
        m = 8.0
        return [
            Point2((area.x + m, area.y + m)),
            Point2((area.x + m, area.y + area.height - m)),
            Point2((area.x + area.width - m, area.y + area.height - m)),
            Point2((area.x + area.width - m, area.y + m)),
        ]

    def _edge_waypoint(self, p: Point2) -> Point2:
        """把 p 投影到最近地图边、离边 ~7 处（贴边航点）。"""
        try:
            area = self.ai.game_info.playable_area
        except Exception:
            return p
        d_left = p.x - area.x
        d_right = area.x + area.width - p.x
        d_bottom = p.y - area.y
        d_top = area.y + area.height - p.y
        m = min(d_left, d_right, d_bottom, d_top)
        margin = 7.0
        if m == d_left:
            return Point2((area.x + margin, p.y))
        if m == d_right:
            return Point2((area.x + area.width - margin, p.y))
        if m == d_bottom:
            return Point2((p.x, area.y + margin))
        return Point2((p.x, area.y + area.height - margin))

    def _make_pathable(self, p: Point2) -> Point2:
        """把 p 朝地图中心微调到一个可寻路点（地图角 / 边常不可走）。"""
        try:
            center = self.ai.game_info.map_center
        except Exception:
            return p
        for step in (0.0, 4.0, 8.0, 12.0, 16.0, 20.0, 26.0):
            cand = p.towards(center, step) if step > 0.0 else p
            try:
                if self.ai.in_pathing_grid(cand):
                    return cand
            except Exception:
                return p
        return p

    def _in_map_bounds(self, pos: Point2) -> bool:
        """pos 是否在 playable_area 内。"""
        try:
            area = self.ai.game_info.playable_area
            return bool(
                area.x <= pos.x <= area.x + area.width and area.y <= pos.y <= area.y + area.height
            )
        except Exception:
            return True

    def _in_enemy_vision(self, pos: Point2) -> bool:
        """pos 是否在**当前可见**的敌方单位/建筑视野内。"""
        try:
            for e in self.ai.enemy_units | self.ai.enemy_structures:
                sight = getattr(e, "sight_range", 8.0)
                if pos.distance_to(e) < sight + 2.0:
                    return True
        except Exception:
            pass
        return False

    def _static_enemy_vision_sources(self) -> list[tuple[Point2, float]]:
        """开局选点时看不见、但位置已知、敌方建好后必然照亮 proxy 的静态视野源。

        = 敌方 Nexus（主 + 二矿）+ 这两个基地的矿点 / 气矿点。气矿 geyser 与
        矿点是地图固定资源，开局即全图已知（`expansion_locations_dict` 不受
        战争迷雾影响）—— 气矿建筑建好后视野 ~9、矿线农民视野 ~8 会照亮 proxy。
        结果缓存：资源点全程不变，只算一次。
        """
        if self._enemy_vision_cache is not None:
            return self._enemy_vision_cache
        out: list[tuple[Point2, float]] = []
        try:
            enemy_main = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return out
        bases: list[Point2] = [enemy_main]
        nat = self._enemy_natural(enemy_main)
        if nat is not None:
            bases.append(nat)
        try:
            exp_dict = dict(self.ai.expansion_locations_dict)
        except Exception:
            exp_dict = {}
        for b in bases:
            out.append((b, 12.0))  # Nexus 视野 ~11 + 余量
            if not exp_dict:
                continue
            key = min(exp_dict, key=lambda k: k.distance_to(b))
            if key.distance_to(b) > 8.0:
                continue
            try:
                for r in exp_dict[key]:
                    # 矿点 / 气矿点：农民驻守 + 气矿建筑视野，~10 半径排除
                    out.append((r.position, 10.0))
            except Exception:
                pass
        self._enemy_vision_cache = out
        return out

    def _predicted_enemy_vision(self, pos: Point2) -> bool:
        """pos 是否会落在敌方视野里 —— 当前可见的敌方单位 + 开局已知的静态视野源
        （敌方 Nexus / 气矿 / 矿线）。第一波 proxy + 光炮必须避开。"""
        if self._in_enemy_vision(pos):
            return True
        return any(
            pos.distance_to(src) < radius for src, radius in self._static_enemy_vision_sources()
        )

    async def _safe_find_placement(self, unit_type: UnitTypeId, near: Point2) -> Point2 | None:
        """find_placement 包装，handle async + exception。"""
        try:
            return await self.ai.find_placement(unit_type, near, max_distance=20)
        except Exception as exc:
            logger.warning(
                f"ForwardCannon find_placement fail for {unit_type.name} "
                f"near ({near.x:.1f},{near.y:.1f}): {exc}"
            )
            return None

    # ------------------------------------------------------------------
    # 赶路（main 经敌方 ramp 上高地；natural 经贴边航点）
    # ------------------------------------------------------------------

    def _compute_edge_route(self) -> list[Point2]:
        """home → proxy 的赶路航点。

        main：经敌方 ramp 底→顶（探机从斜坡上高地，绝不去矿区晃悠）。
        natural：经一个贴边航点（低地，别穿地图中央）。
        """
        if self.proxy_location is None:
            return []
        route: list[Point2] = []
        if self._target_mode == "main":
            ramp = self._enemy_main_ramp()
            if ramp is not None:
                try:
                    route = [ramp.bottom_center, ramp.top_center]
                except Exception:
                    route = []
        else:
            route = [self._edge_waypoint(self.proxy_location)]
        return [self._make_pathable(wp) for wp in route]

    def _travel_step(self, worker: Any, ws: _ProxyWorker) -> None:
        """沿 _route 航点把单个探机往 proxy 带一步；航点到不了（超时）就跳过。"""
        if self.proxy_location is None:
            return
        if ws.route_idx < len(self._route):
            wp = self._route[ws.route_idx]
            reached = worker.distance_to(wp) < 7.0
            stuck = self.ai.time - ws.wp_since > _WP_TIMEOUT_S
            if reached or stuck:
                ws.route_idx += 1
                ws.wp_since = self.ai.time
                return
            worker.move(wp)
            return
        # 航点走完 → 直奔 proxy
        worker.move(self.proxy_location)

    # ------------------------------------------------------------------
    # forward 建筑识别
    # ------------------------------------------------------------------

    def _is_forward_building(self, struct: Any) -> bool:
        """这栋建筑是否属于 forward proxy（不是家里的）。

        判据 = 远离我方所有基地。不用"靠近 proxy 点"判 —— 蛙跳推进后光炮会
        爬离初始 proxy 点很远，按 proxy 距离判会漏掉推进出去的光炮。cannon rush
        只在家附近（AutoPylon 的水晶）和前线建东西，远离家的即前线。
        """
        if self.proxy_location is None:
            return False
        return all(struct.distance_to(nx) >= _MIN_HOME_DIST for nx in self.ai.townhalls)

    def _forward_of(self, unit_type: UnitTypeId) -> list[Any]:
        """proxy 点附近、远离家的该类型自家建筑（含在建）。"""
        out: list[Any] = []
        try:
            for s in self.ai.structures(unit_type):
                if self._is_forward_building(s):
                    out.append(s)
        except Exception:
            pass
        return out

    def _home_forge_ready(self) -> bool:
        """任意 Forge 已建好 —— 家里那个就够（它是光炮的科技前置）。"""
        try:
            return bool(self.ai.structures(UnitTypeId.FORGE).ready.exists)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 暴露检测（被发现 → 多水晶互相掩护 + 补探机）
    # ------------------------------------------------------------------

    def _update_discovered(self) -> None:
        """proxy 是否已暴露：forward 建筑/探机被敌方看到或挨打 → 置位（不复位）。"""
        if self.discovered:
            return
        try:
            for s in self._forward_of(UnitTypeId.PYLON) + self._forward_of(UnitTypeId.PHOTONCANNON):
                if (float(s.health) + float(s.shield)) < (
                    float(s.health_max) + float(s.shield_max)
                ):
                    self.discovered = True
                    return
                if self._in_enemy_vision(s.position):
                    self.discovered = True
                    return
        except Exception:
            pass
        for tag in self._workers:
            w = self._alive(tag)
            if w is None:
                continue
            try:
                if (w.shield + w.health) < (w.shield_max + w.health_max):
                    self.discovered = True
                    return
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 濒死光炮 cancel（research：建造中不该被打死，退 75% 矿）
    # ------------------------------------------------------------------

    def _cancel_dying_cannons(self) -> None:
        """warp 中光炮被攻击、血量掉破阈值 → cancel 退 75% 矿。"""
        try:
            cannons = self._forward_of(UnitTypeId.PHOTONCANNON)
        except Exception:
            return
        alive_tags: set[int] = set()
        for c in cannons:
            alive_tags.add(c.tag)
            if c.build_progress >= 1.0:
                self._cannon_hp.pop(c.tag, None)
                continue
            hp_now = float(c.health) + float(c.shield)
            hp_prev = self._cannon_hp.get(c.tag)
            self._cannon_hp[c.tag] = hp_now
            if hp_prev is not None and hp_now < hp_prev and hp_now < _CANNON_CANCEL_HP:
                try:
                    c(AbilityId.CANCEL_BUILDINPROGRESS)
                    logger.info(f"ForwardCannon cancel dying cannon tag={c.tag} hp={hp_now:.0f}")
                except Exception:
                    pass
        for tag in list(self._cannon_hp):
            if tag not in alive_tags:
                self._cannon_hp.pop(tag, None)

    # ------------------------------------------------------------------
    # 探机管理（多探机：主 + 后补）
    # ------------------------------------------------------------------

    def _alive(self, tag: int) -> Any:
        """tag 对应探机还活着就返回它，否则 None。"""
        try:
            return self.cache.by_tag(tag)
        except Exception:
            return None

    def _reap_dead_workers(self) -> None:
        """清掉死亡探机、累计死亡数；任一探机死 → proxy 已暴露。"""
        for tag in list(self._workers):
            if self._alive(tag) is None:
                self._worker_death_count += 1
                self.discovered = True
                del self._workers[tag]
                logger.info(
                    f"ForwardCannon proxy worker {tag} lost (deaths={self._worker_death_count})"
                )

    def _ensure_workers(self) -> None:
        """保证至少 1 个 proxy 探机；主探机血量低 / 被发现 → 补到 2 个。"""
        if not self._workers:
            self._add_worker()
            return
        if len(self._workers) >= _MAX_PROXY_WORKERS:
            return
        need_backup = self.discovered
        for tag in self._workers:
            w = self._alive(tag)
            if w is None:
                continue
            hp_max = w.shield_max + w.health_max
            if hp_max > 0 and (w.shield + w.health) / hp_max < _SECOND_WORKER_HP:
                need_backup = True
        if need_backup:
            self._add_worker()

    def _add_worker(self) -> None:
        """指派一个新的 proxy 探机（离家最近、未在册的探机）。"""
        try:
            avail = [w for w in self.ai.workers if w.tag not in self._workers]
            if not avail:
                return
            w = min(avail, key=lambda u: u.distance_to(self.ai.start_location))
        except Exception:
            return
        self._workers[w.tag] = _ProxyWorker(w.tag, self.ai.time)
        try:
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, w)
        except Exception:
            pass
        logger.info(f"ForwardCannon assigned worker tag={w.tag} (total={len(self._workers)})")

    def _release_all_workers(self) -> None:
        """完成 / 终止时释放所有 proxy 探机。"""
        for tag in list(self._workers):
            w = self._alive(tag)
            if w is None:
                continue
            try:
                from sharpy.managers.core.roles import UnitTask

                self.knowledge.roles.clear_task(w)
                self.knowledge.roles.set_task(UnitTask.Idle, w)
            except Exception:
                pass
        self._workers.clear()

    # ------------------------------------------------------------------
    # 在途 build 命令跟踪（per-worker）
    # ------------------------------------------------------------------

    def _pending_resolved(self, ws: _ProxyWorker) -> bool:
        """该探机的 pending build 是否已起建（落点处出现建筑）或超时作废。

        用**落点匹配**判定（不是计数）—— 两个探机并行建同类型建筑时，计数会
        互相误判（A 的建筑出现会让 B 以为自己结算了）；按各自落点匹配才唯一。
        """
        if ws.pending_build is None:
            return True
        utype, issued_at, place = ws.pending_build
        for s in self._forward_of(utype):
            if s.position.distance_to(place) < 2.5:
                ws.pending_build = None
                return True
        if self.ai.time - issued_at > _BUILD_PATIENCE_S:
            logger.info(f"ForwardCannon {utype.name} build patience expired → retry")
            ws.pending_build = None
            return True
        return False

    def _pending_count(self, utype: UnitTypeId) -> int:
        """所有探机在途的该类型 build 命令数。"""
        return sum(
            1
            for ws in self._workers.values()
            if ws.pending_build is not None and ws.pending_build[0] == utype
        )

    # ------------------------------------------------------------------
    # 建造序列决策
    # ------------------------------------------------------------------

    def _desired_pylons(self, cannon_count: int) -> int:
        """供电水晶数随光炮数增长（每 _CANNONS_PER_PYLON 个光炮多 1 个供电点），
        封顶 _MAX_PYLONS。第一个光炮前只要 1 个 —— 核心炮优先，绝不被水晶拖延。"""
        return min(_MAX_PYLONS, 1 + cannon_count // _CANNONS_PER_PYLON)

    def _creep_target(self) -> Point2 | None:
        """蛙跳推进的方向目标：main = 敌方主基地 Nexus，natural = 敌方 natural。
        光炮农场一波波朝这个点爬。"""
        try:
            enemy_main: Point2 = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return None
        if self._target_mode == "natural":
            nat = self._enemy_natural(enemy_main)
            return nat if nat is not None else enemy_main
        return enemy_main

    def _ready_forward_cannons(self) -> list[Any]:
        """已建好（可开火）的 forward 光炮。"""
        return [c for c in self._forward_of(UnitTypeId.PHOTONCANNON) if c.is_ready]

    def _ready_cannon_count(self) -> int:
        """已建好的 forward 光炮数（蛙跳推进的掩护火力）。"""
        return len(self._ready_forward_cannons())

    def _frontmost_ready_cannon(self) -> Any:
        """离敌方目标最近的已就绪 forward 光炮（推进最前沿）。"""
        target = self._creep_target()
        cannons = self._ready_forward_cannons()
        if not cannons or target is None:
            return None
        return min(cannons, key=lambda c: c.position.distance_to(target))

    def _pylon_target(self, idx: int) -> Point2 | None:
        """第 idx 个 Pylon 的落点：第 0 个在 proxy 点（第一波隐蔽点），后续从当前
        最前就绪光炮朝敌方推进 _WAVE_ADVANCE_STEP（蛙跳 —— 新一波 Pylon 在前波
        光炮火力掩护内 warp）。"""
        if self.proxy_location is None:
            return None
        if idx <= 0:
            return self.proxy_location
        target = self._creep_target()
        if target is None:
            return self.proxy_location
        front = self._frontmost_ready_cannon()
        base: Point2 = front.position if front is not None else self.proxy_location
        return self._make_pathable(base.towards(target, _WAVE_ADVANCE_STEP))

    def _cannon_anchor(self, ready_pylons: list[Any]) -> Point2 | None:
        """光炮落点：在 Pylon 供电范围内、尽量靠近敌方目标（蛙跳推进）。

        候选 = 各 Pylon 处 + 各 Pylon 朝敌方探出一点（仍在供电半径内）+ 两两
        Pylon 中点。评分偏向**靠近敌方目标**（推进）；覆盖 Pylon 数 + 血量作次要
        项（保证有电、避开濒死 Pylon）。掩护约束分两种情形：
        - 已有就绪光炮（蛙跳波次）：落点不在任一就绪光炮射程内 → 重罚。
        - 还没就绪光炮（第一波）：落点在敌方预测视野内 → 重罚（务必藏好）。
        """
        if not ready_pylons:
            return None
        target = self._creep_target()
        ready_cannons = self._ready_forward_cannons()
        cands: list[Point2] = []
        for p in ready_pylons:
            cands.append(p.position)
            if target is not None:
                cands.append(p.position.towards(target, 3.0))
        for i in range(len(ready_pylons)):
            for j in range(i + 1, len(ready_pylons)):
                a, b = ready_pylons[i].position, ready_pylons[j].position
                if a.distance_to(b) <= 2 * _PYLON_POWER_R:
                    cands.append(Point2(((a.x + b.x) / 2, (a.y + b.y) / 2)))
        best: Point2 | None = None
        best_s = -1e18
        for c in cands:
            coverers = [p for p in ready_pylons if p.position.distance_to(c) <= _PYLON_POWER_R]
            if not coverers:
                continue
            min_hp = min(float(p.health) + float(p.shield) for p in coverers)
            s = len(coverers) * 4.0 + min_hp * 0.05
            if target is not None:
                s -= c.distance_to(target) * 6.0  # 越靠近敌方目标越优先（推进）
            if ready_cannons:
                # 蛙跳波次：新光炮要在某个就绪光炮射程内（warp 时有炮掩护）
                if not any(c.distance_to(rc.position) <= _CANNON_RANGE for rc in ready_cannons):
                    s -= 500.0
            elif self._predicted_enemy_vision(c):
                # 第一波（还没就绪光炮掩护）：必须藏在敌方视野外
                s -= 500.0
            if s > best_s:
                best_s, best = s, c
        return best

    def _anchor_pylon_hp(self, anchor: Point2, ready_pylons: list[Any]) -> float:
        """覆盖 anchor 的 Pylon 中最低血量（health+shield）；无覆盖返回 0。"""
        coverers = [p for p in ready_pylons if p.position.distance_to(anchor) <= _PYLON_POWER_R]
        if not coverers:
            return 0.0
        return min(float(p.health) + float(p.shield) for p in coverers)

    def _next_job(self) -> tuple[UnitTypeId, Point2 | None] | None:
        """空闲探机的下一个建造任务（None = 暂无、待命）。

        含在途命令计数 —— 两个探机同 tick 取任务不会重复抢同一栋。
        第一个光炮最优先（**绝不**被水晶拖延）；之后蛙跳推进：本波光炮塞满
        且已有就绪光炮掩护 → 朝敌方推进下一波 Pylon，建满 _TARGET_CANNONS。
        """
        fwd_pylons = self._forward_of(UnitTypeId.PYLON)
        fwd_cannons = self._forward_of(UnitTypeId.PHOTONCANNON)
        p_total = len(fwd_pylons) + self._pending_count(UnitTypeId.PYLON)
        c_total = len(fwd_cannons) + self._pending_count(UnitTypeId.PHOTONCANNON)

        # 1. 第一个 Pylon —— 光炮供电前置，必须先有
        if p_total == 0:
            return (UnitTypeId.PYLON, self._pylon_target(0))

        # 2. 光炮建满 → 收手
        if c_total >= _TARGET_CANNONS:
            return None

        ready_pylons = [p for p in fwd_pylons if p.is_ready]
        if ready_pylons and self._home_forge_ready():
            anchor = self._cannon_anchor(ready_pylons)
            if anchor is not None:
                weak = self._anchor_pylon_hp(anchor, ready_pylons) < _LOW_PYLON_HP
                saturated = c_total >= len(ready_pylons) * _CANNONS_PER_PYLON
                if p_total < self._desired_pylons(c_total):
                    # 濒死供电 → 立刻补水晶（应急，不等掩护）
                    if weak:
                        return (UnitTypeId.PYLON, self._pylon_target(p_total))
                    # 本波光炮塞满 → 推进下一波 Pylon，但要等本波光炮就绪
                    # （蛙跳 —— 新一波在前波火力掩护内 warp）
                    if saturated and self._ready_cannon_count() >= 1:
                        return (UnitTypeId.PYLON, self._pylon_target(p_total))
                return (UnitTypeId.PHOTONCANNON, anchor)

        # 3. 还没有可用供电点 → 缺水晶就补
        if p_total < self._desired_pylons(c_total):
            return (UnitTypeId.PYLON, self._pylon_target(p_total))
        return None

    # ------------------------------------------------------------------
    # 任务完成判定
    # ------------------------------------------------------------------

    def _is_done(self) -> bool:
        """完成条件 OR，任一满足返回 True。"""
        if self._max_cannons >= _TARGET_CANNONS:
            logger.info(f"ForwardCannon done (A: {self._max_cannons} cannons built)")
            return True
        if self._start_time is not None:
            elapsed = self.ai.time - self._start_time
            if elapsed > _TASK_TIMEOUT_S:
                logger.info(f"ForwardCannon done (B: timeout {elapsed:.0f}s)")
                return True
        if self._worker_death_count >= _MAX_WORKER_DEATHS:
            logger.info(f"ForwardCannon done (C: {self._worker_death_count} worker deaths)")
            return True
        try:
            if getattr(self.ai.knowledge.vibecraft, "combat_intent_override", None) == "attack":
                logger.info("ForwardCannon done (D: main army attacking)")
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # 主 execute
    # ------------------------------------------------------------------

    async def execute(self) -> bool:
        if self._completed:
            return True

        # 第一次 execute：初始化
        if self._start_time is None:
            self._start_time = self.ai.time
            logger.info(f"ForwardCannon start: target_mode={self._target_mode}")
        if self.proxy_location is None:
            self.proxy_location = self._pick_proxy_location()
            if self.proxy_location is None:
                return False
            self.hide_location = self.proxy_location.towards(self.ai.start_location, 10)
            self._route = self._compute_edge_route()

        # 每 tick 维护（与探机状态无关）
        self._cancel_dying_cannons()
        self._reap_dead_workers()
        self._update_discovered()
        self._max_cannons = max(self._max_cannons, len(self._forward_of(UnitTypeId.PHOTONCANNON)))

        # 完成判定
        if self._is_done():
            self._release_all_workers()
            self._completed = True
            return True

        try:
            if self._worker_death_count >= _MAX_WORKER_DEATHS:
                return False
            self._ensure_workers()
            if not self._workers:
                return False

            # 逐个探机推进（各自独立赶路 + 建造）
            for ws in list(self._workers.values()):
                worker = self._alive(ws.tag)
                if worker is None:
                    continue
                await self._step_worker(worker, ws)
        except Exception as exc:
            logger.warning(f"ForwardCannon execute failed: {exc}")

        return False

    async def _step_worker(self, worker: Any, ws: _ProxyWorker) -> None:
        """推进单个 proxy 探机：保命 → 赶路 → 结算在途命令 → 取下一个建造任务。"""
        # 保命评估
        hp_max = worker.shield_max + worker.health_max
        ratio = (worker.shield + worker.health) / hp_max if hp_max > 0 else 1.0
        if not ws.retreating and ratio < _RETREAT_RATIO:
            ws.retreating = True
            logger.debug(f"ForwardCannon worker {ws.tag} retreating (hp={ratio:.2f})")
        elif ws.retreating and ratio > _REENGAGE_RATIO:
            ws.retreating = False
            logger.debug(f"ForwardCannon worker {ws.tag} re-engaging (hp={ratio:.2f})")
        if ws.retreating:
            # 这个探机躲 —— 沿途掩护水晶交给未受威胁的探机 / 后补探机建。
            ws.pending_build = None
            if self.hide_location is not None and worker.distance_to(self.hide_location) > 4:
                worker.move(self.hide_location)
            return

        # 赶路：沿航点走到 proxy（main 走 ramp 上高地）。
        # 总超时兜底：赶路太久 → 放弃航点、直接建（worker.build 自寻路）。
        if not ws.reached_proxy:
            near_proxy = (
                self.proxy_location is not None and worker.distance_to(self.proxy_location) < 9.0
            )
            travel_elapsed = self.ai.time - ws.assigned_at
            if near_proxy or travel_elapsed > _TRAVEL_GIVEUP_S:
                if not near_proxy:
                    logger.info(f"ForwardCannon worker {ws.tag} travel give up → direct build")
                ws.reached_proxy = True
            else:
                self._travel_step(worker, ws)
                return

        # 上一个 build 命令还在途中 → 别发新命令
        if not self._pending_resolved(ws):
            return

        # 取下一个建造任务
        job = self._next_job()
        if job is None:
            self._redirect_worker_to_anchor(worker)
            return
        utype, near = job
        await self._try_build(worker, ws, utype, near)

    async def _try_build(
        self, worker: Any, ws: _ProxyWorker, unit_type: UnitTypeId, near: Point2 | None
    ) -> None:
        """发一个 build 命令（只在 _pending_resolved 后调用）；记录到 ws.pending_build。"""
        if near is None:
            self._redirect_worker_to_anchor(worker)
            return
        if not self.ai.can_afford(unit_type):
            self._redirect_worker_to_anchor(worker)
            return
        target = near
        if unit_type == UnitTypeId.PHOTONCANNON:
            # 并行修多个光炮：每个探机的落点加随机抖动，避免两个探机抢同一格
            ang = random.uniform(0.0, 2.0 * math.pi)
            target = Point2((near.x + 2.5 * math.cos(ang), near.y + 2.5 * math.sin(ang)))
        place = await self._safe_find_placement(unit_type, target)
        if place is None:
            self._redirect_worker_to_anchor(worker)
            return
        worker.build(unit_type, place)
        ws.pending_build = (unit_type, self.ai.time, place)
        logger.info(
            f"ForwardCannon worker {ws.tag} build {unit_type.name} at ({place.x:.1f},{place.y:.1f})"
        )

    # ------------------------------------------------------------------
    # worker 锚定（防 auto-mining 走回家）
    # ------------------------------------------------------------------

    def _redirect_worker_to_anchor(self, worker: Any) -> None:
        """探机空闲且偏离前沿 → 拉回锚点（防 auto-mining 走回家）。

        锚点取最靠敌方的 forward Pylon —— 探机待在推进前沿，下一波一触发就近建。
        """
        target = self._creep_target()
        pylons = self._forward_of(UnitTypeId.PYLON)
        anchor: Point2 | None
        if pylons and target is not None:
            anchor = min(pylons, key=lambda p: p.position.distance_to(target)).position
        elif pylons:
            anchor = pylons[0].position
        else:
            anchor = self.proxy_location
        if anchor is None:
            return
        if worker.distance_to(anchor) <= 4:
            return
        worker.move(anchor)
