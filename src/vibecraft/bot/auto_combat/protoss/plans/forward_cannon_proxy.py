"""炮塔速攻前线 proxy：派 1 探机在对方 natural 附近建 BF（Forge）+ BC（PhotonCannon）。

设计目标
========
- proxy 点选在对方 natural 矿线附近（压制采矿效率），但不能直接贴在主基地
- BF 先建（电力前置），BC 紧接 BF 完成后在 psi matrix 范围内造
- 探机保命：血量低撤退，恢复后继续；死亡超过 2 次放弃
- 任务完成后释放探机回归正常采矿

判定 forward 建筑 vs 家里建筑
=============================
- 空间过滤：靠近 proxy（< 30）且远离所有自家 Nexus（> 25）
- tag 跟踪：一次识别后用 tag 锁定

完成判定（5 重 OR）
==================
A. BF ready + 至少 1 BC ready
B. BF 和 BC 都曾经 ready（destroyed 也算）
C. 超时 180s（BF ~25s + BC ~35s + 走路 ~45s + 余量）
D. worker 死亡 >= 2 次
E. 主力已出门压制

proxy 选点策略
==============
炮塔速攻的 proxy 点和 4bg 不同：
- 选对方 natural 矿线附近（距敌方 natural 10-20 之间），不是地图中线
- 越近越好，这就是 cannon rush 的威胁所在
- 但不能在敌方视野内（否则被杀）→ 先探后建
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

# proxy 周围检测半径（forward 建筑必须在这个范围内）
_PROXY_R: float = 30.0
# 自家 Nexus 这个距离内的建筑视为"家里"
_MIN_HOME_DIST: float = 25.0
# 距离敌方主基地的硬下限（太近主基地必看到）
_MIN_DIST_TO_ENEMY: float = 18.0
# 距离敌方主基地的硬上限（太远 cannon rush 无意义）
_MAX_DIST_TO_ENEMY: float = 55.0
# 任务超时（秒，游戏内）
_TASK_TIMEOUT_S: float = 180.0
# worker 死亡次数上限
# 历史: 2 - cannon proxy 贴敌方 natural 更近，worker 更容易被打死，
# 2 太少：第 1 个 worker 建到一半死，第 2 个接力又死，卡阈值直接放弃。
# 对齐 forward_proxy.py 改成 4（多砸几个探机换 BF 完成，值得）。
_MAX_WORKER_DEATHS: int = 4
# HP+shield 撤退/复出阈值
_RETREAT_RATIO: float = 0.5
_REENGAGE_RATIO: float = 0.9

# 建筑 type → 对应 BUILD AbilityId
_BUILD_ABILITIES: dict[UnitTypeId, set[AbilityId]] = {
    UnitTypeId.FORGE: {AbilityId.PROTOSSBUILD_FORGE},
    UnitTypeId.PHOTONCANNON: {AbilityId.PROTOSSBUILD_PHOTONCANNON},
}


class ForwardCannonProxy(ActBase):  # type: ignore[misc]
    """炮塔速攻前线 proxy：1 探机在对方 natural 附近建 BF + BC，压制矿线。

    BF（Forge）先建提供电力，BC（PhotonCannon）在 BF psi matrix 内建造。
    目标是在 ~1:27-2:30 之间 BC 完成开始压制对方采矿。
    """

    def __init__(self) -> None:
        super().__init__()
        self.proxy_worker_tag: int | None = None
        self.proxy_location: Point2 | None = None
        self.hide_location: Point2 | None = None
        self._completed: bool = False
        self.retreating: bool = False

        # tag 跟踪
        self._proxy_tags: dict[UnitTypeId, int] = {}
        self._ever_ready: set[int] = set()

        # worker 死亡跟踪
        self._worker_death_count: int = 0

        # 任务起始 ts
        self._start_time: float | None = None

    async def start(self, knowledge: Any) -> None:
        await super().start(knowledge)
        self.proxy_location = None
        self.hide_location = None

    # ------------------------------------------------------------------
    # proxy 选点（靠近对方 natural）
    # ------------------------------------------------------------------

    def _pick_proxy_location(self) -> Point2 | None:
        """选点策略：对方 natural expansion 附近，BF 能安全建造但 BC 能压矿。

        炮塔速攻核心：BC 建在对方 natural 矿线旁，让对方 worker 无法采矿。
        """
        candidates = self._generate_cannon_candidates()
        if not candidates:
            return self._sharpy_fallback_proxy()

        scored = [(p, self._score_cannon_pos(p)) for p in candidates]
        scored = [(p, s) for p, s in scored if s > 0]
        if not scored:
            logger.info("ForwardCannon: 0 valid candidates after filter → sharpy fallback")
            return self._sharpy_fallback_proxy()

        scored.sort(key=lambda x: x[1], reverse=True)
        top_n = scored[: min(3, len(scored))]
        chosen, score = random.choice(top_n)
        logger.info(
            f"ForwardCannon picked proxy={chosen} (score={score:.1f}, {len(scored)}/{len(candidates)} valid)"
        )
        return chosen

    def _sharpy_fallback_proxy(self) -> Point2 | None:
        """兜底：地图中线偏敌方一侧（比对方 natural 远但保证能建）。"""
        try:
            return self.ai.game_info.map_center.towards(self.ai.enemy_start_locations[0], 20)
        except Exception:
            return None

    def _generate_cannon_candidates(self) -> list[Point2]:
        """生成候选点：对方 natural 附近不同角度的点 + 地图中线偏敌方侧。

        炮塔速攻的最佳 proxy 点是对方 natural expansion 点旁边：
        - 距敌方主基地 18-55 范围（natural 通常在 16-30 之间）
        - 多个角度的环形点确保有一个能放建筑
        """
        candidates: list[Point2] = []
        try:
            enemy = self.ai.enemy_start_locations[0]
            own = self.ai.start_location
        except (IndexError, AttributeError):
            return []

        # A. 地图中线偏敌方侧（安全过路 proxy）
        dx, dy = enemy.x - own.x, enemy.y - own.y
        path_len = math.hypot(dx, dy)
        if path_len > 1e-3:
            ux, uy = dx / path_len, dy / path_len
            vx, vy = -uy, ux
            # 60-75% 进度点，偏敌方一侧
            for t in (0.60, 0.67, 0.75):
                base_x = own.x + dx * t
                base_y = own.y + dy * t
                for offset in (0.0, 12.0, -12.0):
                    candidates.append(Point2((base_x + vx * offset, base_y + vy * offset)))

        # B. 对方主基地外围近环（炮塔速攻 proxy 比 4bg 更近对方）
        for dist in (20, 25, 30, 35, 40):
            for angle_deg in range(0, 360, 45):
                angle_rad = math.radians(angle_deg)
                candidates.append(
                    Point2(
                        (
                            enemy.x + dist * math.cos(angle_rad),
                            enemy.y + dist * math.sin(angle_rad),
                        )
                    )
                )
        return candidates

    def _score_cannon_pos(self, pos: Point2) -> float:
        """评分 cannon proxy 位置。

        炮塔速攻评分重点：
        - 距敌方越近越好（BC 越快到位）
        - 不能在当前敌方视野内
        - 必须能放建筑
        """
        try:
            enemy = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return -1.0

        if not self._in_map_bounds(pos):
            return -1.0

        dist = pos.distance_to(enemy)
        if dist < _MIN_DIST_TO_ENEMY:
            return -1.0
        if dist > _MAX_DIST_TO_ENEMY:
            return -1.0
        if self._in_enemy_vision(pos):
            return -1.0
        try:
            if not self.ai.in_placement_grid(pos):
                return -1.0
        except Exception:
            return -1.0

        # 核心评分：距离越近越高分（炮塔速攻就是要贴近对方）
        s: float = 100.0
        s -= (float(dist) - _MIN_DIST_TO_ENEMY) * 3.0  # 距离惩罚更重

        # 贴地图边加分（走路安全）
        try:
            edge_d = self._edge_distance(pos)
            s += max(0.0, 30.0 - edge_d * 1.5)
        except Exception:
            pass

        return s

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
        """pos 是否在敌方单位/建筑视野内。"""
        try:
            for e in self.ai.enemy_units | self.ai.enemy_structures:
                sight = getattr(e, "sight_range", 8.0)
                if pos.distance_to(e) < sight + 2.0:
                    return True
        except Exception:
            pass
        return False

    async def _safe_find_placement(self, unit_type: UnitTypeId, near: Point2) -> Point2 | None:
        """find_placement 包装，handle async + exception。"""
        try:
            return await self.ai.find_placement(unit_type, near, max_distance=20)
        except Exception as exc:
            logger.warning(
                f"ForwardCannon find_placement fail for {unit_type.name} near ({near.x:.1f},{near.y:.1f}): {exc}"
            )
            return None

    # ------------------------------------------------------------------
    # 建筑状态 + tag 跟踪
    # ------------------------------------------------------------------

    def _is_forward_building(self, struct: Any) -> bool:
        """这栋建筑是否属于 forward proxy（不是家里的）。"""
        if self.proxy_location is None:
            return False
        if struct.distance_to(self.proxy_location) > _PROXY_R:
            return False
        return all(struct.distance_to(nx) >= _MIN_HOME_DIST for nx in self.ai.townhalls)

    def _building_state(self, unit_type: UnitTypeId) -> str:
        """检测 forward 建筑当前状态：ready / in_progress / ordering / destroyed / none。"""
        tag = self._proxy_tags.get(unit_type)

        if tag is not None:
            struct = self.ai.structures.find_by_tag(tag)
            if struct is None:
                if tag in self._ever_ready:
                    return "destroyed"
                self._proxy_tags.pop(unit_type, None)
                return "none"
            if struct.is_ready:
                self._ever_ready.add(tag)
                return "ready"
            return "in_progress"

        # 没 tagged：找一个 forward 建筑锁定
        try:
            in_range = self.ai.structures.of_type({unit_type})
        except Exception:
            in_range = []
        for s in in_range:
            if self._is_forward_building(s):
                self._proxy_tags[unit_type] = s.tag
                if s.is_ready:
                    self._ever_ready.add(s.tag)
                    return "ready"
                return "in_progress"

        # 看 worker 是否在 ordering 该类型
        worker = self._get_proxy_worker()
        if worker is not None:
            build_abils = _BUILD_ABILITIES.get(unit_type, set())
            for order in getattr(worker, "orders", []):
                ability = getattr(order, "ability", None)
                if ability is not None and getattr(ability, "id", None) in build_abils:
                    return "ordering"

        return "none"

    # ------------------------------------------------------------------
    # worker 管理
    # ------------------------------------------------------------------

    def _get_proxy_worker(self) -> Any:
        """获取当前 proxy worker。"""
        if self.proxy_worker_tag is None:
            return None
        try:
            w = self.cache.by_tag(self.proxy_worker_tag)
        except Exception:
            w = None
        return w

    def _assign_new_worker(self) -> Any:
        """重新指派一个 proxy worker。"""
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
            f"ForwardCannon assigned worker tag={w.tag} (death_count={self._worker_death_count})"
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
        bf_state = self._building_state(UnitTypeId.FORGE)
        bc_state = self._building_state(UnitTypeId.PHOTONCANNON)

        # A. BF ready + BC ready
        if bf_state == "ready" and bc_state == "ready":
            logger.info("ForwardCannon done (A: BF+BC both ready)")
            return True

        # B. 都曾 ready
        if bf_state in ("ready", "destroyed") and bc_state in ("ready", "destroyed"):
            logger.info("ForwardCannon done (B: both ever ready)")
            return True

        # C. 超时
        if self._start_time is not None:
            elapsed = self.ai.time - self._start_time
            if elapsed > _TASK_TIMEOUT_S:
                logger.info(f"ForwardCannon done (C: timeout {elapsed:.0f}s)")
                return True

        # D. worker 死太多
        if self._worker_death_count >= _MAX_WORKER_DEATHS:
            logger.info(f"ForwardCannon done (D: {self._worker_death_count} worker deaths)")
            return True

        # E. 主力已出门
        try:
            intent = getattr(self.ai.knowledge.vibecraft, "combat_intent_override", None)
            if intent == "attack":
                logger.info("ForwardCannon done (E: main army attacking)")
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
        if self.proxy_location is None:
            self.proxy_location = self._pick_proxy_location()
            if self.proxy_location is None:
                return False
            self.hide_location = self.proxy_location.towards(self.ai.start_location, 10)

        # 完成判定
        if self._is_done():
            self._release_worker()
            self._completed = True
            return True

        try:
            # worker 管理
            worker = self._get_proxy_worker()
            if worker is None and self.proxy_worker_tag is not None:
                self._worker_death_count += 1
                self.proxy_worker_tag = None
                if self._worker_death_count >= _MAX_WORKER_DEATHS:
                    return False

            if worker is None:
                worker = self._assign_new_worker()
            if worker is None:
                return False

            # 保命评估
            hp_max = worker.shield_max + worker.health_max
            hp_now = worker.shield + worker.health
            ratio = hp_now / hp_max if hp_max > 0 else 1.0

            if not self.retreating and ratio < _RETREAT_RATIO:
                self.retreating = True
                logger.debug(f"ForwardCannon retreating (hp={ratio:.2f})")
            elif self.retreating and ratio > _REENGAGE_RATIO:
                self.retreating = False
                logger.debug(f"ForwardCannon re-engaging (hp={ratio:.2f})")

            if self.retreating and self.hide_location is not None:
                if worker.distance_to(self.hide_location) > 4:
                    worker.move(self.hide_location)
                return False

            # 下一步动作（严格顺序：BF 先建，BF ready 后建 BC）
            bf_state = self._building_state(UnitTypeId.FORGE)
            bc_state = self._building_state(UnitTypeId.PHOTONCANNON)

            # BF 还没有 → 建 BF
            if bf_state == "none":
                if self.ai.can_afford(UnitTypeId.FORGE):
                    place = await self._safe_find_placement(UnitTypeId.FORGE, self.proxy_location)
                    if place is not None:
                        logger.info(
                            f"ForwardCannon build BF(Forge) at ({place.x:.1f}, {place.y:.1f})"
                        )
                        worker.build(UnitTypeId.FORGE, place)
                        return False
                # 没钱 或 placement 失败 → 锚定 proxy 点待命
                self._redirect_worker_to_anchor(worker, self.proxy_location)
                return False

            # BF ready 但 BC 还没有 → 在 BF 附近建 BC
            if bf_state == "ready" and bc_state == "none":
                if self.ai.can_afford(UnitTypeId.PHOTONCANNON):
                    bf_tag = self._proxy_tags.get(UnitTypeId.FORGE)
                    bf_struct = (
                        self.ai.structures.find_by_tag(bf_tag) if bf_tag is not None else None
                    )
                    near = bf_struct.position if bf_struct is not None else self.proxy_location
                    assert near is not None
                    bc_pos = await self._safe_find_placement(UnitTypeId.PHOTONCANNON, near)
                    if bc_pos is not None:
                        logger.info(
                            f"ForwardCannon build BC(Cannon) at ({bc_pos.x:.1f}, {bc_pos.y:.1f})"
                        )
                        worker.build(UnitTypeId.PHOTONCANNON, bc_pos)
                        return False
                # 没钱 或 placement 失败 → 锚定 BF 待命
                self._redirect_worker_to_anchor(worker, self._anchor_position())
                return False

            # BF/BC ordering / in_progress → 锚定待命（防 auto-mining 走回家）
            self._redirect_worker_to_anchor(worker, self._anchor_position())

        except Exception as exc:
            logger.warning(f"ForwardCannon execute failed: {exc}")

        return False

    # ------------------------------------------------------------------
    # worker 锚定（防 auto-mining 走回家）
    # ------------------------------------------------------------------

    def _anchor_position(self) -> Point2 | None:
        """worker 应锚定的位置：已有 BF 时贴 BF，否则用 proxy_location。"""
        bf_tag = self._proxy_tags.get(UnitTypeId.FORGE)
        bf_struct = self.ai.structures.find_by_tag(bf_tag) if bf_tag is not None else None
        if bf_struct is not None:
            return bf_struct.position
        return self.proxy_location

    def _worker_busy_with_forward_build(self, worker: Any) -> bool:
        """worker.orders 含 PROTOSSBUILD_FORGE/PHOTONCANNON → 正在 build，别打断。"""
        for order in getattr(worker, "orders", None) or []:
            ability = getattr(order, "ability", None)
            ability_id = getattr(ability, "id", None)
            if ability_id in (
                AbilityId.PROTOSSBUILD_FORGE,
                AbilityId.PROTOSSBUILD_PHOTONCANNON,
            ):
                return True
        return False

    def _redirect_worker_to_anchor(self, worker: Any, anchor: Point2 | None) -> None:
        """worker 没在 build forward 建筑且距 anchor 偏远 → issue move 拉回。

        关键:不依赖 `worker.is_idle` —— SC2 默认 idle worker 进入 auto-mining,
        worker.orders 含 HARVEST_GATHER → is_idle=False。旧 `if worker.is_idle:
        worker.move(...)` 因此从不触发,proxy worker 默默走回家挖矿,BF 永远建不成。
        改用距离检查主动拉回（与 forward_proxy.py 的 4bg 野 BG proxy 同机制）。
        """
        if anchor is None:
            return
        if self._worker_busy_with_forward_build(worker):
            return  # 在 build forward 建筑,别打断
        if worker.distance_to(anchor) <= 4:
            return  # 已在 anchor 附近,不重复发命令
        worker.move(anchor)
