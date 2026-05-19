"""enemy_tags.py 单元测试 —— 推断函数 + canonical tag 集校验。"""

from __future__ import annotations

import pytest

from vibecraft.strategy.enemy_tags import (
    ENEMY_COMPOSITION_TAGS,
    compute_enemy_composition_tags,
)

# =========================================================================
# canonical 集 sanity
# =========================================================================


class TestCanonicalTags:
    def test_tags_set_nonempty(self) -> None:
        assert len(ENEMY_COMPOSITION_TAGS) > 40  # design doc 说 53 个

    def test_known_tags_present(self) -> None:
        # 每个种族都有标志性 tag
        assert "zerg_ling_bane" in ENEMY_COMPOSITION_TAGS
        assert "protoss_skytoss" in ENEMY_COMPOSITION_TAGS
        assert "terran_bio" in ENEMY_COMPOSITION_TAGS
        assert "mass_air" in ENEMY_COMPOSITION_TAGS

    def test_tag_naming_convention(self) -> None:
        """所有 tag 应为 snake_case（无大写、无空格、无中文）"""
        for tag in ENEMY_COMPOSITION_TAGS:
            assert tag.islower() or "_" in tag
            assert " " not in tag
            assert tag.isascii()


# =========================================================================
# 推断 —— 通用（race-agnostic）
# =========================================================================


class TestComputeTagsGeneric:
    def test_empty_input_returns_empty(self) -> None:
        tags = compute_enemy_composition_tags({}, enemy_race=None)
        # 空 enemy_summary：没 air / ground / harass / detection（detection 为空 → no_detection_enemy）
        assert "no_detection_enemy" in tags
        # 没 mass_*
        assert "mass_air" not in tags
        assert "mass_ground" not in tags

    def test_mass_air_threshold(self) -> None:
        """50 supply 空军触发 mass_air（Phoenix=2 supply each → 25 phoenix）"""
        tags = compute_enemy_composition_tags({"Phoenix": 25}, enemy_race="protoss")
        assert "mass_air" in tags

    def test_mass_air_below_threshold(self) -> None:
        tags = compute_enemy_composition_tags({"Phoenix": 10}, enemy_race="protoss")
        assert "mass_air" not in tags

    def test_mass_ground_threshold(self) -> None:
        """50 supply 地面战斗单位触发 mass_ground"""
        tags = compute_enemy_composition_tags({"Marine": 50}, enemy_race="terran")
        assert "mass_ground" in tags

    def test_workers_excluded_from_ground(self) -> None:
        """worker (Probe/Drone/SCV) 不算 mass_ground"""
        tags = compute_enemy_composition_tags({"Probe": 80}, enemy_race="protoss")
        assert "mass_ground" not in tags

    def test_mass_corruptor(self) -> None:
        tags = compute_enemy_composition_tags({"Corruptor": 10}, enemy_race="zerg")
        assert "mass_corruptor" in tags

    def test_no_detection_when_no_detectors(self) -> None:
        tags = compute_enemy_composition_tags({"Marine": 10}, enemy_race="terran")
        assert "no_detection_enemy" in tags
        assert "terran_no_detection" in tags

    def test_no_detection_negated_when_raven_present(self) -> None:
        tags = compute_enemy_composition_tags(
            {"Marine": 10, "Raven": 1}, enemy_race="terran"
        )
        assert "no_detection_enemy" not in tags
        assert "terran_no_detection" not in tags

    def test_worker_harass(self) -> None:
        tags = compute_enemy_composition_tags({"Banshee": 2}, enemy_race="terran")
        assert "worker_harass" in tags


# =========================================================================
# 推断 —— 种族特化（虫族）
# =========================================================================


