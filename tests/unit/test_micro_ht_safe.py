"""MicroHighTemplars vibecraft 电兵安全 micro 行为单测（iac_2base v2 改造 2）。

测试目标（全部 mock，无 SC2）：
  1. ht_safe_micro=False（默认）→ 走原始 sharpy 路径（不走安全 micro）
  2. ht_safe_micro=True + 近敌（15 格内，energy=0 普通战斗单位）→ 发出 move 后撤指令（is_attack=False）
  3. ht_safe_micro=True + 无近敌 + energy >= 75 + 敌群密集（>=4）→ 放 Psi Storm
  4. ht_safe_micro=True + 无近敌 + energy < 75 → 跟随大部队（move，is_attack=False）
  5. ht_safe_micro=True + 无近敌 + energy >= 75 + 敌群稀疏（<4）→ 跟随大部队（不放 Storm）
  6. ht_safe_micro=True + 知识 vibecraft ns 不存在 → 不报错，走原始路径（兜底）
  7. ht_safe_micro=True + 已在大部队中心附近（distance < 3）→ 原地 move（不冲前线）
  8. ht_safe_micro=True + Feedback 目标在范围内（energy >= 50，近敌存在）→ 先放 Feedback（不后撤）
  9. ht_safe_micro=True + 范围内单位 energy < 50 → 不放 Feedback，走后撤
  10. 原始 sharpy 路径：energy >= 50 的敌人触发 Feedback（修正 energy > 74 过严阈值）
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

_VENDOR_SHARPY = Path(__file__).resolve().parents[2] / "vendor" / "sharpy"
if str(_VENDOR_SHARPY) not in sys.path:
    sys.path.insert(0, str(_VENDOR_SHARPY))

pytestmark = pytest.mark.skipif(
    not (_VENDOR_SHARPY / "sharpy" / "combat" / "__init__.py").exists(),
    reason="vendor/sharpy not available",
)

pytest.importorskip("sc2.ids.ability_id")
pytest.importorskip("sc2.ids.effect_id")
pytest.importorskip("sharpy.combat")


# ---------------------------------------------------------------------------
# Helper：构造 MicroHighTemplars 实例，绕开 sharpy Component 初始化链
# ---------------------------------------------------------------------------


def _make_micro(ht_safe_micro: bool = True, knowledge_has_vibecraft: bool = True) -> Any:
    """构造 MicroHighTemplars，mock 所有 sharpy 内部依赖。"""
    from sharpy.combat.protoss.micro_hightemplars import MicroHighTemplars

    micro = MicroHighTemplars.__new__(MicroHighTemplars)
    micro.ordered_storms = []

    # knowledge.vibecraft
    knowledge = MagicMock()
    if knowledge_has_vibecraft:
        vbc = MagicMock()
        vbc.ht_safe_micro = ht_safe_micro
        knowledge.vibecraft = vbc
    else:
        # spec 不含 vibecraft → getattr(knowledge, 'vibecraft', None) 返回 MagicMock
        # 要让 getattr 真正返回 None，需要让 hasattr 失败。
        # 用 spec=[] 的 SimpleNamespace 更可靠
        from types import SimpleNamespace

        micro.knowledge = SimpleNamespace()  # 没有 vibecraft 属性
        return _finalize_micro(micro)

    micro.knowledge = knowledge

    return _finalize_micro(micro)


def _finalize_micro(micro: Any) -> Any:
    """设置 micro 的共享 mock 依赖（pather / cache / group / closest_units）。"""
    # cd_manager：默认所有技能 CD 都好了（is_ready=True）
    cd_manager = MagicMock()
    cd_manager.is_ready = MagicMock(return_value=True)
    micro.cd_manager = cd_manager

    # cache：默认无敌人（empty Units）
    empty_units = _make_units([])
    micro.cache = MagicMock()
    micro.cache.enemy_in_range = MagicMock(return_value=empty_units)
    micro.cache.effects = MagicMock(return_value=[])

    # pather：返回传入的 position（简化）
    pather = MagicMock()
    pather.find_weak_influence_ground = MagicMock(side_effect=lambda pos, r: pos)
    pather.find_influence_ground_path = MagicMock(side_effect=lambda start, end, d: end)
    micro.pather = pather

    # group.center：大部队中心（mock Point2）
    group = MagicMock()
    group.center = _make_point2(50.0, 50.0)
    micro.group = group

    # closest_units：GenericMicro.unit_solve_combat 需要此属性（{tag: Unit or None}）
    micro.closest_units = {}

    # GenericMicro 其他需要的属性
    micro.move_type = 1  # MoveType.Assault
    micro.model = 5  # CombatModel.StalkerToStalker（不触发 retreat 路径）
    micro.models_with_retreat = [0, 1, 5, 6]
    micro.cyclone_dodge = False
    micro.enemies_near_by = MagicMock()
    micro.enemies_near_by.amount = 0
    micro.closest_group = None
    micro.ready_to_attack_ratio = 0.5
    micro.center = _make_point2(50.0, 50.0)

    # rules：MicroStep.ready_to_shoot / focus_fire 等需要此属性
    # ready_to_shoot_func 返回 False（weapon on CD，不射击）→ GenericMicro 走 pass-through
    rules = MagicMock()
    rules.ready_to_shoot_func = MagicMock(return_value=False)
    rules.focus_fire_func = MagicMock(side_effect=lambda step, unit, cmd, prio: cmd)
    micro.rules = rules

    return micro


def _make_point2(x: float, y: float) -> Any:
    """构造 mock Point2。"""
    from unittest.mock import MagicMock

    p = MagicMock()
    p.x = x
    p.y = y
    p.distance_to = MagicMock(
        side_effect=lambda other: ((p.x - other.x) ** 2 + (p.y - other.y) ** 2) ** 0.5
    )
    # towards：从 self 朝 other 移动 distance，简化返回 other（方向对，数值近似）
    p.towards = MagicMock(
        side_effect=lambda other, dist: _make_point2(
            p.x
            + (other.x - p.x)
            * (dist / max(0.001, ((p.x - other.x) ** 2 + (p.y - other.y) ** 2) ** 0.5)),
            p.y
            + (other.y - p.y)
            * (dist / max(0.001, ((p.x - other.x) ** 2 + (p.y - other.y) ** 2) ** 0.5)),
        )
    )
    return p


def _make_unit(tag: int = 1, x: float = 50.0, y: float = 50.0) -> Any:
    """构造 mock Unit。"""
    u = MagicMock()
    u.tag = tag
    _pos = _make_point2(x, y)
    u.position = _pos

    def _distance_to(other: Any) -> float:
        """真实欧几里得距离，支持 Point2 mock 和 Unit mock。"""
        if hasattr(other, "position"):
            ox, oy = other.position.x, other.position.y
        elif hasattr(other, "x"):
            ox, oy = other.x, other.y
        else:
            return 0.0
        return ((x - ox) ** 2 + (y - oy) ** 2) ** 0.5

    u.distance_to = MagicMock(side_effect=_distance_to)
    u.energy = 100.0
    return u


def _make_units(units_list: list) -> Any:
    """构造 mock Units collection。"""
    coll = MagicMock()
    coll.amount = len(units_list)
    coll.__len__ = MagicMock(return_value=len(units_list))
    coll.__bool__ = MagicMock(return_value=bool(units_list))

    # not_structure → 返回自身（简化：假设都不是建筑）
    coll.not_structure = coll

    def _further_than(dist, pos):
        remaining = list(units_list)  # 简化：不真正过滤
        return _make_units(remaining)

    def _closer_than(dist, pos):
        return _make_units(units_list)

    coll.further_than = MagicMock(side_effect=_further_than)
    coll.closer_than = MagicMock(side_effect=_closer_than)
    coll.filter = MagicMock(return_value=coll)
    if units_list:
        center = _make_point2(
            sum(u.position.x for u in units_list) / len(units_list),
            sum(u.position.y for u in units_list) / len(units_list),
        )
        coll.center = center
        coll.closest_to = MagicMock(return_value=units_list[0])
    else:
        coll.center = _make_point2(50.0, 50.0)
        coll.closest_to = MagicMock(return_value=None)
    return coll


def _make_action(target: Any = None, is_attack: bool = False) -> Any:
    """构造 mock current_command Action。"""
    from sharpy.combat.action import Action

    return Action(target or _make_point2(100.0, 100.0), is_attack)


# ---------------------------------------------------------------------------
# Test 1: ht_safe_micro=False（默认）→ 不走 vibecraft 安全路径
# ---------------------------------------------------------------------------


def test_default_mode_no_safe_micro():
    """ht_safe_micro=False → _vbc_safe_unit_solve 不被调用（走原始 sharpy 路径）。"""
    from unittest.mock import patch

    from sharpy.combat.action import Action

    micro = _make_micro(ht_safe_micro=False)
    unit = _make_unit(tag=1, x=50.0, y=50.0)

    # 拦截 _vbc_safe_unit_solve：若被调用则测试失败
    with patch.object(
        micro,
        "_vbc_safe_unit_solve",
        side_effect=AssertionError("ht_safe_micro=False 时不应调 _vbc_safe_unit_solve"),
    ):
        # cd_manager.is_ready=False → Storm/Feedback 全跳过，走 super().unit_solve_combat
        micro.cd_manager.is_ready = MagicMock(return_value=False)
        # unit 额外 mock：GenericMicro.should_retreat 需要 shield/health 属性
        unit.shield_max = 0
        unit.health_max = 0
        unit.shield = 0
        unit.health = 0
        unit.weapon_cooldown = 0

        # move_type=1(Assault) + closest_units={} + no closest_group
        # → GenericMicro 返回 current_command 不 crash
        cmd_no_attack = _make_action(_make_point2(80.0, 80.0), is_attack=False)
        result = micro.unit_solve_combat(unit, cmd_no_attack)
        assert isinstance(result, Action)


# ---------------------------------------------------------------------------
# Test 2: ht_safe_micro=True + 近敌（15 格内）→ move 后撤（is_attack=False）
# ---------------------------------------------------------------------------


def test_safe_micro_retreats_from_nearby_threat():
    """ht_safe_micro=True + 15 格内有敌（energy=0，普通战斗单位）→ move 后撤（is_attack=False）。

    注：enemy.energy=0 确保不触发 Feedback 步骤，测试到纯后撤路径。
    真实 Marine/Zealot 等普通战斗单位没有能量，energy=0。
    """
    from sharpy.combat.action import Action

    micro = _make_micro(ht_safe_micro=True)
    unit = _make_unit(tag=2, x=50.0, y=50.0)

    # 设置近敌：cache.enemy_in_range 返回 1 个普通战斗单位（energy=0，不触发 Feedback）
    enemy = _make_unit(tag=99, x=55.0, y=50.0)  # 距离 5 格，< 15 格
    enemy.energy = 0.0  # 普通战斗单位（Marine/Zealot 等）没有能量
    enemy.is_structure = False
    enemies_nearby = _make_units([enemy])
    enemies_nearby.not_structure = enemies_nearby
    # filter 返回空集（energy < 50，Feedback 不触发）
    enemies_nearby.filter = MagicMock(return_value=_make_units([]))
    micro.cache.enemy_in_range = MagicMock(return_value=enemies_nearby)

    cmd = _make_action(_make_point2(80.0, 80.0), is_attack=True)
    result = micro.unit_solve_combat(unit, cmd)

    assert isinstance(result, Action)
    assert result.is_attack is False, "近敌时应后撤（is_attack=False），不进攻"
    assert result.ability is None, "后撤时不应使用技能"


# ---------------------------------------------------------------------------
# Test 3: ht_safe_micro=True + 无近敌 + energy>=75 + 密集敌群(>=4) → 放 Storm
# ---------------------------------------------------------------------------


def test_safe_micro_casts_storm_on_dense_enemy():
    """ht_safe_micro=True + 无近敌 + energy>=75 + >=4 敌人密集 → 放 Psi Storm。"""
    from sc2.ids.ability_id import AbilityId
    from sharpy.combat.action import Action

    micro = _make_micro(ht_safe_micro=True)
    unit = _make_unit(tag=3, x=50.0, y=50.0)
    unit.energy = 100.0  # >= 75

    # 15 格内无敌（near range = _VBC_HT_DANGER_RADIUS=15）
    # 24 格内有 4 个密集敌人（storm range = 9 + 15 = 24）
    distant_enemies = [_make_unit(tag=90 + i, x=60.0 + i, y=50.0) for i in range(4)]
    distant_coll = _make_units(distant_enemies)
    distant_coll.not_structure = distant_coll
    # 设置 closer_than(3, target.position) 返回 4 个（密集）
    dense_coll = _make_units(distant_enemies)
    dense_coll.amount = 4
    distant_coll.closer_than = MagicMock(return_value=dense_coll)

    def _enemy_in_range(pos, radius):
        from sharpy.combat.protoss.micro_hightemplars import _VBC_HT_DANGER_RADIUS

        if radius <= _VBC_HT_DANGER_RADIUS:
            # 近距离（15 格）→ 无敌（不触发后撤）
            return _make_units([])
        # 远距离（24 格）→ 4 个密集敌人
        return distant_coll

    micro.cache.enemy_in_range = MagicMock(side_effect=_enemy_in_range)

    cmd = _make_action(_make_point2(80.0, 80.0), is_attack=True)
    result = micro.unit_solve_combat(unit, cmd)

    assert isinstance(result, Action)
    assert result.ability == AbilityId.PSISTORM_PSISTORM, "密集敌群 + energy>=75 → 应放 Storm"
    assert result.is_attack is False, "Storm 施法不应是 attack 命令"


# ---------------------------------------------------------------------------
# Test 4: ht_safe_micro=True + 无近敌 + energy < 75 → 跟随大部队（move，not attack）
# ---------------------------------------------------------------------------


def test_safe_micro_follows_group_when_low_energy():
    """ht_safe_micro=True + 无近敌 + energy<75 → 跟随大部队（move，is_attack=False）。"""
    from sharpy.combat.action import Action

    micro = _make_micro(ht_safe_micro=True)
    unit = _make_unit(tag=4, x=50.0, y=50.0)
    unit.energy = 50.0  # < 75，不能放 Storm

    # 无近敌
    micro.cache.enemy_in_range = MagicMock(return_value=_make_units([]))

    # 大部队中心在 60, 50（距离 10 > 3 → 应该 move）
    # 直接 mock distance_to 返回 10.0（确保 > 3 → 触发 move 路径）
    unit.distance_to = MagicMock(return_value=10.0)

    cmd = _make_action(_make_point2(80.0, 80.0), is_attack=True)
    result = micro.unit_solve_combat(unit, cmd)

    assert isinstance(result, Action)
    assert result.is_attack is False, "能量不足 + 无近敌 → 跟随大部队（不 attack）"
    assert result.ability is None, "跟随时不应使用技能"


# ---------------------------------------------------------------------------
# Test 5: ht_safe_micro=True + 无近敌 + energy>=75 + 敌群稀疏（<4）→ 跟随（不放 Storm）
# ---------------------------------------------------------------------------


def test_safe_micro_no_storm_on_sparse_enemy():
    """ht_safe_micro=True + 无近敌 + energy>=75 + 稀疏敌群（<4 聚集）→ 跟随大部队，不放 Storm。

    稀疏远敌（15-25 格，energy=0 普通战斗单位）：不触发 Feedback（energy < 50），
    不触发 Storm（聚集 < 4）→ 走步骤 4 跟随大部队。
    """
    from sharpy.combat.action import Action

    micro = _make_micro(ht_safe_micro=True)
    unit = _make_unit(tag=5, x=50.0, y=50.0)
    unit.energy = 100.0  # >= 75
    # 明确 mock distance_to：大部队中心 = 单位自身位置，距离 < 3 → 原地不动
    unit.distance_to = MagicMock(return_value=1.0)

    # 无近敌（15格），有稀疏远敌（3 个，不够 4，且 energy=0 不触发 Feedback）
    sparse_enemies = [_make_unit(tag=80 + i, x=65.0 + i * 5, y=50.0) for i in range(3)]
    for e in sparse_enemies:
        e.energy = 0.0  # 普通战斗单位，不触发 Feedback
    sparse_coll = _make_units(sparse_enemies)
    sparse_coll.not_structure = sparse_coll
    # filter(energy >= 50) → 返回空（energy=0 全部过滤掉）
    sparse_coll.filter = MagicMock(return_value=_make_units([]))
    # closer_than(3) 返回 3 个（不够 4）
    sparse_dense = _make_units(sparse_enemies)
    sparse_dense.amount = 3
    sparse_coll.closer_than = MagicMock(return_value=sparse_dense)

    def _enemy_in_range(pos, radius):
        from sharpy.combat.protoss.micro_hightemplars import _VBC_HT_DANGER_RADIUS

        if radius <= _VBC_HT_DANGER_RADIUS:
            return _make_units([])  # 近距无敌（含 Feedback 9 格范围）
        return sparse_coll  # 远距稀疏（Storm 范围 24 格）

    micro.cache.enemy_in_range = MagicMock(side_effect=_enemy_in_range)

    cmd = _make_action(_make_point2(80.0, 80.0), is_attack=True)
    result = micro.unit_solve_combat(unit, cmd)

    assert isinstance(result, Action)
    from sc2.ids.ability_id import AbilityId

    assert result.ability != AbilityId.PSISTORM_PSISTORM, "稀疏敌群（<4）不应浪费 Storm"
    assert result.is_attack is False, "稀疏敌群 → 跟随大部队（不 attack）"


# ---------------------------------------------------------------------------
# Test 6: knowledge.vibecraft 不存在 → getattr 兜底，走原始路径，不报错
# ---------------------------------------------------------------------------


def test_safe_micro_fallback_when_vibecraft_missing():
    """knowledge.vibecraft 不存在时 → _vbc_safe_unit_solve 不被调用（走原始路径）。"""
    from unittest.mock import patch

    from sharpy.combat.action import Action

    micro = _make_micro(ht_safe_micro=True, knowledge_has_vibecraft=False)
    unit = _make_unit(tag=6, x=50.0, y=50.0)

    # 拦截 safe 路径：若被调用则测试失败（vibecraft ns 不存在 → 不应走 safe micro）
    with patch.object(
        micro,
        "_vbc_safe_unit_solve",
        side_effect=AssertionError("vibecraft ns 不存在时不应调 _vbc_safe_unit_solve"),
    ):
        # cd_manager.is_ready=False → Storm/Feedback 全跳过，走 super()
        micro.cd_manager.is_ready = MagicMock(return_value=False)
        unit.shield_max = 0
        unit.health_max = 0
        unit.shield = 0
        unit.health = 0
        unit.weapon_cooldown = 0
        cmd_no_attack = _make_action(_make_point2(80.0, 80.0), is_attack=False)
        result = micro.unit_solve_combat(unit, cmd_no_attack)
        assert isinstance(result, Action)


# ---------------------------------------------------------------------------
# Test 7: ht_safe_micro=True + 已在大部队中心（distance < 3）→ 原地 move
# ---------------------------------------------------------------------------


def test_safe_micro_stays_put_near_group_center():
    """ht_safe_micro=True + 无近敌 + energy<75 + 已在大部队中心（距离 < 3）→ 原地 move。"""
    from sharpy.combat.action import Action

    micro = _make_micro(ht_safe_micro=True)
    unit = _make_unit(tag=7, x=50.0, y=50.0)
    unit.energy = 50.0  # < 75

    # 无近敌
    micro.cache.enemy_in_range = MagicMock(return_value=_make_units([]))

    # 大部队中心距离 < 3（距离 = 1.0 < 3）→ 原地 move
    unit.distance_to = MagicMock(return_value=1.0)

    cmd = _make_action(_make_point2(80.0, 80.0), is_attack=True)
    result = micro.unit_solve_combat(unit, cmd)

    assert isinstance(result, Action)
    assert result.is_attack is False, "已在大部队附近 → 原地 move（不 attack）"


# ---------------------------------------------------------------------------
# Test 8: ht_safe_micro=True + Feedback 目标在范围内（近敌存在）→ 先放 Feedback
# 修复：旧代码在近敌存在时先后撤，导致 Feedback 永远不发
# ---------------------------------------------------------------------------


def test_safe_micro_feedback_fires_before_retreat():
    """ht_safe_micro=True + 9 格内有 spellcaster（energy >= 50）→ 放 Feedback，不后撤。

    2026-05-29 修复：Feedback 步骤提升到步骤 1（先于近敌后撤），确保电兵主动放法力反馈。
    场景：1 个 Ghost/Infestor 等法师单位在 9 格内（既是 Feedback 目标也是威胁），
    旧代码后撤 → 电兵永远不发 Feedback；新代码先 Feedback 再跑。
    """
    from sc2.ids.ability_id import AbilityId
    from sharpy.combat.action import Action

    micro = _make_micro(ht_safe_micro=True)
    unit = _make_unit(tag=8, x=50.0, y=50.0)

    # 构造 spellcaster：energy >= 50，非建筑（Ghost/Viper/Infestor 等）
    caster = _make_unit(tag=200, x=56.0, y=50.0)  # 距离 6 格，在 Feedback 范围 9 内
    caster.energy = 75.0
    caster.is_structure = False

    # filter 返回 caster（energy >= 50 and not structure）
    caster_coll = _make_units([caster])
    caster_coll.filter = MagicMock(return_value=caster_coll)

    # not_structure 也需要正确（threat 检测用）
    threat_coll = _make_units([caster])
    threat_coll.not_structure = threat_coll
    threat_coll.filter = MagicMock(return_value=caster_coll)

    def _enemy_in_range(pos, radius):
        from sharpy.combat.protoss.micro_hightemplars import _VBC_HT_FEEDBACK_RADIUS

        if radius <= _VBC_HT_FEEDBACK_RADIUS:
            return caster_coll  # Feedback 范围内有 caster
        return threat_coll  # 威胁范围内也有同一 caster

    micro.cache.enemy_in_range = MagicMock(side_effect=_enemy_in_range)

    cmd = _make_action(_make_point2(80.0, 80.0), is_attack=True)
    result = micro.unit_solve_combat(unit, cmd)

    assert isinstance(result, Action)
    assert result.ability == AbilityId.FEEDBACK_FEEDBACK, (
        "有 spellcaster 在 Feedback 范围内 → 应放法力反馈（不后撤）"
    )
    assert result.is_attack is False, "Feedback 施法不是 attack 命令"


# ---------------------------------------------------------------------------
# Test 9: ht_safe_micro=True + 范围内单位 energy < 50 → 不放 Feedback，走后撤
# ---------------------------------------------------------------------------


def test_safe_micro_no_feedback_on_low_energy_units():
    """ht_safe_micro=True + 9 格内单位 energy < 50 → Feedback 不触发，走后撤路径。

    普通战斗单位（Marine/Zealot 等 energy=0，或能量不足 50 的单位）不应触发 Feedback。
    """
    from sharpy.combat.action import Action

    micro = _make_micro(ht_safe_micro=True)
    unit = _make_unit(tag=9, x=50.0, y=50.0)

    # 近敌：energy = 30（低于 50，不能作为 Feedback 有效目标）
    low_energy_unit = _make_unit(tag=201, x=54.0, y=50.0)
    low_energy_unit.energy = 30.0
    low_energy_unit.is_structure = False

    # filter(energy >= 50) → 返回空集（30 < 50，不是 Feedback 目标）
    no_feedback_targets = _make_units([])
    low_energy_coll = _make_units([low_energy_unit])
    low_energy_coll.filter = MagicMock(return_value=no_feedback_targets)
    low_energy_coll.not_structure = low_energy_coll

    micro.cache.enemy_in_range = MagicMock(return_value=low_energy_coll)

    cmd = _make_action(_make_point2(80.0, 80.0), is_attack=True)
    result = micro.unit_solve_combat(unit, cmd)

    from sc2.ids.ability_id import AbilityId

    assert isinstance(result, Action)
    assert result.ability != AbilityId.FEEDBACK_FEEDBACK, "energy < 50 的单位不应触发 Feedback"
    # 近敌存在（amount > 0）→ 应走后撤路径
    assert result.is_attack is False, "后撤时不 attack"


# ---------------------------------------------------------------------------
# Test 10: 原始 sharpy 路径（ht_safe_micro=False）：energy >= 50 触发 Feedback
# 修复原始代码 energy > 74 过于保守（漏掉 50-74 能量段）
# ---------------------------------------------------------------------------


def test_original_path_feedback_with_energy_50():
    """ht_safe_micro=False + 10 格内敌人 energy=60（>= 50）→ 原始路径放 Feedback。

    修复：原始代码 energy > 74 导致 50-74 能量段施法者漏掉。
    真实场景：Ghost 刚充到 60 能量（可 EMP），应该 Feedback 掉。
    """
    from sc2.ids.ability_id import AbilityId
    from sharpy.combat.action import Action

    micro = _make_micro(ht_safe_micro=False)
    unit = _make_unit(tag=10, x=50.0, y=50.0)
    unit.energy = 100.0  # 电兵自身能量足够

    # 构造 spellcaster：energy=60（>= 50 但 < 75，旧代码漏掉）
    medium_energy_caster = _make_unit(tag=202, x=55.0, y=50.0)
    medium_energy_caster.energy = 60.0
    medium_energy_caster.is_structure = False

    caster_coll = _make_units([medium_energy_caster])
    # filter(energy >= 50 and not structure) → 返回 caster
    caster_coll.filter = MagicMock(return_value=caster_coll)
    # Storm 路径：无密集敌群
    sparse_coll = _make_units([])

    def _enemy_in_range(pos, radius):
        if radius <= 10:
            return caster_coll
        return sparse_coll

    micro.cache.enemy_in_range = MagicMock(side_effect=_enemy_in_range)
    # Storm CD 未好（避免 Storm 先触发）
    from unittest.mock import MagicMock as MM

    def _is_ready(tag, ability_id):
        if ability_id == AbilityId.PSISTORM_PSISTORM:
            return False  # Storm CD 中
        # Feedback CD 好了；其他技能 CD 中
        return ability_id == AbilityId.FEEDBACK_FEEDBACK

    micro.cd_manager.is_ready = MM(side_effect=_is_ready)

    cmd = _make_action(_make_point2(80.0, 80.0), is_attack=True)
    result = micro.unit_solve_combat(unit, cmd)

    assert isinstance(result, Action)
    assert result.ability == AbilityId.FEEDBACK_FEEDBACK, (
        "energy=60 的 spellcaster（>= 50）应触发 Feedback（旧代码 >74 会漏掉）"
    )
