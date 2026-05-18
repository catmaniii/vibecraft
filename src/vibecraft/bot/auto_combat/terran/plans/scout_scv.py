"""人族 ScoutSCV plan。

派早期 SCV 侦察对方开局，发现 tech 路线后撤回。
复用 sharpy 内置 WorkerScout act。

设计参考：docs/plans/2026-05-18-zerg-terran-bot-design.md §4.3
"""

from __future__ import annotations

from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder
from sharpy.plans.tactics import WorkerScout


class ScoutSCV(KnowledgeBot):  # type: ignore[misc]
    """SCV 探路：早期 SCV 侦察对方 build order。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Terran ScoutSCV")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            WorkerScout(),
        )
