"""人族 alias 表单测（Task 3a M6.3a）。

测试范围：
1. AliasTable.from_yaml(terran.yaml) 不抛
2. 所有 alias casefold 后无歧义（同字符不映射多 canonical）
3. 关键玩家话语能 resolve：
   - "枪兵" → Marine
   - "船长" → Battlecruiser
   - "医疗船" → Medivac
   - "BB" → Barracks
   - "BC" → FusionCore
"""

from __future__ import annotations

from pathlib import Path

import pytest

_TERRAN_YAML = Path(__file__).parents[2] / "docs" / "aliases" / "terran.yaml"


@pytest.fixture()
def terran_alias_table() -> any:
    """加载真实 terran.yaml 并返回 AliasTable。"""
    from vibecraft.strategy.aliases import AliasTable

    return AliasTable.from_yaml(_TERRAN_YAML)


class TestTerranAliasTableLoad:
    """基础加载和结构完整性测试。"""

    def test_from_yaml_does_not_raise(self) -> None:
        """AliasTable.from_yaml(terran.yaml) 不抛异常。"""
        from vibecraft.strategy.aliases import AliasTable

        table = AliasTable.from_yaml(_TERRAN_YAML)
        assert table is not None

    def test_has_buildings(self, terran_alias_table: any) -> None:
        """加载后有 buildings（建筑别名存在）。"""
        canonical, _group = terran_alias_table.resolve("BB")
        assert canonical == "Barracks"

    def test_has_units(self, terran_alias_table: any) -> None:
        """加载后有 units（人族单位别名存在）。"""
        canonical, _group = terran_alias_table.resolve("枪兵")
        assert canonical == "Marine"

    def test_no_ambiguity_across_canonicals(self, terran_alias_table: any) -> None:
        """同一个 alias 字符串不映射到多个 canonical（无歧义）。"""
        # 遍历 _reverse 反向索引，确认每个 key 只对应一个 canonical
        for alias_key, matches in terran_alias_table._reverse.items():
            canonicals = {m[0] for m in matches}
            assert len(canonicals) == 1, (
                f"alias {alias_key!r} 映射到多个 canonical: {sorted(canonicals)}"
            )


class TestTerranAliasResolve:
    """关键玩家话语 resolve 测试。"""

    def test_marine_resolves(self, terran_alias_table: any) -> None:
        """'枪兵' → Marine。"""
        canonical, _ = terran_alias_table.resolve("枪兵")
        assert canonical == "Marine"

    def test_battlecruiser_captain_resolves(self, terran_alias_table: any) -> None:
        """'船长' → Battlecruiser（玩家口语）。"""
        canonical, _ = terran_alias_table.resolve("船长")
        assert canonical == "Battlecruiser"

    def test_medivac_resolves(self, terran_alias_table: any) -> None:
        """'医疗船' → Medivac。"""
        canonical, _ = terran_alias_table.resolve("医疗船")
        assert canonical == "Medivac"

    def test_bb_resolves_to_barracks(self, terran_alias_table: any) -> None:
        """'BB' → Barracks（人族兵营 hotkey）。"""
        canonical, _ = terran_alias_table.resolve("BB")
        assert canonical == "Barracks"

    def test_bc_resolves_to_fusion_core(self, terran_alias_table: any) -> None:
        """'BC' → FusionCore（人族科技建筑 hotkey；注意跟神族 BC=PhotonCannon 区分）。"""
        canonical, _ = terran_alias_table.resolve("BC")
        assert canonical == "FusionCore"

    def test_siege_tank_resolves(self, terran_alias_table: any) -> None:
        """'坦克' → SiegeTank。"""
        canonical, _ = terran_alias_table.resolve("坦克")
        assert canonical == "SiegeTank"

    def test_scv_resolves(self, terran_alias_table: any) -> None:
        """'农民' → SCV。"""
        canonical, _ = terran_alias_table.resolve("农民")
        assert canonical == "SCV"

    def test_stim_resolves(self, terran_alias_table: any) -> None:
        """'兴奋剂' → Stimpack。"""
        canonical, _ = terran_alias_table.resolve("兴奋剂")
        assert canonical == "Stimpack"

    def test_rax_alias_resolves(self, terran_alias_table: any) -> None:
        """'Rax' → Barracks（英文短称）。"""
        canonical, _ = terran_alias_table.resolve("Rax")
        assert canonical == "Barracks"

    def test_bn_resolves_to_command_center(self, terran_alias_table: any) -> None:
        """'BN' → CommandCenter（人族基地 hotkey）。"""
        canonical, _ = terran_alias_table.resolve("BN")
        assert canonical == "CommandCenter"
