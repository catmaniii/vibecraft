"""三族共享工具：role_map、echo 辅助协程、log callback。

从 ares_adapter.py 抽离的公共部分，避免在各族 bot 子类里重复。
所有内容都是"有了 ares 才能用"的；import 前必须确保 ares-sc2 已装。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _log_move_camera_done(task: Any) -> None:
    """move_camera / follow_unit create_task 的 done callback：异常时 log，不静默丢。

    ADR 0007：fire-and-forget 的异常不向调用方传播；这里捕获并 log。
    """
    if not task.cancelled():
        exc = task.exception()
        if exc is not None:
            logger.error("move_camera_task_failed: %s", exc, exc_info=exc)


def build_role_map() -> dict[Any, Any]:
    """构造 voicecraft UnitRole → ares AresUnitRole 映射表。

    必须在 ares 已 import 后调用（运行时 lazy）。

    LLM_CONTROLLED → CONTROL_GROUP_THREE：
    Aristaeus（vendor）在 cannon_rush_manager 里占用了 CONTROL_GROUP_ONE（炮塔rush探机）
    和 CONTROL_GROUP_TWO（chaos探机）。voicecraft 改用 CONTROL_GROUP_THREE 避冲突。
    THREE 在 ares 源码和 Aristaeus 里均未使用，仍是"排除单元"载体（§3.4）。
    见 docs/plans/2026-05-16-tri-race-bots.md "S2 spike 结论"。
    """
    from ares.consts import UnitRole as AresUnitRole

    from voicecraft.bot.facade import UnitRole

    return {
        UnitRole.LLM_CONTROLLED: AresUnitRole.CONTROL_GROUP_THREE,
        UnitRole.IDLE: AresUnitRole.IDLE,
        UnitRole.ARMY: AresUnitRole.ATTACKING,
        UnitRole.DEFENDER: AresUnitRole.DEFENDING,
        UnitRole.HARASSER: AresUnitRole.HARASSING,
        UnitRole.SCOUT: AresUnitRole.SCOUTING,
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
    from voicecraft.llm.schema import AmbiguousParse, IntentParseResult, ParseError

    outcome = await director.on_player_command(text, now)
    if echo_callback is not None:
        if isinstance(outcome, IntentParseResult):
            echo_callback(text, outcome.interpretation_zh)
        elif isinstance(outcome, AmbiguousParse):
            echo_callback(text, f"[模糊] {outcome.result.interpretation_zh}")
        elif isinstance(outcome, ParseError):
            echo_callback(text, f"[解析失败] {outcome.message}")