class TestComputeTagsZerg:
    def test_ling_bane(self) -> None:
        tags = compute_enemy_composition_tags(
            {"Zergling": 40, "Baneling": 10}, enemy_race="zerg"
        )
        assert "zerg_ling_bane" in tags

    def test_ling_bane_below_ling_threshold(self) -> None:
        tags = compute_enemy_composition_tags(
            {"Zergling": 20, "Baneling": 10}, enemy_race="zerg"
        )
        assert "zerg_ling_bane" not in tags

    def test_zerg_no_bane_negative_tag(self) -> None:
        """少于 3 baneling → zerg_no_bane"""
        tags = compute_enemy_composition_tags({"Zergling": 30}, enemy_race="zerg")
        assert "zerg_no_bane" in tags

    def test_roach_or_hydra(self) -> None:
        """roach 15+ 或 hydra 10+ 都触发"""
        assert "zerg_roach_hydra" in compute_enemy_composition_tags(
            {"Roach": 20}, enemy_race="zerg"
        )
        assert "zerg_roach_hydra" in compute_enemy_composition_tags(
            {"Hydralisk": 12}, enemy_race="zerg"
        )

    def test_mutalisk(self) -> None:
        tags = compute_enemy_composition_tags({"Mutalisk": 10}, enemy_race="zerg")
        assert "zerg_mutalisk" in tags
        assert "mass_air" not in tags  # 10 muta = 20 supply 不够 mass_air 阈值

    def test_ultra_brood_composite(self) -> None:
        tags = compute_enemy_composition_tags(
            {"Ultralisk": 4, "BroodLord": 4}, enemy_race="zerg"
        )
        assert "zerg_ultra" in tags
        assert "zerg_brood" in tags
        assert "zerg_ultra_brood" in tags

    def test_zerg_race_required_for_specific_tags(self) -> None:
        """没指定 race 时不出 zerg_* tag，即使 enemy_summary 像虫族"""
        tags = compute_enemy_composition_tags(
            {"Zergling": 40, "Baneling": 10}, enemy_race=None
        )
        assert "zerg_ling_bane" not in tags


# =========================================================================
# 推断 —— 种族特化（神族）
# =========================================================================


class TestComputeTagsProtoss:
    def test_skytoss_carrier(self) -> None:
        tags = compute_enemy_composition_tags({"Carrier": 5}, enemy_race="protoss")
        assert "protoss_skytoss" in tags

    def test_skytoss_mothership(self) -> None:
        tags = compute_enemy_composition_tags(
            {"Mothership": 1}, enemy_race="protoss"
        )
        assert "protoss_skytoss" in tags

    def test_mothership_carrier_composite(self) -> None:
        tags = compute_enemy_composition_tags(
            {"Carrier": 5, "Mothership": 1}, enemy_race="protoss"
        )
        assert "protoss_mothership_carrier" in tags

    def test_chargelot_requires_upgrade(self) -> None:
        # 没 Charge upgrade → 不 chargelot
        tags = compute_enemy_composition_tags(
            {"Zealot": 20}, enemy_race="protoss", enemy_upgrades=set()
        )
        assert "protoss_chargelot" not in tags
        assert "protoss_no_charge" in tags

        # 有 Charge + 12+ zealot → 触发
        tags = compute_enemy_composition_tags(
            {"Zealot": 20}, enemy_race="protoss", enemy_upgrades={"Charge"}
        )
        assert "protoss_chargelot" in tags

    def test_blink_stalker(self) -> None:
        tags = compute_enemy_composition_tags(
            {"Stalker": 15}, enemy_race="protoss", enemy_upgrades={"Blink"}
        )
        assert "protoss_blink" in tags

    def test_dt(self) -> None:
        tags = compute_enemy_composition_tags(
            {"DarkTemplar": 4}, enemy_race="protoss"
        )
        assert "protoss_dt" in tags

    def test_phoenix_storm_composite(self) -> None:
        tags = compute_enemy_composition_tags(
            {"Phoenix": 5, "HighTemplar": 4},
            enemy_race="protoss",
            enemy_upgrades={"PsiStorm"},
        )
        assert "protoss_phoenix" in tags
        assert "protoss_storm" in tags
        assert "protoss_phoenix_storm" in tags


