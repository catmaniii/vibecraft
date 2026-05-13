"""Bot 编排层：串起 IntentParser + DirectiveBoard + Sc2Facade。

设计取舍：
- `Sc2Facade` 是 Protocol，定义 bot 对 SC2 的全部需求。
- ares-sc2 / python-sc2 的真实绑定放在 `ares_adapter.py`，只有装了 ares
  的环境才会 import。
- 单测用 `FakeFacade` 完整 mock，所有 M0b 单测不依赖真实 ares 或 SC2。
- `Director` 是核心可测组件，承担 hook 分派逻辑。

`VoiceCraftBot` 类（继承 AresBot）极薄，只把 ares 事件转发给 Director。
"""

from __future__ import annotations

from voicecraft.bot.director import Director, DirectorConfig
from voicecraft.bot.facade import (
    BotState,
    FakeFacade,
    Sc2Facade,
    UnitRole,
)

__all__ = [
    "BotState",
    "Director",
    "DirectorConfig",
    "FakeFacade",
    "Sc2Facade",
    "UnitRole",
]
