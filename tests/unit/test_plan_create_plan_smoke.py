"""Smoke 测试：每个 vibecraft plan class 必须能 instantiate + create_plan() 成功。

**为什么需要这条测试**：
2026-05-18 实战 bug —— Robo1GateImmortal 因 `UnitTypeId.BELON`（应为 PYLON）拼错
被 sharpy KnowledgeBot.create_plan() 抛 AttributeError，bot.py 的
`_make_fallback_plan()` 兜底成 `[ActUnit(PROBE, NEXUS, 14)]`，玩家看到的现象是
"bot 攒了 600+ minerals 不修二基地也不修 BG"。

只验证 import 不够（class 定义层面的拼错抓不到），必须实际调 `create_plan()`
让 sharpy 解析所有 `Step(UnitReady(...))` / `GridBuilding(UnitTypeId.X)` 等。

测试策略：
1. 把 vendor/sharpy 加入 sys.path（生产环境也这么干）
2. 临时塞一个 fake config.ini（sharpy KnowledgeBot.__init__() 要求）
3. instantiate plan class + await create_plan()
4. 期望返回 BuildOrder 实例，无 AttributeError / NameError

**注意**：此测试需要 sharpy 实际可 import（`uv sync --extra sc2`），CI 若无
sc2 extras 会被自动 skip。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_VENDOR_SHARPY = _PROJECT_ROOT / "vendor" / "sharpy"


@pytest.fixture(scope="module", autouse=True)
def _setup_sharpy_path():
    """让 vendor/sharpy 可 import + 提供 fake config.ini。"""
    sharpy_path_str = str(_VENDOR_SHARPY)
    inserted = False
    if sharpy_path_str not in sys.path:
        sys.path.insert(0, sharpy_path_str)
        inserted = True

    # **绝不**碰 vendor/sharpy/config.ini —— 那是 bot 真实运行用的，
    # fake 写入风险：若 cleanup 失败/被打断，会留个 fake config 让 bot 起不来。
    # 若 config.ini 不存在，直接 skip。
    config_path = _VENDOR_SHARPY / "config.ini"
    if not config_path.exists():
        if inserted:
            sys.path.remove(sharpy_path_str)
        pytest.skip(
            "vendor/sharpy/config.ini 不存在 → bot 跑不起来。"
            "从 git checkout 恢复：git checkout HEAD -- vendor/sharpy/config.ini"
        )

    # sharpy KnowledgeBot.__init__() 用 cwd 找 config.ini
    old_cwd = Path.cwd()
    os.chdir(_VENDOR_SHARPY)

    try:
        import sharpy.knowledges  # noqa: F401 — 仅验 import OK
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
        ("vibecraft.bot.auto_combat.protoss.plans.gate4_pressure", "Gate4Pressure"),
        ("vibecraft.bot.auto_combat.protoss.plans.robo_1gate", "Robo1GateImmortal"),
        ("vibecraft.bot.auto_combat.protoss.plans.skytoss", "Skytoss"),
        ("vibecraft.bot.auto_combat.protoss.plans.skytoss_no_ht", "SkytossNoHT"),
        ("vibecraft.bot.auto_combat.protoss.plans.colossus_immortal", "ColossusImmortal"),
        ("vibecraft.bot.auto_combat.protoss.plans.colossus_no_ht", "ColossusNoHT"),
        ("vibecraft.bot.auto_combat.protoss.plans.immortal_archon", "ImmortalArchon"),
        ("vibecraft.bot.auto_combat.protoss.plans.immortal_archon_no_ht", "ImmortalArchonNoHT"),
        ("vibecraft.bot.auto_combat.protoss.plans.blink_harass", "BlinkHarass"),
        ("vibecraft.bot.auto_combat.protoss.plans.phoenix_control", "PhoenixControl"),
        ("vibecraft.bot.auto_combat.protoss.plans.iac_2base", "IacTwoBase"),
        ("vibecraft.bot.auto_combat.protoss.plans.dt_rush", "DtRush"),
        ("vibecraft.bot.auto_combat.protoss.plans.phoenix_2base", "Phoenix2Base"),
        ("vibecraft.bot.auto_combat.protoss.plans.blink_stalker", "BlinkStalker"),
        ("vibecraft.bot.auto_combat.protoss.plans.cannon_rush", "CannonRush"),
        ("vibecraft.bot.auto_combat.terran.plans.bc_late", "BcLate"),
        ("vibecraft.bot.auto_combat.terran.plans.mech", "Mech"),
        ("vibecraft.bot.auto_combat.terran.plans.bio_max", "BioMax"),
        ("vibecraft.bot.auto_combat.terran.plans.liberator", "LiberatorSky"),
        ("vibecraft.bot.auto_combat.terran.plans.ghost_nuke", "GhostNuke"),
        ("vibecraft.bot.auto_combat.zerg.plans.ultralisk", "Ultralisk"),
        ("vibecraft.bot.auto_combat.zerg.plans.lurker_hydra", "LurkerHydra"),
        ("vibecraft.bot.auto_combat.zerg.plans.muta_ling_bane", "MutaLingBane"),
        ("vibecraft.bot.auto_combat.zerg.plans.roach_hydra_viper", "RoachHydraViper"),
        ("vibecraft.bot.auto_combat.zerg.plans.ling_bane", "LingBane"),
        ("vibecraft.bot.auto_combat.zerg.plans.roach_hydra", "RoachHydra"),
        ("vibecraft.bot.auto_combat.zerg.plans.roach_ravager", "RoachRavager"),
        ("vibecraft.bot.auto_combat.zerg.plans.nydus", "NydusRush"),
        ("vibecraft.bot.auto_combat.zerg.plans.roach_allin", "RoachAllin"),
    ],
)
def test_plan_create_plan_smoke(module_path: str, class_name: str) -> None:
    """plan class instantiate + create_plan() 不抛 AttributeError / NameError 等。"""
    import importlib

    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)

    inst = cls()
    plan = inst.create_plan()
    if asyncio.iscoroutine(plan):
        # asyncio.run 自带 loop，不依赖全局 current loop（get_event_loop 已废弃，
        # 且会被前面跑过 asyncio.run 的测试污染成 RuntimeError）。
        plan = asyncio.run(plan)

    # 验证返回的是 BuildOrder（或其子类）
    from sharpy.plans import BuildOrder

    assert isinstance(plan, BuildOrder), (
        f"{class_name}.create_plan() should return BuildOrder, got {type(plan).__name__}"
    )
