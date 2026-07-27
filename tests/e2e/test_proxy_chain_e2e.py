"""代理建造野外链端到端测试(需真实 SC2)。

覆盖"必须运行 SC2 才能测"的部分:农民真移动到野外、卡片创建/刷新坐标/完成、最后建筑真修成。
逻辑全在 `scripts/proxy_chain_selftest.py`(mock LLM 绕开真 LLM,non-realtime fast 跑真局,
A/B 对照验 (a) 野外建造 + 卡片刷新 + (b) 家里让路)。

本测试以**子进程**方式跑那个脚本 —— 因为根级 conftest 的 `_block_sc2_child_entry` 会在
**同进程**里把 SC2 spawn stub 掉(防单测误弹窗),子进程有独立 Python、不受影响,能真起 SC2。

default 跳过(标 e2e + 需 SC2PATH)。手动跑:
    uv run pytest -m e2e tests/e2e/test_proxy_chain_e2e.py -s
或直接跑脚本(更快看输出):
    .venv/Scripts/python.exe scripts/proxy_chain_selftest.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.e2e
def test_proxy_chain_field_build_and_home_yield() -> None:
    """真局:派农民去野外修水晶→修两个 VS。

    脚本退出码 0 = 全过:水晶 + 2 个不同 VS 都建在野外、链绑定、水晶建好刷新出 2 个不同坐标、
    且下指令时家里让路(基线不让路)。退出码 1 = 某项失败(stdout 有详情)。
    """
    if not os.environ.get("SC2PATH"):
        pytest.skip("SC2PATH 未设置,跳过(本测试需真实 SC2 客户端)")

    script = _ROOT / "scripts" / "proxy_chain_selftest.py"
    # 跑 A/B 全套(基线 + 测试),~5 分钟。CI/手动跑 e2e 时执行。
    proc = subprocess.run(
        [sys.executable, str(script), "--seconds", "160", "--inject-after", "3"],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    # 把脚本输出透传给 pytest(-s 时可见),失败时便于排查
    print(proc.stdout[-4000:])
    if proc.returncode != 0:
        print("STDERR(tail):", proc.stderr[-2000:])
    assert proc.returncode == 0, "代理建造野外链 e2e 自验失败,见上方脚本输出"
