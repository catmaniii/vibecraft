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
import os
from typing import Any
from unittest.mock import patch

import pytest

logger = logging.getLogger(__name__)

# 单测进程内全局禁用 HangWatchdog（2026-06-13 排查实录，损失半小时）：
# 某些测试驱动 bot.on_start → 启动真 HangWatchdog daemon 线程 → 泄漏到整个
# pytest 进程；30s 墙钟后判"bot.time 卡死" → os._exit(87) **把 pytest 整个杀掉**。
# 症状：全量 suite 在随墙钟漂移的位置无声死亡（exit 87），单跑任何文件全绿；
# suite 较短时它在收尾边缘开火，表现为"passed 但退出码诡异(5/255/0xC0000005)"。
# common_bot 本就留了此环境开关，单测一律关闭。
os.environ.setdefault("VIBECRAFT_DISABLE_HANG_WATCHDOG", "1")


def pytest_collection_modifyitems(config: pytest.Config, items: list[Any]) -> None:
    """默认 skip e2e marker(需真实 SC2 客户端,会弹窗/烧时间)。

    `pytest -m e2e`(或 -m "e2e or ...")时 markexpr 含 e2e → 跑;没明确指定 → 全 skip。
    (注:e2e 测试以子进程跑脚本,不受 `_block_sc2_child_entry` 的同进程 stub 影响。)
    """
    markexpr = config.getoption("-m", "") or ""
    if "e2e" in markexpr:
        return  # 用户明确选了 e2e,放过
    skip_marker = pytest.mark.skip(reason="e2e 默认 skip(需真实 SC2);用 -m e2e 跑")
    for item in items:
        if "e2e" in {m.name for m in item.iter_markers()}:
            item.add_marker(skip_marker)


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
