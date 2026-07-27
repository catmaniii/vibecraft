"""推理图谱一致性 —— 薄壳:调用全局 skill 的 rg_validate.py 校验本项目 yaml。

批 F（2026-07-14）:一致性门逻辑迁进全局 skill scripts/rg_validate.py(独立可跑),
repo 测试只负责"用本项目 yaml 调它"。skill 未装则 skip(推理图谱/skill 不进开源交付物,
不强制 CI 环境有 skill;开发机装了即生效)。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
YAML = REPO / "docs" / "reasoning-graph.yaml"
VALIDATE = Path.home() / ".claude" / "skills" / "reasoning-graph" / "scripts" / "rg_validate.py"

# Windows 控制台默认代码页(GBK/936)会让子进程 stdout/stderr 不是 UTF-8，显式强制子进程用
# UTF-8 输出，避免 subprocess.run(..., encoding="utf-8") 解码含中文的 print() 输出时报错。
_UTF8_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


@pytest.mark.skipif(not VALIDATE.exists(), reason="全局 reasoning-graph skill 未安装")
def test_reasoning_graph_consistency() -> None:
    r = subprocess.run(
        [sys.executable, str(VALIDATE), "--yaml", str(YAML)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_UTF8_ENV,
    )
    assert r.returncode == 0, f"推理图谱一致性门失败:\n{r.stdout}\n{r.stderr}"
