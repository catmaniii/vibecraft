"""no_ht 三变体单测。

覆盖：
1. persistent_skytoss_no_ht
   - yaml 加载：id / display_name_zh / aliases 正确
   - 无 TemplarArchives / PsiStorm / HighTemplar（关键删除验证）
   - 有 DarkShrine / DarkTemplar（Archon 来源正确）
   - plan class instantiate 正常（mock sharpy）

2. persistent_colossus_no_ht
   - yaml 加载：id / display_name_zh / aliases 正确
   - 无 TemplarArchives / PsiStorm / HighTemplar
   - 保留 TwilightCouncil（Charge 用）
   - 无 DarkShrine（ColossusNoHT 不用 DT 合球，只是无 HT）
   - plan class instantiate 正常

3. persistent_immortal_archon_no_ht
   - yaml 加载：id / display_name_zh / aliases 正确
   - 无 TemplarArchives / PsiStorm / HighTemplar
   - 有 DarkShrine / DarkTemplar（DT 合球路线）
   - plan class instantiate 正常

所有 plan class 实例化用 mock sharpy 方式（不拉起真实 SC2），
同 test_plan_create_plan_smoke.py 的 skip 模式（需 vendor/sharpy）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_VENDOR_SHARPY = _PROJECT_ROOT / "vendor" / "sharpy"

# =========================================================================
# YAML 加载测试（不依赖 sharpy，直接读 yaml 文件）
# =========================================================================


def _load_yaml(filename: str) -> dict:  # type: ignore[type-arg]
    """直接读 strategies/protoss/<filename> 用 pyyaml。"""
    import yaml  # type: ignore[import-untyped]

    path = _PROJECT_ROOT / "strategies" / "protoss" / filename
    assert path.exists(), f"yaml 文件不存在: {path}"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestSkytossNoHTYaml:
    def setup_method(self) -> None:
        self.doc = _load_yaml("skytoss_no_ht.yaml")

    def test_id(self) -> None:
        assert self.doc["id"] == "persistent_skytoss_no_ht"

    def test_kind(self) -> None:
        assert self.doc["kind"] == "persistent_doctrine"

    def test_display_name_zh(self) -> None:
        assert "无电兵" in self.doc["display_name_zh"]

    def test_has_no_ht_aliases(self) -> None:
        aliases = self.doc["aliases"]
        assert any("无电" in a for a in aliases), f"aliases 缺无电兵关键词: {aliases}"

    def test_sharpy_dummy_class_points_to_no_ht_class(self) -> None:
        cls_path = self.doc["sharpy_dummy_class"]
        assert "skytoss_no_ht" in cls_path
        assert "SkytossNoHT" in cls_path

    def test_no_templar_archives_in_required_structures(self) -> None:
        structs = self.doc.get("required_structures", {})
        assert "TemplarArchives" not in structs, "no_ht 变体不应有 TemplarArchives"

    def test_no_psi_storm_in_required_tech(self) -> None:
        tech = self.doc.get("required_tech", [])
        assert "PsiStorm" not in tech, "no_ht 变体不应有 PsiStorm"

    def test_no_high_templar_in_target_composition(self) -> None:
        comp = self.doc.get("target_composition", {})
        assert "HighTemplar" not in comp, "no_ht 变体 target_composition 不应有 HighTemplar"

    def test_has_dark_shrine_in_required_structures(self) -> None:
        structs = self.doc.get("required_structures", {})
        assert "DarkShrine" in structs, "skytoss_no_ht 应有 DarkShrine（VD，DT 合 Archon 前置）"

    def test_has_dark_templar_in_target_composition(self) -> None:
        comp = self.doc.get("target_composition", {})
        assert "DarkTemplar" in comp, "skytoss_no_ht 应有 DarkTemplar（合 Archon 原料）"

    def test_has_archon_in_target_composition(self) -> None:
        comp = self.doc.get("target_composition", {})
        assert "Archon" in comp, "skytoss_no_ht 应有 Archon（DT 合球结果）"

    def test_has_fleet_beacon(self) -> None:
        structs = self.doc.get("required_structures", {})
        assert "FleetBeacon" in structs, "Skytoss 变体需要 FleetBeacon"

    def test_has_carrier_in_target_composition(self) -> None:
        comp = self.doc.get("target_composition", {})
        assert "Carrier" in comp
        assert comp["Carrier"] >= 10, f"Carrier 目标数量偏少: {comp['Carrier']}"

    def test_graviton_catapult_in_required_tech(self) -> None:
        tech = self.doc.get("required_tech", [])
        assert "GravitonCatapult" in tech, "Carrier 需要 GravitonCatapult 升级"


class TestColossusNoHTYaml:
    def setup_method(self) -> None:
        self.doc = _load_yaml("colossus_no_ht.yaml")

    def test_id(self) -> None:
        assert self.doc["id"] == "persistent_colossus_no_ht"

    def test_kind(self) -> None:
        assert self.doc["kind"] == "persistent_doctrine"

    def test_display_name_zh(self) -> None:
        assert "无电兵" in self.doc["display_name_zh"]

    def test_has_no_ht_aliases(self) -> None:
        aliases = self.doc["aliases"]
        assert any("无" in a for a in aliases), f"aliases 缺无电兵关键词: {aliases}"

    def test_sharpy_dummy_class_points_to_no_ht_class(self) -> None:
        cls_path = self.doc["sharpy_dummy_class"]
        assert "colossus_no_ht" in cls_path
        assert "ColossusNoHT" in cls_path

    def test_no_templar_archives_in_required_structures(self) -> None:
        structs = self.doc.get("required_structures", {})
        assert "TemplarArchives" not in structs, "no_ht 变体不应有 TemplarArchives"

    def test_no_psi_storm_in_required_tech(self) -> None:
        tech = self.doc.get("required_tech", [])
        assert "PsiStorm" not in tech, "no_ht 变体不应有 PsiStorm"

    def test_no_high_templar_in_target_composition(self) -> None:
        comp = self.doc.get("target_composition", {})
        assert "HighTemplar" not in comp, "no_ht 变体 target_composition 不应有 HighTemplar"

    def test_no_dark_shrine_in_required_structures(self) -> None:
        # ColossusNoHT 只是去掉 HT，不用 DT 合球路线（巨像自己 AoE）
        structs = self.doc.get("required_structures", {})
        assert "DarkShrine" not in structs, (
            "ColossusNoHT 不需要 DarkShrine（巨像自身 AoE 覆盖，无需 DT 合球兜底）"
        )

    def test_has_twilight_council_for_charge(self) -> None:
        structs = self.doc.get("required_structures", {})
        assert "TwilightCouncil" in structs, "保留 VC 用于 Charge 升级"

    def test_charge_in_required_tech(self) -> None:
        tech = self.doc.get("required_tech", [])
        assert "Charge" in tech, "冲锋叉是核心 gateway 单位，Charge 必须研"

    def test_has_robotics_bay(self) -> None:
        structs = self.doc.get("required_structures", {})
        assert "RoboticsBay" in structs, "巨像需要 VB"

    def test_has_extended_thermal_lance_in_required_tech(self) -> None:
        tech = self.doc.get("required_tech", [])
        assert "ExtendedThermalLance" in tech, "巨像射程必须"

    def test_has_colossus_in_target_composition(self) -> None:
        comp = self.doc.get("target_composition", {})
        assert "Colossus" in comp
        assert comp["Colossus"] >= 4, f"Colossus 目标数量偏少: {comp['Colossus']}"


class TestImmortalArchonNoHTYaml:
    def setup_method(self) -> None:
        self.doc = _load_yaml("immortal_archon_no_ht.yaml")

    def test_id(self) -> None:
        assert self.doc["id"] == "persistent_immortal_archon_no_ht"

    def test_kind(self) -> None:
        assert self.doc["kind"] == "persistent_doctrine"

    def test_display_name_zh(self) -> None:
        assert "隐刀合球" in self.doc["display_name_zh"] or "DT" in self.doc["display_name_zh"]

    def test_has_dt_aliases(self) -> None:
        aliases = self.doc["aliases"]
        # 必须有 DT / 隐刀 相关 alias
        assert any("DT" in a or "隐刀" in a for a in aliases), (
            f"aliases 缺 DT/隐刀 关键词: {aliases}"
        )

    def test_sharpy_dummy_class_points_to_no_ht_class(self) -> None:
        cls_path = self.doc["sharpy_dummy_class"]
        assert "immortal_archon_no_ht" in cls_path
        assert "ImmortalArchonNoHT" in cls_path

    def test_no_templar_archives_in_required_structures(self) -> None:
        structs = self.doc.get("required_structures", {})
        assert "TemplarArchives" not in structs, "no_ht 变体不应有 TemplarArchives"

    def test_no_psi_storm_in_required_tech(self) -> None:
        tech = self.doc.get("required_tech", [])
        assert "PsiStorm" not in tech, "no_ht 变体不应有 PsiStorm"

    def test_no_high_templar_in_target_composition(self) -> None:
        comp = self.doc.get("target_composition", {})
        assert "HighTemplar" not in comp, "no_ht 变体 target_composition 不应有 HighTemplar"

    def test_has_dark_shrine_in_required_structures(self) -> None:
        structs = self.doc.get("required_structures", {})
        assert "DarkShrine" in structs, "immortal_archon_no_ht 应有 DarkShrine（VD，DT 合球前置）"

    def test_has_dark_templar_in_target_composition(self) -> None:
        comp = self.doc.get("target_composition", {})
        assert "DarkTemplar" in comp, "immortal_archon_no_ht 应有 DarkTemplar（合球原料）"

    def test_dt_count_sufficient_for_4_archons(self) -> None:
        comp = self.doc.get("target_composition", {})
        dt_count = comp.get("DarkTemplar", 0)
        archon_count = comp.get("Archon", 0)
        # 每 2 DT 合 1 Archon，DT 数量应 >= Archon 目标 × 2
        assert dt_count >= archon_count * 2, (
            f"DT({dt_count}) 不够合 Archon({archon_count})×2 — 需至少 {archon_count * 2} 个 DT"
        )

    def test_has_archon_in_target_composition(self) -> None:
        comp = self.doc.get("target_composition", {})
        assert "Archon" in comp
        assert comp["Archon"] >= 4, f"Archon 目标数量偏少: {comp['Archon']}"

    def test_has_immortal_in_target_composition(self) -> None:
        comp = self.doc.get("target_composition", {})
        assert "Immortal" in comp
        assert comp["Immortal"] >= 4

    def test_charge_in_required_tech(self) -> None:
        tech = self.doc.get("required_tech", [])
        assert "Charge" in tech

    def test_has_warpprism_in_target_composition(self) -> None:
        comp = self.doc.get("target_composition", {})
        assert "WarpPrism" in comp, "棱镜多线是 immortal_archon 系战术的核心之一"


# =========================================================================
# Plan class 实例化 smoke（需 vendor/sharpy 可 import）
# =========================================================================


@pytest.fixture(scope="module")
def _sharpy_available():
    """尝试加载 vendor/sharpy；不可用时 skip。"""
    import os

    sharpy_path_str = str(_VENDOR_SHARPY)
    inserted = False
    if sharpy_path_str not in sys.path:
        sys.path.insert(0, sharpy_path_str)
        inserted = True

    config_path = _VENDOR_SHARPY / "config.ini"
    if not config_path.exists():
        if inserted:
            sys.path.remove(sharpy_path_str)
        pytest.skip("vendor/sharpy/config.ini 不存在")

    old_cwd = Path.cwd()
    os.chdir(_VENDOR_SHARPY)

    try:
        import sharpy.knowledges  # noqa: F401
    except ImportError:
        os.chdir(old_cwd)
        if inserted:
            sys.path.remove(sharpy_path_str)
        pytest.skip("sharpy 未安装（需 uv sync --extra sc2）")

    yield

    os.chdir(old_cwd)
    if inserted:
        sys.path.remove(sharpy_path_str)


@pytest.mark.parametrize(
    "module_path,class_name",
    [
        ("vibecraft.bot.auto_combat.protoss.plans.skytoss_no_ht", "SkytossNoHT"),
        ("vibecraft.bot.auto_combat.protoss.plans.colossus_no_ht", "ColossusNoHT"),
        ("vibecraft.bot.auto_combat.protoss.plans.immortal_archon_no_ht", "ImmortalArchonNoHT"),
    ],
)
def test_no_ht_plan_create_plan_smoke(
    _sharpy_available: None, module_path: str, class_name: str
) -> None:
    """no_ht plan class instantiate + create_plan() 不抛 AttributeError / NameError。"""
    import asyncio
    import importlib

    from sharpy.plans import BuildOrder

    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)

    inst = cls()
    plan = inst.create_plan()
    if asyncio.iscoroutine(plan):
        plan = asyncio.run(plan)

    assert isinstance(plan, BuildOrder), (
        f"{class_name}.create_plan() 应返回 BuildOrder，实际: {type(plan).__name__}"
    )


# =========================================================================
# Archon 来源变体测试（纯逻辑验证，不依赖 sharpy）
# =========================================================================


class TestNoHTVariantsArchonSource:
    """验证三个变体的 Archon 来源逻辑（yaml 层面）。"""

    def test_skytoss_no_ht_archon_from_dt_not_ht(self) -> None:
        doc = _load_yaml("skytoss_no_ht.yaml")
        comp = doc.get("target_composition", {})
        # Archon 必须有，且无 HT（Archon 来源为 DT）
        assert "Archon" in comp
        assert "HighTemplar" not in comp
        assert "DarkTemplar" in comp

    def test_colossus_no_ht_no_archon_needed(self) -> None:
        doc = _load_yaml("colossus_no_ht.yaml")
        comp = doc.get("target_composition", {})
        # ColossusNoHT 不需要 Archon（巨像 AoE 自己覆盖）
        assert "HighTemplar" not in comp
        assert "DarkTemplar" not in comp
        assert "Archon" not in comp

    def test_immortal_archon_no_ht_archon_from_dt(self) -> None:
        doc = _load_yaml("immortal_archon_no_ht.yaml")
        comp = doc.get("target_composition", {})
        assert "Archon" in comp
        assert "HighTemplar" not in comp
        assert "DarkTemplar" in comp
        # DT 数量 >= Archon × 2
        assert comp["DarkTemplar"] >= comp["Archon"] * 2


class TestOriginalVariantsUnchanged:
    """原版三个变体的 yaml 未被修改（Archon 来源仍是 HT）。"""

    def test_skytoss_original_still_has_ht(self) -> None:
        doc = _load_yaml("skytoss.yaml")
        comp = doc.get("target_composition", {})
        assert "HighTemplar" in comp, "原版 Skytoss 应保留 HT（未被改动）"
        structs = doc.get("required_structures", {})
        assert "TemplarArchives" in structs

    def test_colossus_immortal_original_still_has_ht(self) -> None:
        doc = _load_yaml("colossus_immortal.yaml")
        comp = doc.get("target_composition", {})
        assert "HighTemplar" in comp, "原版 ColossusImmortal 应保留 HT"

    def test_immortal_archon_original_still_has_ht(self) -> None:
        doc = _load_yaml("immortal_archon.yaml")
        comp = doc.get("target_composition", {})
        assert "HighTemplar" in comp, "原版 ImmortalArchon 应保留 HT"
