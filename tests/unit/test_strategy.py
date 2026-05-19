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
    PersistentDoctrine,
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

        真实 docs/aliases/protoss.yaml 当前无同形别名（VR 仅建筑），这里构造一个
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
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        # 两层架构（2026-05-19）：iac_2base 是 opening；skytoss 改名 persistent_skytoss 且 kind=persistent
        assert "1g_robo_immortal" in lib.all_ids(StrategyKind.OPENING)
        assert "iac_2base" in lib.all_ids(StrategyKind.OPENING)
        assert "persistent_skytoss" in lib.all_ids(StrategyKind.PERSISTENT)

    def test_loaded_opening_has_correct_shape(self) -> None:
        lib = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        ob = lib.get_opening("1g_robo_immortal")
        assert isinstance(ob, OpeningBuild)
        assert "1门Robo" in ob.aliases
        # 第一步：14 build BE（标准 1 BG Robo build：13 农 + 14 时下 Pylon；
        # 神族建筑用 SC2 hotkey 缩写，Pylon=BE，不是 PY）
        steps = ob.parsed_steps()
        assert steps[0].supply == 14
        assert steps[0].verb == "build"
        assert steps[0].obj == "BE"

    def test_loaded_iac_2base_as_opening(self) -> None:
        """两层架构（2026-05-19）：iac_2base 从 midgame 迁到 opening，attack_window 仍在"""
        lib = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        ob = lib.get_opening("iac_2base")
        assert isinstance(ob, OpeningBuild)
        assert ob.attack_window is not None
        # iac_2base attack_window 在 commit d03654e 改为「叉球一波」6:15 timing
        assert ob.attack_window.open_at == "6:15"

    def test_loaded_persistent_skytoss(self) -> None:
        """两层架构（2026-05-19）：skytoss 改名 persistent_skytoss，kind=persistent_doctrine"""
        lib = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        pd = lib.get_persistent("persistent_skytoss")
        from vibecraft.strategy import PersistentDoctrine

        assert isinstance(pd, PersistentDoctrine)
        assert pd.target_composition["Carrier"] == 12  # PascalCase key
        assert pd.gas_intensity == "high"

    def test_race_inferred_from_directory(self) -> None:
        """from_directories 应按 strategies/<race>/foo.yaml 推断种族。"""
        lib = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        assert lib.race_of("4bg") == "protoss"
        assert lib.race_of("iac_2base") == "protoss"
        assert lib.race_of("12pool") == "zerg"
        assert lib.race_of("macro_hatch") == "zerg"
        assert lib.race_of("marine_rush") == "terran"
        assert lib.race_of("not_a_strategy") is None

    def test_all_ids_for_race_filters(self) -> None:
        lib = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        protoss_ids = set(lib.all_ids_for_race("protoss"))
        zerg_ids = set(lib.all_ids_for_race("zerg"))
        terran_ids = set(lib.all_ids_for_race("terran"))
        # 各种族不重叠
        assert protoss_ids.isdisjoint(zerg_ids)
        assert protoss_ids.isdisjoint(terran_ids)
        assert zerg_ids.isdisjoint(terran_ids)
        # 已知样例
        assert "4bg" in protoss_ids
        assert "iac_2base" in protoss_ids
        assert "12pool" in zerg_ids
        assert "marine_rush" in terran_ids
        # 大小写不敏感
        assert lib.all_ids_for_race("Protoss") == lib.all_ids_for_race("protoss")

    def test_get_unknown_raises(self) -> None:
        lib = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        with pytest.raises(StrategyNotFoundError):
            lib.get("nope")

    def test_transitions_of(self) -> None:
        lib = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
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
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        # VR 在真实别名表里仅指建筑（虚空辉光舰不叫 VR）
        canonical, _ = lib.aliases.resolve("VR", verb=VerbHint.BUILD)
        assert canonical == "RoboticsFacility"
        # 单位别名走 TRAIN：虚空 → VoidRay
        canonical, group = lib.aliases.resolve("虚空", verb=VerbHint.TRAIN)
        assert canonical == "VoidRay"
        assert group == "unit"


# =========================================================================
# 2026-05-19 两层架构：OpeningCompletion / PersistentDoctrine schema
# =========================================================================


