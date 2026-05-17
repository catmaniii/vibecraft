"""根级 conftest：session 范围的全局保险。

规则：单元测试不得拉起真实 SC2 进程。
_child_entry 是 GameProcess 子进程的入口，正常 unit test 不需要真跑它。
若有测试意外触发了真 spawn（而非 mock），这里会让子进程立即退出并 log warning，
而不是弹出 SC2 黑窗口。

注意：现有 TestGameProcessStart 测试已 patch 整个 multiprocessing 模块，
_child_entry 永远不会被调用，此 fixture 对它们无副作用。
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

import pytest

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True, scope="session")
def _block_sc2_child_entry() -> Any:
    """把 _child_entry 替换成立即返回的 stub，防止单测意外弹 SC2 窗口。"""

    def _stub_child_entry(*args: Any, **kwargs: Any) -> None:
        logger.warning(
            "BLOCKED: _child_entry called inside pytest — SC2 must not be launched in unit tests. "
            "Use patch('vibecraft.server.game_process.multiprocessing') in the test."
        )

    with patch("vibecraft.server.game_process._child_entry", side_effect=_stub_child_entry):
        yield
