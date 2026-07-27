"""build-aware sustain：core_units → 产能建筑 + 出兵 specs 的纯函数翻译。"""

from __future__ import annotations

import pytest

from vibecraft.bot.auto_combat.opening_sustain_act import OpeningSustainAct, plan_from_core_units
from vibecraft.strategy.models import CoreUnit


def test_plan_bio_scales_barracks_to_8():
    cus = [
        CoreUnit(unit="MARINE", policy="mass"),
        CoreUnit(unit="MARAUDER", policy="ratio", per="MARINE", n=4),
        CoreUnit(unit="MEDIVAC", policy="ratio", per="bio", n=8),
        CoreUnit(unit="SIEGETANK", policy="cap", value=3),
    ]
    pt, us = plan_from_core_units(cus)
    # 主产线(兵营)拉到 8(这是治余钱的关键：旧 macro 只 ~2)
    assert pt["BARRACKS"] == 8
    assert pt["STARPORT"] == 2  # 医疗船
    assert pt["FACTORY"] == 2  # 坦克
    specs = {u: (p, c) for u, p, c in us}
    assert specs["MARINE"] == ("BARRACKS", 9999)  # mass
    assert specs["SIEGETANK"] == ("FACTORY", 3)  # cap


def test_plan_player_policy_skipped():
    # player 兵种 auto 不出（玩家指令控制）
    pt, us = plan_from_core_units([CoreUnit(unit="MARAUDER", policy="player")])
    assert us == []
    assert pt == {}


def test_plan_cap_default():
    _pt, us = plan_from_core_units([CoreUnit(unit="SIEGETANK", policy="cap")])
    specs = {u: c for u, _p, c in us}
    assert specs["SIEGETANK"] == 3  # value None → 默认 3


def test_plan_empty():
    assert plan_from_core_units([]) == ({}, [])


def test_plan_zerg_morphs_from_larva_not_tech_building():
    # 回归：虫族 ActUnit 的 from_building 必须是 LARVA（真正孵化来源），
    # 不能是科技楼 ROACHWARREN —— 否则 roachwarren.train(ROACH) 无效、sustain 永不出兵。
    cus = [
        CoreUnit(unit="ROACH", policy="mass"),
        CoreUnit(unit="HYDRALISK", policy="ratio", per="ROACH", n=2),
        CoreUnit(unit="ZERGLING", policy="light", value=6),
    ]
    pt, us = plan_from_core_units(cus)
    specs = {u: (p, c) for u, p, c in us}
    assert specs["ROACH"] == ("LARVA", 9999)  # mass，从 larva 孵
    assert specs["HYDRALISK"][0] == "LARVA"
    assert specs["ZERGLING"][0] == "LARVA"
    # 科技楼是前置依赖（GridBuilding 确保 1 座），不是训练者
    assert pt == {"ROACHWARREN": 1, "HYDRALISKDEN": 1, "SPAWNINGPOOL": 1}


def test_morph_baneling_ability_fixed():
    # 回归：vendored MorphBaneling 旧 ability MORPHZERGLINGTOBANELING_BANELING 对小狗已失效
    # → 小狗永不变蛹、build-aware sustain 爆虫冻结。必须用 MORPHTOBANELING_BANELING。
    # sharpy 升级若 revert 此 vendor 修，本测试捕获（见 docs/sharpy-patches.md §6）。
    pytest.importorskip("sharpy.plans.acts.zerg.morph_units")
    from sc2.ids.ability_id import AbilityId
    from sharpy.plans.acts.zerg.morph_units import MorphBaneling

    assert MorphBaneling(20).ability_type == AbilityId.MORPHTOBANELING_BANELING


def test_zerg_unit_dispatches_baneling_to_morph():
    # 回归：ZergUnit(BANELING) 必须走 MorphBaneling（不是裸 ActUnit.train）；ZERGLING 走 larva。
    pytest.importorskip("sharpy.plans.acts.zerg")
    from sc2.ids.unit_typeid import UnitTypeId
    from sharpy.plans.acts.zerg import ZergUnit
    from sharpy.plans.acts.zerg.morph_units import MorphBaneling

    assert isinstance(ZergUnit(UnitTypeId.BANELING, 150).morph_unit, MorphBaneling)
    assert ZergUnit(UnitTypeId.ZERGLING, 9999).morph_unit is None


def test_make_baneling_morph_dispatch_by_mode():
    # 回归：forward → ForwardBanelingZergUnit(前压护蛹)；home/默认 → 裸 ZergUnit(home MorphBaneling)。
    pytest.importorskip("sharpy.plans.acts.zerg")
    from vibecraft.bot.auto_combat.zerg.baneling_morph import (
        ForwardBanelingMorph,
        ForwardBanelingZergUnit,
        make_baneling_morph,
    )

    fwd = make_baneling_morph(250, mode="forward")
    assert isinstance(fwd, ForwardBanelingZergUnit)
    assert isinstance(fwd.morph_unit, ForwardBanelingMorph)
    home = make_baneling_morph(250, mode="home")
    assert not isinstance(home, ForwardBanelingZergUnit)
    assert not isinstance(home.morph_unit, ForwardBanelingMorph)


