"""4 BG 早压前线支援:派 1 农民兼做"探路+保命+躲起来修 PY+BG"。

为什么不分两个农民(scout / proxy builder)?
  - 多派农民经济损失大
  - 同一农民先探一眼敌情,再回到中场视野盲区修 PY + BG
  - 4bg 时通用 ScoutWorker 让位(active_recipe=="4bg" 检查),避免双农民出门

设计:
  - proxy 点 = map_center 朝敌方 12 距离(中场,接近视野盲区,不深入)
  - HP+shield < 50% → retreat 到 proxy_location 附近躲(不回家,继续待命修建筑)
  - HP > 90% → 恢复工作
  - 完成 1 PY + 1 BG → 释放农民回家

不指望"完美保命":sharpy/python-sc2 没有微操精修,农民被多敌人围会死。死了就死了,
4bg 还有家里 4 BG 主力。"必要时牺牲"语义保留。

实现:每帧 dispatcher
  - HP 状态 → retreating flag
  - retreating 时:hold at proxy_location 附近
  - 正常时:没 PY 造 PY → 没 BG 造 BG → 都有则完成
"""

from __future__ import annotations

import logging
from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

logger = logging.getLogger(__name__)


class ForwardSupportPylonGateway(ActBase):  # type: ignore[misc]
    """4bg 探路+前线支援(1 PY + 1 BG),保命优先。"""

    RETREAT_RATIO: float = 0.5
    REENGAGE_RATIO: float = 0.9

    def __init__(self) -> None:
        super().__init__()
        self.proxy_worker_tag: int | None = None
        self.proxy_location: Point2 | None = None
        self.hide_location: Point2 | None = None  # retreat 时躲的位置
        self._completed: bool = False
        self._py_ordered: bool = False
        self._bg_ordered: bool = False
        self.retreating: bool = False

    async def start(self, knowledge: Any) -> None:
        await super().start(knowledge)
        try:
            # proxy 点:map 中心朝敌方 12 距离(中场,不深入敌方半场)
            self.proxy_location = self.ai.game_info.map_center.towards(
                self.ai.enemy_start_locations[0], 12
            )
            # 躲藏点:在 proxy 朝自己方向再退 6 距离(更安全的盲区)
            self.hide_location = self.proxy_location.towards(self.ai.start_location, 6)
            logger.info("ForwardSupport proxy=%s hide=%s", self.proxy_location, self.hide_location)
        except Exception as exc:
            logger.warning("ForwardSupport: cannot determine locations: %s", exc)
            self.proxy_location = None
            self.hide_location = None

    def _release_worker(self) -> None:
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

    async def execute(self) -> bool:
        if self._completed or self.proxy_location is None:
            return True

        try:
            from sharpy.managers.core.roles import UnitTask

            # 已造好检测(在 proxy 周围 14 格内有 PY+BG)
            py_at_proxy = self.ai.structures(UnitTypeId.PYLON).closer_than(14, self.proxy_location)
            bg_at_proxy = self.ai.structures.of_type(
                {UnitTypeId.GATEWAY, UnitTypeId.WARPGATE}
            ).closer_than(14, self.proxy_location)
            if py_at_proxy.exists and bg_at_proxy.exists:
                self._release_worker()
                self._completed = True
                logger.info("ForwardSupport completed: 1 PY + 1 BG forward")
                return True

            # 选 / 锁定 proxy 农民
            worker = None
            if self.proxy_worker_tag is not None:
                worker = self.cache.by_tag(self.proxy_worker_tag)
            if worker is None:
                if not self.ai.workers:
                    return False
                worker = self.ai.workers.closest_to(self.proxy_location)
                self.proxy_worker_tag = worker.tag
                logger.info("ForwardSupport assigned worker tag=%d", worker.tag)
            self.knowledge.roles.set_task(UnitTask.Reserved, worker)

            # 保命评估
            hp_max = worker.shield_max + worker.health_max
            hp_now = worker.shield + worker.health
            ratio = hp_now / hp_max if hp_max > 0 else 1.0

            if not self.retreating and ratio < self.RETREAT_RATIO:
                self.retreating = True
                logger.debug("ForwardSupport retreating to hide (hp ratio=%.2f)", ratio)
            elif self.retreating and ratio > self.REENGAGE_RATIO:
                self.retreating = False
                logger.debug("ForwardSupport re-engaging (hp ratio=%.2f)", ratio)

            # 躲藏:不回家,在中场后退处待命,血回了继续修
            if self.retreating and self.hide_location is not None:
                if worker.distance_to(self.hide_location) > 4:
                    worker.move(self.hide_location)
                return False

            # 正常推进:没 PY → 造 PY
            if not py_at_proxy.exists:
                if self.ai.can_afford(UnitTypeId.PYLON) and not self._py_ordered:
                    ok = worker.build(UnitTypeId.PYLON, self.proxy_location)
                    if ok:
                        self._py_ordered = True
                        logger.info("ForwardSupport ordered PYLON at %s", self.proxy_location)
                    return False
                if worker.is_idle:
                    worker.move(self.proxy_location)
                return False

            # 有 PY,没 BG → 在 PY 附近 psi matrix 内造 BG
            if py_at_proxy.exists and not bg_at_proxy.exists:
                if self.ai.can_afford(UnitTypeId.GATEWAY) and not self._bg_ordered:
                    py = py_at_proxy.first
                    bg_pos = py.position.towards(self.ai.enemy_start_locations[0], 3)
                    ok = worker.build(UnitTypeId.GATEWAY, bg_pos)
                    if ok:
                        self._bg_ordered = True
                        logger.info("ForwardSupport ordered GATEWAY at %s", bg_pos)
                    return False
                if worker.is_idle:
                    worker.move(py_at_proxy.first.position)
                return False
        except Exception as exc:
            logger.warning("ForwardSupport execute failed: %s", exc)
        return False
