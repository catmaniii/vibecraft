"""PrismWarpDropAct DONE 后 DT 微操测试。

Task 8b spec:
- DONE 状态后每 tick 调 DtHarassMicro.tick()
- 验证 DT raid 微操:dt.attack(worker) 被调
- 验证 DT 被打/被 detector → released,clear Reserved
- 验证家里 DT（d_home < 30）不被接管
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _fake_sharpy():
    """注入 fake sharpy 让 import 过。"""
    created = []
    for name in (
        "sharpy",
        "sharpy.plans",
        "sharpy.plans.acts",
        "sharpy.managers",
        "sharpy.managers.core",
        "sharpy.managers.core.roles",
    ):
        if name not in sys.modules:
            sys.modules[name] = ModuleType(name)
            created.append(name)
    acts = sys.modules["sharpy.plans.acts"]
    if not hasattr(acts, "ActBase"):
        acts.ActBase = type("ActBase", (), {"__init__": lambda self: None})  # type: ignore[attr-defined]
    roles_mod = sys.modules["sharpy.managers.core.roles"]
    if not hasattr(roles_mod, "UnitTask"):
        roles_mod.UnitTask = type("UnitTask", (), {"Reserved": 1, "Idle": 2})()  # type: ignore[attr-defined]
    yield
    for mod in (
        "vibecraft.bot.auto_combat.protoss.plans.dt_micro",
        "vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act",
    ):
        sys.modules.pop(mod, None)
    for name in created:
        sys.modules.pop(name, None)


def _mock_position(x: float, y: float):
    from sc2.position import Point2

    return Point2((x, y))


def _make_dt(
    tag: int, pos, health: float = 40.0, shield: float = 80.0, build_progress: float = 1.0
):
    from sc2.ids.unit_typeid import UnitTypeId

    dt = MagicMock()
    dt.type_id = UnitTypeId.DARKTEMPLAR
    dt.position = pos
    dt.tag = tag
    dt.build_progress = build_progress
    dt.health = health
    dt.shield = shield
    dt.distance_to = lambda p: ((dt.position.x - p.x) ** 2 + (dt.position.y - p.y) ** 2) ** 0.5
    return dt


def _make_worker(tag: int, pos):
    from sc2.ids.unit_typeid import UnitTypeId

    w = MagicMock()
    w.type_id = UnitTypeId.PROBE
    w.tag = tag
    w.position = pos
    w.distance_to = lambda p: ((w.position.x - p.x) ** 2 + (w.position.y - p.y) ** 2) ** 0.5
    return w


def _make_ai(
    dts: list, workers: list | None = None, home_pos=None, enemy_pos=None, game_time: float = 200.0
):
    """构造最小 ai mock。"""
    from sc2.ids.unit_typeid import UnitTypeId

    if home_pos is None:
        home_pos = _mock_position(127.0, 119.0)
    if enemy_pos is None:
        enemy_pos = _mock_position(32.0, 32.0)

    ai = MagicMock()
    ai.time = game_time
    ai.start_location = home_pos
    ai.enemy_start_locations = [enemy_pos]

    # units(DARKTEMPLAR) 返回 mock collection
    def _units_call(t):
        if t == UnitTypeId.DARKTEMPLAR:
            m = MagicMock()
            m.__iter__ = lambda self: iter(dts)
            m.__bool__ = lambda self: len(dts) > 0
            m.amount = len(dts)
            m.ready = dts
            # ready 也是 mock 可迭代
            return m
        m = MagicMock()
        m.__iter__ = lambda self: iter([])
        m.__bool__ = lambda self: False
        m.amount = 0
        m.ready = []
        return m

    ai.units = _units_call

    # enemy_units.filter → workers collection
    if workers is not None:
        workers_collection = MagicMock()
        workers_collection.exists = len(workers) > 0
        workers_collection.__bool__ = lambda self: len(workers) > 0
        workers_collection.__iter__ = lambda self: iter(workers)

        # closer_than 返回 workers 中足够近的子集
        def _closer_than(dist, pos):
            nearby = [w for w in workers if w.distance_to(pos) < dist]
            m = MagicMock()
            m.exists = len(nearby) > 0
            m.__iter__ = lambda self: iter(nearby)
            if nearby:
                m.closest_to = lambda ref: min(
                    nearby,
                    key=lambda w: w.distance_to(ref.position if hasattr(ref, "position") else ref),
                )
            return m

        workers_collection.closer_than = _closer_than
        ai.enemy_units.filter = lambda f: workers_collection
    else:
        empty = MagicMock()
        empty.exists = False
        empty.__bool__ = lambda self: False
        ai.enemy_units.filter = lambda f: empty

    # enemy_units / enemy_structures for detector check
    ai.enemy_units = MagicMock(
        return_value=MagicMock(__iter__=lambda self: iter([]), __bool__=lambda self: False)
    )
    ai.enemy_structures = MagicMock(
        return_value=MagicMock(__iter__=lambda self: iter([]), __bool__=lambda self: False)
    )

    if workers is not None:
        workers_collection = MagicMock()
        workers_collection.exists = len(workers) > 0
        workers_collection.__iter__ = lambda self: iter(workers)

        def _closer_than(dist, pos):
            nearby = [w for w in workers if w.distance_to(pos) < dist]
            m = MagicMock()
            m.exists = len(nearby) > 0
            m.__iter__ = lambda self: iter(nearby)
            if nearby:
                m.closest_to = lambda ref: min(
                    nearby,
                    key=lambda w: w.distance_to(ref.position if hasattr(ref, "position") else ref),
                )
            return m

        workers_collection.closer_than = _closer_than
        ai.enemy_units.filter = lambda f: workers_collection

    return ai


def _make_knowledge():
    k = MagicMock()
    k.roles.set_task = MagicMock()
    k.roles.clear_task = MagicMock()
    return k


# ============================================================
# DtHarassMicro 直接测试
# ============================================================


class TestDtHarassMicroRaidCommand:
    """DtHarassMicro.tick() → DT 到矿区 → attack worker。"""

    def test_dt_at_enemy_attacks_worker(self):
        """DT 已在矿区附近(d < 22)，有可见农民 → dt.attack(worker) 被调。"""
        from vibecraft.bot.auto_combat.protoss.plans.dt_micro import DtHarassMicro

        # DT 在敌方基地 (32,32)，离 home (127,119) >> 30
        dt = _make_dt(tag=101, pos=_mock_position(35.0, 35.0))
        worker = _make_worker(tag=200, pos=_mock_position(33.0, 33.0))

        ai = _make_ai([dt], workers=[worker])
        k = _make_knowledge()

        micro = DtHarassMicro()
        micro.tick(ai, k)

        dt.attack.assert_called()

    def test_dt_far_from_enemy_attack_moves(self):
        """DT 距矿区 > 22 格 → dt.attack(enemy_main) 推进。"""
        from vibecraft.bot.auto_combat.protoss.plans.dt_micro import DtHarassMicro

        # DT 在中途 (80,80)，离 home (127,119) >> 30，离 enemy (32,32) >> 22
        dt = _make_dt(tag=101, pos=_mock_position(80.0, 80.0))

        ai = _make_ai([dt], workers=[])
        k = _make_knowledge()

        micro = DtHarassMicro()
        micro.tick(ai, k)

        dt.attack.assert_called()

    def test_home_dt_not_touched(self):
        """DT 在 home 附近(d < 30) → 不接管，attack 不调。"""
        from vibecraft.bot.auto_combat.protoss.plans.dt_micro import DtHarassMicro

        # DT 在 home (127,119) 旁边
        dt = _make_dt(tag=101, pos=_mock_position(130.0, 120.0))

        ai = _make_ai([dt], workers=[])
        k = _make_knowledge()

        micro = DtHarassMicro()
        micro.tick(ai, k)

        dt.attack.assert_not_called()

    def test_dt_released_when_hp_drops(self):
        """DT HP 比上 tick 低 → released: clear_task 被调，attack 不再调。"""
        from vibecraft.bot.auto_combat.protoss.plans.dt_micro import DtHarassMicro

        dt = _make_dt(tag=101, pos=_mock_position(35.0, 35.0), health=40.0, shield=80.0)
        ai = _make_ai([dt], workers=[])
        k = _make_knowledge()

        micro = DtHarassMicro()
        # 第一 tick：建立 state + 记录 hp=120
        micro.tick(ai, k)
        dt.attack.reset_mock()

        # 模拟 HP 下降
        dt.health = 30.0  # hp 掉了
        micro.tick(ai, k)

        # released → clear_task 调过
        k.roles.clear_task.assert_called()
        # released 后 attack 不再被调
        dt.attack.assert_not_called()

    def test_dead_dt_cleaned_up(self):
        """DT 死亡（从 ai.units 消失）→ 其 state 被清除（不内存泄漏）。"""
        from vibecraft.bot.auto_combat.protoss.plans.dt_micro import DtHarassMicro

        dt = _make_dt(tag=101, pos=_mock_position(35.0, 35.0))
        ai = _make_ai([dt], workers=[])
        k = _make_knowledge()

        micro = DtHarassMicro()
        micro.tick(ai, k)
        assert 101 in micro._dt_raid_state

        # 下 tick DT 消失
        ai2 = _make_ai([], workers=[])
        micro.tick(ai2, k)

        assert 101 not in micro._dt_raid_state


class TestDtHarassMicroReserved:
    """raid state DT 应被标 Reserved。"""

    def test_raid_dt_is_reserved(self):
        from vibecraft.bot.auto_combat.protoss.plans.dt_micro import DtHarassMicro

        dt = _make_dt(tag=101, pos=_mock_position(35.0, 35.0))
        ai = _make_ai([dt], workers=[])
        k = _make_knowledge()

        micro = DtHarassMicro()
        micro.tick(ai, k)

        # set_task(Reserved, dt) 应被调
        k.roles.set_task.assert_called()
        call_args = k.roles.set_task.call_args
        assert call_args[0][1] is dt


# ============================================================
# PrismWarpDropAct DONE 状态触发 DT 微操
# ============================================================


class TestPrismWarpDropActDoneDtMicro:
    """DONE 状态时 DtHarassMicro.tick 被调 (cargo_unit=DARKTEMPLAR)。"""

    @pytest.mark.asyncio
    async def test_done_state_calls_dt_micro_tick(self):
        """DONE + DARKTEMPLAR → _dt_micro.tick() 被调。"""
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            PrismWarpDropAct,
            WarpDropState,
        )
        from vibecraft.bot.named_spot import DropTarget

        warp_pos = DropTarget(
            position=_mock_position(60, 60),
            zone_kind="production",
            base_index=0,
            source_spec="enemy_main:ramp_outside",
        )
        final_pos = DropTarget(
            position=_mock_position(48, 28),
            zone_kind="production",
            base_index=0,
            source_spec="enemy_main:production",
        )

        act = PrismWarpDropAct(
            cargo_unit=UnitTypeId.DARKTEMPLAR,
            cargo_count=4,
            warp_pos=warp_pos,
            final_drop_pos=final_pos,
        )
        act.ai = MagicMock()
        act.ai.time = 300.0
        act.knowledge = MagicMock()
        act._state = WarpDropState.DONE

        # mock _dt_micro.tick
        act._dt_micro = MagicMock()

        result = await act.execute()

        act._dt_micro.tick.assert_called_once_with(act.ai, act.knowledge)
        # DONE 状态返回 False（保持 act 在 plan 持续 tick）
        assert result is False

    @pytest.mark.asyncio
    async def test_non_done_state_also_calls_dt_micro(self):
        """非 DONE 状态(如 RETREAT_HOME)也应调 _dt_micro.tick()（对齐 DTPrismHarass）。"""
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            PrismWarpDropAct,
            WarpDropState,
        )
        from vibecraft.bot.named_spot import DropTarget

        warp_pos = DropTarget(
            position=_mock_position(60, 60),
            zone_kind="production",
            base_index=0,
            source_spec="enemy_main:ramp_outside",
        )
        final_pos = DropTarget(
            position=_mock_position(48, 28),
            zone_kind="production",
            base_index=0,
            source_spec="enemy_main:production",
        )

        act = PrismWarpDropAct(
            cargo_unit=UnitTypeId.DARKTEMPLAR,
            cargo_count=4,
            warp_pos=warp_pos,
            final_drop_pos=final_pos,
        )
        act.ai = MagicMock()
        act.ai.time = 300.0
        act.ai.start_location = _mock_position(127.0, 119.0)
        act.knowledge = MagicMock()
        act._state = WarpDropState.RETREAT_HOME
        act._state_entered_ts = 300.0  # just entered, cooldown not over

        # mock prism at home
        from sc2.ids.unit_typeid import UnitTypeId as UType

        prism = MagicMock()
        prism.type_id = UType.WARPPRISM
        prism.tag = 1
        prism.health = 200
        prism.shield = 100
        prism.health_max = 200
        prism.shield_max = 100
        prism.position = _mock_position(127.0, 119.0)
        prism.distance_to = lambda p: 0.5

        empty_col = MagicMock()
        empty_col.__iter__ = lambda self: iter([])
        empty_col.__bool__ = lambda self: False
        empty_col.amount = 0
        empty_col.tags_in = lambda tags: []
        act.ai.units = MagicMock()
        act.ai.units.of_type = lambda types: (
            [prism] if any(t in types for t in [UType.WARPPRISM, UType.WARPPRISMPHASING]) else []
        )

        # Simplify: just mock _dt_micro
        act._dt_micro = MagicMock()

        await act.execute()

        act._dt_micro.tick.assert_called()

    @pytest.mark.asyncio
    async def test_done_state_non_darktemplar_no_micro(self):
        """DONE + 非 DARKTEMPLAR → _dt_micro.tick() 不调。"""
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            PrismWarpDropAct,
            WarpDropState,
        )
        from vibecraft.bot.named_spot import DropTarget

        warp_pos = DropTarget(
            position=_mock_position(60, 60),
            zone_kind="production",
            base_index=0,
            source_spec="enemy_main:ramp_outside",
        )
        final_pos = DropTarget(
            position=_mock_position(48, 28),
            zone_kind="production",
            base_index=0,
            source_spec="enemy_main:production",
        )

        act = PrismWarpDropAct(
            cargo_unit=UnitTypeId.ZEALOT,  # 非 DT
            cargo_count=4,
            warp_pos=warp_pos,
            final_drop_pos=final_pos,
        )
        act.ai = MagicMock()
        act.ai.time = 300.0
        act.knowledge = MagicMock()
        act._state = WarpDropState.DONE

        act._dt_micro = MagicMock()

        await act.execute()

        act._dt_micro.tick.assert_not_called()
