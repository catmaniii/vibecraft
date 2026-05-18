"""虫族 ScoutOverlord plan。

派第二只 OL 前往敌方 natural 视野，触发条件：
- 有第 2 只 Overlord 可用
- 发现 anti-air 时立即撤退（ATA / Cyclone / Phoenix）

复用 sharpy 内置 OverlordScout act 完成 OL 探路逻辑。
设计参考：docs/plans/2026-05-18-zerg-terran-bot-design.md §4.2
"""

from __future__ import annotations

from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder
from sharpy.plans.tactics.zerg import OverlordScout


class ScoutOverlord(KnowledgeBot):  # type: ignore[misc]
    """OL 探路：第二只 OL 前往敌方 natural 视野。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Zerg ScoutOverlord")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            OverlordScout(),
        )
