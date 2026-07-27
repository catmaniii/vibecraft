"""transition_cost.py + pick_best_persistent 单元测试。

覆盖：
- 6 分量各自的边界
- 完整公式 worked example（1g_robo 完成后 → ground_mech 应最低）
- pick_best_persistent 选择逻辑
- never returns None（Q4 不变量准备）
"""

from __future__ import annotations

import pytest

from vibecraft.strategy import (
    PersistentDoctrine,
    StrategyLibrary,
)
from vibecraft.strategy.transition_cost import (
    GameSnapshot,
    estimate_gas_demand,
    pick_best_persistent,
    transition_cost,
)

# =========================================================================
# Test fixtures
# =========================================================================


def _make_doctrine(
    sid: str,
    target_composition: dict[str, int] | None = None,
    required_structures: dict[str, int] | None = None,
    required_tech: list[str] | None = None,
    counters_against: list[str] | None = None,
    weak_against: list[str] | None = None,
    gas_intensity: str = "medium",
) -> PersistentDoctrine:
    return PersistentDoctrine.model_validate(
        {
            "kind": "persistent_doctrine",
            "id": sid,
            "display_name_zh": sid,
            "target_composition": target_composition or {},
            "required_structures": required_structures or {},
            "required_tech": required_tech or [],
            "counters_against": counters_against or [],
            "weak_against": weak_against or [],
            "gas_intensity": gas_intensity,
        }
    )


# =========================================================================
# GameSnapshot
# =========================================================================


class TestGameSnapshot:
    def test_defaults(self) -> None:
        g = GameSnapshot()
        assert g.structure_count("Gateway") == 0
        assert g.unit_count("Stalker") == 0
        assert not g.has_upgrade("Charge")
        assert g.gas_income_per_minute == 0.0

    def test_own_army_excludes_workers(self) -> None:
        g = GameSnapshot(
            units={"Probe": 50, "Stalker": 8, "Sentry": 3, "Overlord": 5},
        )
        army = g.own_army_summary
        assert "Probe" not in army
        assert "Overlord" not in army
        assert army["Stalker"] == 8
        assert army["Sentry"] == 3


# =========================================================================
# transition_cost: 各分量边界
# =========================================================================


class TestBuildComponent:
    def test_low_cost_when_all_built_and_gas_sufficient(self) -> None:
        """所有建筑齐全 + gas income 充足 → build/tech/unit/gas 都接近 0"""
        target = _make_doctrine(
            "t",
            required_structures={"Gateway": 2, "RoboticsFacility": 1},
            gas_intensity="low",
        )
        game = GameSnapshot(
            structures={"Gateway": 2, "RoboticsFacility": 1, "Nexus": 1, "CyberneticsCore": 1},
            gas_income_per_minute=200,  # 超过 low 需求 100
        )
        cost = transition_cost(target, game, set())
        assert cost == 0.0  # 所有分量都 0

    def test_missing_building_adds_cost(self) -> None:
        target_with = _make_doctrine("a", required_structures={"FleetBeacon": 1})
        target_no = _make_doctrine("b", required_structures={})
        game = GameSnapshot()  # 完全空白
        cost_with = transition_cost(target_with, game, set())
        cost_no = transition_cost(target_no, game, set())
        assert cost_with > cost_no  # 缺 FleetBeacon + transitive prereq

    def test_prereq_chain_counted(self) -> None:
        """缺 FleetBeacon 时也会算入 Stargate / CyberneticsCore / Gateway / Nexus 前置"""
        target_just_fb = _make_doctrine("fb", required_structures={"FleetBeacon": 1})
        target_fb_with_prereqs_satisfied = _make_doctrine(
            "fb2", required_structures={"FleetBeacon": 1}
        )
        game_empty = GameSnapshot()
        game_with_prereqs = GameSnapshot(
            structures={
                "Nexus": 1,
                "Gateway": 1,
                "CyberneticsCore": 1,
                "Stargate": 1,
            }
        )
        c_empty = transition_cost(target_just_fb, game_empty, set())
        c_with = transition_cost(target_fb_with_prereqs_satisfied, game_with_prereqs, set())
        assert c_empty > c_with  # 完整 prereq 时成本更低


class TestTechComponent:
    def test_missing_upgrade_adds_cost(self) -> None:
        target_with_tech = _make_doctrine("a", required_tech=["Charge", "Blink"])
        target_no_tech = _make_doctrine("b", required_tech=[])
        game = GameSnapshot()
        c_with = transition_cost(target_with_tech, game, set())
        c_no = transition_cost(target_no_tech, game, set())
        assert c_with > c_no

    def test_already_researched_zero(self) -> None:
        target = _make_doctrine("a", required_tech=["Charge"])
        game_done = GameSnapshot(upgrades={"Charge"})
        game_missing = GameSnapshot()
        assert transition_cost(target, game_done, set()) < transition_cost(
            target, game_missing, set()
        )

    def test_in_progress_research_zero(self) -> None:
        target = _make_doctrine("a", required_tech=["Charge"])
        game_in_progress = GameSnapshot(researching={"Charge"})
        game_done = GameSnapshot(upgrades={"Charge"})
        # 正在研究的不算缺
        assert transition_cost(target, game_in_progress, set()) == transition_cost(
            target, game_done, set()
        )


