"""虫族 alias 表单测（Task 2a M6.2a）。

测试范围：
1. AliasTable.from_yaml(zerg.yaml) 不抛
2. 所有 alias casefold 后无歧义（同字符不映射多 canonical）
3. 关键玩家话语能 resolve：
   - "小狗" → Zergling
   - "妖虫" → Baneling
   - "BL" → BroodLord
   - "BS" → SpawningPool
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ZERG_YAML = Path(__file__).parents[2] / "docs" / "aliases" / "zerg.yaml"


@pytest.fixture()
def zerg_alias_table() -> any:
    """加载真实 zerg.yaml 并返回 AliasTable。"""
    from vibecraft.strategy.aliases import AliasTable

    return AliasTable.from_yaml(_ZERG_YAML)


class TestZergAliasTableLoad:
    """基础加载和结构完整性测试。"""

    def test_from_yaml_does_not_raise(self) -> None:
        """AliasTable.from_yaml(zerg.yaml) 不抛异常。"""
        from vibecraft.strategy.aliases import AliasTable

        table = AliasTable.from_yaml(_ZERG_YAML)
        assert table is not None

    def test_has_buildings(self, zerg_alias_table: any) -> None:
        """加载后有 buildings（建筑别名存在）。"""
        # AliasTable.resolve 返回 (canonical, group)
        canonical, _group = zerg_alias_table.resolve("BH")
        assert canonical == "Hatchery"

    def test_has_units(self, zerg_alias_table: any) -> None:
        """加载后有 units（虫族单位别名存在）。"""
        canonical, _group = zerg_alias_table.resolve("小狗")
        assert canonical == "Zergling"

    def test_no_ambiguity_across_canonicals(self, zerg_alias_table: any) -> None:
        """同一个 alias 字符串不映射到多个 canonical（无歧义）。"""
        # 遍历 _reverse 反向索引，确认每个 key 只对应一个 canonical
        for alias_key, matches in zerg_alias_table._reverse.items():
            canonicals = {m[0] for m in matches}
            assert len(canonicals) == 1, (
                f"alias {alias_key!r} 映射到多个 canonical: {sorted(canonicals)}"
            )


class TestZergAliasResolve:
    """关键玩家话语 resolve 测试。"""

    def test_small_dog_resolves_to_zergling(self, zerg_alias_table: any) -> None:
        """'小狗' → Zergling。"""
        canonical, _ = zerg_alias_table.resolve("小狗")
        assert canonical == "Zergling"

    def test_yao_chong_resolves_to_baneling(self, zerg_alias_table: any) -> None:
        """'妖虫' → Baneling。"""
        canonical, _ = zerg_alias_table.resolve("妖虫")
        assert canonical == "Baneling"

    def test_bl_resolves_to_broodlord(self, zerg_alias_table: any) -> None:
        """'BL' → BroodLord。"""
        canonical, _ = zerg_alias_table.resolve("BL")
        assert canonical == "BroodLord"

    def test_bs_resolves_to_spawningpool(self, zerg_alias_table: any) -> None:
        """'BS' → SpawningPool。"""
        canonical, _ = zerg_alias_table.resolve("BS")
        assert canonical == "SpawningPool"

    def test_roach_resolves(self, zerg_alias_table: any) -> None:
        """'蟑螂' → Roach。"""
        canonical, _ = zerg_alias_table.resolve("蟑螂")
        assert canonical == "Roach"

    def test_xiao_qiang_resolves_to_roach(self, zerg_alias_table: any) -> None:
        """'小强' → Roach（玩家口语）。"""
        canonical, _ = zerg_alias_table.resolve("小强")
        assert canonical == "Roach"

    def test_hydra_alias_resolves(self, zerg_alias_table: any) -> None:
        """'刺蛇' → Hydralisk。"""
        canonical, _ = zerg_alias_table.resolve("刺蛇")
        assert canonical == "Hydralisk"

    def test_drone_alias_resolves(self, zerg_alias_table: any) -> None:
        """'农民' → Drone。"""
        canonical, _ = zerg_alias_table.resolve("农民")
        assert canonical == "Drone"

    def test_ultralisk_resolves(self, zerg_alias_table: any) -> None:
        """'雷兽' → Ultralisk。"""
        canonical, _ = zerg_alias_table.resolve("雷兽")
        assert canonical == "Ultralisk"

    def test_bh_resolves_to_hatchery(self, zerg_alias_table: any) -> None:
        """'BH' → Hatchery（hotkey 别名）。"""
        canonical, _ = zerg_alias_table.resolve("BH")
        assert canonical == "Hatchery"
