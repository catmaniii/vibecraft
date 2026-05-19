"""ForwardRallyStalker:覆盖 sharpy 全局 gather_point 到 forward PYLON,让
PlanZoneGather / RallyBuilding / 默认集结全部走前线。

为什么不能用 per-unit `unit.move()` 对抗
=====================================
最初版本(2026-05-19)用 per-unit move 命令 + 距离判断"≤ 12 就 skip"。
症状(用户反馈 2026-05-20):"造出来的追猎一会往家集结点走,一会出门,
一会又回家"。

根因:
- sharpy `PlanZoneGather` 每 tick 给所有 GATEWAY 设 `RALLY_BUILDING`,
  target = sharpy 全局 `gather_point`(默认 home ramp 附近)
- WARPGATE 是 GATEWAY morph 上来的,继承 rally → 新 warp 的 stalker
  spawn 即自动 rally home
- PlanZoneGather 同时把 idle 兵 combat-move 到 gather_point(home)
- 我的 per-unit move 命令"距离 ≤ 12 就 skip"→ stalker 到 forward 12 范围内
  就停发命令 → PlanZoneGather 下 tick 又拉回家 → 反复横跳

正确解法
========
直接调 `knowledge.gather_point_solver.set_gather_point(forward_pylon.position)`,
PlanZoneGather 看到 gather_point 变了会:
1. 把所有 GATEWAY 的 rally 重设到 forward(新 warp 兵自动走前线)
2. combat-move idle 兵到 forward
统一一致,没 race condition。

sharpy `GatherPointSolver.update` 是一次性 flag(_gather_point_set),只持续 1 tick
就被下一帧 update 重算成 home,所以本 act 每 tick 都要重新 set。

VibeCraftZoneAttack 触发时,attack 命令自然覆盖 rally + combat-move,顺利出门。

为什么必须 return True (2026-05-20 用户反馈)
============================================
sharpy `SequentialList.execute` 是"任一 act 返回 False 就停止后续 acts":
```python
for order in self.orders:
    result = await order.execute()
    if not result:
        return result  # ← 停在 False 处!
```
本 act 在 tactics SequentialList 中位于 PlanZoneGather 之后、VibeCraftZoneAttack
之前。如果 return False(按"我还在持续工作"的语义),会让 ZoneAttack /
PlanFinishEnemy 整个 SequentialList 后半段**永远不运行** → 攻击不触发、新刷
追猎没人指挥停在 forward 等死。

本 act 只做 side-effect(set gather_point),没有"工作中"状态,**始终 return
True**(=本 step done,SequentialList 继续下一个)。
"""

from __future__ import annotations

import logging
from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.interfaces import IGatherPointSolver
from sharpy.plans.acts import ActBase

logger = logging.getLogger(__name__)

# "forward" 判定:距敌方 < 距家 * 此比率(与 ForwardWarpStalker 同步)
_FORWARD_RATIO: float = 0.7


class ForwardRallyStalker(ActBase):  # type: ignore[misc]
    """每 tick set sharpy 全局 gather_point 到 forward PYLON 位置。

    `unit_types` 参数保留兼容旧调用 / 测试,本实现不直接对单位发命令(由
    sharpy PlanZoneGather 走 gather_point 路径)。
    """

    def __init__(self, unit_types: tuple[UnitTypeId, ...] = (UnitTypeId.STALKER,)) -> None:
        super().__init__()
        self.unit_types = unit_types
        self._last_logged: tuple[float, float] | None = None  # log 节流

    async def execute(self) -> bool:
        try:
            home = self.ai.start_location
            enemy = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return True  # 地图信息不全,退出让 sharpy 默认 home gather

        forward_pylon = self._find_forward_pylon(home, enemy)
        if forward_pylon is None:
            return True  # 没前线 PYLON,退出

        target = forward_pylon.position

        # 2026-05-20 用户反馈"刷兵又走回家堵门口"找出的根因:
        # sharpy `Knowledge` 类(self.knowledge)**没有** `gather_point_solver` 属性
        # —— 它只在 KnowledgeBot 实例上(`knowledge_bot.py:38`)。正确访问是
        # `knowledge.get_required_manager(IGatherPointSolver)`。之前直接访问
        # `self.knowledge.gather_point_solver` 抛 AttributeError,被 except 吞掉,
        # gather_point 永远不被覆盖,新刷兵继续走默认 home rally,反复横跳。
        try:
            solver = self.knowledge.get_required_manager(IGatherPointSolver)
        except Exception as exc:
            logger.warning("forward_rally get IGatherPointSolver fail: %s", exc)
            return True

        try:
            solver.set_gather_point(target)
        except Exception as exc:
            logger.warning("forward_rally set_gather_point fail: %s", exc)
            return True

        # log 节流:gather_point 没变就不刷屏
        current = (target.x, target.y)
        if self._last_logged != current:
            logger.info(
                "forward_rally: override gather_point → (%.1f, %.1f)",
                target.x,
                target.y,
            )
            self._last_logged = current

        return True  # 始终 True — 见上方"为什么必须 return True"注释

    def _find_forward_pylon(self, home: Point2, enemy: Point2) -> Any:
        try:
            pylons = self.ai.structures(UnitTypeId.PYLON).ready
        except Exception:
            return None
        for py in pylons:
            d_home = py.distance_to(home)
            d_enemy = py.distance_to(enemy)
            if d_enemy < d_home * _FORWARD_RATIO:
                return py
        return None
