"""warp_cooldowns 表 + helper 单测。

数据正确性靠 Liquipedia(LotV Faster speed) — 此处只验证 dict / helper 行为
+ 保护"如果以后误改某个常量值"。
"""

from __future__ import annotations

import pytest

pytest.importorskip("sc2.ids.unit_typeid")


def test_six_warpable_units_present():
    from sc2.ids.unit_typeid import UnitTypeId

    from vibecraft.bot.auto_combat.protoss.plans.warp_cooldowns import WARP_COOLDOWN_S

    expected = {
        UnitTypeId.ZEALOT,
        UnitTypeId.ADEPT,
        UnitTypeId.STALKER,
        UnitTypeId.SENTRY,
        UnitTypeId.HIGHTEMPLAR,
        UnitTypeId.DARKTEMPLAR,
    }
    assert set(WARP_COOLDOWN_S.keys()) == expected


def test_cooldown_values_match_liquipedia():
    """LotV Faster speed:Z/A=20, S/Sen=23, HT/DT=32。"""
    from sc2.ids.unit_typeid import UnitTypeId

    from vibecraft.bot.auto_combat.protoss.plans.warp_cooldowns import WARP_COOLDOWN_S

    assert WARP_COOLDOWN_S[UnitTypeId.ZEALOT] == 20.0
    assert WARP_COOLDOWN_S[UnitTypeId.ADEPT] == 20.0
    assert WARP_COOLDOWN_S[UnitTypeId.STALKER] == 23.0
    assert WARP_COOLDOWN_S[UnitTypeId.SENTRY] == 23.0
    assert WARP_COOLDOWN_S[UnitTypeId.HIGHTEMPLAR] == 32.0
    assert WARP_COOLDOWN_S[UnitTypeId.DARKTEMPLAR] == 32.0


def test_get_warp_cooldown_known():
    from sc2.ids.unit_typeid import UnitTypeId

    from vibecraft.bot.auto_combat.protoss.plans.warp_cooldowns import get_warp_cooldown

    assert get_warp_cooldown(UnitTypeId.STALKER) == 23.0
    assert get_warp_cooldown(UnitTypeId.ZEALOT) == 20.0
    assert get_warp_cooldown(UnitTypeId.DARKTEMPLAR) == 32.0


def test_get_warp_cooldown_unknown_fallback_conservative():
    """未知 unit 返回最大值(32s)兜底 — 不会"误报 ready""。"""
    from sc2.ids.unit_typeid import UnitTypeId

    from vibecraft.bot.auto_combat.protoss.plans.warp_cooldowns import get_warp_cooldown

    # PROBE 不是 warpable unit,走 fallback
    assert get_warp_cooldown(UnitTypeId.PROBE) == 32.0