# =========================================================================
# 推断 —— 种族特化（人族）
# =========================================================================


class TestComputeTagsTerran:
    def test_bio_medivac(self) -> None:
        tags = compute_enemy_composition_tags(
            {"Marine": 30, "Medivac": 4}, enemy_race="terran"
        )
        assert "terran_bio" in tags

    def test_bio_no_stim(self) -> None:
        tags = compute_enemy_composition_tags(
            {"Marine": 30, "Medivac": 4},
            enemy_race="terran",
            enemy_upgrades=set(),
        )
        assert "terran_bio_no_stim" in tags

    def test_mech_tank(self) -> None:
        tags = compute_enemy_composition_tags(
            {"SiegeTank": 8, "Marine": 10}, enemy_race="terran"
        )
        assert "terran_mech" in tags
        assert "terran_mech_tank" in tags

    def test_sky_terran_bc(self) -> None:
        tags = compute_enemy_composition_tags(
            {"Battlecruiser": 5}, enemy_race="terran"
        )
        assert "terran_sky" in tags

    def test_marine_widow(self) -> None:
        tags = compute_enemy_composition_tags(
            {"Marine": 20, "WidowMine": 6}, enemy_race="terran"
        )
        assert "terran_marine_widow" in tags

    def test_ghost(self) -> None:
        tags = compute_enemy_composition_tags(
            {"Ghost": 4, "Marine": 10}, enemy_race="terran"
        )
        assert "terran_ghost" in tags


# =========================================================================
# 推断函数边界
# =========================================================================


class TestEdgeCases:
    def test_unknown_unit_doesnt_crash(self) -> None:
        """未知单位 fallback 到 default cost，不挂"""
        tags = compute_enemy_composition_tags(
            {"NonExistentUnit": 100}, enemy_race="zerg"
        )
        # 未知单位算入 ground supply（default supply=2），100 个 → 200 supply
        assert "mass_ground" in tags

    def test_unknown_race(self) -> None:
        tags = compute_enemy_composition_tags(
            {"Marine": 50}, enemy_race="unknown"
        )
        # 通用 tag 仍 work
        assert "mass_ground" in tags
        # 种族 tag 不出
        assert "terran_bio" not in tags

    def test_enemy_upgrades_default(self) -> None:
        """enemy_upgrades=None 应等同空集"""
        tags = compute_enemy_composition_tags(
            {"Zealot": 20}, enemy_race="protoss"
        )
        # 没 Charge → no_charge
        assert "protoss_no_charge" in tags
        assert "protoss_chargelot" not in tags

    @pytest.mark.parametrize(
        "race", ["protoss", "zerg", "terran", None]
    )
    def test_all_returned_tags_in_canonical_set(self, race: str | None) -> None:
        """无论输入如何，返回的 tag 必须全部在 canonical 集里"""
        test_summary = {
            "Marine": 30, "Medivac": 5, "Zealot": 20, "Carrier": 4,
            "Mothership": 1, "Zergling": 40, "Baneling": 10, "Roach": 20,
            "Mutalisk": 10, "Ultralisk": 4, "BroodLord": 4,
            "SiegeTank": 8, "Battlecruiser": 5, "WidowMine": 6, "Ghost": 4,
            "Stalker": 15, "DarkTemplar": 4, "Phoenix": 5, "HighTemplar": 4,
            "Disruptor": 3, "Immortal": 5, "Colossus": 3, "Raven": 1,
        }
        tags = compute_enemy_composition_tags(
            test_summary,
            enemy_race=race,
            enemy_upgrades={"Charge", "Blink", "Stimpack", "PsiStorm"},
        )
        for tag in tags:
            assert tag in ENEMY_COMPOSITION_TAGS, (
                f"tag {tag!r} not in canonical ENEMY_COMPOSITION_TAGS — "
                f"add to enemy_tags.py or fix推断逻辑"
            )
