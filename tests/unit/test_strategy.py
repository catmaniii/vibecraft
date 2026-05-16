"""Strategy library 单元测试。

覆盖 (§4.2 / §4.4)：
- BuildStep 紧凑三段式解析
- 三种 kind 的 schema ser/de
- AliasTable verb 消歧（"造/出/研" 决定查哪组 + 同形别名消歧）
- StrategyLibrary 加载 / 查询 / 转移图
- 跨引用校验（opening → midgame 必须存在）
- 真实 YAML 文件能加载（fixture 用项目 strategies/）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.strategy import (
    AliasTable,
    BuildStep,
    LategameDoctrine,
    MidgameStance,
    OpeningBuild,
    StrategyKind,
    StrategyLibrary,
    StrategyNotFoundError,
    VerbHint,
)
from vibecraft.strategy.errors import StrategyValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# =========================================================================
# BuildStep
# =========================================================================


class TestBuildStep:
    def test_simple(self) -> None:
        s = BuildStep.parse("13 build Pylon")
        assert s.supply == 13
        assert s.verb == "build"
        assert s.obj == "Pylon"
        assert s.modifier is None

    def test_with_modifier(self) -> None:
        s = BuildStep.parse("22 research WarpGateResearch @chrono")
        assert s.modifier == "chrono"

    def test_train(self) -> None:
        s = BuildStep.parse("34 train Immortal")
        assert s.verb == "train"

    def test_send_probe(self) -> None:
        s = BuildStep.parse("17 send_probe enemy_natural")
        assert s.verb == "send_probe"
        assert s.obj == "enemy_natural"

    def test_unicode_object(self) -> None:
        s = BuildStep.parse("13 build 水晶")
        assert s.obj == "水晶"

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError, match="格式非法"):
            BuildStep.parse("13 attack 敌人主基地")


# =========================================================================
# Schema models
# =========================================================================


class TestOpeningBuild:
    def test_minimal(self) -> None:
        ob = OpeningBuild.model_validate(
            {
                "kind": "opening_build",
                "id": "test_open",
                "display_name_zh": "测试开局",
                "phases": [{"id": "p1", "display": "P1"}],
                "steps": ["13 build Pylon"],
            }
        )
        assert ob.id == "test_open"
        assert ob.parsed_steps()[0].obj == "Pylon"

    def test_step_validator_rejects_bad_step(self) -> None:
        with pytest.raises(Exception, match=r"格式非法|build step"):
            OpeningBuild.model_validate(
                {
                    "kind": "opening_build",
                    "id": "x",
                    "display_name_zh": "y",
                    "phases": [],
                    "steps": ["randomstring"],
                }
            )


# =========================================================================
# AliasTable
# =========================================================================


@pytest.fixture
def alias_table() -> AliasTable:
    raw = {
        "buildings": {
            "RoboticsFacility": {
                "default_display": "VR",
                "aliases": ["VR", "Robo", "机械工厂"],
                "hotkey": "B+R",
            },
            "Gateway": {
                "default_display": "BG",
                "aliases": ["BG", "兵营", "折跃门"],
                "hotkey": "B+G",
            },
        },
        "units": {
            "VoidRay": {
                "default_display": "虚空",
                "aliases": ["虚空", "虚空辐射"],
            },
            "Stalker": {
                "default_display": "追猎",
                "aliases": ["追猎", "追猎者", "Stalker"],
            },
        },
        "upgrades": {
            "Blink": {
                "default_display": "闪烁",
                "aliases": ["闪烁", "Blink", "闪追"],
            },
        },
    }
    return AliasTable.from_dict(raw)


class TestAliasTable:
    def test_unambiguous_lookup(self, alias_table: AliasTable) -> None:
        canonical, group = alias_table.resolve("追猎")
        assert canonical == "Stalker"
        assert group == "unit"

    def test_lookup_by_hotkey(self, alias_table: AliasTable) -> None:
        canonical, group = alias_table.resolve("BG")
        assert canonical == "Gateway"
        assert group == "building"

    def test_ambiguous_alias_without_verb_raises(self) -> None:
        """同形别名（building+unit 共用）在 verb=ANY 时抛歧义错误。

        真实 aliases/protoss.yaml 当前无同形别名（VR 仅建筑），这里构造一个
        同形场景覆盖 resolve 的歧义分支。
        """
        table = AliasTable.from_dict(
            {
                "buildings": {"Gateway": {"default_display": "BG", "aliases": ["同形"]}},
                "units": {"Zealot": {"default_display": "叉子", "aliases": ["同形"]}},
                "upgrades": {},
            }
        )
        with pytest.raises(StrategyValidationError, match="歧义"):
            table.resolve("同形")

    def test_verb_disambiguates_to_building(self, alias_table: AliasTable) -> None:
        canonical, group = alias_table.resolve("VR", verb=VerbHint.BUILD)
        assert canonical == "RoboticsFacility"
        assert group == "building"

    def test_verb_disambiguates_to_unit(self, alias_table: AliasTable) -> None:
        canonical, group = alias_table.resolve("虚空", verb=VerbHint.TRAIN)
        assert canonical == "VoidRay"
        assert group == "unit"

    def test_verb_research_for_upgrade(self, alias_table: AliasTable) -> None:
        canonical, _ = alias_table.resolve("闪烁", verb=VerbHint.RESEARCH)
        assert canonical == "Blink"

    def test_unknown_alias_raises(self, alias_table: AliasTable) -> None:
        with pytest.raises(KeyError, match="未知别名"):
            alias_table.resolve("不存在的东西")

    def test_alias_in_wrong_group_raises(self, alias_table: AliasTable) -> None:
        # "追猎" 是 unit；用 BUILD verb 找应失败
        with pytest.raises(KeyError, match="没有匹配"):
            alias_table.resolve("追猎", verb=VerbHint.BUILD)

    def test_display_of(self, alias_table: AliasTable) -> None:
        assert alias_table.display_of("Gateway") == "BG"

    def test_all_aliases_includes_canonical(self, alias_table: AliasTable) -> None:
        aliases = list(alias_table.all_aliases("unit"))
        assert "Stalker" in aliases
        assert "追猎" in aliases

    def test_case_insensitive_match(self, alias_table: AliasTable) -> None:
        canonical, _ = alias_table.resolve("bg")
        assert canonical == "Gateway"


# =========================================================================
# StrategyLibrary
# =========================================================================


class TestStrategyLibrary:
    def test_loads_real_strategies(self) -> None:
        lib = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        assert "1g_robo_immortal" in lib.all_ids(StrategyKind.OPENING)
        assert "iac_2base" in lib.all_ids(StrategyKind.MIDGAME)
        assert "skytoss" in lib.all_ids(StrategyKind.LATEGAME)

    def test_loaded_opening_has_correct_shape(self) -> None:
        lib = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        ob = lib.get_opening("1g_robo_immortal")
        assert isinstance(ob, OpeningBuild)
        assert "1门Robo" in ob.aliases
        # 第一步：13 build PY(vibecraft 用快捷键命名,PY=Pylon)
        steps = ob.parsed_steps()
        assert steps[0].supply == 13
        assert steps[0].verb == "build"
        assert steps[0].obj == "PY"

    def test_loaded_midgame(self) -> None:
        lib = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        mg = lib.get_midgame("iac_2base")
        assert isinstance(mg, MidgameStance)
        assert mg.attack_window is not None
        # iac_2base attack_window 在 commit d03654e 改为「叉球一波」6:15 timing
        assert mg.attack_window.open_at == "6:15"

    def test_loaded_lategame(self) -> None:
        lib = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        lg = lib.get_lategame("skytoss")
        assert isinstance(lg, LategameDoctrine)
        assert lg.target_composition["carrier"] == 12

    def test_get_unknown_raises(self) -> None:
        lib = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        with pytest.raises(StrategyNotFoundError):
            lib.get("nope")

    def test_transitions_of(self) -> None:
        lib = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        midgames = lib.transitions_of("1g_robo_immortal")
        assert "iac_2base" in midgames

    def test_cross_reference_validation(self) -> None:
        """opening 引用不存在的 midgame_id → 加载失败。"""
        bad_opening = OpeningBuild.model_validate(
            {
                "kind": "opening_build",
                "id": "broken",
                "display_name_zh": "坏的",
                "phases": [],
                "steps": ["13 build Pylon"],
                "default_transitions": [{"midgame_id": "nope", "when": "default"}],
            }
        )
        with pytest.raises(StrategyValidationError, match="未注册"):
            StrategyLibrary(openings=[bad_opening], midgames=[], lategames=[])

    def test_aliases_loaded_from_real_yaml(self) -> None:
        lib = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        # VR 在真实别名表里仅指建筑（虚空辉光舰不叫 VR）
        canonical, _ = lib.aliases.resolve("VR", verb=VerbHint.BUILD)
        assert canonical == "RoboticsFacility"
        # 单位别名走 TRAIN：虚空 → VoidRay
        canonical, group = lib.aliases.resolve("虚空", verb=VerbHint.TRAIN)
        assert canonical == "VoidRay"
        assert group == "unit"

    def test_hotkey_aliases(self) -> None:
        lib = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        for hotkey, expected in [
            ("BG", "Gateway"),
            ("BF", "Forge"),
            ("VS", "Stargate"),
            ("VD", "RoboticsBay"),
            ("VX", "FleetBeacon"),
            ("VA", "TemplarArchives"),
        ]:
            canonical, group = lib.aliases.resolve(hotkey, verb=VerbHint.BUILD)
            assert canonical == expected, f"hotkey {hotkey!r} 应解析为 {expected!r}"
            assert group == "building"
