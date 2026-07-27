"""推理图谱脚本黑盒验证 —— 薄壳:调用全局 skill 的 rg_query.py / rg_render.py。

批 F（2026-07-14）：repo 内 scripts/rg_query.py 与 scripts/build_kg_viz.py 已删（迁进全局
skill 的 rg_query.py / rg_render.py），这里改成 subprocess 调用 skill 版脚本，验证它们能
拿本项目 `docs/reasoning-graph.yaml` 跑通。skill 未装则 skip。

注意：skill 脚本的 `--yaml` 默认值是相对 skill 自身目录（非本项目），所以这里全部显式传
`--yaml`，不测"默认路径"（那测的是 skill 自己的默认，不是本项目集成点）。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_SKILL_ROOT = Path.home() / ".claude" / "skills" / "reasoning-graph"
QUERY = _SKILL_ROOT / "scripts" / "rg_query.py"
BUILD = _SKILL_ROOT / "scripts" / "rg_render.py"
TEMPLATE = _SKILL_ROOT / "assets" / "rg-viewer.html"
YAML = REPO / "docs" / "reasoning-graph.yaml"

# Windows 控制台默认代码页(GBK/936)会让子进程 stdout 不是 UTF-8，显式强制子进程用 UTF-8
# 输出，避免 subprocess.run(..., encoding="utf-8") 解码含中文的 print() 输出时报错。
_UTF8_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def _run_script(script, args):
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_UTF8_ENV,
    )


@pytest.mark.skipif(not QUERY.exists(), reason="全局 reasoning-graph skill 未安装")
def test_rg_query_accepts_explicit_yaml_path():
    r = _run_script(QUERY, ["--yaml", str(YAML), "--stats"])
    assert r.returncode == 0, r.stderr
    assert "总节点=" in r.stdout


@pytest.mark.skipif(
    not (BUILD.exists() and TEMPLATE.exists()), reason="全局 reasoning-graph skill 未安装"
)
def test_rg_render_accepts_explicit_paths(tmp_path):
    out = tmp_path / "rg.html"
    r = _run_script(BUILD, ["--yaml", str(YAML), "--template", str(TEMPLATE), "--out", str(out)])
    assert r.returncode == 0, r.stderr
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    # 占位符被真实节点 JSON 取代(不绑具体节点 id，防 yaml 数据演进导致假失败)
    assert "/*__RG_JSON__*/" not in html
    assert '"id":"' in html
