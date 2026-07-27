"""三族共享工具：role_map、echo 辅助协程。

sharpy 迁移（M1）：build_role_map 改用 sharpy UnitTask（原 ares AresUnitRole 删除）。
所有内容都是"有了 python-sc2 + sharpy 才能用"的；import 前必须确保已装。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_role_map() -> dict[Any, Any]:
    """构造 vibecraft UnitRole → sharpy UnitTask 映射表。

    必须在 sharpy 已 import 后调用（运行时 lazy）。

    LLM_CONTROLLED → UnitTask.Reserved：
    sharpy 里 Reserved(8) 是"为未知目的保留"的槽位，相当于 ares 里的
    CONTROL_GROUP_THREE —— sharpy 各 Manager 不会主动把 Reserved 单位
    拉去执行任务，恰好满足 §3.4 "LLM 接管单元不被 base bot 占用"的需求。

    其他映射：
    - IDLE     → UnitTask.Idle(0)
    - ARMY     → UnitTask.Attacking(7)
    - DEFENDER → UnitTask.Defending(6)
    - HARASSER → UnitTask.Attacking(7)（sharpy 无专用 Harassing task）
    - SCOUT    → UnitTask.Scouting(3)

    """
    from vibecraft.bot.auto_combat.common_bot import _ensure_sharpy_on_path

    _ensure_sharpy_on_path()

    from sharpy.managers.core.roles.unit_task import UnitTask

    from vibecraft.bot.facade import UnitRole

    return {
        UnitRole.LLM_CONTROLLED: UnitTask.Reserved,
        UnitRole.IDLE: UnitTask.Idle,
        UnitRole.ARMY: UnitTask.Attacking,
        UnitRole.DEFENDER: UnitTask.Defending,
        UnitRole.HARASSER: UnitTask.Attacking,  # sharpy 无 Harassing
        UnitRole.SCOUT: UnitTask.Scouting,
    }


async def run_command_with_echo(
    director: Any,
    text: str,
    now: float,
    echo_callback: Any,
) -> None:
    """调 director.on_player_command，完成后用 echo_callback 推基础 echo。

    echo 是设计文档 §9.3 基础 echo 的最小实现：
    玩家知道指令收到了 + 解析结果（完整撤销 / pending 计时器留 M3）。

    echo_callback 签名：(user_text: str, interpretation: str) -> None，可为 None。
    """
    from vibecraft.i18n import t
    from vibecraft.llm.schema import AmbiguousParse, IntentParseResult, ParseError

    # 玩家语言（zh/en）：echo 前缀按玩家界面语言本地化（interpretation 本身由 LLM 按语言生成）。
    lang = getattr(getattr(director, "parser", None), "locale", "zh") or "zh"

    outcome = await director.on_player_command(text, now)
    if echo_callback is not None:
        if isinstance(outcome, IntentParseResult):
            echo_callback(text, outcome.interpretation_zh)
        elif isinstance(outcome, AmbiguousParse):
            echo_callback(text, t("echo.ambiguous", lang, detail=outcome.result.interpretation_zh))
        elif isinstance(outcome, ParseError):
            echo_callback(text, t("echo.parse_failed", lang, detail=outcome.message))
