"""4 BG 早压前线支援：派 1 农民兼做"探路 + 保命 + 隐蔽地点修 BE + 野 BG"。

设计目标
========
- proxy 点尽可能离敌方主基地近（折跃 timing 短），但不被敌方视野看到
- 候选点：敌方扩张点（含 natural/third/...）+ 环形点兜底
- 评分：距离越近越高分；硬过滤掉敌方视野/placement 不可行的
- top 3 候选随机选（防被对手摸到规律）

判定 forward 建筑 vs 家里建筑
=============================
- 空间过滤：靠近 proxy（< 30）且远离所有自家 Nexus（> 25）
- tag 跟踪：一次识别后用 tag 锁定，避免与其它建筑混淆
- 5 状态：none / ordering / in_progress / ready / destroyed

完成判定（5 重 OR）
==================
A. PYLON ready + GATEWAY ready
B. PYLON 和 GATEWAY 都曾经 ready（destroyed 也算 —— 不重建被拆的）
C. 超时 90s
D. worker 死亡 ≥ 2 次（不再砸农民）
E. 主力已出门压制（VibeCraftZoneAttack 已触发，proxy 已失去意义）

worker 管理
===========
- worker 死了/丢了 → 重新指派 + 死亡计数 +1
- 死亡计数 ≥ 2 → 整个任务终止
"""

from __future__ import annotations

import logging
import math
import random
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

logger = logging.getLogger(__name__)

# proxy 周围检测半径（forward 建筑必须在 proxy 这个范围内）
_PROXY_R: float = 30.0
# 自家所有 Nexus 这个距离内的建筑视为"家里"，不算 forward
_MIN_HOME_DIST: float = 25.0
# 距离敌方主基地的硬下限（太近敌方视野必看到）
_MIN_DIST_TO_ENEMY: float = 12.0
# 距离敌方主基地的硬上限（太远没折跃 timing 价值）
_MAX_DIST_TO_ENEMY: float = 55.0
# 任务超时（秒，游戏内）
_TASK_TIMEOUT_S: float = 90.0
# worker 死亡次数上限（再多就放弃）
_MAX_WORKER_DEATHS: int = 2
# HP+shield 撤退/复出阈值
_RETREAT_RATIO: float = 0.5
_REENGAGE_RATIO: float = 0.9

# 建筑 type → 对应 BUILD AbilityId（用来识别 worker 是否在 ordering 该建筑）
_BUILD_ABILITIES: dict[UnitTypeId, set[AbilityId]] = {
    UnitTypeId.PYLON: {AbilityId.PROTOSSBUILD_PYLON},
    UnitTypeId.GATEWAY: {AbilityId.PROTOSSBUILD_GATEWAY},
}