class TestUnitComponent:
    def test_missing_units_proportional(self) -> None:
        target_more = _make_doctrine("a", target_composition={"Stalker": 30})
        target_less = _make_doctrine("b", target_composition={"Stalker": 10})
        game = GameSnapshot()
        c_more = transition_cost(target_more, game, set())
        c_less = transition_cost(target_less, game, set())
        assert c_more > c_less

    def test_already_have_units_zero(self) -> None:
        target = _make_doctrine("a", target_composition={"Stalker": 10})
        game_full = GameSnapshot(units={"Stalker": 10})
        game_empty = GameSnapshot()
        assert transition_cost(target, game_full, set()) < transition_cost(
            target, game_empty, set()
        )


class TestGasBottleneck:
    def test_high_intensity_more_costly_when_low_income(self) -> None:
        target_high = _make_doctrine("h", gas_intensity="high")
        target_low = _make_doctrine("l", gas_intensity="low")
        game = GameSnapshot(gas_income_per_minute=50)  # 低 gas income
        c_high = transition_cost(target_high, game, set())
        c_low = transition_cost(target_low, game, set())
        assert c_high > c_low  # high gas demand > low gas income → 大 bottleneck

    def test_no_bottleneck_when_gas_sufficient(self) -> None:
        target = _make_doctrine("a", gas_intensity="medium")
        game_plenty = GameSnapshot(gas_income_per_minute=500)
        game_scarce = GameSnapshot(gas_income_per_minute=50)
        assert transition_cost(target, game_plenty, set()) < transition_cost(
            target, game_scarce, set()
        )


class TestCounterComponent:
    def test_counter_against_enemy_reduces_cost(self) -> None:
        target = _make_doctrine(
            "a",
            counters_against=["zerg_ling_bane", "terran_bio"],
        )
        enemy_matches = {"zerg_ling_bane"}
        enemy_no_match = {"protoss_skytoss"}
        c_with_counter = transition_cost(target, GameSnapshot(), enemy_matches)
        c_no_counter = transition_cost(target, GameSnapshot(), enemy_no_match)
        assert c_with_counter < c_no_counter  # counter 减成本

    def test_weak_against_enemy_increases_cost(self) -> None:
        target = _make_doctrine("a", weak_against=["mass_air"])
        enemy_weak_for_us = {"mass_air"}
        enemy_no_match = {"mass_ground"}
        c_with_weak = transition_cost(target, GameSnapshot(), enemy_weak_for_us)
        c_no_weak = transition_cost(target, GameSnapshot(), enemy_no_match)
        assert c_with_weak > c_no_weak

    def test_multiple_counter_hits_stack(self) -> None:
        target = _make_doctrine(
            "a",
            counters_against=["zerg_ling_bane", "terran_bio", "protoss_dt"],
        )
        single_hit = {"terran_bio"}
        triple_hit = {"zerg_ling_bane", "terran_bio", "protoss_dt"}
        assert transition_cost(target, GameSnapshot(), triple_hit) < transition_cost(
            target, GameSnapshot(), single_hit
        )


class TestObsoleteComponent:
    def test_obsolete_units_add_cost(self) -> None:
        """当前 army 全是 Stalker，转 skytoss (target=Carrier) → stalker obsolete"""
        target_skytoss = _make_doctrine("a", target_composition={"Carrier": 10})
        game_with_stalkers = GameSnapshot(units={"Stalker": 20})
        game_empty = GameSnapshot()
        # stalker 沉没成本 → 加分
        assert transition_cost(target_skytoss, game_with_stalkers, set()) > transition_cost(
            target_skytoss, game_empty, set()
        )

    def test_no_obsolete_when_army_matches(self) -> None:
        """当前 army 全是 Stalker，转 blink_harass (target=Stalker) → 0 obsolete"""
        target_blink = _make_doctrine("a", target_composition={"Stalker": 30})
        game_with_stalkers = GameSnapshot(units={"Stalker": 20})
        # army 全是 target，obsolete 几乎 0
        cost = transition_cost(target_blink, game_with_stalkers, set())
        # 应该比"全是 Carrier"成本低（沉没成本 0 + 已有部分 unit）
        target_skytoss = _make_doctrine("b", target_composition={"Carrier": 10})
        assert cost < transition_cost(target_skytoss, game_with_stalkers, set())