class TestOpeningCompletion:
    """OpeningCompletion model 校验"""

    def test_valid_with_timeout_only(self) -> None:
        from vibecraft.strategy import OpeningCompletion

        oc = OpeningCompletion(timeout_s=420)
        assert oc.timeout_s == 420
        assert oc.goal_when is None

    def test_valid_with_goal_when(self) -> None:
        from vibecraft.strategy import OpeningCompletion

        oc = OpeningCompletion(
            timeout_s=420,
            goal_when={
                "kind": "all_of",
                "conditions": [
                    {"kind": "structure_count", "structure_type": "Gateway", "op": ">=", "value": 4},
                    {"kind": "time_elapsed_since", "seconds": 360, "ref": "game_start"},
                ],
            },
        )
        assert oc.goal_when["kind"] == "all_of"  # type: ignore[index]

    def test_timeout_must_be_positive(self) -> None:
        from pydantic import ValidationError

        from vibecraft.strategy import OpeningCompletion

        with pytest.raises(ValidationError):
            OpeningCompletion(timeout_s=0)
        with pytest.raises(ValidationError):
            OpeningCompletion(timeout_s=-1)

    def test_extra_field_rejected(self) -> None:
        from pydantic import ValidationError

        from vibecraft.strategy import OpeningCompletion

        with pytest.raises(ValidationError):
            OpeningCompletion(timeout_s=300, extra_field="nope")  # type: ignore[call-arg]


class TestOpeningBuildWithCompletion:
    """OpeningBuild 的 completion 字段（None 或 OpeningCompletion）"""

    def test_completion_field_optional(self) -> None:
        """现有 yaml 不带 completion 字段时仍能加载"""
        from vibecraft.strategy import OpeningBuild

        ob = OpeningBuild.model_validate(
            {
                "kind": "opening_build",
                "id": "test",
                "display_name_zh": "测试",
                "phases": [],
                "steps": ["13 build Pylon"],
            }
        )
        assert ob.completion is None

    def test_completion_field_loads(self) -> None:
        from vibecraft.strategy import OpeningBuild

        ob = OpeningBuild.model_validate(
            {
                "kind": "opening_build",
                "id": "test",
                "display_name_zh": "测试",
                "phases": [],
                "steps": ["13 build Pylon"],
                "completion": {
                    "timeout_s": 420,
                    "goal_when": {
                        "kind": "structure_count",
                        "structure_type": "Gateway",
                        "op": ">=",
                        "value": 4,
                    },
                },
            }
        )
        assert ob.completion is not None
        assert ob.completion.timeout_s == 420


class TestPersistentDoctrine:
    """PersistentDoctrine schema（新 kind）"""

    def _minimal_doctrine(self, **overrides) -> dict:
        base = {
            "kind": "persistent_doctrine",
            "id": "test_persistent",
            "display_name_zh": "测试 doctrine",
            "target_composition": {"Stalker": 20, "Sentry": 3},
        }
        base.update(overrides)
        return base

    def test_minimal_loads(self) -> None:
        from vibecraft.strategy import PersistentDoctrine

        d = PersistentDoctrine.model_validate(self._minimal_doctrine())
        assert d.id == "test_persistent"
        assert d.gas_intensity == "medium"  # 默认值
        assert d.ramp_up_time_s == 90.0  # 默认值
        assert d.counters_against == []

    def test_gas_intensity_validation(self) -> None:
        from pydantic import ValidationError

        from vibecraft.strategy import PersistentDoctrine

        # 合法
        for valid in ("low", "medium", "high"):
            d = PersistentDoctrine.model_validate(
                self._minimal_doctrine(gas_intensity=valid)
            )
            assert d.gas_intensity == valid
        # 非法
        with pytest.raises(ValidationError):
            PersistentDoctrine.model_validate(self._minimal_doctrine(gas_intensity="extreme"))

    def test_ramp_up_must_be_positive(self) -> None:
        from pydantic import ValidationError

        from vibecraft.strategy import PersistentDoctrine

        with pytest.raises(ValidationError):
            PersistentDoctrine.model_validate(self._minimal_doctrine(ramp_up_time_s=0))

    def test_counters_against_list(self) -> None:
        from vibecraft.strategy import PersistentDoctrine

        d = PersistentDoctrine.model_validate(
            self._minimal_doctrine(
                counters_against=["zerg_ling_bane", "terran_bio"],
                weak_against=["mass_air"],
            )
        )
        assert "zerg_ling_bane" in d.counters_against
        assert "mass_air" in d.weak_against


