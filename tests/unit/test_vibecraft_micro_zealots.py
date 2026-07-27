"""VibeCraftMicroZealots：去掉 sharpy 叉子 group 级聚团（行军不停回缩重心）。

对照测试：
- 父类 MicroZealots 在 Assault 行军 + engage_ratio>0.25 时把整团拉向团重心（复现 bug）。
- VibeCraftMicroZealots 原样返回 current_command（推进目标不变，不聚团）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

# vendor/sharpy 不在标准 path 上，按生产同样的注入函数先上 path 再 import sharpy。
from vibecraft.bot.auto_combat.common_bot import _ensure_sharpy_on_path

_ensure_sharpy_on_path()

# 同 test_production_block_intercept.py：本文件导入**真** sharpy，它会连带导入 vendored 的
# 编译扩展 sc2pathlib（仓库里只有 cp311-win_amd64 的 .pyd）。非 Windows/3.11 环境整体跳过。
pytest.importorskip(
    "sc2pathlib.sc2pathlib",
    reason="vendored sc2pathlib 只有 cp311-win_amd64 的编译产物，非 Windows/3.11 环境跳过",
)

from sc2.position import Point2  # noqa: E402
from sharpy.combat import Action, MoveType  # noqa: E402
from sharpy.combat.protoss.micro_zealots import MicroZealots  # noqa: E402

from vibecraft.bot.auto_combat.protoss.vibecraft_micro_zealots import (  # noqa: E402
    VibeCraftMicroZealots,
)


def _make_micro(cls: type) -> object:
    """绕过 MicroStep.__init__（需 knowledge），直接塞 group_solve_combat 读的状态。

    场景：正常进攻行军（Assault），前排已进敌方射程（engage_ratio 0.9 > 0.25），
    closest_group 重心在 (50,50)，current_command 推进目标在 (99,99)。
    """
    m = object.__new__(cls)
    m.move_type = MoveType.Assault
    m.engage_ratio = 0.9
    m.ready_to_attack_ratio = 0.9
    m.closest_group_distance = 1.0
    m.center = Point2((10.0, 10.0))
    m.closest_group = SimpleNamespace(center=Point2((50.0, 50.0)))
    return m


def test_parent_micro_zealots_balls_to_group_center() -> None:
    """复现 bug：原版 sharpy MicroZealots 行军中把整团拉向 closest_group.center。"""
    m = _make_micro(MicroZealots)
    current = Action(Point2((99.0, 99.0)), True)
    result = m.group_solve_combat(None, current)  # type: ignore[attr-defined]
    # 原版聚团：target 变成团重心 (50,50)，而不是 current_command 的 (99,99)
    assert result.target == Point2((50.0, 50.0))


def test_vibecraft_micro_zealots_no_grouping() -> None:
    """修复：VibeCraftMicroZealots 原样返回 current_command（不聚团）。"""
    m = _make_micro(VibeCraftMicroZealots)
    current = Action(Point2((99.0, 99.0)), True)
    result = m.group_solve_combat(None, current)  # type: ignore[attr-defined]
    assert result is current
    assert result.target == Point2((99.0, 99.0))


def test_vibecraft_micro_zealots_inherits_unit_solve() -> None:
    """子类只改 group 级聚团，unit_solve_combat（冲锋/focus fire）仍继承父类。"""
    assert VibeCraftMicroZealots.unit_solve_combat is MicroZealots.unit_solve_combat
