"""敌方兵种组合 tag canonical 集 + 推断函数。

P0 Step 2：transition_cost 公式（src/vibecraft/strategy/transition_cost.py）的 counter / weak
打分依据。yaml 的 `counters_against` / `weak_against` 字段只能用这里列出的 tag。

侦察数据（enemy_summary: dict[unit_type, count]）经 compute_enemy_composition_tags()
推断出零或多个 tag，与每个 persistent doctrine 的 counters_against / weak_against 做集合
交集，影响 transition_cost。

依赖：
- vibecraft.strategy.unit_data 的 UNIT_COSTS（拿单位 supply）
- 不依赖 sc2 SDK（unit tests 可纯 mock）
"""

from __future__ import annotations

from vibecraft.strategy.unit_data import get_unit_cost

# =========================================================================
# 53 canonical tag（design doc §3.4 完整集）
# =========================================================================

ENEMY_COMPOSITION_TAGS: frozenset[str] = frozenset(
    {
        # --- 虫族 ---
        "zerg_ling_bane",
        "zerg_roach_hydra",
        "zerg_mutalisk",
        "zerg_lurker",
        "zerg_brood",
        "zerg_corruptor",
        "zerg_ultra",
        "zerg_swarm_host",
        "zerg_ultra_brood",
        # --- 神族 ---
        "protoss_skytoss",
        "protoss_chargelot",
        "protoss_blink",
        "protoss_dt",
        "protoss_phoenix",
        "protoss_storm",
        "protoss_disruptor",
        "protoss_ground_mech",
        "protoss_ground_no_storm",
        "protoss_no_charge",
        "protoss_no_phoenix",
        "protoss_phoenix_storm",
        "protoss_mothership_carrier",
        # --- 人族 ---
        "terran_bio",
        "terran_bio_no_stim",
        "terran_bio_no_thor",
        "terran_bio_no_widow",
        "terran_mech",
        "terran_mech_tank",
        "terran_sky",
        "terran_marine_widow",
        "terran_thor_marine_medivac",
        "terran_ghost",
        "terran_no_detection",
        # --- 通用（race-agnostic）---
        "mass_air",
        "mass_ground",
        "mass_light",
        "mass_armored",
        "mass_massive",
        "mass_voidray",
        "mass_corruptor",
        "mass_viking",
        "mass_marauder",
        "mass_observer",
        "mass_overseer",
        "worker_harass",
        "no_detection_enemy",
        "mobile_army",
        # --- 否定 tag（用于其它 doctrine 的 weak_against / counter context）---
        "zerg_no_bane",
        "zerg_no_widow",
        "protoss_no_storm",
    }
)


# =========================================================================
# 推断辅助常量
# =========================================================================

# 空军单位（按 race 分组）
_AIR_UNITS: frozenset[str] = frozenset(
    {
        # Protoss
        "Phoenix", "Oracle", "VoidRay", "Carrier", "Tempest",
        "Mothership", "WarpPrism",
        # Zerg
        "Mutalisk", "Corruptor", "BroodLord", "Viper", "Overseer", "Overlord",
        # Terran
        "Viking", "Medivac", "Liberator", "Raven", "Banshee", "Battlecruiser",
    }
)

# 隐形侦测单位（用于 no_detection 判定）
_DETECTOR_UNITS: frozenset[str] = frozenset(
    {
        "Observer", "Overseer", "Raven", "PhotonCannon", "SporeCrawler",
        "MissileTurret", "OracleStasisTrap",
    }
)

# 工人骚扰单位（出现在我方矿区附近）
_HARASS_UNITS: frozenset[str] = frozenset(
    {
        "Phoenix", "Oracle", "Banshee", "Reaper", "Hellion", "Mutalisk",
        "DarkTemplar", "Adept", "WidowMine",
    }
)


