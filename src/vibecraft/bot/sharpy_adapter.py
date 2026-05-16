"""sharpy / python-sc2 与 Sc2Facade 的 binding。

**仅在装了 python-sc2 + sharpy(vendor/sharpy)的环境才能 import**。
单测全部用 FakeFacade，本文件不被 import；只在真实对局用。

实现按 M1 sharpy migration plan (docs/plans/2026-05-16-sharpy-migration.md):
- bot 继承 `sharpy.knowledges.KnowledgeBot`
- `set_build(name)` → M1 占位：记录 active_recipe；M3 才接 IfElse 树
- `set_unit_role()` → `bot.knowledge.roles.set_task(sharpy_task, unit)`
- `move_camera()` → 暂存模式（ADR 0008），on_step 末尾串行 drain

调用方式（与 ares_adapter.py 完全向后兼容）：

    from vibecraft.bot.sharpy_adapter import make_bot_class
    BotClass = make_bot_class(director_factory, race="Protoss")
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from vibecraft.strategy.library import StrategyLibrary

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def make_bot_class(
    director_factory: Any,
    strategy_library: StrategyLibrary | None = None,
    status_callback: Callable[[str, str, str], None] | None = None,
    down_q: Any | None = None,
    echo_callback: Callable[[str, str], None] | None = None,
    snapshot_callback: Callable[[dict[str, Any]], None] | None = None,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    minimap_callback: Callable[[dict[str, Any]], None] | None = None,
    race: str = "Protoss",
) -> type:
    """工厂：返回一个继承 sharpy.KnowledgeBot 的 bot 类，把事件转给 director。

    签名与 ares_adapter.make_bot_class 完全一致（向后兼容 game_process.py 调用方）。

    race="Protoss" → 返回 _VibeCraftProtossBot（KnowledgeBot 子类）。
    Terran / Zerg 留 M3+。
    """
    if race == "Protoss":
        from vibecraft.bot.auto_combat.common import run_command_with_echo
        from vibecraft.bot.auto_combat.protoss.bot import make_protoss_bot_class

        return make_protoss_bot_class(
            director_factory=director_factory,
            strategy_library=strategy_library,
            status_callback=status_callback,
            down_q=down_q,
            echo_callback=echo_callback,
            snapshot_callback=snapshot_callback,
            event_callback=event_callback,
            minimap_callback=minimap_callback,
            run_command_with_echo_fn=run_command_with_echo,
        )
    raise NotImplementedError(f"race={race!r} 暂未实现（Terran/Zerg 留 M3+）")
