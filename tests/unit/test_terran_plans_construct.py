"""人族 9 个开局 plan 的 create_plan() 都能无异常构造出 BuildOrder。

build_acceptance 在 bot 启动时对每个 dummy 调 create_plan();构造期 TypeError
(BuildAddon 漏 to_count、Tech 用了 SDK 不认的 UpgradeId 等)会让 plan 静默落
fallback——bot 看似在跑实则只造农民。这个测试在无 SC2 的单测阶段就拦住这类 bug。
"""

from __future__ import annotations

import asyncio

import pytest

_TERRAN_OPENINGS = [
    ("marine_rush", "MarineRush"),
    ("reaper_expand", "ReaperExpand"),
    ("hellion_expand", "HellionExpand"),
    ("widow_mine_drop", "WidowMineDrop"),
    ("two_one_one", "TwoOneOne"),
    ("banshee_harass", "BansheeHarass"),
    ("one_one_one", "OneOneOne"),
    ("bio_stim", "BioStim"),
    ("two_base_tanks", "TwoBaseTanks"),
    ("ghost_nuke", "GhostNuke"),
    ("bc_rush", "BcRush"),
    ("proxy_4rax", "Proxy4Rax"),
    ("mass_reaper", "MassReaper"),
    # ── 持续 doctrine plan（opening_completed 后 auto-switch 进来的）──────────
    # 不加这些会漏测:bc_rush 开局完成 → 切 persistent_skyterran(BcLate),BcLate 里
    # TerranUnit(VIKING) 占位 enum 整局崩(2026-06-19 真局踩)。doctrine plan 必须一起验。
    ("bc_late", "BcLate"),
    ("liberator", "LiberatorSky"),
    ("mech", "Mech"),
    ("bio_max", "BioMax"),
]

# 不可训练的占位 UnitTypeId 名（creation_ability=None）——TerranUnit/ActUnit 训练它
# 会在 act_unit.py:131 calculate_ability_cost(None) 抛 AssertionError 杀整局。
# 与 Director._UNIT_NAME_MAP 同源（那张表把这些占位名归一到可训练 enum）。
_PLACEHOLDER_UNIT_NAMES = {"VIKING"}


def _walk_acts(node: object, seen: set[int] | None = None):
    """递归遍历 sharpy plan 树（BuildOrder/SequentialList.orders + IfElse.action/action_else），
    yield 每个 act 节点。"""
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


@pytest.mark.parametrize(("module", "cls_name"), _TERRAN_OPENINGS)
def test_terran_plan_no_placeholder_train_unit(module: str, cls_name: str) -> None:
    """没有 plan 用占位 enum（如 VIKING）做 ActUnit/TerranUnit 的训练目标。

    构造 plan 不报错 ≠ 安全：占位 enum 的 train 在 execute() 才崩（运行时）。这里
    静态走 plan 树，把 unit_type 落在占位名上的 ActUnit 全揪出来，单测阶段就拦死。
    """
    from vibecraft.bot.auto_combat.common_bot import _ensure_sharpy_on_path

    _ensure_sharpy_on_path()
    from sc2.ids.unit_typeid import UnitTypeId

    mod = __import__(f"vibecraft.bot.auto_combat.terran.plans.{module}", fromlist=[cls_name])
    plan = asyncio.run(getattr(mod, cls_name)().create_plan())

    bad = []
    for node in _walk_acts(plan):
        ut = getattr(node, "unit_type", None)
        if isinstance(ut, UnitTypeId) and ut.name in _PLACEHOLDER_UNIT_NAMES:
            bad.append(ut.name)
    assert not bad, (
        f"{cls_name} 用占位 enum 训练单位 {bad}（不可训练，会在 act_unit.py "
        f"calculate_ability_cost(None) 崩整局）——换成可训练 enum"
    )


@pytest.mark.parametrize(("module", "cls_name"), _TERRAN_OPENINGS)
def test_terran_opening_create_plan_constructs(module: str, cls_name: str) -> None:
    """create_plan() 无异常返回 BuildOrder —— 拦截构造期 TypeError。"""
    from vibecraft.bot.auto_combat.common_bot import _ensure_sharpy_on_path

    _ensure_sharpy_on_path()
    from sharpy.plans import BuildOrder

    mod = __import__(f"vibecraft.bot.auto_combat.terran.plans.{module}", fromlist=[cls_name])
    plan = asyncio.run(getattr(mod, cls_name)().create_plan())
    assert isinstance(plan, BuildOrder)
