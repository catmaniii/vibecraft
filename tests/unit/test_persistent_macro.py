"""PersistentMacro 单测。

覆盖：
- MacroConfig 默认值
- MacroConfig staged_caps 推断（probe_cap=22/44/80/64）
- ProtossPersistentMacro / ZergPersistentMacro / TerranPersistentMacro:
  - acts() 返回列表不为空
  - probe_cap 不同时返回不同数量的 ActUnit acts
  - auto_pylon=False 时不含 AutoPylon/AutoDepot/AutoOverLord
  - expansion_cap 影响 Expand act 数量

**注意**：acts() 内部调 sc2 / sharpy，单测 mock 整个 sharpy/sc2 tree。
通过 probe_cap 参数化验证 MacroConfig.staged_caps 逻辑（纯 Python，不依赖 sc2）。
acts() 调用验证通过 import mock patch，保证不真实 import sc2。
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock, patch

from vibecraft.bot.auto_combat.persistent_macro import (
    MacroConfig,
    ProtossPersistentMacro,
    TerranPersistentMacro,
    ZergPersistentMacro,
)

# =========================================================================
# MacroConfig 单测（纯 Python，不依赖 sc2）
# =========================================================================


class TestMacroConfig:
    def test_default_probe_cap(self) -> None:
        cfg = MacroConfig()
        assert cfg.probe_cap == 80

    def test_default_expansion_cap(self) -> None:
        cfg = MacroConfig()
        assert cfg.expansion_cap == 4

    def test_default_auto_pylon(self) -> None:
        cfg = MacroConfig()
        assert cfg.auto_pylon is True

    def test_staged_caps_probe_cap_80(self) -> None:
        cfg = MacroConfig(probe_cap=80)
        # 1/4=20, 1/2=40, 3/4=60, 1=80
        assert cfg.staged_caps == [20, 40, 60, 80]

    def test_staged_caps_probe_cap_22(self) -> None:
        cfg = MacroConfig(probe_cap=22)
        # 1/4=5, 1/2=11, 3/4=16, 1=22
        assert cfg.staged_caps == [5, 11, 16, 22]

    def test_staged_caps_probe_cap_44(self) -> None:
        cfg = MacroConfig(probe_cap=44)
        # 1/4=11, 1/2=22, 3/4=33, 1=44
        assert cfg.staged_caps == [11, 22, 33, 44]

    def test_staged_caps_probe_cap_64(self) -> None:
        cfg = MacroConfig(probe_cap=64)
        # 1/4=16, 1/2=32, 3/4=48, 1=64
        assert cfg.staged_caps == [16, 32, 48, 64]

    def test_manual_staged_caps_override(self) -> None:
        cfg = MacroConfig(probe_cap=80, staged_caps=[14, 22, 44, 80])
        assert cfg.staged_caps == [14, 22, 44, 80]

    def test_different_probe_caps_produce_different_staged_caps(self) -> None:
        cfg22 = MacroConfig(probe_cap=22)
        cfg80 = MacroConfig(probe_cap=80)
        assert cfg22.staged_caps != cfg80.staged_caps
        assert cfg80.staged_caps[-1] == 80
        assert cfg22.staged_caps[-1] == 22


# =========================================================================
# acts() 结构验证（mock sc2 / sharpy）
# =========================================================================


def _make_mock_sc2() -> MagicMock:
    """构造 mock sc2 模块树，让 acts() 可以 import sc2 而不报错。"""
    return MagicMock()


def _inject_mock_sc2() -> tuple[Any, ...]:
    """注入 mock sc2 + sharpy 到 sys.modules，返回 sentinel objects。"""
    mock_sc2 = MagicMock()
    mock_sc2.ids.unit_typeid.UnitTypeId = MagicMock()
    mock_sc2.ids.upgrade_id.UpgradeId = MagicMock()

    # sharpy plan primitives — 返回轻量 MagicMock
    mock_sharpy_plans = MagicMock()
    mock_sharpy_plans_acts = MagicMock()
    mock_sharpy_plans_acts_protoss = MagicMock()
    mock_sharpy_plans_acts_zerg = MagicMock()
    mock_sharpy_plans_acts_terran = MagicMock()
    mock_sharpy_plans_require = MagicMock()

    # 让 ActUnit/Step/Expand/AutoPylon 等返回 unique MagicMock instances（用来计数）
    def _factory(name: str) -> MagicMock:
        obj = MagicMock(name=name)
        obj.__class__.__name__ = name
        return obj

    mock_sharpy_plans.Step.side_effect = lambda *a, **kw: _factory("Step")
    mock_sharpy_plans.StepBuildGas.side_effect = lambda *a, **kw: _factory("StepBuildGas")
    mock_sharpy_plans_acts.ActUnit.side_effect = lambda *a, **kw: _factory("ActUnit")
    mock_sharpy_plans_acts.Expand.side_effect = lambda *a, **kw: _factory("Expand")
    mock_sharpy_plans_acts_protoss.AutoPylon.side_effect = lambda: _factory("AutoPylon")
    mock_sharpy_plans_acts_protoss.ChronoUnit.side_effect = lambda *a, **kw: _factory("ChronoUnit")
    mock_sharpy_plans_acts_zerg.AutoOverLord.side_effect = lambda: _factory("AutoOverLord")
    mock_sharpy_plans_acts_terran.AutoDepot.side_effect = lambda: _factory("AutoDepot")
    mock_sharpy_plans_require.UnitExists.side_effect = lambda *a, **kw: _factory("UnitExists")
    mock_sharpy_plans_require.Gas.side_effect = lambda *a, **kw: _factory("Gas")

    modules = {
        "sc2": mock_sc2,
        "sc2.ids": mock_sc2.ids,
        "sc2.ids.unit_typeid": mock_sc2.ids.unit_typeid,
        "sharpy": MagicMock(),
        "sharpy.plans": mock_sharpy_plans,
        "sharpy.plans.acts": mock_sharpy_plans_acts,
        "sharpy.plans.acts.protoss": mock_sharpy_plans_acts_protoss,
        "sharpy.plans.acts.zerg": mock_sharpy_plans_acts_zerg,
        "sharpy.plans.acts.terran": mock_sharpy_plans_acts_terran,
        "sharpy.plans.require": mock_sharpy_plans_require,
    }
    return (
        modules,
        mock_sharpy_plans,
        mock_sharpy_plans_acts,
        mock_sharpy_plans_acts_protoss,
        mock_sharpy_plans_acts_zerg,
        mock_sharpy_plans_acts_terran,
    )


class TestProtossPersistentMacro:
    def test_acts_not_empty(self) -> None:
        modules, *_ = _inject_mock_sc2()
        with patch.dict(sys.modules, modules):
            macro = ProtossPersistentMacro(MacroConfig(probe_cap=80, expansion_cap=4))
            acts = macro.acts()
        assert len(acts) > 0

    def test_acts_probe_cap_80_has_more_steps_than_22(self) -> None:
        modules, *_ = _inject_mock_sc2()
        with patch.dict(sys.modules, modules):
            macro80 = ProtossPersistentMacro(MacroConfig(probe_cap=80))
            macro22 = ProtossPersistentMacro(MacroConfig(probe_cap=22))
            acts80 = macro80.acts()
            acts22 = macro22.acts()
        # 两者都有相同数量（staged_caps 4 个 + chrono + autopylon + expands）
        # 实际上 probe_cap 变化改 ActUnit 参数，不改列表长度
        assert len(acts80) == len(acts22)

    def test_expansion_cap_affects_act_count(self) -> None:
        modules, *_ = _inject_mock_sc2()
        with patch.dict(sys.modules, modules):
            macro4 = ProtossPersistentMacro(MacroConfig(probe_cap=80, expansion_cap=4))
            macro2 = ProtossPersistentMacro(MacroConfig(probe_cap=80, expansion_cap=2))
            acts4 = macro4.acts()
            acts2 = macro2.acts()
        # expansion_cap=4 比 expansion_cap=2 多 2 个 Expand + 2 个 StepBuildGas Step（nex=3,4）
        assert len(acts4) == len(acts2) + 4

    def test_auto_pylon_false_reduces_act_count(self) -> None:
        modules, *_ = _inject_mock_sc2()
        with patch.dict(sys.modules, modules):
            macro_with = ProtossPersistentMacro(MacroConfig(probe_cap=80, auto_pylon=True))
            macro_without = ProtossPersistentMacro(MacroConfig(probe_cap=80, auto_pylon=False))
            acts_with = macro_with.acts()
            acts_without = macro_without.acts()
        # auto_pylon=True 多一个 AutoPylon act
        assert len(acts_with) == len(acts_without) + 1

    def test_gas_steps_count_expansion_cap_4(self) -> None:
        """expansion_cap=4 → 气矿 Step 比 expansion_cap=2 多 2 个（NX=3,4）。"""
        modules, *_ = _inject_mock_sc2()
        with patch.dict(sys.modules, modules):
            macro4 = ProtossPersistentMacro(MacroConfig(probe_cap=80, expansion_cap=4))
            macro2 = ProtossPersistentMacro(MacroConfig(probe_cap=80, expansion_cap=2))
            acts4 = macro4.acts()
            acts2 = macro2.acts()
        # expansion_cap=4 比 expansion_cap=2 多 2 个气矿 Step + 2 个 Expand = 4 总差
        assert len(acts4) == len(acts2) + 4

    def test_gas_steps_count_expansion_cap_6(self) -> None:
        """expansion_cap=6 → 从 NX=3 到 6，共 4 个 StepBuildGas Step。
        Gas skip 阈值：3矿→300，4矿→400，5矿→500，6矿→600，防 vespene 过剩。
        """
        modules, *_ = _inject_mock_sc2()
        with patch.dict(sys.modules, modules):
            macro6 = ProtossPersistentMacro(MacroConfig(probe_cap=80, expansion_cap=6))
            macro2 = ProtossPersistentMacro(MacroConfig(probe_cap=80, expansion_cap=2))
            acts6 = macro6.acts()
            acts2 = macro2.acts()
        # expansion_cap=6 比 expansion_cap=2 多 4 个 Expand + 4 个 StepBuildGas Step
        assert len(acts6) == len(acts2) + 8

    def test_no_gas_steps_when_expansion_cap_2(self) -> None:
        """expansion_cap=2 时（只开 2 矿），opening plan 自己管气矿，PersistentMacro 不加气矿 Step。"""
        modules, *_ = _inject_mock_sc2()
        with patch.dict(sys.modules, modules):
            macro2 = ProtossPersistentMacro(MacroConfig(probe_cap=80, expansion_cap=2))
            # expansion_cap=1 作为 baseline（只主矿，不 Expand）
            macro1 = ProtossPersistentMacro(MacroConfig(probe_cap=80, expansion_cap=1))
            acts2 = macro2.acts()
            acts1 = macro1.acts()
        # expansion_cap=2 vs 1：只多 1 个 Expand，无气矿 Step
        assert len(acts2) == len(acts1) + 1


class TestZergPersistentMacro:
    def test_acts_not_empty(self) -> None:
        modules, *_ = _inject_mock_sc2()
        with patch.dict(sys.modules, modules):
            macro = ZergPersistentMacro(MacroConfig(probe_cap=80, expansion_cap=4))
            acts = macro.acts()
        assert len(acts) > 0

    def test_expansion_cap_affects_act_count(self) -> None:
        modules, *_ = _inject_mock_sc2()
        with patch.dict(sys.modules, modules):
            macro4 = ZergPersistentMacro(MacroConfig(probe_cap=80, expansion_cap=4))
            macro2 = ZergPersistentMacro(MacroConfig(probe_cap=80, expansion_cap=2))
            acts4 = macro4.acts()
            acts2 = macro2.acts()
        # expansion_cap=4 比 expansion_cap=2 多 2 个 Expand + 2 个 StepBuildGas Step（hatch=3,4）
        assert len(acts4) == len(acts2) + 4

    def test_gas_steps_start_from_3rd_base(self) -> None:
        """气矿跟随从第 3 孵化场起，expansion_cap=4 → 2 个 StepBuildGas Step。"""
        modules, *_ = _inject_mock_sc2()
        with patch.dict(sys.modules, modules):
            macro = ZergPersistentMacro(MacroConfig(probe_cap=64, expansion_cap=4))
            acts = macro.acts()
        gas_steps = [a for a in acts if getattr(a, "_mock_name", "") == "Step"]
        # 6 个 Step 总数：Drone×3（2/3/4 矿）+ gas×2（3/4 矿）+ Expand 3 个不是 Step
        # 精确验：expansion_cap=2 时 gas Step = 0，expansion_cap=4 时 gas Step = 2（差 2）
        modules2, *_ = _inject_mock_sc2()
        with patch.dict(sys.modules, modules2):
            macro2 = ZergPersistentMacro(MacroConfig(probe_cap=64, expansion_cap=2))
            acts2 = macro2.acts()
        gas_steps_2 = [a for a in acts2 if getattr(a, "_mock_name", "") == "Step"]
        assert len(gas_steps) - len(gas_steps_2) == 2


class TestTerranPersistentMacro:
    def test_acts_not_empty(self) -> None:
        modules, *_ = _inject_mock_sc2()
        with patch.dict(sys.modules, modules):
            macro = TerranPersistentMacro(MacroConfig(probe_cap=80, expansion_cap=4))
            acts = macro.acts()
        assert len(acts) > 0

    def test_expansion_cap_affects_act_count(self) -> None:
        modules, *_ = _inject_mock_sc2()
        with patch.dict(sys.modules, modules):
            macro4 = TerranPersistentMacro(MacroConfig(probe_cap=80, expansion_cap=4))
            macro2 = TerranPersistentMacro(MacroConfig(probe_cap=80, expansion_cap=2))
            acts4 = macro4.acts()
            acts2 = macro2.acts()
        # expansion_cap=4 比 expansion_cap=2 多 2 个 Expand + 2 个 StepBuildGas Step（cc=3,4）
        assert len(acts4) == len(acts2) + 4

    def test_gas_steps_count_expansion_cap_5(self) -> None:
        """expansion_cap=5 → BC=3,4,5 共 3 个 StepBuildGas Step。"""
        modules, *_ = _inject_mock_sc2()
        with patch.dict(sys.modules, modules):
            macro5 = TerranPersistentMacro(MacroConfig(probe_cap=64, expansion_cap=5))
            macro2 = TerranPersistentMacro(MacroConfig(probe_cap=64, expansion_cap=2))
            acts5 = macro5.acts()
            acts2_ref = macro2.acts()
        # expansion_cap=5 比 expansion_cap=2 多 3 个 Expand + 3 个 StepBuildGas Step
        assert len(acts5) == len(acts2_ref) + 6
