"""SC2 单位 / 建筑 / 科技 成本和元数据表。

P0 Step 2：transition_cost 公式（src/vibecraft/strategy/transition_cost.py）的数据底座。
所有数值取自 SC2 Legacy of the Void 平衡（Liquipedia 权威）。

只覆盖 18 个 persistent doctrine + 8 个 protoss opening 实际用到的单位/建筑/升级；
缺失项 fallback 到 DEFAULT_UNIT_COST，便于在 doctrine 加新单位时无需立刻补 table。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnitCost:
    """单位/建筑/科技的基础成本属性。

    - mineral / gas: 造价
    - build_time: 造时（游戏内秒，正常速度）；对升级是 research_time
    - supply: 仅 unit 用（建筑/科技为 0）
    """

    mineral: int
    gas: int
    build_time: float
    supply: int = 0


# fallback：未知 unit/struct/tech 用此值（warning log），避免 KeyError 中断
DEFAULT_UNIT_COST = UnitCost(mineral=100, gas=50, build_time=30, supply=2)


# =========================================================================
# 建筑（STRUCT_COSTS）—— key 用 sc2 SDK 的 UnitTypeId 名（大小写敏感字符串）
# =========================================================================

STRUCT_COSTS: dict[str, UnitCost] = {
    # --- Protoss ---
    "Nexus": UnitCost(400, 0, 71),
    "Pylon": UnitCost(100, 0, 18),
    "Assimilator": UnitCost(75, 0, 21),
    "Gateway": UnitCost(150, 0, 46),
    "WarpGate": UnitCost(0, 0, 7),  # transform 不算 resource
    "Forge": UnitCost(150, 0, 32),
    "CyberneticsCore": UnitCost(150, 0, 36),
    "PhotonCannon": UnitCost(150, 0, 29),
    "ShieldBattery": UnitCost(100, 0, 29),
    "RoboticsFacility": UnitCost(200, 100, 46),
    "Stargate": UnitCost(150, 150, 43),
    "TwilightCouncil": UnitCost(150, 100, 36),
    "TemplarArchives": UnitCost(150, 200, 36),
    "DarkShrine": UnitCost(150, 150, 71),
    "FleetBeacon": UnitCost(300, 200, 43),
    "RoboticsBay": UnitCost(200, 200, 46),
    # --- Zerg ---
    "Hatchery": UnitCost(300, 0, 71),
    "Lair": UnitCost(150, 100, 57),  # 从 Hatchery morph
    "Hive": UnitCost(200, 150, 71),  # 从 Lair morph
    "Extractor": UnitCost(25, 0, 21),
    "SpawningPool": UnitCost(200, 0, 46),
    "EvolutionChamber": UnitCost(75, 0, 25),
    "RoachWarren": UnitCost(150, 0, 39),
    "BanelingNest": UnitCost(100, 50, 43),
    "SpineCrawler": UnitCost(100, 0, 36),
    "SporeCrawler": UnitCost(75, 0, 21),
    "HydraliskDen": UnitCost(100, 100, 29),
    "LurkerDen": UnitCost(150, 150, 86),
    "InfestationPit": UnitCost(100, 100, 36),
    "Spire": UnitCost(200, 200, 71),
    "GreaterSpire": UnitCost(100, 150, 71),  # 从 Spire morph
    "NydusNetwork": UnitCost(150, 150, 36),
    "UltraliskCavern": UnitCost(150, 200, 46),
    # --- Terran ---
    "CommandCenter": UnitCost(400, 0, 71),
    "OrbitalCommand": UnitCost(150, 0, 25),  # 从 CC upgrade
    "PlanetaryFortress": UnitCost(150, 150, 36),  # 从 CC upgrade
    "SupplyDepot": UnitCost(100, 0, 21),
    "Refinery": UnitCost(75, 0, 21),
    "Barracks": UnitCost(150, 0, 46),
    "EngineeringBay": UnitCost(125, 0, 25),
    "Bunker": UnitCost(100, 0, 29),
    "MissileTurret": UnitCost(100, 0, 18),
    "SensorTower": UnitCost(125, 100, 18),
    "Factory": UnitCost(150, 100, 43),
    "Starport": UnitCost(150, 100, 36),
    "Armory": UnitCost(150, 100, 46),
    "GhostAcademy": UnitCost(150, 50, 29),
    "FusionCore": UnitCost(150, 150, 46),
}


# =========================================================================
# 单位（UNIT_COSTS）—— key 同 sc2 SDK UnitTypeId 名
# =========================================================================

UNIT_COSTS: dict[str, UnitCost] = {
    # --- Protoss ---
    "Probe": UnitCost(50, 0, 12, 1),
    "Zealot": UnitCost(100, 0, 27, 2),
    "Stalker": UnitCost(125, 50, 27, 2),
    "Sentry": UnitCost(50, 100, 27, 2),
    "Adept": UnitCost(100, 25, 27, 2),
    "HighTemplar": UnitCost(50, 150, 39, 2),
    "DarkTemplar": UnitCost(125, 125, 39, 2),
    "Archon": UnitCost(0, 0, 9, 4),  # 2 HT/DT 合
    "Immortal": UnitCost(275, 100, 39, 4),
    "Colossus": UnitCost(300, 200, 54, 6),
    "Disruptor": UnitCost(150, 150, 36, 3),
    "Observer": UnitCost(25, 75, 21, 1),
    "WarpPrism": UnitCost(200, 0, 36, 2),
    "Phoenix": UnitCost(150, 100, 25, 2),
    "VoidRay": UnitCost(250, 150, 43, 4),
    "Oracle": UnitCost(150, 150, 37, 3),
    "Carrier": UnitCost(350, 250, 64, 6),
    "Tempest": UnitCost(250, 175, 43, 5),
    "Mothership": UnitCost(400, 400, 114, 8),
    # --- Zerg (基于 Larva morph) ---
    "Drone": UnitCost(50, 0, 12, 1),
    "Overlord": UnitCost(100, 0, 18, 0),  # 0 supply（自己是 supply provider）
    "Queen": UnitCost(150, 0, 36, 2),
    "Zergling": UnitCost(25, 0, 17, 1),  # 1 egg = 2 ling，但单只计算
    "Baneling": UnitCost(25, 25, 14, 0),  # 从 Ling morph，不占 supply（继承）
    "Roach": UnitCost(75, 25, 19, 2),
    "Ravager": UnitCost(25, 75, 9, 1),  # 从 Roach morph
    "Hydralisk": UnitCost(100, 50, 24, 2),
    "Lurker": UnitCost(50, 100, 18, 1),  # 从 Hydra morph
    "Mutalisk": UnitCost(100, 100, 24, 2),
    "Corruptor": UnitCost(150, 100, 29, 2),
    "BroodLord": UnitCost(150, 150, 24, 2),  # 从 Corruptor morph
    "Viper": UnitCost(100, 200, 29, 3),
    "Infestor": UnitCost(100, 150, 36, 2),
    "SwarmHost": UnitCost(100, 75, 29, 3),
    "Ultralisk": UnitCost(300, 200, 39, 6),
    "Overseer": UnitCost(50, 50, 12, 0),  # 从 Overlord morph
    # --- Terran ---
    "SCV": UnitCost(50, 0, 12, 1),
    "Marine": UnitCost(50, 0, 18, 1),
    "Marauder": UnitCost(100, 25, 21, 2),
    "Reaper": UnitCost(50, 50, 32, 1),
    "Ghost": UnitCost(150, 125, 29, 2),
    "Hellion": UnitCost(100, 0, 21, 2),
    "Hellbat": UnitCost(100, 0, 21, 2),  # 从 Hellion morph
    "WidowMine": UnitCost(75, 25, 21, 2),
    "Cyclone": UnitCost(150, 100, 32, 3),
    "SiegeTank": UnitCost(150, 125, 32, 3),
    "Thor": UnitCost(300, 200, 43, 6),
    "Viking": UnitCost(150, 75, 30, 2),
    "Medivac": UnitCost(100, 100, 30, 2),
    "Liberator": UnitCost(150, 150, 43, 3),
    "Raven": UnitCost(100, 200, 43, 2),
    "Banshee": UnitCost(150, 100, 43, 3),
    "Battlecruiser": UnitCost(400, 300, 64, 6),
}


# =========================================================================
# 升级（TECH_COSTS）—— key 同 sc2 SDK UpgradeId 名
# =========================================================================

TECH_COSTS: dict[str, UnitCost] = {
    # --- Protoss ---
    "WarpGateResearch": UnitCost(50, 50, 100),
    "Charge": UnitCost(100, 100, 100),
    "Blink": UnitCost(150, 150, 121),
    "ResonatingGlaives": UnitCost(100, 100, 100),  # Adept 攻速
    "PsiStorm": UnitCost(200, 200, 79),
    "GravitonCatapult": UnitCost(150, 150, 64),  # 航母弹射
    "ExtendedThermalLance": UnitCost(150, 150, 100),  # 巨像射程
    "PhoenixRangeUpgrade": UnitCost(150, 150, 64),  # 凤凰射程
    "ProtossGroundWeaponsLevel1": UnitCost(100, 100, 129),
    "ProtossGroundWeaponsLevel2": UnitCost(150, 150, 154),
    "ProtossGroundWeaponsLevel3": UnitCost(200, 200, 179),
    "ProtossGroundArmorsLevel1": UnitCost(100, 100, 129),
    "ProtossGroundArmorsLevel2": UnitCost(150, 150, 154),
    "ProtossGroundArmorsLevel3": UnitCost(200, 200, 179),
    "ProtossShieldsLevel1": UnitCost(150, 150, 129),
    "ProtossShieldsLevel2": UnitCost(225, 225, 154),
    "ProtossShieldsLevel3": UnitCost(300, 300, 179),
    "ProtossAirWeaponsLevel1": UnitCost(100, 100, 129),
    "ProtossAirWeaponsLevel2": UnitCost(175, 175, 154),
    "ProtossAirWeaponsLevel3": UnitCost(250, 250, 179),
    "ProtossAirArmorsLevel1": UnitCost(150, 150, 129),
    "ProtossAirArmorsLevel2": UnitCost(225, 225, 154),
    "ProtossAirArmorsLevel3": UnitCost(300, 300, 179),
    # --- Zerg ---
    "MetabolicBoost": UnitCost(100, 100, 79),  # Zergling speed
    "AdrenalGlands": UnitCost(200, 200, 93),  # Zergling attack speed
    "CentrificalHooks": UnitCost(150, 150, 79),  # Baneling speed
    "GlialReconstitution": UnitCost(100, 100, 79),  # Roach speed
    "GroovedSpines": UnitCost(100, 100, 71),  # Hydra range
    "MuscularAugments": UnitCost(100, 100, 71),  # Hydra speed
    "Burrow": UnitCost(100, 100, 71),
    "TunnelingClaws": UnitCost(150, 150, 79),  # Roach 移动钻地
    "ChitinousPlating": UnitCost(150, 150, 79),  # Ultra armor
    "AnabolicSynthesis": UnitCost(150, 150, 43),  # Ultra speed
    "PathogenGlands": UnitCost(150, 150, 57),  # Infestor 能量
    "ZergMeleeWeaponsLevel1": UnitCost(100, 100, 114),
    "ZergMissileWeaponsLevel1": UnitCost(100, 100, 114),
    "ZergGroundArmorsLevel1": UnitCost(150, 150, 114),
    "ZergFlyerWeaponsLevel1": UnitCost(100, 100, 114),
    "ZergFlyerArmorsLevel1": UnitCost(150, 150, 114),
    # --- Terran ---
    "Stimpack": UnitCost(100, 100, 121),
    "CombatShield": UnitCost(100, 100, 79),
    "ConcussiveShells": UnitCost(50, 50, 43),
    "InfernalPreigniter": UnitCost(150, 150, 79),  # Hellion 蓝焰
    "DrillingClaws": UnitCost(75, 75, 79),  # WidowMine
    "HighCapacityFuelTanks": UnitCost(100, 100, 79),  # Medivac speed
    "RavenCorvidReactor": UnitCost(150, 150, 79),
    "PersonalCloaking": UnitCost(150, 150, 121),  # Ghost cloak
    "BansheeCloak": UnitCost(100, 100, 121),
    "WeaponRefit": UnitCost(150, 150, 43),  # BC YamatoBoost
    "TerranInfantryWeaponsLevel1": UnitCost(100, 100, 114),
    "TerranInfantryArmorsLevel1": UnitCost(100, 100, 114),
    "TerranShipWeaponsLevel1": UnitCost(100, 100, 114),
}


# =========================================================================
# 建筑前置链（STRUCT_PREREQS）—— 算成本时把缺的前置也算进去
# 例：Carrier 需要 Stargate + FleetBeacon，FleetBeacon 需要 Stargate
# 仅记直接前置，递归展开由 transition_cost 处理
# =========================================================================

STRUCT_PREREQS: dict[str, list[str]] = {
    # Protoss
    "Gateway": ["Nexus"],
    "Forge": ["Nexus"],
    "Assimilator": ["Nexus"],
    "CyberneticsCore": ["Gateway"],
    "PhotonCannon": ["Forge"],
    "ShieldBattery": ["CyberneticsCore"],
    "RoboticsFacility": ["CyberneticsCore"],
    "Stargate": ["CyberneticsCore"],
    "TwilightCouncil": ["CyberneticsCore"],
    "TemplarArchives": ["TwilightCouncil"],
    "DarkShrine": ["TwilightCouncil"],
    "FleetBeacon": ["Stargate"],
    "RoboticsBay": ["RoboticsFacility"],
    # Zerg
    "Extractor": ["Hatchery"],
    "SpawningPool": ["Hatchery"],
    "RoachWarren": ["SpawningPool"],
    "EvolutionChamber": ["Hatchery"],
    "BanelingNest": ["SpawningPool"],
    "SpineCrawler": ["SpawningPool"],
    "SporeCrawler": ["EvolutionChamber"],
    "HydraliskDen": ["Lair"],
    "LurkerDen": ["HydraliskDen"],
    "InfestationPit": ["Lair"],
    "Spire": ["Lair"],
    "GreaterSpire": ["Hive", "Spire"],
    "NydusNetwork": ["Lair"],
    "UltraliskCavern": ["Hive"],
    # Terran
    "Refinery": ["CommandCenter"],
    "Barracks": ["SupplyDepot"],
    "EngineeringBay": ["CommandCenter"],
    "Bunker": ["Barracks"],
    "MissileTurret": ["EngineeringBay"],
    "SensorTower": ["EngineeringBay"],
    "Factory": ["Barracks"],
    "Starport": ["Factory"],
    "Armory": ["Factory"],
    "GhostAcademy": ["Barracks"],
    "FusionCore": ["Starport"],
}


# =========================================================================
# 查询 helpers（默认 fallback 避免 KeyError）
# =========================================================================


def get_struct_cost(name: str) -> UnitCost:
    return STRUCT_COSTS.get(name, DEFAULT_UNIT_COST)


def get_unit_cost(name: str) -> UnitCost:
    return UNIT_COSTS.get(name, DEFAULT_UNIT_COST)


def get_tech_cost(name: str) -> UnitCost:
    return TECH_COSTS.get(name, DEFAULT_UNIT_COST)


def get_struct_prereqs(name: str) -> list[str]:
    """返回直接前置；空 list 表示无前置（Nexus / Hatchery / CommandCenter）。"""
    return STRUCT_PREREQS.get(name, [])


def transitive_prereqs(name: str) -> list[str]:
    """递归展开前置链（去重，保持顺序）。

    例：transitive_prereqs("Carrier") → ["Stargate", "CyberneticsCore", "Gateway", "Nexus", "FleetBeacon"]
    （Carrier 单位需要 Stargate + FleetBeacon；FleetBeacon 需要 Stargate；Stargate 需要 CyberneticsCore 等）
    """
    seen: set[str] = set()
    result: list[str] = []

    def _walk(s: str) -> None:
        for prereq in get_struct_prereqs(s):
            if prereq not in seen:
                seen.add(prereq)
                result.append(prereq)
                _walk(prereq)

    _walk(name)
    return result


# =========================================================================
# canonical name 反查：全大写 SDK name → 本表 PascalCase key
# =========================================================================

# Director 从 facade 拿到的 state 是全大写 SDK name（UnitTypeId / UpgradeId.name），
# 要转成本表 key（PascalCase）才能查成本 / 跟 doctrine yaml 的 required_* 对上。
_CANON_BY_UPPER: dict[str, str] = {k.upper(): k for k in (*STRUCT_COSTS, *UNIT_COSTS, *TECH_COSTS)}
# UpgradeId.name 跟 TECH_COSTS key 不是纯大小写差的特例（SDK 带 TECH 后缀）
_CANON_BY_UPPER["BLINKTECH"] = "Blink"
_CANON_BY_UPPER["PSISTORMTECH"] = "PsiStorm"
_CANON_BY_UPPER["WARPGATERESEARCH"] = "WarpGateResearch"


def canonical_name(raw: str) -> str:
    """全大写 SDK name → 成本表 canonical key（PascalCase）。查不到原样返回。"""
    return _CANON_BY_UPPER.get(raw.upper(), raw)