def _supply_of_units(enemy_summary: dict[str, int], unit_filter: frozenset[str]) -> int:
    """累计某 filter 集合内所有单位的 supply 总和（未知单位 fallback 到 DEFAULT_UNIT_COST.supply=2）。"""
    total = 0
    for unit_type, count in enemy_summary.items():
        if unit_type in unit_filter:
            cost = get_unit_cost(unit_type)
            total += count * cost.supply
    return total


# =========================================================================
# 主推断函数
# =========================================================================


def compute_enemy_composition_tags(
    enemy_summary: dict[str, int],
    enemy_race: str | None = None,
    enemy_upgrades: set[str] | None = None,
) -> set[str]:
    """从侦察数据推断敌方组合 tag。

    Args:
        enemy_summary: {unit_type: count}，已观察到的敌方单位数（含 in-fog 估算）
        enemy_race: 'protoss' / 'zerg' / 'terran' / None（未知）；
                    为 None 时跳过 race-specific tag
        enemy_upgrades: 已观察到的升级 id 集合（如 {"Stimpack", "Charge", "Blink"}）；
                        部分 tag 依赖此判定（如 protoss_chargelot 要看 Charge upgrade）

    Returns:
        匹配的 tag 集合（可能为空集；多 tag 可同时成立）
    """
    tags: set[str] = set()
    enemy_upgrades = enemy_upgrades or set()
    race = (enemy_race or "").lower()

    # ---- 通用 supply 阈值 ----
    air_supply = _supply_of_units(enemy_summary, _AIR_UNITS)
    # ground supply = 总战斗单位 supply - air_supply（粗算，排除 worker）
    worker_units = {"Probe", "Drone", "SCV", "MULE"}
    ground_combat_supply = 0
    for unit_type, count in enemy_summary.items():
        if unit_type in worker_units or unit_type in _AIR_UNITS:
            continue
        cost = get_unit_cost(unit_type)
        ground_combat_supply += count * cost.supply

    if air_supply >= 50:
        tags.add("mass_air")
    if ground_combat_supply >= 50:
        tags.add("mass_ground")

    # 高频特定单位 mass_*
    if enemy_summary.get("VoidRay", 0) >= 6:
        tags.add("mass_voidray")
    if enemy_summary.get("Corruptor", 0) >= 8:
        tags.add("mass_corruptor")
    if enemy_summary.get("Viking", 0) >= 8:
        tags.add("mass_viking")
    if enemy_summary.get("Marauder", 0) >= 15:
        tags.add("mass_marauder")
    if enemy_summary.get("Observer", 0) >= 2:
        tags.add("mass_observer")
    if enemy_summary.get("Overseer", 0) >= 2:
        tags.add("mass_overseer")

    # 反隐
    has_detection = any(
        enemy_summary.get(unit, 0) > 0 for unit in _DETECTOR_UNITS
    )
    if not has_detection:
        tags.add("no_detection_enemy")
        if race == "terran":
            tags.add("terran_no_detection")

    # 工人骚扰：有任何 harass-类单位出现就标 worker_harass
    if any(enemy_summary.get(u, 0) > 0 for u in _HARASS_UNITS):
        tags.add("worker_harass")

    # ---- 种族特化 ----
    if race == "zerg":
        if (
            enemy_summary.get("Zergling", 0) >= 30
            and enemy_summary.get("Baneling", 0) >= 5
        ):
            tags.add("zerg_ling_bane")
        if enemy_summary.get("Baneling", 0) < 3:
            tags.add("zerg_no_bane")
        if (
            enemy_summary.get("Roach", 0) >= 15
            or enemy_summary.get("Hydralisk", 0) >= 10
        ):
            tags.add("zerg_roach_hydra")
        if enemy_summary.get("Mutalisk", 0) >= 8:
            tags.add("zerg_mutalisk")
        if enemy_summary.get("Lurker", 0) >= 3:
            tags.add("zerg_lurker")
        if enemy_summary.get("BroodLord", 0) >= 4:
            tags.add("zerg_brood")
        if enemy_summary.get("Corruptor", 0) >= 6:
            tags.add("zerg_corruptor")
        if enemy_summary.get("Ultralisk", 0) >= 4:
            tags.add("zerg_ultra")
        if enemy_summary.get("SwarmHost", 0) >= 4:
            tags.add("zerg_swarm_host")
        # 复合
        if enemy_summary.get("Ultralisk", 0) >= 3 and enemy_summary.get("BroodLord", 0) >= 3:
            tags.add("zerg_ultra_brood")

    elif race == "protoss":
        if enemy_summary.get("Carrier", 0) >= 3 or enemy_summary.get("Mothership", 0) >= 1:
            tags.add("protoss_skytoss")
        if enemy_summary.get("Carrier", 0) >= 3 and enemy_summary.get("Mothership", 0) >= 1:
            tags.add("protoss_mothership_carrier")
        if enemy_summary.get("Zealot", 0) >= 12 and "Charge" in enemy_upgrades:
            tags.add("protoss_chargelot")
        if "Charge" not in enemy_upgrades:
            tags.add("protoss_no_charge")
        if enemy_summary.get("Stalker", 0) >= 10 and "Blink" in enemy_upgrades:
            tags.add("protoss_blink")
        if enemy_summary.get("DarkTemplar", 0) >= 3:
            tags.add("protoss_dt")
        if enemy_summary.get("Phoenix", 0) >= 4:
            tags.add("protoss_phoenix")
        elif enemy_summary.get("Phoenix", 0) == 0:
            tags.add("protoss_no_phoenix")
        if "PsiStorm" in enemy_upgrades or enemy_summary.get("HighTemplar", 0) >= 3:
            tags.add("protoss_storm")
        else:
            tags.add("protoss_no_storm")
        if enemy_summary.get("Disruptor", 0) >= 2:
            tags.add("protoss_disruptor")
        if (
            enemy_summary.get("Immortal", 0) >= 4
            and enemy_summary.get("Colossus", 0) >= 2
        ):
            tags.add("protoss_ground_mech")
        # 复合：地面无 storm
        if (
            enemy_summary.get("Zealot", 0) >= 8
            and "PsiStorm" not in enemy_upgrades
        ):
            tags.add("protoss_ground_no_storm")
        if enemy_summary.get("Phoenix", 0) >= 4 and "PsiStorm" in enemy_upgrades:
            tags.add("protoss_phoenix_storm")

    elif race == "terran":
        marines = enemy_summary.get("Marine", 0)
        medivacs = enemy_summary.get("Medivac", 0)
        if marines >= 20 and medivacs >= 1:
            tags.add("terran_bio")
            if "Stimpack" not in enemy_upgrades:
                tags.add("terran_bio_no_stim")
            if enemy_summary.get("Thor", 0) < 2:
                tags.add("terran_bio_no_thor")
            if enemy_summary.get("WidowMine", 0) < 3:
                tags.add("terran_bio_no_widow")
        if enemy_summary.get("SiegeTank", 0) >= 6 or enemy_summary.get("Thor", 0) >= 4:
            tags.add("terran_mech")
            if enemy_summary.get("SiegeTank", 0) >= 6:
                tags.add("terran_mech_tank")
        if (
            enemy_summary.get("Battlecruiser", 0) >= 4
            or enemy_summary.get("Banshee", 0) >= 6
        ):
            tags.add("terran_sky")
        if marines >= 15 and enemy_summary.get("WidowMine", 0) >= 5:
            tags.add("terran_marine_widow")
        if (
            enemy_summary.get("Thor", 0) >= 4
            and marines >= 20
            and medivacs >= 2
        ):
            tags.add("terran_thor_marine_medivac")
        if enemy_summary.get("Ghost", 0) >= 3:
            tags.add("terran_ghost")

    return tags
