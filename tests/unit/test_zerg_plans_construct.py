"""虫族 plan 的 create_plan() 构造 + 占位 enum 审计（#550 zvp_macro 起）。

构造期 TypeError / 占位不可训练 enum（execute() 才崩）→ 真局炸。这里静态拦死。
对标 tests/unit/test_terran_plans_construct.py。
"""

from __future__ import annotations

import asyncio

import pytest

# 新增/改虫族 opening / doctrine plan 都进这里（含 auto-switch 进来的 doctrine）。
_ZERG_PLANS = [
    ("zvp_macro", "ZvpMacro"),
    ("nydus", "NydusRush"),
    # 2026-07-10 结构冻结修复(worker-saturation-floor 设计文档 4 结构病)：
    # 两个 doctrine 都从阻塞 SequentialList 改并行 BuildOrder，构造 + 占位审计
    # 一起进来防回归。
    ("roach_hydra_viper", "RoachHydraViper"),
    ("ultralisk", "Ultralisk"),
]

# 不可训练的占位 UnitTypeId 名（creation_ability=None）——morph 类单位用 ZergUnit/ActUnit
# 直接训练会在 act_unit.py calculate_ability_cost(None) 抛 AssertionError 杀整局。
# 虫族 morph 单位：OVERSEER(从 Overlord morph) / BROODLORD / LURKERMP / RAVAGER 等不能直 train。
_PLACEHOLDER_UNIT_NAMES = {
    "OVERSEER",
    "BROODLORD",
    "LURKERMP",
    "RAVAGER",
    "BANELING",
    "OVERLORDTRANSPORT",
}


def _walk_acts(node: object, seen: set[int] | None = None):
    if seen is None:
        seen = set()
    if id(node) in seen:
        return
    seen.add(id(node))
    yield node
    for attr in ("orders", "action", "action_else"):
        child = getattr(node, attr, None)
        if child is None:
            continue
        if isinstance(child, (list, tuple)):
            for c in child:
                yield from _walk_acts(c, seen)
        else:
            yield from _walk_acts(child, seen)


@pytest.mark.parametrize(("module", "cls_name"), _ZERG_PLANS)
def test_zerg_plan_create_plan_constructs(module: str, cls_name: str) -> None:
    """create_plan() 无异常返回 BuildOrder —— 拦截构造期 TypeError / import 错。"""
    from vibecraft.bot.auto_combat.common_bot import _ensure_sharpy_on_path

    _ensure_sharpy_on_path()
    from sharpy.plans import BuildOrder

    mod = __import__(f"vibecraft.bot.auto_combat.zerg.plans.{module}", fromlist=[cls_name])
    plan = asyncio.run(getattr(mod, cls_name)().create_plan())
    assert isinstance(plan, BuildOrder)


@pytest.mark.parametrize(("module", "cls_name"), _ZERG_PLANS)
def test_zerg_plan_no_placeholder_train_unit(module: str, cls_name: str) -> None:
    """plan 树里没有用占位/morph enum 做 ActUnit/ZergUnit 的训练目标。"""
    from vibecraft.bot.auto_combat.common_bot import _ensure_sharpy_on_path

    _ensure_sharpy_on_path()
    from sc2.ids.unit_typeid import UnitTypeId

    mod = __import__(f"vibecraft.bot.auto_combat.zerg.plans.{module}", fromlist=[cls_name])
    plan = asyncio.run(getattr(mod, cls_name)().create_plan())

    bad = []
    for node in _walk_acts(plan):
        ut = getattr(node, "unit_type", None)
        if isinstance(ut, UnitTypeId) and ut.name in _PLACEHOLDER_UNIT_NAMES:
            # DefensiveBuilding 的 unit_type 是建筑(SPORECRAWLER)，不在占位名单里，安全跳过
            bad.append(ut.name)
    assert not bad, (
        f"{cls_name} 用占位/morph enum 训练单位 {bad}（不可直接 train，会在 act_unit.py "
        f"calculate_ability_cost(None) 崩整局）——morph 类用专门的 Morph act"
    )