# =========================================================================
# 完整 worked example（design doc §4.3）
# =========================================================================


class TestWorkedExample:
    """1g_robo_immortal 完成后选 persistent —— 应选 ground_mech（VR 已有，counter bio）"""

    def _setup(self) -> tuple[GameSnapshot, set[str], StrategyLibrary]:
        """模拟 1g_robo 完成时的状态。"""
        game = GameSnapshot(
            structures={
                "Nexus": 2,
                "Pylon": 4,
                "Gateway": 1,
                "CyberneticsCore": 1,
                "Assimilator": 2,
                "RoboticsFacility": 1,
                "Forge": 1,
            },
            units={
                "Probe": 40,
                "Stalker": 3,
                "Sentry": 3,
                "Immortal": 5,
            },
            upgrades={"WarpGateResearch", "ProtossGroundArmorsLevel1"},
            gas_income_per_minute=150,
        )
        enemy_tags = {"terran_bio"}  # 侦察看到对方 bio

        # 模拟 5 个 persistent doctrine（真实 yaml 会在 Step 7 创建）
        doctrines = [
            _make_doctrine(
                "persistent_ground_mech",
                target_composition={
                    "Immortal": 6,
                    "Colossus": 4,
                    "Stalker": 8,
                    "Zealot": 8,
                    "Sentry": 3,
                },
                required_structures={
                    "RoboticsFacility": 2,
                    "RoboticsBay": 1,
                    "TwilightCouncil": 1,
                    "Forge": 1,
                    "Nexus": 3,
                    "Gateway": 5,
                },
                required_tech=["Charge", "ProtossGroundWeaponsLevel1"],
                counters_against=["zerg_roach_hydra", "zerg_ling_bane", "terran_bio"],
                weak_against=["mass_air", "protoss_storm"],
                gas_intensity="medium",
            ),
            _make_doctrine(
                "persistent_iac_macro",
                target_composition={
                    "Zealot": 16,
                    "Immortal": 4,
                    "Archon": 6,
                    "HighTemplar": 4,
                    "Sentry": 3,
                },
                required_structures={
                    "RoboticsFacility": 1,
                    "TwilightCouncil": 1,
                    "TemplarArchives": 1,
                    "Gateway": 7,
                    "Nexus": 3,
                    "Forge": 1,
                },
                required_tech=["Charge", "PsiStorm"],
                counters_against=["terran_bio", "zerg_ling_bane"],
                weak_against=["terran_mech_tank", "mass_air"],
                gas_intensity="high",
            ),
            _make_doctrine(
                "persistent_skytoss",
                target_composition={
                    "Carrier": 12,
                    "Tempest": 3,
                    "HighTemplar": 5,
                    "Mothership": 1,
                    "Observer": 2,
                },
                required_structures={
                    "Stargate": 4,
                    "FleetBeacon": 1,
                    "TemplarArchives": 1,
                    "TwilightCouncil": 1,
                    "RoboticsFacility": 1,
                    "Forge": 1,
                    "Nexus": 4,
                },
                required_tech=["ProtossAirWeaponsLevel1", "PsiStorm", "GravitonCatapult"],
                counters_against=["mass_ground", "zerg_roach_hydra", "terran_bio"],
                weak_against=["mass_corruptor", "mass_viking"],
                gas_intensity="high",
            ),
            _make_doctrine(
                "persistent_blink_harass",
                target_composition={"Stalker": 30, "Observer": 2, "WarpPrism": 2, "Sentry": 3},
                required_structures={
                    "TwilightCouncil": 1,
                    "RoboticsFacility": 1,
                    "Gateway": 6,
                    "Nexus": 3,
                },
                required_tech=["Blink", "ProtossGroundWeaponsLevel1"],
                counters_against=["terran_bio_no_stim", "zerg_mutalisk"],
                weak_against=["terran_mech_tank", "mass_marauder"],
                gas_intensity="medium",
            ),
            _make_doctrine(
                "persistent_phoenix_storm",
                target_composition={"Phoenix": 12, "Tempest": 6, "HighTemplar": 4, "Sentry": 3},
                required_structures={
                    "Stargate": 3,
                    "FleetBeacon": 1,
                    "TemplarArchives": 1,
                    "TwilightCouncil": 1,
                    "Nexus": 4,
                    "Gateway": 4,
                    "Forge": 1,
                },
                required_tech=["PsiStorm", "ProtossAirWeaponsLevel1"],
                counters_against=["mass_air", "worker_harass"],
                weak_against=["mass_viking", "zerg_corruptor"],
                gas_intensity="high",
            ),
        ]
        # StrategyLibrary 构造时也要传 races（不然 persistent_doctrines("protoss") 返回空）
        races = {d.id: "protoss" for d in doctrines}
        library = StrategyLibrary(persistents=doctrines, races=races)
        return game, enemy_tags, library

    def test_picks_low_transition_cost_doctrine(self) -> None:
        """1g_robo 完成 + 侦察到 terran_bio → 应选 VR/VC 已有的 doctrine（不是 skytoss/phoenix_storm）

        实际 transition_cost 公式输出（v1 权重）：
        - iac_macro:     ~4700（VR 已有，4 immortal 接近 target，仅需 charge/HT/Archon）
        - ground_mech:   ~5100（VR 已有，但需 Colossus 4 + RoboticsBay 大投入）
        - blink_harass:  ~5500（VR 已有但要重建 6 Gateway + 大量 Stalker）
        - phoenix_storm: ~9700（要 3 Stargate + FleetBeacon + 12 Phoenix，全新空军链）
        - skytoss:      ~12400（要 4 Stargate + FleetBeacon + 12 航母，全新链）

        合理的选择是 ground_mech / iac_macro / blink_harass 三者之一，都是地面延续。
        skytoss / phoenix_storm 太贵。
        """
        game, enemy_tags, library = self._setup()
        chosen, _, all_costs = pick_best_persistent(game, enemy_tags, library, "protoss")
        cheap_options = {
            "persistent_ground_mech",
            "persistent_iac_macro",
            "persistent_blink_harass",
        }
        expensive_options = {"persistent_skytoss", "persistent_phoenix_storm"}
        assert chosen in cheap_options, (
            f"expected one of {cheap_options} but got {chosen}\n"
            "all costs:\n"
            + "\n".join(
                f"  {k}: {v:.1f}" for k, v in sorted(all_costs.items(), key=lambda kv: kv[1])
            )
        )
        for exp in expensive_options:
            assert all_costs[chosen] < all_costs[exp], (
                f"{chosen} ({all_costs[chosen]:.1f}) should be cheaper than {exp} ({all_costs[exp]:.1f})"
            )

    def test_skytoss_costs_much_more_than_chosen(self) -> None:
        """skytoss 需要全新空军链 → 远高于任何地面 doctrine"""
        game, enemy_tags, library = self._setup()
        _, chosen_cost, all_costs = pick_best_persistent(game, enemy_tags, library, "protoss")
        # 至少贵 1000（其实贵 7000+）
        assert all_costs["persistent_skytoss"] - chosen_cost > 1000


