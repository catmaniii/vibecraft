"""2026-05-23 用户:依赖树自动补齐扩到三族 + 升级。

测试 tech_tree.prereq_chain 三族单位/建筑/升级正确。
"""

from __future__ import annotations

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from vibecraft.bot.tech_tree import (
    equivalent_structures,
    prereq_chain,
    required_for,
)


class TestRequiredFor:
    """单层 prereq 查询。"""

    def test_dt_requires_darkshrine(self) -> None:
        assert required_for(UnitTypeId.DARKTEMPLAR, Race.Protoss) == UnitTypeId.DARKSHRINE

    def test_zergling_requires_spawningpool(self) -> None:
        assert required_for(UnitTypeId.ZERGLING, Race.Zerg) == UnitTypeId.SPAWNINGPOOL

    def test_marine_requires_barracks(self) -> None:
        # 2026-05-27 修复:Marine 在 TERRAN_TECH_REQUIREMENT 没列(基础兵无额外
        # 科技要求),但 TRAIN_INFO fallback 找到 BARRACKS 作 producer = prereq。
        # 旧 assert None 反映 sc2.constants 的 gap,新 assert BARRACKS 更准确。
        # 实战:玩家"出 Marine" 时通常已有 Barracks → ready check pass;
        # 没 Barracks 时 auto_prereq 正确补 Barracks(老行为是静默失败)。
        assert required_for(UnitTypeId.MARINE, Race.Terran) == UnitTypeId.BARRACKS

    def test_blink_requires_twilight(self) -> None:
        # Blink upgrade → researched from TwilightCouncil
        assert required_for(UpgradeId.BLINKTECH, Race.Protoss) == UnitTypeId.TWILIGHTCOUNCIL

    def test_glial_requires_roachwarren(self) -> None:
        # 蟑螂速 → researched from RoachWarren(注:实际游戏还要 Lair,这里只直接前置)
        assert required_for(UpgradeId.GLIALRECONSTITUTION, Race.Zerg) == UnitTypeId.ROACHWARREN

    def test_stim_requires_techlab(self) -> None:
        assert required_for(UpgradeId.STIMPACK, Race.Terran) == UnitTypeId.BARRACKSTECHLAB

    def test_zealot_requires_gateway(self) -> None:
        # 2026-05-27 修复:同 Marine,Zealot 在 PROTOSS_TECH_REQUIREMENT 没列
        # (基础兵),TRAIN_INFO fallback 找到 GATEWAY。
        assert required_for(UnitTypeId.ZEALOT, Race.Protoss) == UnitTypeId.GATEWAY

    def test_probe_no_prereq(self) -> None:
        assert required_for(UnitTypeId.PROBE, Race.Protoss) is None

    def test_scv_no_prereq(self) -> None:
        """SCV / DRONE 也走 worker 短路,即使 TRAIN_INFO 含 OrbitalCommand 等
        morph 形态(非 base structure),也不应被当 prereq。"""
        assert required_for(UnitTypeId.SCV, Race.Terran) is None
        assert required_for(UnitTypeId.DRONE, Race.Zerg) is None

    def test_observer_requires_robotics(self) -> None:
        """2026-05-27 Issue B 修复:OBSERVER 在 PROTOSS_TECH_REQUIREMENT 里没列
        (sc2 只记额外科技要求,不记 producer),TRAIN_INFO fallback 找到
        ROBOTICSFACILITY 作 prereq。修前 returns None → train 静默失败 →
        玩家"出 OB" 无反应,auto_prereq 也不触发补 VR。"""
        assert required_for(UnitTypeId.OBSERVER, Race.Protoss) == UnitTypeId.ROBOTICSFACILITY

    def test_immortal_requires_robotics(self) -> None:
        assert required_for(UnitTypeId.IMMORTAL, Race.Protoss) == UnitTypeId.ROBOTICSFACILITY

    def test_warpprism_requires_robotics(self) -> None:
        assert required_for(UnitTypeId.WARPPRISM, Race.Protoss) == UnitTypeId.ROBOTICSFACILITY


