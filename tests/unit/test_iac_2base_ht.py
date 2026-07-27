"""IacTwoBase 电兵叉球一波单测（HT 路线，2026-05-29 改造 v2）。

测试目标：
  1. build 序列含 VT(TEMPLARARCHIVE) 而不是 VD(DARKSHRINE)
  2. build 序列含 HighTemplar 而不是 DarkTemplar（大量 unit）
  3. build 序列含 PSISTORMTECH 升级研究
  4. _ready_to_pressure：Charge + Storm + 2 Archon 满足 → True
  5. _ready_to_pressure：Charge 未完成时不触发
  6. _ready_to_pressure：Storm 未完成时不触发
  7. _ready_to_pressure：Archon < 2 时不触发（army_supply 也不够）
  8. _ready_to_pressure：army_supply >= 30 时 time 不够也可触发
  9. _ready_to_pressure：time >= 7:00 兜底触发（army_supply 不够）
 10. _ready_to_pressure：time 刚好 7:00 边界
 11. EmitOpeningCompleteAct 存在（opening_completed 触发不被破坏）
 12. Archon 合来源是 HIGHTEMPLAR 而非 DARKTEMPLAR
 13. plan 含 BF +1 攻 chrono step（改造 1：两 BF 升级 chrono 覆盖）
 14. plan 含 BF +1 防 chrono step（改造 1）
 15. plan 使用 ArchonAfterStorm 而非原始 Archon([HIGHTEMPLAR])（改造 2）
 16. ArchonAfterStorm：energy < threshold(75) 时满足能量条件
 17. ArchonAfterStorm：fresh 电兵（energy=50）在家（无敌人）→ 不合（战斗保护）
 18. ArchonAfterStorm：只 1 个低能电兵时不触发（需要至少 2 个）
 19. ArchonAfterStorm：1 高能 + 1 低能电兵 → 只低能 1 个，不够 2 → 不合
 20. ArchonAfterStorm：energy_threshold=0 等价于永不合（极端 case）
 21. Charge 后置：step 含 UnitExists(ZEALOT, 8) 条件（修复 1 防回归）
 22. 哨兵后置：step 含 UnitExists(HIGHTEMPLAR, 6) 条件（修复 2 + 追加 2 防回归）
 23. 哨兵数量 ≤ 2（修复 2 防过多哨兵消耗气矿）
 24. 追猎数量 >= 8（v2 改造：kite 主力，从 1 升到 10）
 25. 电兵数量 == 6（追加 2：6 HT = 3 Archon，叉球一波核心输出）
 26. 叉子数量 >= 16 且 <= 32（v2 改造：肉盾，减少到 24 避免一发 Storm 全清）
 27. HT chrono skip 上限同步升到 6（追加 2 防 chrono 卡在旧 4）
 28. ArchonAfterStorm：战斗中（有敌方战斗单位）energy<75 → 合（核心 case）
 29. ArchonAfterStorm：不在战场（无敌人）energy<75 → 不合（require_combat 保护）
 30. ArchonAfterStorm：energy=74 + 有敌人 → 合（74<75 刚好满足）
 31. ArchonAfterStorm：energy=75 + 有敌人 → 不合（75<75=False）
 32. ArchonAfterStorm：周围只有农民 → 不合（农民不算战斗单位）
 33. ArchonAfterStorm：require_combat=False 时不看战场，只看 energy
 34. iac_2base 调用 ArchonAfterStorm() 默认参数（threshold=75 默认值）
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------- vendor sharpy path ----------
_VENDOR_SHARPY = Path(__file__).resolve().parents[2] / "vendor" / "sharpy"
_SRC_ROOT = Path(__file__).resolve().parents[2] / "src"

if str(_VENDOR_SHARPY) not in sys.path:
    sys.path.insert(0, str(_VENDOR_SHARPY))

pytestmark = pytest.mark.skipif(
    not (_VENDOR_SHARPY / "sharpy" / "plans" / "acts" / "__init__.py").exists(),
    reason="vendor/sharpy not available",
)

pytest.importorskip("sc2.ids.unit_typeid")
pytest.importorskip("sc2.ids.upgrade_id")
pytest.importorskip("sharpy.plans.acts")

# 计划源码路径（从测试文件位置推算）
_PLAN_SRC = _SRC_ROOT / "vibecraft" / "bot" / "auto_combat" / "protoss" / "plans" / "iac_2base.py"


# ---------- helpers ----------


def _make_ai(
    charge_done: bool = True,
    storm_done: bool = True,
    ht_count: int = 4,
    stalker_count: int = 6,
    archon_count: int = 0,
    time: float = 420.0,  # 7:00 default（兜底触发）
    army_supply: int = 0,
) -> MagicMock:
    """构造一个满足（或不满足）出门条件的 mock ai。

    2026-05-30:出门条件从"2 Archon ready"改为"4 电兵 + 6 追猎 ready"
    (Archon 战场合,家里 0 Archon)。helper 默认 ht=4 stalker=6 archon=0,满足条件。
    """
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.ids.upgrade_id import UpgradeId

    ai = MagicMock()

    upgrades_set = set()
    if charge_done:
        upgrades_set.add(UpgradeId.CHARGE)
    if storm_done:
        upgrades_set.add(UpgradeId.PSISTORMTECH)
    ai.state.upgrades = upgrades_set
    ai.already_pending_upgrade = lambda _upg: 0.0  # 所有升级都不 pending

    # 每个 unit_type 一个 mock,按 ht_count / stalker_count / archon_count 返回
    type_counts = {
        UnitTypeId.HIGHTEMPLAR: ht_count,
        UnitTypeId.STALKER: stalker_count,
        UnitTypeId.ARCHON: archon_count,
    }

    def _units(unit_type):
        if unit_type in type_counts:
            coll = MagicMock()
            coll.ready = MagicMock(amount=type_counts[unit_type])
            return coll
        # 其他单位产 army_supply / 2 个 ZEALOT（supply 2 each）来模拟兵力
        zealot_count = army_supply // 2
        coll = MagicMock()
        coll.ready = MagicMock(amount=zealot_count if unit_type == UnitTypeId.ZEALOT else 0)
        return coll

    ai.units = _units
    ai.time = time
    return ai


# ---------- 1. build 序列含 VT 不含 VD ----------


def test_build_plan_contains_templararchive_not_darkshrine():
    """plan 序列必须有 TEMPLARARCHIVE，不应含 DARKSHRINE。"""
    assert _PLAN_SRC.exists(), f"plan 文件不存在: {_PLAN_SRC}"
    src = _PLAN_SRC.read_text(encoding="utf-8")
    assert "TEMPLARARCHIVE" in src, "build 序列应含 TEMPLARARCHIVE (VT)"
    assert "DARKSHRINE" not in src, "build 序列不应含 DARKSHRINE (VD) — HT 路线已不用 VD"


# ---------- 2. build 序列含 HT 不含大量 DT ----------


def test_build_plan_contains_hightemplar_not_darktemplar():
    """plan 序列含 HIGHTEMPLAR 出兵，不含 DarkTemplar 出兵 target。"""
    src = _PLAN_SRC.read_text(encoding="utf-8")
    assert "HIGHTEMPLAR" in src, "build 序列应含 HIGHTEMPLAR"
    # ProtossUnit(DARKTEMPLAR, ...) 不应再出现
    assert "ProtossUnit(UnitTypeId.DARKTEMPLAR" not in src, "不应再出 ProtossUnit(DARKTEMPLAR,...)"


# ---------- 3. build 序列含 PSISTORMTECH 研究 ----------


def test_build_plan_contains_psistormtech():
    src = _PLAN_SRC.read_text(encoding="utf-8")
    assert "PSISTORMTECH" in src, "build 序列应含 PSISTORMTECH 升级"


# ---------- 4. Archon 合来源是 HIGHTEMPLAR（ArchonAfterStorm，改造 2）----------


def test_archon_merge_source_is_hightemplar():
    src = _PLAN_SRC.read_text(encoding="utf-8")
    # 改造 2：用 ArchonAfterStorm 替代 Archon([HIGHTEMPLAR])，plan 不应出现旧写法
    assert "ArchonAfterStorm" in src, "改造 2 后应用 ArchonAfterStorm（能量门控合 HT）"
    # Archon([UnitTypeId.DARKTEMPLAR]) 不应出现
    assert "Archon([UnitTypeId.DARKTEMPLAR])" not in src, "Archon 合源不应为 DARKTEMPLAR"
    # 旧 Archon([HIGHTEMPLAR]) 直接调用不应再出现
    assert "Archon([UnitTypeId.HIGHTEMPLAR])" not in src, (
        "改造 2 后不应再用 Archon([HIGHTEMPLAR])，应用 ArchonAfterStorm"
    )


# ---------- 5. _ready_to_pressure：time >= 7:00 兜底触发 ----------


def test_ready_to_pressure_time_fallback_7min():
    from vibecraft.bot.auto_combat.protoss.plans.iac_2base import IacTwoBase

    # Charge + Storm + 2 Archon 满足，army_supply < 30，time = 7:00 → 时间兜底
    ai = _make_ai(
        charge_done=True, storm_done=True, ht_count=4, stalker_count=6, time=420.0, army_supply=0
    )
    assert IacTwoBase._ready_to_pressure(ai) is True


# ---------- 6. Charge 未完成 → False ----------


def test_ready_to_pressure_charge_missing():
    from vibecraft.bot.auto_combat.protoss.plans.iac_2base import IacTwoBase

    ai = _make_ai(charge_done=False, storm_done=True, ht_count=4, stalker_count=6, time=500.0)
    assert IacTwoBase._ready_to_pressure(ai) is False


# ---------- 7. Storm 未完成 → False ----------


def test_ready_to_pressure_storm_missing():
    from vibecraft.bot.auto_combat.protoss.plans.iac_2base import IacTwoBase

    ai = _make_ai(charge_done=True, storm_done=False, ht_count=4, stalker_count=6, time=500.0)
    assert IacTwoBase._ready_to_pressure(ai) is False


# ---------- 8. Archon < 2，army_supply 也不够 → False ----------


def test_ready_to_pressure_ht_too_few():
    """电兵 < 4 → False(改造 2026-05-30:HT 替代 Archon 检查,白球战场合不在家)"""
    from vibecraft.bot.auto_combat.protoss.plans.iac_2base import IacTwoBase

    # 电兵 < 4 且 army_supply < 30，time < 7:00 → False
    ai = _make_ai(
        charge_done=True,
        storm_done=True,
        ht_count=3,
        stalker_count=6,
        time=370.0,
        army_supply=10,
    )
    assert IacTwoBase._ready_to_pressure(ai) is False


def test_ready_to_pressure_stalker_too_few():
    """追猎 < 6 → False(改造 2026-05-30:加追猎 6 条件)"""
    from vibecraft.bot.auto_combat.protoss.plans.iac_2base import IacTwoBase

    # 追猎 < 6 且 army_supply < 30,time < 7:00 → False
    ai = _make_ai(
        charge_done=True,
        storm_done=True,
        ht_count=4,
        stalker_count=5,
        time=370.0,
        army_supply=10,
    )
    assert IacTwoBase._ready_to_pressure(ai) is False


# ---------- 9. army_supply >= 30，时间不够也触发 ----------


def test_ready_to_pressure_army_supply_override_time():
    from vibecraft.bot.auto_combat.protoss.plans.iac_2base import IacTwoBase

    # time = 5:00（不够 7:00）但 army_supply = 30（2 archon supply=8 + zealots supply=22）
    ai = _make_ai(
        charge_done=True, storm_done=True, ht_count=4, stalker_count=6, time=300.0, army_supply=30
    )
    assert IacTwoBase._ready_to_pressure(ai) is True


# ---------- 10. time 刚好 7:00 边界 ----------


def test_ready_to_pressure_time_exactly_420():
    from vibecraft.bot.auto_combat.protoss.plans.iac_2base import IacTwoBase

    ai = _make_ai(
        charge_done=True, storm_done=True, ht_count=4, stalker_count=6, time=60 * 7, army_supply=0
    )
    assert IacTwoBase._ready_to_pressure(ai) is True


# ---------- 11. time 6:59 + army_supply < 30 → False ----------


def test_ready_to_pressure_just_before_7min():
    from vibecraft.bot.auto_combat.protoss.plans.iac_2base import IacTwoBase

    # time = 6:59,army_supply < 30,电兵 < 4 → False(改造 2026-05-30:HT/Stalker 替代 archon)
    ai = _make_ai(
        charge_done=True,
        storm_done=True,
        ht_count=3,
        stalker_count=6,
        time=419.0,
        army_supply=10,
    )
    assert IacTwoBase._ready_to_pressure(ai) is False


# ---------- 12. EmitOpeningCompleteAct 存在于 plan 源码 ----------


def test_emit_opening_complete_act_present():
    src = _PLAN_SRC.read_text(encoding="utf-8")
    assert "EmitOpeningCompleteAct" in src, (
        "EmitOpeningCompleteAct 必须保留（opening_completed 触发）"
    )


# ---------- 13/14. BF 攻防升级 chrono 步骤存在（改造 1）----------


def test_plan_contains_forge_weapon_chrono():
    """plan 源码必须含 BF +1 攻升级 chrono step（改造 1：两 BF 升级 chrono 覆盖）。"""
    src = _PLAN_SRC.read_text(encoding="utf-8")
    assert "FORGERESEARCH_PROTOSSGROUNDWEAPONSLEVEL1" in src, (
        "plan 应含 ChronoTech(FORGERESEARCH_PROTOSSGROUNDWEAPONSLEVEL1, FORGE)"
    )
    # 确认 chrono step 在 forge ready gate 之后（要求 FORGE ready）
    assert "UnitReady(UnitTypeId.FORGE, 1)" in src, (
        "BF +1 攻 chrono 应在 UnitReady(FORGE, 1) gate 之后"
    )


def test_plan_contains_forge_armor_chrono():
    """plan 源码必须含 BF +1 防升级 chrono step（改造 1）。"""
    src = _PLAN_SRC.read_text(encoding="utf-8")
    assert "FORGERESEARCH_PROTOSSGROUNDARMORLEVEL1" in src, (
        "plan 应含 ChronoTech(FORGERESEARCH_PROTOSSGROUNDARMORLEVEL1, FORGE)"
    )
    assert "UnitReady(UnitTypeId.FORGE, 2)" in src, (
        "BF +1 防 chrono 应在 UnitReady(FORGE, 2) gate 之后"
    )


# ---------- 15. ArchonAfterStorm 替代 Archon([HIGHTEMPLAR])（改造 2）----------


def test_plan_uses_archon_after_storm_not_raw_archon():
    """plan 应使用 ArchonAfterStorm 而非 sharpy 原始 Archon([HIGHTEMPLAR])。"""
    src = _PLAN_SRC.read_text(encoding="utf-8")
    assert "ArchonAfterStorm" in src, "plan 应使用 ArchonAfterStorm（能量门控合球）"
    # 原始 Archon([UnitTypeId.HIGHTEMPLAR]) 不应再出现（已被 ArchonAfterStorm 替代）
    assert "Archon([UnitTypeId.HIGHTEMPLAR])" not in src, (
        "plan 不应再用 Archon([HIGHTEMPLAR])，改造 2 用 ArchonAfterStorm 替代"
    )


# ---------- 16-20. ArchonAfterStorm 单元测试（energy threshold 行为）----------
#
# ArchonAfterStorm 继承 sharpy ActBase，测试时用 __new__ 绕开 sharpy 依赖，
# 手动塞 mock 字段（和 test_phoenix_harass.py 相同模式）。


def _make_ht(energy: float, tag: int = 1, is_idle: bool = True) -> MagicMock:
    """构造一个 mock HT Unit。"""
    from unittest.mock import MagicMock

    ht = MagicMock()
    ht.energy = energy
    ht.tag = tag
    ht.is_idle = is_idle
    return ht


def _make_ht_collection(*hts: MagicMock) -> MagicMock:
    """构造一个 mock Units collection。"""
    from unittest.mock import MagicMock

    coll = MagicMock()
    coll.amount = len(hts)
    coll.__iter__ = MagicMock(return_value=iter(hts))
    coll.__getitem__ = MagicMock(side_effect=lambda i: hts[i])

    def _tags_not_in(tags):
        remaining = [h for h in hts if h.tag not in tags]
        return _make_ht_collection(*remaining)

    def _filter(fn):
        matched = [h for h in hts if fn(h)]
        return _make_ht_collection(*matched)

    coll.tags_not_in = MagicMock(side_effect=_tags_not_in)
    coll.filter = MagicMock(side_effect=_filter)
    coll.closest_to = MagicMock(side_effect=lambda u: next(h for h in hts if h.tag != u.tag))
    return coll


def _make_enemy_units(
    count: int = 0,
    include_workers: bool = False,
) -> MagicMock:
    """构造一个 mock enemy units collection，用于战斗判定。

    Args:
        count: 敌方战斗单位数量（不含农民）
        include_workers: True 时只包含农民（无战斗单位）
    """
    from unittest.mock import MagicMock

    from sc2.ids.unit_typeid import UnitTypeId

    _WORKER_TYPES = frozenset([UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE])

    if include_workers:
        # 只有农民，战斗单位 = 0
        workers = [MagicMock(type_id=UnitTypeId.PROBE) for _ in range(3)]
        units_list = workers
    else:
        # count 个非农民战斗单位
        units_list = [MagicMock(type_id=UnitTypeId.STALKER) for _ in range(count)]

    def _filter(fn):
        matched = [u for u in units_list if fn(u)]
        result = MagicMock()
        result.amount = len(matched)
        return result

    coll = MagicMock()
    coll.filter = MagicMock(side_effect=_filter)
    return coll


def _make_archon_act(
    threshold: int = 100,
    require_combat: bool = True,
    combat_radius: float = 15.0,
    enemy_combat_count: int = 0,
    enemy_only_workers: bool = False,
    max_ht_count: int = 4,
) -> Any:
    """构造 ArchonAfterStorm，绕开 sharpy ActBase.__init__。

    Args:
        threshold: energy_threshold（默认 75 = 心灵风暴费用）
        require_combat: 是否开启战斗判定（默认 True）
        combat_radius: 战斗判定半径
        enemy_combat_count: 模拟周围敌方战斗单位数（0=无敌 → 在家）
        enemy_only_workers: True 时周围只有农民（不算战斗）
    """
    import sys
    from pathlib import Path
    from unittest.mock import MagicMock

    src_root = Path(__file__).resolve().parents[2] / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))

    from vibecraft.bot.auto_combat.protoss.plans.archon_after_storm import ArchonAfterStorm

    act = ArchonAfterStorm.__new__(ArchonAfterStorm)
    act.energy_threshold = threshold
    act.require_combat = require_combat
    act.combat_radius = combat_radius
    act.max_ht_count = max_ht_count
    act.already_merging_tags = []
    # mock sharpy internals
    act.cache = MagicMock()
    act.roles = MagicMock()
    act.knowledge = MagicMock()

    # mock ai.all_enemy_units.closer_than 返回 mock enemy collection
    from unittest.mock import AsyncMock

    enemy_coll = _make_enemy_units(count=enemy_combat_count, include_workers=enemy_only_workers)
    ai = MagicMock()
    ai.all_enemy_units.closer_than = MagicMock(return_value=enemy_coll)
    ai._client._execute = AsyncMock()  # execute() 里 await 下发 MORPH_ARCHON
    act.ai = ai
    return act


def test_archon_after_storm_merges_low_energy_hts():
    """energy < 75（低于心灵风暴费用）的电兵满足能量条件（_can_merge）。"""
    act = _make_archon_act(threshold=75)
    ht1 = _make_ht(energy=10, tag=10)  # 能量 10 < 75 → 放过 Storm 后的电兵 → 候选
    ht2 = _make_ht(energy=50, tag=11)  # 能量 50 < 75 → fresh 电兵，能量条件满足 ✓
    assert act._can_merge(ht1) is True
    assert act._can_merge(ht2) is True


def test_archon_after_storm_skips_fresh_hts():
    """fresh 电兵（energy=50）在家（无敌人）时不合（require_combat 保护）。

    threshold=75：50 < 75 = True（能量条件满足），
    但 require_combat=True + 无敌人 → _in_combat=False → 不合。
    防止在家待命的 fresh 电兵被误合。
    """
    # 无敌方战斗单位（enemy_combat_count=0 → 在家）
    act = _make_archon_act(threshold=75, require_combat=True, enemy_combat_count=0)
    ht_fresh = _make_ht(energy=50, tag=20)  # fresh 电兵

    # 能量条件满足（50 < 75）
    assert act._can_merge(ht_fresh) is True
    # 但战斗条件不满足（家里无敌）
    assert act._in_combat(ht_fresh) is False


def test_archon_after_storm_requires_two_candidates():
    """只有 1 个低能电兵时不合（需要 2 个）。"""
    act = _make_archon_act(threshold=75)
    ht1 = _make_ht(energy=20, tag=30)  # 低能（< 75）
    ht2 = _make_ht(energy=150, tag=31)  # 高能 → 被 filter 排除

    # filter(_can_merge) 后只剩 1 个 → 不合
    low_candidates = [h for h in [ht1, ht2] if act._can_merge(h)]
    assert len(low_candidates) == 1  # 不够 2，不应触发


def test_archon_after_storm_threshold_zero_merges_all():
    """energy_threshold=0 时任意电兵都不满足 < 0，等价于永不合（极端 case）。"""
    act = _make_archon_act(threshold=0)
    ht_any = _make_ht(energy=0, tag=40)  # energy=0，< 0 也不满足
    assert act._can_merge(ht_any) is False


def test_archon_after_storm_threshold_infinite_merges_all():
    """energy_threshold=201（> 电兵最大能量 200）时所有电兵都满足能量条件 → 合（旧行为）。"""
    act = _make_archon_act(threshold=201)
    ht_full = _make_ht(energy=200, tag=50)
    assert act._can_merge(ht_full) is True


def test_archon_default_threshold_is_100():
    """2026-06-02 用户:默认 energy_threshold=100（战斗电兵能量<100 立刻合）。"""
    from vibecraft.bot.auto_combat.protoss.plans.archon_after_storm import ArchonAfterStorm

    act = ArchonAfterStorm()
    assert act.energy_threshold == 100
    assert act.max_ht_count == 4
    # energy=99 满足(<100),energy=100 不满足
    assert act._can_merge(_make_ht(energy=99)) is True
    assert act._can_merge(_make_ht(energy=100)) is False


@pytest.mark.asyncio
async def test_archon_over_cap_merges_lowest_energy():
    """优先级1：HT 总数 > max_ht_count(4) → 强制合能量最低的两个（不看战斗/能量阈值）。"""
    from unittest.mock import MagicMock

    act = _make_archon_act(max_ht_count=4, enemy_combat_count=0)  # 无敌:证明 over_cap 不看战斗
    hts = _make_ht_collection(
        _make_ht(energy=200, tag=1),
        _make_ht(energy=200, tag=2),
        _make_ht(energy=200, tag=3),
        _make_ht(energy=60, tag=4),
        _make_ht(energy=50, tag=5),
    )
    act.cache.own.return_value.ready = hts
    act.knowledge = MagicMock()
    await act.execute()
    # 合 energy 最低的两个:tag 5(50) + tag 4(60);高能的不动
    assert 5 in act.already_merging_tags and 4 in act.already_merging_tags
    assert 1 not in act.already_merging_tags
    act.ai._client._execute.assert_awaited()


@pytest.mark.asyncio
async def test_archon_at_cap_no_force_merge():
    """HT 总数 == max_ht_count(4) 且都高能、无敌 → 优先级1 不触发，不合。"""
    from unittest.mock import MagicMock

    act = _make_archon_act(max_ht_count=4, enemy_combat_count=0)
    hts = _make_ht_collection(*[_make_ht(energy=200, tag=i) for i in range(1, 5)])
    act.cache.own.return_value.ready = hts
    act.knowledge = MagicMock()
    await act.execute()
    assert act.already_merging_tags == []


# ---------- 21. Charge 后置：8 叉子条件（修复 1，防回归）----------


def test_charge_research_gated_by_zealot_count():
    """Charge 研究 step 必须含 UnitExists(ZEALOT, 8) 条件（叉子够了才研，不抢 VT 气矿）。"""
    src = _PLAN_SRC.read_text(encoding="utf-8")
    # 条件：同时包含 ZEALOT 和数字 8（实现用 All + UnitExists(ZEALOT, 8)）
    assert "UnitExists(UnitTypeId.ZEALOT, 8)" in src, (
        "Charge 研究 step 应含 UnitExists(ZEALOT, 8) 门控，"
        "确保 4 BG 刷过 1-2 轮叉子后再研 Charge（防叉子速度升级太早抢 VT 气矿）"
    )
    # All 条件类要被导入（用来组合 VC ready + 叉子 ≥8）
    assert "All" in src, "plan 应导入 All（用于 Charge 研究的多条件门控）"


# ---------- 22. 哨兵后置：电兵出齐条件（修复 2 + 追加 2，防回归）----------


def test_sentry_gated_by_hightemplar_ready():
    """哨兵 ProtossUnit step 必须含 UnitExists(HIGHTEMPLAR, 6) 条件（6 电兵出齐才出哨兵）。

    2026-05-29 追加 2：门槛从 4 升到 6，配合 HighTemplar 目标值 4→6 同步。
    """
    import re

    src = _PLAN_SRC.read_text(encoding="utf-8")
    assert "UnitExists(UnitTypeId.HIGHTEMPLAR, 6)" in src, (
        "哨兵 step 应含 UnitExists(HIGHTEMPLAR, 6) 门控，"
        "确保 6 电兵 ready 后才出哨兵（避免早出哨兵抢气矿拖慢电兵 timing）"
    )
    # 哨兵 step 的门控不应是旧 HIGHTEMPLAR, 4（防回归）。
    # 注：2026-05-30 追猎 gate 合法用了 UnitExists(HIGHTEMPLAR, 4)，
    # 但哨兵 Step 必须用 6 做 gate —— 只检查 SENTRY Step 紧邻的 gate 行。
    # 找哨兵 Step 块：Step(\n    <gate>,\n    ProtossUnit(SENTRY, ...)  这个模式
    sentry_step_match = re.search(
        r"Step\(\s*\n\s*(UnitExists\(UnitTypeId\.HIGHTEMPLAR,\s*\d+\)),\s*\n\s*ProtossUnit\(UnitTypeId\.SENTRY",
        src,
    )
    assert sentry_step_match is not None, "哨兵 step 应使用 UnitExists(HIGHTEMPLAR, N) 做 gate"
    sentry_gate_expr = sentry_step_match.group(1)
    assert "4" not in sentry_gate_expr, (
        f"哨兵门槛已从 4 升到 6（追加 2），哨兵 step 不应用 UnitExists(HIGHTEMPLAR, 4) 做 gate；"
        f"实际找到: {sentry_gate_expr}"
    )
    assert "6" in sentry_gate_expr, (
        f"哨兵 gate 应为 UnitExists(HIGHTEMPLAR, 6)；实际找到: {sentry_gate_expr}"
    )


# ---------- 23. 哨兵数量 ≤ 2（修复 2，防过多哨兵消耗气矿）----------


def test_sentry_count_at_most_two():
    """哨兵出兵目标 ≤ 2（出门前少量切阵够用，不能多占气矿）。"""
    import re

    src = _PLAN_SRC.read_text(encoding="utf-8")
    # 找所有 ProtossUnit(UnitTypeId.SENTRY, N, ...) 的数量
    matches = re.findall(r"ProtossUnit\(UnitTypeId\.SENTRY,\s*(\d+)", src)
    assert matches, "plan 应含 ProtossUnit(SENTRY, N) 步骤"
    for count_str in matches:
        count = int(count_str)
        assert count <= 2, f"哨兵出兵数量 {count} 超过 2，出门前哨兵数量应 ≤ 2（只需切阵用）"


# ---------- 24. 追猎 >= 8（v2 改造 1：kite 主力，从 1 升到 10）----------


def test_stalker_count_kite_main():
    """追猎出兵目标 >= 8（v2 改造：kite 主力，远程消耗 + 风筝保护电兵放 Storm）。

    2026-05-29 v2：追猎从 1 升到 10，承担 kite 主力角色。
    不依赖 BF +1/+1（升级有 buff 但不等升级就出）。
    """
    import re

    src = _PLAN_SRC.read_text(encoding="utf-8")
    matches = re.findall(r"ProtossUnit\(UnitTypeId\.STALKER,\s*(\d+)", src)
    assert matches, "plan 应含 ProtossUnit(STALKER, N) 步骤"
    for count_str in matches:
        count = int(count_str)
        assert count >= 8, f"追猎出兵数量 {count} < 8，v2 改造要求追猎 >= 8（kite 主力）"
    # 旧值 1 不应再出现（防回归到"只造 1 追猎"旧设计）
    assert "ProtossUnit(UnitTypeId.STALKER, 1," not in src, (
        "追猎目标已从 1 升到 10（v2 kite 主力），plan 不应再含 ProtossUnit(STALKER, 1,...)"
    )


# ---------- 25. 电兵目标 == 6（追加 2，2026-05-29 细化）----------


def test_hightemplar_count_is_six():
    """电兵出兵目标 == 6（叉球一波核心输出，6 HT = 3 Archon）。"""
    import re

    src = _PLAN_SRC.read_text(encoding="utf-8")
    # 找 ProtossUnit(UnitTypeId.HIGHTEMPLAR, N, ...) 的数量（HT 出兵 step，不算 chrono 的 skip 条件）
    matches = re.findall(r"ProtossUnit\(UnitTypeId\.HIGHTEMPLAR,\s*(\d+)", src)
    assert matches, "plan 应含 ProtossUnit(HIGHTEMPLAR, N) 步骤"
    for count_str in matches:
        count = int(count_str)
        assert count == 6, f"电兵出兵数量 {count} != 6，追加 2 要求目标 6 电兵（= 3 Archon 池）"
    # 旧值 4 不应再出现于 HIGHTEMPLAR 出兵 step（防回归）
    assert "ProtossUnit(UnitTypeId.HIGHTEMPLAR, 4" not in src, (
        "电兵目标已从 4 升到 6（追加 2），plan 不应再含 ProtossUnit(HIGHTEMPLAR, 4,...)"
    )


# ---------- 26. 叉子目标 16-32（v2 改造：肉盾，减少到 24 避免一发 Storm 全清）----------


def test_zealot_count_moderate_meatshield():
    """叉子出兵目标在 16-32 之间（v2 改造：肉盾角色，减少数量避免一发 Storm 全清）。

    2026-05-29 v2：叉子从 40 降到 24。
    - 叉子角色 = 肉盾（前排吸伤），不是 kite 主力
    - 数量减少 → 不会被一发 Storm 全清
    - kite 由追猎承担
    """
    import re

    src = _PLAN_SRC.read_text(encoding="utf-8")
    matches = re.findall(r"ProtossUnit\(UnitTypeId\.ZEALOT,\s*(\d+)", src)
    assert matches, "plan 应含 ProtossUnit(ZEALOT, N) 步骤"
    for count_str in matches:
        count = int(count_str)
        assert 16 <= count <= 32, (
            f"叉子出兵数量 {count} 不在 16-32 范围内，v2 要求肉盾叉子 24 只（避免 Storm 全清）"
        )


# ---------- 27. HT chrono 上限同步升到 6（追加 2，防 chrono 卡在旧 4）----------


def test_ht_chrono_skip_at_six():
    """HT chrono step 的 skip 条件必须用 6（与 ProtossUnit HIGHTEMPLAR 6 目标对齐）。

    旧值 skip=UnitExists(HIGHTEMPLAR, 4, include_pending=True) 会让 chrono 在
    4 HT 时就停，导致 5/6 HT 慢出 → 出门 timing 推迟。
    """
    src = _PLAN_SRC.read_text(encoding="utf-8")
    # plan 中 HT chrono 的 skip 表达式应包含 6
    assert "UnitExists(UnitTypeId.HIGHTEMPLAR, 6, include_pending=True)" in src, (
        "HT chrono skip 条件应为 UnitExists(HIGHTEMPLAR, 6, include_pending=True)"
    )
    # 旧 skip=4 不应再出现
    assert "UnitExists(UnitTypeId.HIGHTEMPLAR, 4, include_pending=True)" not in src, (
        "HT chrono skip 已从 4 升到 6（追加 2），plan 不应再含旧值"
    )


# ---------- 28-34. ArchonAfterStorm 战斗判定单测（新增 require_combat 逻辑）----------


def test_archon_merge_only_in_combat():
    """战斗中（有敌方战斗单位）+ energy<75 → 合。"""
    # enemy_combat_count=2：周围有 2 个追猎 → 在战场
    act = _make_archon_act(threshold=75, require_combat=True, enemy_combat_count=2)
    ht = _make_ht(energy=50, tag=100)

    assert act._can_merge(ht) is True  # 50 < 75 ✓
    assert act._in_combat(ht) is True  # 有敌方战斗单位 ✓


def test_archon_no_merge_not_in_combat():
    """不在战场（无敌人）+ energy<75 → 不合（require_combat 保护）。"""
    act = _make_archon_act(threshold=75, require_combat=True, enemy_combat_count=0)
    ht = _make_ht(energy=50, tag=101)  # fresh 电兵在家

    assert act._can_merge(ht) is True  # 50 < 75 能量条件满足
    assert act._in_combat(ht) is False  # 无敌 → 不在战场 → 不合


def test_archon_merge_threshold_74_with_enemy():
    """energy=74 + 有敌人 → 合（74 < 75 = True，刚好满足）。"""
    act = _make_archon_act(threshold=75, require_combat=True, enemy_combat_count=1)
    ht = _make_ht(energy=74, tag=102)

    assert act._can_merge(ht) is True  # 74 < 75 ✓
    assert act._in_combat(ht) is True  # 有敌 ✓


def test_archon_no_merge_threshold_75_exact_with_enemy():
    """energy=75 + 有敌人 → 不合（75 < 75 = False，刚好不满足）。"""
    act = _make_archon_act(threshold=75, require_combat=True, enemy_combat_count=1)
    ht = _make_ht(energy=75, tag=103)

    assert act._can_merge(ht) is False  # 75 < 75 = False，能量够放下一发心灵风暴


def test_archon_excludes_worker_enemies():
    """周围只有农民（探机）→ 不算战斗单位 → 不合。"""
    # include_workers=True：mock 只返回探机，filter 排除后 fighters.amount=0
    act = _make_archon_act(threshold=75, require_combat=True, enemy_only_workers=True)
    ht = _make_ht(energy=50, tag=104)

    assert act._can_merge(ht) is True  # 50 < 75 能量满足
    assert act._in_combat(ht) is False  # 只有农民不算战斗 → 不合


def test_archon_require_combat_false_ignores_battlefield():
    """require_combat=False 时不检查战场状态，只看 energy。"""
    # 无敌人，但 require_combat=False → 不检查战场
    act = _make_archon_act(threshold=75, require_combat=False, enemy_combat_count=0)
    ht = _make_ht(energy=50, tag=105)

    assert act._can_merge(ht) is True  # 50 < 75 ✓
    # require_combat=False 时 _in_combat 不影响合并逻辑，但自身值无关


def test_iac_2base_uses_archon_after_storm_default_params():
    """iac_2base 应调用 ArchonAfterStorm() 默认参数（threshold=75 默认），不再传 energy_threshold=50。"""
    src = _PLAN_SRC.read_text(encoding="utf-8")
    # 新调用方式：ArchonAfterStorm() 无参数 或 ArchonAfterStorm(energy_threshold=75)
    assert "ArchonAfterStorm()" in src or "ArchonAfterStorm(energy_threshold=75)" in src, (
        "iac_2base 应使用 ArchonAfterStorm() 默认参数（threshold=75）或显式传 75"
    )
    # 旧调用 energy_threshold=50 不应再出现（防回归）
    assert "ArchonAfterStorm(energy_threshold=50)" not in src, (
        "旧调用 ArchonAfterStorm(energy_threshold=50) 已改为默认 75，不应再出现"
    )


# ---------- 35. 追猎 gate 改为 UnitExists(HIGHTEMPLAR, 4)（2026-05-30 用户要求）----------


def test_stalker_gated_by_hightemplar_ready_at_4():
    """追猎 step 的 gate 必须是 UnitExists(HIGHTEMPLAR, 4)，不能直接用 BY ready 开始产。

    2026-05-30 用户要求：至少出 4 个电兵以后再开始出追猎。
    在这之前只出叉子和电兵（气矿优先给电兵，追猎 500 气后置）。
    """
    src = _PLAN_SRC.read_text(encoding="utf-8")
    assert "UnitExists(UnitTypeId.HIGHTEMPLAR, 4)" in src, (
        "追猎 step 应含 UnitExists(HIGHTEMPLAR, 4) 门控，"
        "确保 4 电兵 ready 后才开始产追猎（用户 2026-05-30 要求）"
    )
    # 旧设计：直接 ProtossUnit(STALKER, 10) 无任何 gate 或用 CYBERNETICSCORE gate
    # —— 这两种写法都不应再出现为追猎的直接产出形式
    assert (
        "UnitReady(UnitTypeId.CYBERNETICSCORE, 1),\n                ProtossUnit(UnitTypeId.STALKER"
        not in src
    ), "追猎 gate 不应再是 UnitReady(CYBERNETICSCORE, 1)（BY ready 就产，过早）"


def test_stalker_count_unchanged_at_ten():
    """追猎出兵总数保持 10 不变（只改 gate，不改数量；用户明确要求保持 10）。

    gate 改为 UnitExists(HIGHTEMPLAR, 4) 后，追猎数量仍为 10 只 kite 主力。
    防止改 gate 时不小心把数量也改掉。
    """
    import re

    src = _PLAN_SRC.read_text(encoding="utf-8")
    matches = re.findall(r"ProtossUnit\(UnitTypeId\.STALKER,\s*(\d+)", src)
    assert matches, "plan 应含 ProtossUnit(STALKER, N) 步骤"
    counts = [int(c) for c in matches]
    assert 10 in counts, f"追猎出兵数量应含 10（当前找到 {counts}），gate 改变后总数不应变"


# ---------- 36. +2 攻防升级链（2026-05-30 新增）----------


def test_iac_2base_has_plus_two_upgrades():
    """+2 攻 / +2 防升级 Tech step 必须存在于 plan 源码。

    升级链：+1 完成 + VC(TWILIGHTCOUNCIL) ready 后接 +2（VC 是 +2/+3 前置建筑）。
    2026-05-30 用户要求：宏观策略里加上自动升级二攻二防。
    """
    src = _PLAN_SRC.read_text(encoding="utf-8")
    assert "PROTOSSGROUNDWEAPONSLEVEL2" in src, (
        "plan 应含 Tech(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL2) —— +2 攻升级链"
    )
    assert "PROTOSSGROUNDARMORSLEVEL2" in src, (
        "plan 应含 Tech(UpgradeId.PROTOSSGROUNDARMORSLEVEL2) —— +2 防升级链"
    )


def test_iac_2base_plus_two_gated_by_vc_and_plus_one():
    """+2 升级 step 必须以 VC ready + +1 完成 为前置条件。

    - UnitReady(TWILIGHTCOUNCIL, 1)：VC 是 +2/+3 前置建筑
    - TechReady(PROTOSSGROUNDWEAPONSLEVEL1) / TechReady(PROTOSSGROUNDARMORSLEVEL1)：
      +1 完成才能研 +2，防止两个 Forge 都卡在 +1 时 +2 无处研究
    """
    src = _PLAN_SRC.read_text(encoding="utf-8")
    assert "TechReady(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1)" in src, (
        "+2 攻升级 gate 应含 TechReady(PROTOSSGROUNDWEAPONSLEVEL1)（+1 完成才接 +2）"
    )
    assert "TechReady(UpgradeId.PROTOSSGROUNDARMORSLEVEL1)" in src, (
        "+2 防升级 gate 应含 TechReady(PROTOSSGROUNDARMORSLEVEL1)（+1 完成才接 +2）"
    )


def test_iac_2base_plus_two_chrono_steps_exist():
    """+2 攻 / +2 防的 chrono ChronoTech step 必须存在于 plan 源码。

    chrono 加速 +2 升级，与 +1 chrono 同逻辑（BF 持续 chrono）。
    """
    src = _PLAN_SRC.read_text(encoding="utf-8")
    assert "FORGERESEARCH_PROTOSSGROUNDWEAPONSLEVEL2" in src, (
        "plan 应含 ChronoTech(FORGERESEARCH_PROTOSSGROUNDWEAPONSLEVEL2, FORGE)"
    )
    assert "FORGERESEARCH_PROTOSSGROUNDARMORLEVEL2" in src, (
        "plan 应含 ChronoTech(FORGERESEARCH_PROTOSSGROUNDARMORLEVEL2, FORGE)"
    )
