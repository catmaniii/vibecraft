"""偷矿（Stealth Mining）模块。

WP1: StealthCell 状态容器 + StealthCellManager 骨架。
后续 WP 填充：建造链（WP2）、FENCE patch（WP3）、本地产线（WP4）、
受击释放（WP5）、多 cell UI（WP6）、LLM prompt（WP7）。
"""

from vibecraft.bot.stealth.cell import StealthCell, StealthState
from vibecraft.bot.stealth.manager import StealthCellManager

__all__ = ["StealthCell", "StealthCellManager", "StealthState"]
