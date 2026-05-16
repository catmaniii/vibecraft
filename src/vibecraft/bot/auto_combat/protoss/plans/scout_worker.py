"""通用探路农民:不论什么宏观策略,都派 1 农民去对面侦察。

策略:
- 保命优先:HP+shield < 50% 起始 → 撤回最近友方基地;> 90% 重新出发
- 巡逻目标:enemy_start_locations[0] + 2-3 个靠近敌方的 expansion
- 每 30s 切下一个目标(避免卡死)
- 死了重派下一个农民(只要还有农民)
- 必要时牺牲:撤退路上无法绕开 → 也接受被打死(SC2 引擎自动 attack-move 中会优先逃,死了就死了)

实现层级:protoss/bot.py:create_plan() 把 ScoutWorker() 放在 IfElse 路由之外,
所有 active_recipe 共享这一个探路农民。Reserved task 不会被 DistributeWorkers 拉回采矿。
"""

from __future__ import annotations

import logging
from typing import Any

from sc2.position import Point2
from sharpy.plans.acts import ActBase

logger = logging.getLogger(__name__)


class ScoutWorker(ActBase):  # type: ignore[misc]
    """1 农民巡逻探路,保命 + 必要时牺牲。"""

    # HP+shield 占比阈值
    RETREAT_RATIO: float = 0.5
    REENGAGE_RATIO: float = 0.9
    # 切下一个巡逻目标的间隔(秒,游戏内)
    TARGET_SWITCH_INTERVAL: float = 30.0

    def __init__(self) -> None:
        super().__init__()
        self.scout_tag: int | None = None
        self.targets: list[Point2] = []
        self.target_idx: int = 0
        self.last_switch_s: float = -999.0
        self.retreating: bool = False

    async def start(self, knowledge: Any) -> None:
        await super().start(knowledge)
        try:
            enemy_start = self.ai.enemy_start_locations[0]
            self.targets = [enemy_start]
            # 取 6 个 expansion 候选,选离敌方 5-40 距离内的(偏敌方半场)
            for exp in self.ai.expansion_locations_list[:8]:
                d = exp.distance_to(enemy_start)
                if 5 < d < 40:
                    self.targets.append(exp)
            logger.info("ScoutWorker initialized: %d targets", len(self.targets))
        except Exception as exc:
            logger.warning("ScoutWorker init failed: %s", exc)
            self.targets = []

    def _pick_scout(self) -> Any:
        """选 1 个农民:优先离敌方近的(可能本身就在采远矿)。返回 sc2 Unit 或 None。"""
        if not self.ai.workers:
            return None
        try:
            enemy_start = self.ai.enemy_start_locations[0]
            return self.ai.workers.closest_to(enemy_start)
        except Exception:
            return self.ai.workers.first

    async def execute(self) -> bool:
        if not self.targets:
            return True
        # 开局 60s 内不抢农民:开局 13-14 农 + BG/PYLON build 阶段对 worker 位置敏感,
        # ScoutWorker 抢 1 个 closest_to(enemy) 的农民可能干扰 sharpy BuildingSolver
        # 的 builder 选择(实测 bug:开局一直 "Can't find free position to build PYLON")
        if self.ai.time < 60.0:
            return False
        # 4bg 策略下让位给 ForwardSupportPylonGateway(它承担"探路+保命+躲起来修建筑")
        # 不同时派两个农民出去
        if getattr(self.ai, "active_recipe", None) == "4bg" and self.scout_tag is None:
            return False
        try:
            from sharpy.managers.core.roles import UnitTask

            # 拿 / 重派 scout
            scout = self.cache.by_tag(self.scout_tag) if self.scout_tag is not None else None
            if scout is None:
                new = self._pick_scout()
                if new is None:
                    return False  # 没农民可派
                self.scout_tag = new.tag
                scout = new
                self.retreating = False
                logger.info("ScoutWorker assigned tag=%d", new.tag)

            self.knowledge.roles.set_task(UnitTask.Reserved, scout)

            # HP 评估
            hp_max = scout.shield_max + scout.health_max
            hp_now = scout.shield + scout.health
            ratio = hp_now / hp_max if hp_max > 0 else 1.0

            if not self.retreating and ratio < self.RETREAT_RATIO:
                self.retreating = True
                logger.debug("ScoutWorker retreating (hp ratio=%.2f)", ratio)
            elif self.retreating and ratio > self.REENGAGE_RATIO:
                self.retreating = False
                logger.debug("ScoutWorker re-engaging (hp ratio=%.2f)", ratio)

            if self.retreating:
                if self.ai.townhalls:
                    home = self.ai.townhalls.first.position
                    if scout.distance_to(home) > 8:
                        scout.move(home)
                return False

            # 巡逻:每 30s 换下个目标
            now = float(self.ai.time)
            if now - self.last_switch_s > self.TARGET_SWITCH_INTERVAL:
                self.target_idx = (self.target_idx + 1) % len(self.targets)
                self.last_switch_s = now
            target = self.targets[self.target_idx]
            if scout.is_idle or scout.distance_to(target) > 5:
                scout.move(target)
        except Exception as exc:
            logger.warning("ScoutWorker execute failed: %s", exc)
        return False
