"""玩家折跃"在X刷N兵"端到端测试(需真实 SC2)。

覆盖"必须运行 SC2 才能测"的部分:折跃门兵种真折跃到最近能量场、能量场/折跃门没好时挂起
等待、折满。逻辑在 `scripts/player_warp_selftest.py`(mock LLM + non-realtime fast 跑真局,
注入"在主基地刷4追猎",抓 PLAYERWARP 日志验证折满 >=4)。

以子进程跑脚本(绕开根 conftest 的同进程 SC2 拦截)。default 跳过(标 e2e + 需 SC2PATH)。
手动:uv run pytest -m e2e tests/e2e/test_player_warp_e2e.py -s
或直接:.venv/Scripts/python.exe scripts/player_warp_selftest.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.e2e
def test_player_warp_to_nearest_power_source() -> None:
    """真局:在主基地刷 4 追猎 → 折跃门兵种折跃在最近能量场(主基地水晶),折满 >=4。"""
    if not os.environ.get("SC2PATH"):
        pytest.skip("SC2PATH 未设置,跳过(本测试需真实 SC2 客户端)")

    script = _ROOT / "scripts" / "player_warp_selftest.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--seconds", "220", "--inject-after", "3"],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    print(proc.stdout[-4000:])
    if proc.returncode != 0:
        print("STDERR(tail):", proc.stderr[-2000:])
    assert proc.returncode == 0, "玩家折跃 e2e 自验失败(折满 <4),见上方脚本输出"