# =========================================================================
# pick_best_persistent: 选择器边界
# =========================================================================


class TestPickBestPersistent:
    def test_returns_min_cost(self) -> None:
        cheap = _make_doctrine("cheap", target_composition={"Stalker": 1})
        pricey = _make_doctrine("pricey", target_composition={"Carrier": 50})
        races = {"cheap": "protoss", "pricey": "protoss"}
        lib = StrategyLibrary(persistents=[cheap, pricey], races=races)
        chosen, cost, all_costs = pick_best_persistent(GameSnapshot(), set(), lib, "protoss")
        assert chosen == "cheap"
        assert cost == all_costs["cheap"]
        assert all_costs["pricey"] > cost

    def test_filters_by_race(self) -> None:
        """zerg 的 doctrine 不会被推荐给 protoss 玩家"""
        zerg_d = _make_doctrine("z", target_composition={"Zergling": 30})
        proto_d = _make_doctrine("p", target_composition={"Stalker": 10})
        races = {"z": "zerg", "p": "protoss"}
        lib = StrategyLibrary(persistents=[zerg_d, proto_d], races=races)
        chosen, _, all_costs = pick_best_persistent(GameSnapshot(), set(), lib, "protoss")
        assert chosen == "p"
        assert "z" not in all_costs

    def test_raises_when_no_doctrine_for_race(self) -> None:
        lib = StrategyLibrary(persistents=[], races={})
        with pytest.raises(ValueError, match="No persistent doctrine"):
            pick_best_persistent(GameSnapshot(), set(), lib, "protoss")

    def test_never_returns_none(self) -> None:
        """Q4 不变量准备：只要有 doctrine，就有答案"""
        d = _make_doctrine("a", target_composition={"Stalker": 10})
        lib = StrategyLibrary(persistents=[d], races={"a": "protoss"})
        chosen, _, _ = pick_best_persistent(GameSnapshot(), set(), lib, "protoss")
        assert chosen is not None
        assert chosen == "a"


# =========================================================================
# estimate_gas_demand
# =========================================================================


class TestEstimateGasDemand:
    def test_intensity_mapping(self) -> None:
        low = _make_doctrine("low", gas_intensity="low")
        med = _make_doctrine("med", gas_intensity="medium")
        high = _make_doctrine("high", gas_intensity="high")
        assert estimate_gas_demand(low) < estimate_gas_demand(med)
        assert estimate_gas_demand(med) < estimate_gas_demand(high)
