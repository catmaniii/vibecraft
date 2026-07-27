"""产能封锁机制级拦截（2026-06-02）：ActUnit / WarpUnit.execute 下令前检查
knowledge.vibecraft.production_blocked，命中该兵种 → return True 跳过（不训练/折跃）。

替代了从未实现的反应式 ProductionBlockAct。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

# vendor/sharpy 不在标准 path，按生产同款注入。
from vibecraft.bot.auto_combat.common_bot import _ensure_sharpy_on_path

_ensure_sharpy_on_path()

# 本文件导入的是**真** sharpy（不是别处那种 sys.modules 打桩），而真 sharpy 会连带导入
# sc2pathlib —— 它是 vendored 的编译扩展，仓库里只有 `sc2pathlib.cp311-win_amd64.pyd`
# （Windows + CPython 3.11 专用，上游 DrInfy 提供，我们没有其它平台的构建）。
# 所以在 Linux CI / 非 3.11 的环境里这个文件整体跳过；开发机（Windows + 3.11）正常跑。
pytest.importorskip(
    "sc2pathlib.sc2pathlib",
    reason="vendored sc2pathlib 只有 cp311-win_amd64 的编译产物，非 Windows/3.11 环境跳过",
)

from sc2.ids.unit_typeid import UnitTypeId  # noqa: E402
from sharpy.plans.acts.act_unit import ActUnit  # noqa: E402
from sharpy.plans.acts.protoss.warp_unit import WarpUnit  # noqa: E402


def _knowledge(blocked: set[str]) -> Any:
    return SimpleNamespace(vibecraft=SimpleNamespace(production_blocked=set(blocked)))


def test_act_unit_blocked_returns_true_without_training() -> None:
    """ActUnit：unit_type 在 production_blocked 里 → execute 顶部 return True，不下训练令。"""
    a = object.__new__(ActUnit)
    a.knowledge = _knowledge({"Stalker"})
    a.unit_type = UnitTypeId.STALKER
    # 若没被拦截会往下访问 self.ai/_game_data 抛 AttributeError；拦截则干净返回 True
    assert asyncio.run(a.execute()) is True


def test_act_unit_case_insensitive() -> None:
    """canonical 名(Stalker) vs UnitTypeId.name(STALKER)大小写不敏感匹配。"""
    a = object.__new__(ActUnit)
    a.knowledge = _knowledge({"stalker"})
    a.unit_type = UnitTypeId.STALKER
    assert asyncio.run(a.execute()) is True


def test_warp_unit_blocked_returns_true() -> None:
    """WarpUnit（折跃路径）：blocked → execute 顶部 return True。"""
    w = object.__new__(WarpUnit)
    w.knowledge = _knowledge({"Zealot"})
    w.unit_type = UnitTypeId.ZEALOT
    assert asyncio.run(w.execute()) is True


def test_act_unit_empty_block_does_not_short_circuit() -> None:
    """没封锁时不在 block 处短路 —— 会继续往下(此处必访问 self.ai 抛错,证明没被拦)。"""
    a = object.__new__(ActUnit)
    a.knowledge = _knowledge(set())
    a.unit_type = UnitTypeId.STALKER
    # 空 block set → 不 return True，继续执行 → 访问未设置的 self.ai → AttributeError
    try:
        asyncio.run(a.execute())
    except AttributeError:
        return  # 预期:越过 block 检查继续往下
    raise AssertionError("空 block set 不应在拦截处短路")
