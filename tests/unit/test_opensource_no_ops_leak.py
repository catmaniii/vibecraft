"""开源投影泄漏回归：私有运营工具 vibecraft-ops 绝不能进开源仓（opus 评审高危 A）。

三层保险各验一道：① 主仓不跟踪 vibecraft-ops（git archive 天然看不到）；
② 投影脚本 denylist 兜底含它；③ 投影前硬闸门代码在位。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def test_main_repo_does_not_track_vibecraft_ops() -> None:
    """根因级：主仓 git 不跟踪任何 vibecraft-ops 文件 → git archive HEAD 不含它。"""
    out = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files", "vibecraft-ops/"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert out == "", f"主仓不该跟踪 vibecraft-ops，但发现：\n{out}"


def test_vibecraft_ops_is_gitignored() -> None:
    r = subprocess.run(
        ["git", "-C", str(_REPO), "check-ignore", "vibecraft-ops/"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, "vibecraft-ops/ 必须被 .gitignore 覆盖"


def test_sync_script_has_ops_defenses() -> None:
    """投影脚本 denylist + 硬闸门双保险在位。"""
    src = (_REPO / "scripts" / "sync_to_opensource.py").read_text(encoding="utf-8")
    assert '"vibecraft-ops"' in src, "_REMOVE_DIRS 应含 vibecraft-ops"
    assert "vibecraft-ops" in src and "p.relative_to(stage).parts" in src, "应有投影前硬闸门"