class TestStrategyLibraryPersistent:
    """StrategyLibrary 对 PersistentDoctrine 的 CRUD / 查询"""

    def _make_persistent(self, sid: str) -> PersistentDoctrine:
        return PersistentDoctrine.model_validate(
            {
                "kind": "persistent_doctrine",
                "id": sid,
                "display_name_zh": sid,
                "target_composition": {"Stalker": 10},
            }
        )

    def test_constructor_accepts_persistents(self) -> None:
        d1 = self._make_persistent("persistent_a")
        d2 = self._make_persistent("persistent_b")
        lib = StrategyLibrary(persistents=[d1, d2])
        assert lib.get_persistent("persistent_a").id == "persistent_a"
        assert {d.id for d in lib.persistents} == {"persistent_a", "persistent_b"}

    def test_all_ids_includes_persistents(self) -> None:
        d = self._make_persistent("persistent_x")
        lib = StrategyLibrary(persistents=[d])
        assert "persistent_x" in lib.all_ids()
        assert lib.all_ids(StrategyKind.PERSISTENT) == ["persistent_x"]

    def test_get_dispatches_to_persistent(self) -> None:
        d = self._make_persistent("persistent_y")
        lib = StrategyLibrary(persistents=[d])
        got = lib.get("persistent_y")
        assert got.id == "persistent_y"
        assert got.kind == StrategyKind.PERSISTENT

    def test_persistent_doctrines_filter_by_race(self) -> None:
        d_proto = self._make_persistent("persistent_proto")
        d_zerg = self._make_persistent("persistent_zerg")
        lib = StrategyLibrary(
            persistents=[d_proto, d_zerg],
            races={"persistent_proto": "protoss", "persistent_zerg": "zerg"},
        )
        proto_doctrines = lib.persistent_doctrines("protoss")
        assert [d.id for d in proto_doctrines] == ["persistent_proto"]
        # 无 race 限制
        assert len(lib.persistent_doctrines()) == 2

    def test_kind_of_returns_correct_kind(self) -> None:
        d = self._make_persistent("persistent_a")
        ob = OpeningBuild.model_validate(
            {
                "kind": "opening_build",
                "id": "open_a",
                "display_name_zh": "open",
                "phases": [],
                "steps": ["13 build Pylon"],
            }
        )
        lib = StrategyLibrary(openings=[ob], persistents=[d])
        assert lib.kind_of("open_a") == StrategyKind.OPENING
        assert lib.kind_of("persistent_a") == StrategyKind.PERSISTENT
        assert lib.kind_of("nope") is None

    def test_hotkey_aliases(self) -> None:
        lib = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        # hotkey 串 = Liquipedia Standard 布局真实 hotkey (Probe build menu
        # 分两层:B basic + V advanced)。2026-05-17 把原项目自创简称(VD=RoboticsBay
        # / VA=TemplarArchives / VX=FleetBeacon 等)改成真 hotkey。
        for hotkey, expected in [
            ("BG", "Gateway"),  # B+G
            ("BF", "Forge"),  # B+F
            ("BA", "Assimilator"),  # B+A (原 VC)
            ("BB", "ShieldBattery"),  # B+B (原 B+H)
            ("VS", "Stargate"),  # V+S
            ("VR", "RoboticsFacility"),  # V+R (原 B+R)
            ("VB", "RoboticsBay"),  # V+B (原 VD)
            ("VC", "TwilightCouncil"),  # V+C (原 VT)
            ("VT", "TemplarArchives"),  # V+T (原 VA)
            ("VD", "DarkShrine"),  # V+D (原 VB)
            ("VF", "FleetBeacon"),  # V+F (原 VX)
        ]:
            canonical, group = lib.aliases.resolve(hotkey, verb=VerbHint.BUILD)
            assert canonical == expected, f"hotkey {hotkey!r} 应解析为 {expected!r}"
            assert group == "building"