class TestPrereqChain:
    """完整 chain 递归展开。"""

    def test_dt_full_chain(self) -> None:
        """DARKTEMPLAR → Gateway → CyberneticsCore → TwilightCouncil → DarkShrine。"""
        chain = prereq_chain(UnitTypeId.DARKTEMPLAR, Race.Protoss)
        assert chain == [
            UnitTypeId.GATEWAY,
            UnitTypeId.CYBERNETICSCORE,
            UnitTypeId.TWILIGHTCOUNCIL,
            UnitTypeId.DARKSHRINE,
        ]

    def test_zergling_chain(self) -> None:
        """ZERGLING → SpawningPool(Hatchery 是基础建筑过滤掉)。"""
        chain = prereq_chain(UnitTypeId.ZERGLING, Race.Zerg)
        assert chain == [UnitTypeId.SPAWNINGPOOL]

    def test_baneling_chain(self) -> None:
        """BANELING → SpawningPool → BanelingNest。"""
        chain = prereq_chain(UnitTypeId.BANELING, Race.Zerg)
        assert chain == [
            UnitTypeId.SPAWNINGPOOL,
            UnitTypeId.BANELINGNEST,
        ]

    def test_mutalisk_chain(self) -> None:
        """MUTALISK → Pool → Lair → Spire。"""
        chain = prereq_chain(UnitTypeId.MUTALISK, Race.Zerg)
        assert chain == [
            UnitTypeId.SPAWNINGPOOL,
            UnitTypeId.LAIR,
            UnitTypeId.SPIRE,
        ]

    def test_broodlord_chain(self) -> None:
        """BROODLORD → Pool → Lair → InfestationPit → Hive → GreaterSpire。
        (BL morph 自 Corruptor,链上不显式 — corruptor 也走 SPIRE 同枝。)
        """
        chain = prereq_chain(UnitTypeId.BROODLORD, Race.Zerg)
        # GreaterSpire 需要 Hive,Hive 需要 InfestationPit,Pit 需要 Lair
        assert chain[-1] == UnitTypeId.GREATERSPIRE
        assert UnitTypeId.HIVE in chain
        assert UnitTypeId.INFESTATIONPIT in chain
        assert UnitTypeId.LAIR in chain

    def test_thor_chain(self) -> None:
        """THOR → Barracks → Factory → Armory(SupplyDepot 过滤掉)。"""
        chain = prereq_chain(UnitTypeId.THOR, Race.Terran)
        assert chain == [
            UnitTypeId.BARRACKS,
            UnitTypeId.FACTORY,
            UnitTypeId.ARMORY,
        ]

    def test_battlecruiser_chain(self) -> None:
        """BC → Barracks → Factory → Starport → FusionCore。"""
        chain = prereq_chain(UnitTypeId.BATTLECRUISER, Race.Terran)
        assert chain == [
            UnitTypeId.BARRACKS,
            UnitTypeId.FACTORY,
            UnitTypeId.STARPORT,
            UnitTypeId.FUSIONCORE,
        ]

    def test_blink_chain(self) -> None:
        """BLINK(upgrade)→ Gateway → CyberneticsCore → TwilightCouncil。"""
        chain = prereq_chain(UpgradeId.BLINKTECH, Race.Protoss)
        assert chain == [
            UnitTypeId.GATEWAY,
            UnitTypeId.CYBERNETICSCORE,
            UnitTypeId.TWILIGHTCOUNCIL,
        ]

    def test_zergling_speed_chain(self) -> None:
        """ZERGLINGMOVEMENTSPEED → SpawningPool。"""
        chain = prereq_chain(UpgradeId.ZERGLINGMOVEMENTSPEED, Race.Zerg)
        assert chain == [UnitTypeId.SPAWNINGPOOL]

    def test_marine_chain_barracks(self) -> None:
        """2026-05-27 修复:Marine → Barracks(TRAIN_INFO fallback)。
        旧测试 assert [] 反映了 PROTOSS_TECH_REQUIREMENT 的 gap。"""
        assert prereq_chain(UnitTypeId.MARINE, Race.Terran) == [UnitTypeId.BARRACKS]

    def test_zealot_chain_gateway(self) -> None:
        assert prereq_chain(UnitTypeId.ZEALOT, Race.Protoss) == [UnitTypeId.GATEWAY]

    def test_observer_full_chain(self) -> None:
        """2026-05-27 Issue B regression:OBSERVER → Gateway → CyberCore → Robotics。
        修前 chain 为空(required_for=None),auto_prereq 不补 VR → 玩家"出 OB"无反应。"""
        chain = prereq_chain(UnitTypeId.OBSERVER, Race.Protoss)
        assert chain == [
            UnitTypeId.GATEWAY,
            UnitTypeId.CYBERNETICSCORE,
            UnitTypeId.ROBOTICSFACILITY,
        ]

    def test_immortal_full_chain(self) -> None:
        chain = prereq_chain(UnitTypeId.IMMORTAL, Race.Protoss)
        assert chain == [
            UnitTypeId.GATEWAY,
            UnitTypeId.CYBERNETICSCORE,
            UnitTypeId.ROBOTICSFACILITY,
        ]

    def test_colossus_full_chain(self) -> None:
        """COLOSSUS 在 TECH_REQUIREMENT 已列(=Robo Bay),fallback 不影响。"""
        chain = prereq_chain(UnitTypeId.COLOSSUS, Race.Protoss)
        assert chain == [
            UnitTypeId.GATEWAY,
            UnitTypeId.CYBERNETICSCORE,
            UnitTypeId.ROBOTICSFACILITY,
            UnitTypeId.ROBOTICSBAY,
        ]


class TestEquivalentStructures:
    """morph 等价形态查询。"""

    def test_gateway_warpgate_equivalent(self) -> None:
        equiv = equivalent_structures(UnitTypeId.GATEWAY)
        assert UnitTypeId.GATEWAY in equiv
        assert UnitTypeId.WARPGATE in equiv

    def test_no_equivalent(self) -> None:
        # Forge 没 morph 形态
        equiv = equivalent_structures(UnitTypeId.FORGE)
        assert equiv == {UnitTypeId.FORGE}
