"""unit_data.py 单元测试 —— 成本表 + 前置链查询。"""

from __future__ import annotations

from vibecraft.strategy.unit_data import (
    DEFAULT_UNIT_COST,
    STRUCT_COSTS,
    TECH_COSTS,
    UNIT_COSTS,
    UnitCost,
    get_struct_cost,
    get_struct_prereqs,
    get_unit_cost,
    transitive_prereqs,
)


class TestUnitCost:
    def test_dataclass_frozen(self) -> None:
        c = UnitCost(100, 50, 30)
        assert c.mineral == 100
        assert c.supply == 0  # default

    def test_unit_with_supply(self) -> None:
        c = UnitCost(50, 0, 12, supply=1)
        assert c.supply == 1


class TestStructCosts:
    def test_protoss_basic(self) -> None:
        assert STRUCT_COSTS["Nexus"].mineral == 400
        assert STRUCT_COSTS["Pylon"].mineral == 100
        assert STRUCT_COSTS["Gateway"].mineral == 150

    def test_protoss_advanced(self) -> None:
        # 议会
        assert STRUCT_COSTS["TwilightCouncil"].gas == 100
        # 圣堂档案需要议会前置
        assert STRUCT_COSTS["TemplarArchives"].gas == 200

    def test_zerg_basic(self) -> None:
        assert STRUCT_COSTS["Hatchery"].mineral == 300

    def test_terran_basic(self) -> None:
        assert STRUCT_COSTS["CommandCenter"].mineral == 400
        assert STRUCT_COSTS["Barracks"].mineral == 150

    def test_get_struct_cost_unknown_fallback(self) -> None:
        c = get_struct_cost("NotARealBuilding")
        assert c == DEFAULT_UNIT_COST


class TestUnitCosts:
    def test_protoss_units(self) -> None:
        # 探机
        assert UNIT_COSTS["Probe"].mineral == 50
        assert UNIT_COSTS["Probe"].supply == 1
        # 不朽
        assert UNIT_COSTS["Immortal"].gas == 100
        assert UNIT_COSTS["Immortal"].supply == 4
        # 航母
        assert UNIT_COSTS["Carrier"].mineral == 350
        assert UNIT_COSTS["Carrier"].supply == 6

    def test_zerg_units(self) -> None:
        assert UNIT_COSTS["Zergling"].mineral == 25
        assert UNIT_COSTS["BroodLord"].supply == 2

    def test_terran_units(self) -> None:
        assert UNIT_COSTS["Marine"].mineral == 50
        assert UNIT_COSTS["Battlecruiser"].mineral == 400

    def test_get_unit_cost_unknown_fallback(self) -> None:
        assert get_unit_cost("UnknownUnit") == DEFAULT_UNIT_COST


class TestTechCosts:
    def test_protoss_upgrades(self) -> None:
        assert TECH_COSTS["WarpGateResearch"].mineral == 50
        assert TECH_COSTS["Charge"].mineral == 100
        assert TECH_COSTS["PsiStorm"].mineral == 200

    def test_terran_stim(self) -> None:
        assert TECH_COSTS["Stimpack"].mineral == 100
        assert TECH_COSTS["Stimpack"].build_time == 121

    def test_zerg_meta_boost(self) -> None:
        assert TECH_COSTS["MetabolicBoost"].mineral == 100


class TestStructPrereqs:
    def test_no_prereq(self) -> None:
        assert get_struct_prereqs("Nexus") == []
        assert get_struct_prereqs("Hatchery") == []

    def test_direct_prereq(self) -> None:
        assert "Gateway" in get_struct_prereqs("CyberneticsCore")
        assert "TwilightCouncil" in get_struct_prereqs("TemplarArchives")

    def test_terran_chain(self) -> None:
        assert "Factory" in get_struct_prereqs("Starport")
        assert "Starport" in get_struct_prereqs("FusionCore")

    def test_unknown_returns_empty(self) -> None:
        assert get_struct_prereqs("UnknownBuilding") == []


class TestTransitivePrereqs:
    def test_protoss_carrier_chain(self) -> None:
        """Carrier 自身是 unit，但若用 FleetBeacon 做查询：
        FleetBeacon → Stargate → CyberneticsCore → Gateway → Nexus"""
        chain = transitive_prereqs("FleetBeacon")
        # 顺序: Stargate 在前, Nexus 在末
        assert "Stargate" in chain
        assert "CyberneticsCore" in chain
        assert "Gateway" in chain
        assert "Nexus" in chain

    def test_zerg_lurker_den_chain(self) -> None:
        """LurkerDen → HydraliskDen → Lair (我们没记 Lair→Hatchery)"""
        chain = transitive_prereqs("LurkerDen")
        assert "HydraliskDen" in chain
        assert "Lair" in chain

    def test_terran_fusion_chain(self) -> None:
        chain = transitive_prereqs("FusionCore")
        assert "Starport" in chain
        assert "Factory" in chain
        assert "Barracks" in chain
        assert "SupplyDepot" in chain

    def test_no_prereq_returns_empty(self) -> None:
        assert transitive_prereqs("Nexus") == []
        assert transitive_prereqs("Hatchery") == []

    def test_deduplicates(self) -> None:
        """前置链不应有重复（如 Templar/Dark Shrine 都来自 TwilightCouncil，
        但查 TemplarArchives 时不会双倍计算 Twilight）"""
        chain = transitive_prereqs("TemplarArchives")
        assert len(chain) == len(set(chain))
