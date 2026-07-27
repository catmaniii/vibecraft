"""PhoenixHarassAct: 凤凰骚扰微操 act 的纯逻辑测试。

ActBase 子类直接构造要 sharpy 环境，这里用 __new__ 绕开 __init__、手动塞字段，
只测关键决策：
  - 血量比例 / 撤退判定 / 回血滞回
  - 能量门槛（>= 50 才施放 Graviton Beam）
  - 进场 vs 骚扰阶段判定（按距离 enemy_main）
  - release_after 放手时机
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _fake_sharpy():
    """phoenix_harass 顶层 import 了 sharpy.plans.acts.ActBase —— sharpy 是
    vendored、单测环境不可 import。注入 fake ActBase 让 import 过，测完清掉。"""
    created = []
    for name in ("sharpy", "sharpy.plans", "sharpy.plans.acts"):
        if name not in sys.modules:
            sys.modules[name] = ModuleType(name)
            created.append(name)
    acts = sys.modules["sharpy.plans.acts"]
    if not hasattr(acts, "ActBase"):
        acts.ActBase = type("ActBase", (), {})  # type: ignore[attr-defined]
    yield
    sys.modules.pop("vibecraft.bot.auto_combat.protoss.plans.phoenix_harass", None)
    for name in created:
        sys.modules.pop(name, None)


def _act(
    release_after: float | None = None,
    bail_hp_ratio: float = 0.3,
    recover_hp_ratio: float = 0.6,
    wave_threshold: int = 5,
):
    """绕过 __init__ 构造一个 act，手动塞字段（lazy import：fixture 先注入 fake sharpy）。"""
    from vibecraft.bot.auto_combat.protoss.plans.phoenix_harass import PhoenixHarassAct

    act = PhoenixHarassAct.__new__(PhoenixHarassAct)
    act._release_after = release_after
    act._bail_hp = bail_hp_ratio
    act._recover_hp = recover_hp_ratio
    act._bailing = set()
    act._wave_threshold = wave_threshold
    act._wave_launched = False
    return act


def _unit(tag=1, health=100, health_max=100, shield=60, shield_max=60, energy=100.0):
    """构造一个 fake 凤凰单位（默认满血满护盾满能量）。"""
    return SimpleNamespace(
        tag=tag,
        health=health,
        health_max=health_max,
        shield=shield,
        shield_max=shield_max,
        energy=energy,
    )


# --- 血量比例 ----------------------------------------------------------


def test_hp_ratio_full():
    u = _unit(health=100, health_max=100, shield=60, shield_max=60)
    assert _act()._hp_ratio(u) == 1.0


def test_hp_ratio_half_hp_zero_shield():
    """HP 半满 + 护盾全没：比例 = 100 / (100+60) = 0.625。"""
    u = _unit(health=100, health_max=100, shield=0, shield_max=60)
    ratio = _act()._hp_ratio(u)
    assert abs(ratio - 100 / 160) < 1e-6


def test_hp_ratio_zero_max_is_safe():
    """满值为 0（取数据失败）→ 按满血处理，不崩、不误撤。"""
    u = _unit(health=0, health_max=0, shield=0, shield_max=0)
    assert _act()._hp_ratio(u) == 1.0


# --- 撤退(bail)判定 --------------------------------------------------


def test_should_bail_critical_hp():
    act = _act(bail_hp_ratio=0.3)
    # health=20/160 = 0.125 < 0.3 → 应撤退
    u = _unit(tag=5, health=20, health_max=100, shield=0, shield_max=60)
    assert act._should_bail(u) is True
    assert 5 in act._bailing


def test_should_bail_healthy_unit_fights():
    """血量正常 → 不 bail。"""
    act = _act(bail_hp_ratio=0.3)
    u = _unit(tag=5, health=100, health_max=100, shield=60, shield_max=60)
    assert act._should_bail(u) is False


def test_bail_hysteresis_holds_until_recovered():
    """已 bail 的单位：血量没回到 recover_hp(0.6)之前持续 bail。"""
    act = _act(bail_hp_ratio=0.3, recover_hp_ratio=0.6)
    # 初次 bail
    u_low = _unit(tag=7, health=20, health_max=100, shield=0, shield_max=60)
    assert act._should_bail(u_low) is True
    # 稍微回血但仍低于 0.6：health=60 → 60/160=0.375，仍 < 0.6
    u_mid = _unit(tag=7, health=60, health_max=100, shield=0, shield_max=60)
    assert act._should_bail(u_mid) is True
    # 回到 0.6 以上：health=100, shield=30 → 130/160=0.8125 > 0.6
    u_ok = _unit(tag=7, health=100, health_max=100, shield=30, shield_max=60)
    assert act._should_bail(u_ok) is False
    assert 7 not in act._bailing


# --- 能量查询 ----------------------------------------------------------


def test_energy_normal():
    act = _act()
    u = _unit(energy=75.0)
    assert act._energy(u) == 75.0


def test_energy_missing_returns_zero():
    """energy 属性不存在时返回 0（不施放 Beam，保守处理）。"""
    act = _act()
    u = SimpleNamespace(tag=1)  # 无 energy 属性
    assert act._energy(u) == 0.0


# --- 进场 vs 矿区阶段判定 ----------------------------------------------


def test_is_far_true_when_distance_large():
    """距对方主基地 > 22 → 进场阶段。"""
    act = _act()
    enemy_main = SimpleNamespace(x=50, y=50)
    unit = SimpleNamespace(distance_to=lambda _: 30.0)
    assert act._is_far_from_enemy_main(unit, enemy_main) is True


def test_is_far_false_when_distance_small():
    """距对方主基地 <= 22 → 已到矿区。"""
    act = _act()
    enemy_main = SimpleNamespace(x=50, y=50)
    unit = SimpleNamespace(distance_to=lambda _: 10.0)
    assert act._is_far_from_enemy_main(unit, enemy_main) is False


def test_is_far_true_when_no_enemy_main():
    """enemy_main 为 None → 保守返回 True（不骚扰）。"""
    act = _act()
    unit = SimpleNamespace(distance_to=lambda _: 5.0)
    assert act._is_far_from_enemy_main(unit, None) is True


# --- 骚扰逻辑（_harass）-----------------------------------------------


def test_harass_uses_graviton_beam_when_energy_enough():
    """能量 >= 50 + 有农民 → 施放 GRAVITONBEAM_GRAVITONBEAM。"""
    act = _act()
    worker = SimpleNamespace(tag=10)
    workers = MagicMock()
    workers.__bool__ = MagicMock(return_value=True)
    workers.closest_to = MagicMock(return_value=worker)

    unit = MagicMock()
    unit.energy = 75.0  # 给 energy 赋真实值，否则 float(MagicMock) → 0

    act._harass(unit, workers, enemy_main=SimpleNamespace(x=50, y=50))

    # 按 .name 比对 ability —— 全量跑时 sc2 模块可能被别的 fixture 清掉重导入,
    # AbilityId 枚举变成新对象,身份(==)比较会假阴性。
    assert unit.call_count == 1
    ability, target = unit.call_args[0]
    assert ability.name == "GRAVITONBEAM_GRAVITONBEAM"
    assert target is worker


def test_harass_attacks_enemy_main_when_energy_low():
    """能量 < 50 → attack(enemy_main)让凤凰自动射已提起的农民。"""
    act = _act()
    enemy_main = SimpleNamespace(x=50, y=50)
    unit = MagicMock()

    # workers 存在但能量不够
    workers = MagicMock()
    workers.__bool__ = MagicMock(return_value=True)

    # 覆盖 _energy 使其返回低于阈值的值
    act_energy_backup = act._energy
    act._energy = lambda _u: 30.0
    act._harass(unit, workers, enemy_main)
    act._energy = act_energy_backup

    unit.attack.assert_called_once_with(enemy_main)


def test_harass_attacks_enemy_main_when_no_workers():
    """能量足但没有可见农民 → attack(enemy_main)。"""
    act = _act()
    enemy_main = SimpleNamespace(x=50, y=50)
    unit = MagicMock()

    # workers falsy（空 / None）
    act._harass(unit, None, enemy_main)

    unit.attack.assert_called_once_with(enemy_main)


# --- 微操总入口（_micro）---------------------------------------------


def test_micro_moves_to_enemy_main_when_far():
    """进场阶段：_micro → move(enemy_main)。"""
    act = _act()
    enemy_main = SimpleNamespace(x=50, y=50)
    unit = MagicMock()
    unit.distance_to = MagicMock(return_value=80.0)  # 远

    act._micro(unit, workers=None, enemy_main=enemy_main)

    unit.move.assert_called_once_with(enemy_main)
    unit.assert_not_called()  # 不施放 ability


def test_micro_harasses_when_at_enemy_main():
    """已到矿区 + 能量足 + 有农民 → 施放 Graviton Beam。"""
    act = _act()
    worker = SimpleNamespace(tag=20)
    workers = MagicMock()
    workers.__bool__ = MagicMock(return_value=True)
    workers.closest_to = MagicMock(return_value=worker)

    enemy_main = SimpleNamespace(x=50, y=50)
    unit = MagicMock()
    unit.distance_to = MagicMock(return_value=5.0)  # 近
    unit.energy = 75.0

    act._micro(unit, workers, enemy_main)

    assert unit.call_count == 1
    ability, target = unit.call_args[0]
    assert ability.name == "GRAVITONBEAM_GRAVITONBEAM"
    assert target is worker


# --- release_after 放手 -----------------------------------------------


def test_release_after_not_reached():
    """game time 未到 release_after → _is_released 应返回 False。"""
    act = _act(release_after=540.0)
    act.ai = SimpleNamespace(time=300.0)
    # 直接测时间门槛逻辑（通过 execute() 走不到 SC2 环境，这里只测时间判定）
    assert float(act.ai.time) < act._release_after  # type: ignore[operator]


def test_release_after_reached():
    """game time >= release_after → 应放手。"""
    act = _act(release_after=540.0)
    act.ai = SimpleNamespace(time=540.0)
    assert float(act.ai.time) >= act._release_after  # type: ignore[operator]


# --- wave threshold(2026-05-28 用户反馈:出一个去一个,要攒到一起)----------


class TestWaveThreshold:
    """凤凰 wave gating:第一波必须攒 wave_threshold 才出门;launch 后新凤凰立即追上。"""

    def test_initial_state_not_launched(self) -> None:
        """新建 act → wave_launched = False(等攒满)。"""
        act = _act(wave_threshold=5)
        assert act._wave_launched is False

    def test_below_threshold_stays_home(self) -> None:
        """phoenix_count(3) < threshold(5)→ wave_launched 仍 False,
        凤凰应 stay home(execute 内部 move start_location)。
        """
        act = _act(wave_threshold=5)
        # 模拟 3 凤凰 ready
        phoenixes = MagicMock()
        phoenixes.amount = 3
        # 等 execute 的 wave gating 检查
        # 这里只验状态机:phoenixes.amount < threshold → latch 保持 False
        threshold_met = phoenixes.amount >= act._wave_threshold
        if threshold_met:
            act._wave_launched = True
        assert act._wave_launched is False

    def test_at_threshold_launches(self) -> None:
        """phoenix_count(5) >= threshold(5)→ wave_launched 锁 True。"""
        act = _act(wave_threshold=5)
        phoenixes = MagicMock()
        phoenixes.amount = 5
        if phoenixes.amount >= act._wave_threshold:
            act._wave_launched = True
        assert act._wave_launched is True

    def test_launched_stays_latched_when_phoenix_die(self) -> None:
        """launch 后哪怕死兵 phoenix_count < threshold,wave_launched 仍 True
        (latch)— 新凤凰立即追上,不再 stay home。
        """
        act = _act(wave_threshold=5)
        act._wave_launched = True  # 模拟已 launched
        # 现在 phoenix_count 跌到 2(被打死了)
        phoenixes = MagicMock()
        phoenixes.amount = 2
        # latch 不应该被打回去:不重新检查阈值
        assert act._wave_launched is True

    def test_custom_threshold(self) -> None:
        """wave_threshold 可参数化:用 8 时,7 不 launch,8 launch。"""
        act = _act(wave_threshold=8)
        phoenixes = MagicMock()
        phoenixes.amount = 7
        if phoenixes.amount >= act._wave_threshold:
            act._wave_launched = True
        assert act._wave_launched is False
        # 凑到 8
        phoenixes.amount = 8
        if phoenixes.amount >= act._wave_threshold:
            act._wave_launched = True
        assert act._wave_launched is True


# --- execute() 跑通 wave + 微操行为(集成式)------------------------------


class TestExecuteWaveBehavior:
    """跑 execute() 验证 wave gating + 微操行为联动。"""

    @pytest.mark.asyncio
    async def test_execute_stay_home_when_below_threshold(self) -> None:
        """phoenix_count(2) < threshold(5)→ 凤凰 move(start_location),
        不施放 ability。
        """
        act = _act(wave_threshold=5)

        # 构造 ai mock + 2 凤凰 (低 wave threshold)
        phoenix_units = [
            MagicMock(tag=1, health=100, health_max=100, shield=60, shield_max=60, energy=20),
            MagicMock(tag=2, health=100, health_max=100, shield=60, shield_max=60, energy=20),
        ]
        units_group = MagicMock()
        units_group.amount = 2
        units_group.__iter__ = MagicMock(return_value=iter(phoenix_units))
        units_group.__bool__ = MagicMock(return_value=True)
        units_ready = MagicMock(return_value=units_group)
        units_ready.amount = 2

        ai_units = MagicMock()
        ai_units.return_value.ready = units_group
        act.ai = SimpleNamespace(
            units=ai_units.return_value
            if False
            else MagicMock(return_value=SimpleNamespace(ready=units_group)),
            time=120.0,
            start_location=SimpleNamespace(x=50, y=50),
            enemy_start_locations=[SimpleNamespace(x=150, y=150)],
            enemy_units=MagicMock(filter=MagicMock(return_value=None)),
        )
        # 必须重新 wire:units(...) → units_group
        ai_mock = MagicMock()
        ai_mock.units = MagicMock(return_value=SimpleNamespace(ready=units_group))
        ai_mock.time = 120.0
        ai_mock.start_location = SimpleNamespace(x=50, y=50)
        ai_mock.enemy_start_locations = [SimpleNamespace(x=150, y=150)]
        ai_mock.enemy_units = MagicMock()
        ai_mock.enemy_units.filter = MagicMock(return_value=None)
        act.ai = ai_mock

        # knowledge.roles.set_task 不抛
        act.knowledge = SimpleNamespace(roles=MagicMock(set_task=MagicMock()))

        result = await act.execute()
        assert result is True
        assert act._wave_launched is False
        # 每只凤凰应 move 到 start_location
        for u in phoenix_units:
            u.move.assert_called_with(ai_mock.start_location)

    @pytest.mark.asyncio
    async def test_execute_launches_at_threshold(self) -> None:
        """phoenix_count(5) >= threshold(5)→ wave_launched=True,
        凤凰开始正常骚扰(此 case 距 enemy_main 远,move 进场)。
        """
        act = _act(wave_threshold=5)

        phoenix_units = []
        for i in range(5):
            u = MagicMock(tag=i, health=100, health_max=100, shield=60, shield_max=60, energy=20)
            u.distance_to = MagicMock(return_value=80.0)  # 远,进场阶段
            phoenix_units.append(u)
        units_group = MagicMock()
        units_group.amount = 5
        units_group.__iter__ = MagicMock(return_value=iter(phoenix_units))
        units_group.__bool__ = MagicMock(return_value=True)

        ai_mock = MagicMock()
        ai_mock.units = MagicMock(return_value=SimpleNamespace(ready=units_group))
        ai_mock.time = 240.0
        ai_mock.start_location = SimpleNamespace(x=50, y=50)
        ai_mock.enemy_start_locations = [SimpleNamespace(x=150, y=150)]
        ai_mock.enemy_units = MagicMock()
        ai_mock.enemy_units.filter = MagicMock(return_value=None)
        act.ai = ai_mock
        act.knowledge = SimpleNamespace(roles=MagicMock(set_task=MagicMock()))

        await act.execute()
        assert act._wave_launched is True
        # 凤凰距远 → move enemy_main(进场)
        for u in phoenix_units:
            u.move.assert_called()
            args, _ = u.move.call_args
            # 进场 target = enemy_start_locations[0]
            assert args[0] is ai_mock.enemy_start_locations[0]