class ForwardSupportPylonGateway(ActBase):  # type: ignore[misc]
    """4bg 早压前线支援：1 农民造 1 BE + 1 野 BG，保命优先，必要时牺牲。"""

    def __init__(self) -> None:
        super().__init__()
        self.proxy_worker_tag: int | None = None
        self.proxy_location: Point2 | None = None
        self.hide_location: Point2 | None = None  # retreat 时躲的位置
        self._completed: bool = False
        self.retreating: bool = False

        # ---- tag 跟踪 ----
        # type → 锁定的 forward 建筑 tag（None 表示还没出现 / 还没锁定）
        self._proxy_tags: dict[UnitTypeId, int] = {}
        # 历史曾 ready 过的 tag（区分 destroyed vs 没造完就被拆）
        self._ever_ready: set[int] = set()

        # ---- worker 死亡跟踪 ----
        self._worker_death_count: int = 0

        # ---- 任务起始 ts（超时判断）----
        self._start_time: float | None = None

    async def start(self, knowledge: Any) -> None:
        await super().start(knowledge)
        # proxy 选点延后到第一次 execute（需要 game_info + zone_manager 完整 init）
        self.proxy_location = None
        self.hide_location = None

    # ------------------------------------------------------------------
    # proxy 选点
    # ------------------------------------------------------------------

    def _pick_proxy_location(self) -> Point2 | None:
        """从候选点里选最好的（top 3 随机）。

        Fallback 链：
        1. 优先：自定义候选（敌方扩张 zones + 环形）按评分排序，top 3 随机
        2. 兜底：所有候选硬过滤后空（极端地图 / 敌方视野全覆盖）→
           **沿用 sharpy 标准算法**（参考 sharpy/dummies/protoss/proxy_zealot_rush.py:73）
           `map_center.towards(enemy_start, 25)` —— 经过 sharpy 实战验证的兜底点
        """
        candidates = self._generate_candidates()
        if not candidates:
            return self._sharpy_fallback_proxy()

        # 评分 + 过滤负分（硬约束违反）
        scored = [(p, self._score_pos(p)) for p in candidates]
        scored = [(p, s) for p, s in scored if s > 0]
        if not scored:
            logger.info(
                "ForwardSupport: 0 valid candidates after hard filter → sharpy fallback"
            )
            return self._sharpy_fallback_proxy()

        scored.sort(key=lambda x: x[1], reverse=True)
        # top 3 随机
        top_n = scored[: min(3, len(scored))]
        chosen, score = random.choice(top_n)
        logger.info(
            "ForwardSupport picked proxy=%s (score=%.1f, %d/%d candidates valid)",
            chosen,
            score,
            len(scored),
            len(candidates),
        )
        return chosen

    def _sharpy_fallback_proxy(self) -> Point2 | None:
        """sharpy 标准算法：map_center 朝敌方主基地 25 距离。

        参考 sharpy/dummies/protoss/proxy_zealot_rush.py:73 的实现，
        这是 sharpy 实战 proxy zealot rush 用的固定算法，在大多数 SC2 标准
        地图上是合理的中场盲区。
        """
        try:
            return self.ai.game_info.map_center.towards(
                self.ai.enemy_start_locations[0], 25
            )
        except Exception:
            return None

    def _generate_candidates(self) -> list[Point2]:
        """生成候选 proxy 点：敌方扩张点（含 natural）+ 环形点。"""
        candidates: list[Point2] = []
        try:
            enemy = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return []

        # A. 敌方扩张点 zones[1:]（跳过 enemy_main 本身）
        zone_mgr = getattr(self.knowledge, "zone_manager", None)
        enemy_zones = getattr(zone_mgr, "enemy_expansion_zones", None) if zone_mgr else None
        if enemy_zones:
            for zone in enemy_zones[1:]:
                pos = getattr(zone, "center_location", None)
                if pos is not None:
                    candidates.append(pos)

        # B. 环形点：敌方主基地外环 15/20/25/30 距离 × 6 方向
        for dist in (15, 20, 25, 30):
            for angle_deg in range(0, 360, 60):
                angle_rad = math.radians(angle_deg)
                candidates.append(
                    Point2((
                        enemy.x + dist * math.cos(angle_rad),
                        enemy.y + dist * math.sin(angle_rad),
                    ))
                )
        return candidates

    def _score_pos(self, pos: Point2) -> float:
        """评分 proxy 候选点。负分 = 硬约束违反（必排除）。"""
        try:
            enemy = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return -1.0

        dist = pos.distance_to(enemy)
        # —— 硬约束 ——
        if dist < _MIN_DIST_TO_ENEMY:
            return -1.0  # 太近，敌方主基地必看到
        if dist > _MAX_DIST_TO_ENEMY:
            return -1.0  # 太远没意义
        if self._in_enemy_vision(pos):
            return -1.0  # 当前被敌方单位/建筑看到
        try:
            if not self.ai.in_placement_grid(pos):
                return -1.0  # 放不了建筑
        except Exception:
            pass  # 不能查 placement 时不否决

        # —— 软评分：距离越近越好 ——
        s = 100.0
        s -= (dist - _MIN_DIST_TO_ENEMY) * 2.0

        # 偏离敌方"主路"加分（侧翼隐蔽）
        try:
            off_path = self._off_main_path(pos)
            s += min(off_path, 20)
        except Exception:
            pass

        # 低地小加分（敌方主基地通常在高地）
        try:
            enemy_height = self.ai.get_terrain_height(enemy)
            pos_height = self.ai.get_terrain_height(pos)
            if pos_height < enemy_height:
                s += 8
        except Exception:
            pass

        return s

    def _in_enemy_vision(self, pos: Point2) -> bool:
        """pos 是否在任何已见敌方单位/建筑的视野半径内。"""
        try:
            for e in self.ai.enemy_units | self.ai.enemy_structures:
                sight = getattr(e, "sight_range", 8.0)
                if pos.distance_to(e) < sight + 2.0:
                    return True
        except Exception:
            pass
        return False

    def _off_main_path(self, pos: Point2) -> float:
        """pos 到 enemy_main→map_center 直线的垂直距离。越大越偏路。"""
        enemy = self.ai.enemy_start_locations[0]
        center = self.ai.game_info.map_center
        # 点到线段的垂直距离（线段：enemy → center）
        dx, dy = center.x - enemy.x, center.y - enemy.y
        line_len_sq = dx * dx + dy * dy
        if line_len_sq < 1e-6:
            return 0.0
        px, py = pos.x - enemy.x, pos.y - enemy.y
        t = max(0.0, min(1.0, (px * dx + py * dy) / line_len_sq))
        proj_x = enemy.x + t * dx
        proj_y = enemy.y + t * dy
        return math.hypot(pos.x - proj_x, pos.y - proj_y)

    # ------------------------------------------------------------------
    # 建筑状态 + tag 跟踪
    # ------------------------------------------------------------------

    def _is_forward_building(self, struct: Any) -> bool:
        """这栋建筑是不是 forward proxy 的（不是家里的）。"""
        if self.proxy_location is None:
            return False
        if struct.distance_to(self.proxy_location) > _PROXY_R:
            return False
        for nx in self.ai.townhalls:
            if struct.distance_to(nx) < _MIN_HOME_DIST:
                return False
        return True

    def _building_state(self, unit_type: UnitTypeId) -> str:
        """检测 forward 建筑当前状态：ready / in_progress / ordering / destroyed / none。"""
        # 跟踪 GATEWAY 时也算 WARPGATE（升级后 tag 不变，但 type 切换）
        types_to_check = {unit_type}
        if unit_type == UnitTypeId.GATEWAY:
            types_to_check.add(UnitTypeId.WARPGATE)

        tag = self._proxy_tags.get(unit_type)

        # —— 已 tagged：用 tag 跟到底 ——
        if tag is not None:
            struct = self.ai.structures.find_by_tag(tag)
            if struct is None:
                # tag 实体没了
                if tag in self._ever_ready:
                    return "destroyed"  # 建完后被拆
                # 没造完就被拆：清 tag 重新派
                self._proxy_tags.pop(unit_type, None)
                return "none"
            if struct.is_ready:
                self._ever_ready.add(tag)
                return "ready"
            return "in_progress"

        # —— 没 tagged：找一个 forward 建筑锁定 ——
        try:
            in_range = self.ai.structures.of_type(types_to_check)
        except Exception:
            in_range = []
        for s in in_range:
            if self._is_forward_building(s):
                self._proxy_tags[unit_type] = s.tag
                if s.is_ready:
                    self._ever_ready.add(s.tag)
                    return "ready"
                return "in_progress"

        # —— 没建筑实体，看 worker 是否在 ordering 该类型 ——
        worker = self._get_proxy_worker()
        if worker is not None:
            build_abils = _BUILD_ABILITIES.get(unit_type, set())
            for order in getattr(worker, "orders", []):
                if order.ability_id in build_abils:
                    return "ordering"

        return "none"

    # ------------------------------------------------------------------
    # worker 管理
    # ------------------------------------------------------------------

    def _get_proxy_worker(self) -> Any:
        """获取当前 proxy worker（None 表示丢/死，需要重新指派）。"""
        if self.proxy_worker_tag is None:
            return None
        try:
            w = self.cache.by_tag(self.proxy_worker_tag)
        except Exception:
            w = None
        return w

    def _assign_new_worker(self) -> Any:
        """重新指派一个 proxy worker。返回 worker 或 None（没农民可用）。"""
        try:
            if not self.ai.workers:
                return None
            if self.proxy_location is None:
                return None
            w = self.ai.workers.closest_to(self.proxy_location)
        except Exception:
            return None
        self.proxy_worker_tag = w.tag
        logger.info(
            "ForwardSupport assigned worker tag=%d (death_count=%d)",
            w.tag,
            self._worker_death_count,
        )
        try:
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, w)
        except Exception:
            pass
        return w

    def _release_worker(self) -> None:
        """完成 / 终止时释放 worker。"""
        if self.proxy_worker_tag is None:
            return
        try:
            from sharpy.managers.core.roles import UnitTask

            w = self.cache.by_tag(self.proxy_worker_tag)
            if w is not None:
                self.knowledge.roles.clear_task(w)
                self.knowledge.roles.set_task(UnitTask.Idle, w)
        except Exception:
            pass
        self.proxy_worker_tag = None

    # ------------------------------------------------------------------
    # 任务完成判定
    # ------------------------------------------------------------------

    def _is_done(self) -> bool:
        """5 重 OR 完成条件，任一满足返回 True。"""
        py_state = self._building_state(UnitTypeId.PYLON)
        bg_state = self._building_state(UnitTypeId.GATEWAY)

        # A. 双 ready
        if py_state == "ready" and bg_state == "ready":
            logger.info("ForwardSupport done (A: both ready)")
            return True

        # B. 都曾 ready（destroyed 也算 —— 不重建被拆的）
        if py_state in ("ready", "destroyed") and bg_state in ("ready", "destroyed"):
            logger.info("ForwardSupport done (B: both ever ready)")
            return True

        # C. 超时
        if self._start_time is not None:
            elapsed = self.ai.time - self._start_time
            if elapsed > _TASK_TIMEOUT_S:
                logger.info("ForwardSupport done (C: timeout %.0fs)", elapsed)
                return True

        # D. worker 死太多
        if self._worker_death_count >= _MAX_WORKER_DEATHS:
            logger.info(
                "ForwardSupport done (D: %d worker deaths)", self._worker_death_count
            )
            return True

        # E. 主力已出门 —— 通过 knowledge.vibecraft.combat_intent_override 推断
        try:
            intent = getattr(
                self.ai.knowledge.vibecraft, "combat_intent_override", None
            )
            if intent == "attack":
                logger.info("ForwardSupport done (E: main army attacking)")
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

        # —— 1. 第一次 execute：初始化 ——
        if self._start_time is None:
            self._start_time = self.ai.time
        if self.proxy_location is None:
            self.proxy_location = self._pick_proxy_location()
            if self.proxy_location is None:
                # 选不出来，跳过此帧再试
                return False
            self.hide_location = self.proxy_location.towards(
                self.ai.start_location, 8
            )

        # —— 2. 完成判定（5 重 OR）——
        if self._is_done():
            self._release_worker()
            self._completed = True
            return True

        try:
            # —— 3. worker 管理 ——
            worker = self._get_proxy_worker()
            if worker is None and self.proxy_worker_tag is not None:
                # 上一次有 tag，现在 by_tag 没了 → 死了
                self._worker_death_count += 1
                self.proxy_worker_tag = None
                if self._worker_death_count >= _MAX_WORKER_DEATHS:
                    return False  # 下一帧 _is_done 触发 D 终止

            if worker is None:
                worker = self._assign_new_worker()
            if worker is None:
                return False  # 没农民可用

            # 保命评估
            hp_max = worker.shield_max + worker.health_max
            hp_now = worker.shield + worker.health
            ratio = hp_now / hp_max if hp_max > 0 else 1.0

            if not self.retreating and ratio < _RETREAT_RATIO:
                self.retreating = True
                logger.debug("ForwardSupport retreating (hp=%.2f)", ratio)
            elif self.retreating and ratio > _REENGAGE_RATIO:
                self.retreating = False
                logger.debug("ForwardSupport re-engaging (hp=%.2f)", ratio)

            if self.retreating and self.hide_location is not None:
                if worker.distance_to(self.hide_location) > 4:
                    worker.move(self.hide_location)
                return False

            # —— 4. 下一步动作 —— 完全基于游戏实际状态（无 boolean flag）——
            py_state = self._building_state(UnitTypeId.PYLON)
            bg_state = self._building_state(UnitTypeId.GATEWAY)

            # 没 PYLON 也没在 build → 造 PYLON
            if py_state == "none":
                if self.ai.can_afford(UnitTypeId.PYLON):
                    worker.build(UnitTypeId.PYLON, self.proxy_location)
                elif worker.is_idle:
                    worker.move(self.proxy_location)  # 走过去等钱
                return False

            # PYLON ready 但 GATEWAY none → 造 GATEWAY
            if py_state == "ready" and bg_state == "none":
                if self.ai.can_afford(UnitTypeId.GATEWAY):
                    # 在 PYLON 附近找位置造 BG（psi matrix 内）
                    py_tag = self._proxy_tags.get(UnitTypeId.PYLON)
                    py_struct = (
                        self.ai.structures.find_by_tag(py_tag)
                        if py_tag is not None
                        else None
                    )
                    if py_struct is not None:
                        bg_pos = py_struct.position.towards(
                            self.ai.enemy_start_locations[0], 3
                        )
                        worker.build(UnitTypeId.GATEWAY, bg_pos)
                elif worker.is_idle:
                    py_tag = self._proxy_tags.get(UnitTypeId.PYLON)
                    py_struct = (
                        self.ai.structures.find_by_tag(py_tag)
                        if py_tag is not None
                        else None
                    )
                    if py_struct is not None:
                        worker.move(py_struct.position)
                return False

            # PYLON ordering / in_progress 中或 GATEWAY ordering / in_progress 中 → 等着
            if worker.is_idle and self.proxy_location is not None:
                worker.move(self.proxy_location)
        except Exception as exc:
            logger.warning("ForwardSupport execute failed: %s", exc)

        return False