def test_plan_protoss_gateway_unit_producer_is_gateway_not_warpgate():
    # 回归：神族 gateway 兵 UNIT_TRAINED_FROM = {GATEWAY, WARPGATE}（set 无序）。
    # 必须显式选 GATEWAY —— GridBuilding 只能造 GATEWAY，WARPGATE 直接造无效。
    cus = [CoreUnit(unit="STALKER", policy="mass")]
    pt, us = plan_from_core_units(cus)
    assert pt == {"GATEWAY": 8}  # 主产线扩 8
    specs = {u: (p, c) for u, p, c in us}
    assert specs["STALKER"] == ("GATEWAY", 9999)


def test_plan_protoss_robo_and_stargate_producers():
    cus = [
        CoreUnit(unit="IMMORTAL", policy="mass"),
        CoreUnit(unit="COLOSSUS", policy="cap", value=4),
        CoreUnit(unit="VOIDRAY", policy="mass"),
    ]
    pt, us = plan_from_core_units(cus)
    specs = {u: p for u, p, _c in us}
    assert specs["IMMORTAL"] == "ROBOTICSFACILITY"
    assert specs["COLOSSUS"] == "ROBOTICSFACILITY"
    assert specs["VOIDRAY"] == "STARGATE"
    assert pt["ROBOTICSFACILITY"] == 8  # IMMORTAL mass（地面 8）
    assert pt["STARGATE"] == 4  # VOIDRAY mass：空军降档 4（贵+气瓶颈，8 会空转/气浮）


def test_plan_air_mass_producer_capped_at_4():
    # 空军主力（凤凰/虚空/航母）producer=STARGATE 时降档到 4，不沿用地面的 8。
    pt, _us = plan_from_core_units([CoreUnit(unit="PHOENIX", policy="mass")])
    assert pt == {"STARGATE": 4}


def test_plan_mothership_does_not_grid_nexus():
    # MOTHERSHIP 从 NEXUS 训练，但 NEXUS 是 townhall —— 不能 GridBuilding 扩（会误盖基地）。
    # 仍续兵（unit_specs 有 MOTHERSHIP），但 producer_targets 不含 NEXUS。
    pt, us = plan_from_core_units([CoreUnit(unit="MOTHERSHIP", policy="cap", value=1)])
    assert "NEXUS" not in pt
    assert pt == {}
    specs = {u: (p, c) for u, p, c in us}
    assert specs["MOTHERSHIP"] == ("NEXUS", 1)


def test_plan_zerg_ravager_morphs_from_roach():
    # 飞蛇从蟑螂变（UNIT_TRAINED_FROM[RAVAGER]=ROACH），科技楼仍是 ROACHWARREN 前置。
    cus = [
        CoreUnit(unit="ROACH", policy="mass"),
        CoreUnit(unit="RAVAGER", policy="cap", value=6),
    ]
    pt, us = plan_from_core_units(cus)
    specs = {u: (p, c) for u, p, c in us}
    assert specs["ROACH"] == ("LARVA", 9999)
    assert specs["RAVAGER"] == ("ROACH", 6)
    assert pt == {"ROACHWARREN": 1}


# --- 虫族采气优先级（gas_per_base）按主力兵种判定 ---


@pytest.mark.parametrize(
    ("core_units", "expected"),
    [
        # 纯狗（12pool）：0 气 → 减气
        ([CoreUnit(unit="ZERGLING", policy="mass")], 1),
        # 狗+爆虫（ling_bane）：爆虫吃气 → 满气供副兵
        (
            [
                CoreUnit(unit="ZERGLING", policy="mass"),
                CoreUnit(unit="BANELING", policy="ratio", per="ZERGLING", n=1),
            ],
            2,
        ),
        # 蟑螂系（macro_hatch / roach_allin / nydus）：吃矿为主、矿瓶颈 → 减气
        ([CoreUnit(unit="ROACH", policy="mass")], 1),
        # 蟑螂+刺蛇（roach_hydra）：主力仍是蟑螂 → 减气（实测气浮 5000+，用户）
        (
            [
                CoreUnit(unit="ROACH", policy="mass"),
                CoreUnit(unit="HYDRALISK", policy="ratio", per="ROACH", n=2),
            ],
            1,
        ),
        # 蟑螂+破坏者（roach_ravager）：主力蟑螂 → 减气
        (
            [
                CoreUnit(unit="ROACH", policy="mass"),
                CoreUnit(unit="RAVAGER", policy="cap", value=6),
            ],
            1,
        ),
        # 飞龙（mutalisk_harass）：气耗大主力 → 满气
        ([CoreUnit(unit="MUTALISK", policy="mass")], 2),
        # 无 core_units → 默认安全满气
        ([], 2),
    ],
)
def test_zerg_gas_per_base(core_units: list[CoreUnit], expected: int) -> None:
    act = OpeningSustainAct("ZERG")
    act._active_core_units = lambda: core_units  # type: ignore[method-assign]
    assert act._zerg_gas_per_base() == expected
