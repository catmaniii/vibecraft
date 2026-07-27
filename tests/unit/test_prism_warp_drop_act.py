"""PrismWarpDropAct state machine 单测(mock prism + DT + warp)。

二段空投 9 状态机:
  IDLE → FLY_TO_WARP_SPOT → DEPLOY_PHASING → WARP_UNITS →
  WAIT_WARP_COMPLETE → MORPH_TRANSPORT → LOAD_CARGO →
  FLY_TO_FINAL → UNLOAD_FINAL → DONE
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _fake_sharpy():
    """注入 fake sharpy 让 import 过,不需真实 sharpy 安装。"""
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
    sys.modules.pop("vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act", None)
    for name in created:
        sys.modules.pop(name, None)


def _mock_position(x: float, y: float):
    from sc2.position import Point2

    return Point2((x, y))


@pytest.fixture
def warp_target():
    from vibecraft.bot.named_spot import DropTarget

    return DropTarget(
        position=_mock_position(60, 60),
        zone_kind="production",
        base_index=0,
        source_spec="enemy_main:ramp_outside",
    )


@pytest.fixture
def final_target():
    from vibecraft.bot.named_spot import DropTarget

    return DropTarget(
        position=_mock_position(48, 28),
        zone_kind="production",
        base_index=0,
        source_spec="enemy_main:production",
    )


def _make_prism(pos, phasing: bool = False):
    from sc2.ids.unit_typeid import UnitTypeId

    prism = MagicMock()
    prism.type_id = UnitTypeId.WARPPRISMPHASING if phasing else UnitTypeId.WARPPRISM
    prism.position = pos
    prism.cargo_used = 0
    prism.health = 200
    prism.shield = 100
    prism.health_max = 200
    prism.shield_max = 100
    prism.tag = 1
    prism.distance_to = lambda p: (
        ((prism.position.x - p.x) ** 2 + (prism.position.y - p.y) ** 2) ** 0.5
    )
    return prism


def _make_dt(tag: int, pos, build_progress: float = 1.0):
    from sc2.ids.unit_typeid import UnitTypeId

    dt = MagicMock()
    dt.type_id = UnitTypeId.DARKTEMPLAR
    dt.position = pos
    dt.tag = tag
    dt.build_progress = build_progress
    dt.health = 40
    dt.shield = 80
    dt.distance_to = lambda p: ((dt.position.x - p.x) ** 2 + (dt.position.y - p.y) ** 2) ** 0.5
    return dt


def _make_bot(prism, dts=None, prism_at_warp_spot: bool = False):
    """最小 bot mock,含 1 棱镜 + N DT。"""
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.position import Point2

    if dts is None:
        dts = []

    bot = MagicMock()
    bot.start_location = Point2((127, 119))
    bot.time = 100.0
    bot.knowledge.zone_manager.enemy_expansion_zones = []
    bot.game_info.map_center = Point2((80, 80))
    bot.game_info.playable_area = MagicMock(x=0, y=0, width=160, height=160)
    bot.supply_left = 20

    def _units_of_type(types):
        result = []
        if UnitTypeId.WARPPRISM in types or UnitTypeId.WARPPRISMPHASING in types:
            result.append(prism)
        m = MagicMock()
        m.__iter__ = lambda self: iter(result)
        m.__bool__ = lambda self: len(result) > 0
        m.amount = len(result)
        m.tags_in = lambda tags: [u for u in result if u.tag in tags]
        return m

    def _units_call(t):
        if t == UnitTypeId.DARKTEMPLAR:
            m = MagicMock()
            m.__iter__ = lambda self: iter(dts)
            m.__bool__ = lambda self: len(dts) > 0
            m.amount = len(dts)
            m.ready = dts
            return m
        if t == UnitTypeId.WARPPRISMPHASING:
            phasings = [prism] if prism.type_id == UnitTypeId.WARPPRISMPHASING else []
            m = MagicMock()
            m.__iter__ = lambda self: iter(phasings)
            m.__bool__ = lambda self: len(phasings) > 0
            m.amount = len(phasings)
            if phasings:
                m.first = phasings[0]
            return m
        m = MagicMock()
        m.__iter__ = lambda self: iter([])
        m.__bool__ = lambda self: False
        m.amount = 0
        m.ready = []
        return m

    units_callable = _units_call
    units_callable.of_type = _units_of_type
    bot.units = units_callable

    # warpgates (empty by default)
    bot.structures = MagicMock(return_value=MagicMock(ready=[], __iter__=lambda s: iter([])))
    bot.can_afford = MagicMock(return_value=True)

    return bot


def _make_act(warp_target, final_target):
    from sc2.ids.unit_typeid import UnitTypeId

    from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
        PrismWarpDropAct,
    )

    return PrismWarpDropAct(
        cargo_unit=UnitTypeId.DARKTEMPLAR,
        cargo_count=4,
        warp_pos=warp_target,
        final_drop_pos=final_target,
        after_unload="attack_workers",
    )


def _inject(act, bot):
    """注入 sharpy ActBase 字段。"""
    act.ai = bot
    act.knowledge = MagicMock()
    act.knowledge.roles.set_task = MagicMock()
    act.knowledge.roles.clear_task = MagicMock()
    act.knowledge.cooldown_manager.is_ready = MagicMock(return_value=True)
    # cooldown_manager.used_ability
    act.knowledge.cooldown_manager.used_ability = MagicMock()
    return act


# ============================================================
# Test classes
# ============================================================


class TestPrismWarpDropActInit:
    """Task 6 TestInit: 初始状态 IDLE + 字段 stored。"""

    def test_initial_state_idle(self, warp_target, final_target):
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            PrismWarpDropAct,
            WarpDropState,
        )

        act = PrismWarpDropAct(
            cargo_unit=UnitTypeId.DARKTEMPLAR,
            cargo_count=4,
            warp_pos=warp_target,
            final_drop_pos=final_target,
            after_unload="attack_workers",
        )
        assert act._state == WarpDropState.IDLE

    def test_params_stored(self, warp_target, final_target):
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            PrismWarpDropAct,
            WarpDropState,
        )

        act = PrismWarpDropAct(
            cargo_unit=UnitTypeId.DARKTEMPLAR,
            cargo_count=4,
            warp_pos=warp_target,
            final_drop_pos=final_target,
            after_unload="retreat",
        )
        assert act.cargo_unit == UnitTypeId.DARKTEMPLAR
        assert act.cargo_count == 4
        assert act.warp_pos is warp_target
        assert act.final_drop_pos is final_target
        assert act.after_unload == "retreat"
        assert act._state == WarpDropState.IDLE
        assert act._prism_tag is None
        assert act._waypoints is None
        assert act._warp_timeout_start is None


class TestFlyToWarpSpot:
    """Task 6 TestFlyToWarpSpot: 棱镜飞到 warp_pos 后切 DEPLOY_PHASING。"""

    @pytest.mark.asyncio
    async def test_at_warp_spot_transitions_to_deploy(self, warp_target, final_target):
        """棱镜已在 warp_pos 附近 → 切 DEPLOY_PHASING。"""
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        # prism 已在 warp_spot 附近(距 < 3)
        prism = _make_prism(_mock_position(60.5, 60.5))  # 距 (60,60) = ~0.7
        bot = _make_bot(prism)
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.FLY_TO_WARP_SPOT
        # waypoints = [start, warp_pos] — prism 已到 warp_pos
        act._waypoints = [_mock_position(127, 119), _mock_position(60, 60)]
        act._wp_idx = 1  # already at last waypoint

        await act.execute()

        assert act._state == WarpDropState.DEPLOY_PHASING

    @pytest.mark.asyncio
    async def test_moves_toward_warp_spot(self, warp_target, final_target):
        """棱镜未到 warp_pos → prism.move 被调用。"""
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(127, 119))  # 距 warp_pos (60,60) 很远
        bot = _make_bot(prism)
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.FLY_TO_WARP_SPOT
        act._waypoints = [_mock_position(127, 119), _mock_position(60, 60)]
        act._wp_idx = 0

        await act.execute()

        prism.move.assert_called()


class TestStateChain:
    """2026-05-24 用户:state 切完同 tick chain dispatch 下个 handler 省 tick 延迟。"""

    @pytest.mark.asyncio
    async def test_fly_arrival_chains_to_deploy_morph(self, warp_target, final_target):
        """到达 warp_pos 同 tick: FLY → DEPLOY → 发 morph 命令(原来要 2 tick)。"""
        from sc2.ids.ability_id import AbilityId

        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(60.5, 60.5), phasing=False)  # arrived
        bot = _make_bot(prism)
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.FLY_TO_WARP_SPOT
        act._waypoints = [_mock_position(127, 119), _mock_position(60, 60)]
        act._wp_idx = 1

        await act.execute()

        # state 切到 DEPLOY_PHASING(FLY handler 末尾)
        assert act._state == WarpDropState.DEPLOY_PHASING
        # 同 tick chain dispatch DEPLOY handler → 发 morph 命令(value of chain)
        prism.assert_called_with(AbilityId.MORPH_WARPPRISMPHASINGMODE)

    @pytest.mark.asyncio
    async def test_no_transition_no_chain(self, warp_target, final_target):
        """state 没变化时 chain loop break(不无限递归)。"""
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        # prism 在 warp_pos 但已是 phasing → FLY 不切 state(需 morph transport 先)
        prism = _make_prism(_mock_position(60.5, 60.5), phasing=True)
        bot = _make_bot(prism)
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.FLY_TO_WARP_SPOT
        act._waypoints = [_mock_position(127, 119), _mock_position(60, 60)]
        act._wp_idx = 1

        await act.execute()
        # state 没变(FLY handler 在 phasing 状态下调 morph transport 然后 return)
        assert act._state == WarpDropState.FLY_TO_WARP_SPOT


class TestDeployPhasing:
    """Task 6 TestDeployPhasing: morph 调用 + 切 WARP_UNITS。"""

    @pytest.mark.asyncio
    async def test_morph_to_phasing_mode(self, warp_target, final_target):
        """DEPLOY_PHASING: prism 是 transport 形态 → 调 MORPH_WARPPRISMPHASINGMODE。"""
        from sc2.ids.ability_id import AbilityId

        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(60, 60), phasing=False)
        bot = _make_bot(prism)
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.DEPLOY_PHASING

        await act.execute()

        prism.assert_called_with(AbilityId.MORPH_WARPPRISMPHASINGMODE)

    @pytest.mark.asyncio
    async def test_transitions_to_warp_units_when_phasing(self, warp_target, final_target):
        """DEPLOY_PHASING: prism 已是 phasing 形态 → 切 WARP_UNITS。"""
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(60, 60), phasing=True)
        bot = _make_bot(prism)
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.DEPLOY_PHASING

        await act.execute()

        assert act._state == WarpDropState.WARP_UNITS


class TestWarpUnitsGating:
    """WARP_UNITS 状态转换门控(2026-05-24 修 Bug 1):
    只有 ready+warping >= cargo_count 才切 WAIT_WARP_COMPLETE,
    否则留在 WARP_UNITS 下 tick 重试。"""

    @pytest.mark.asyncio
    async def test_stays_in_warp_units_when_nothing_warped(self, warp_target, final_target):
        """WARP_UNITS:无 warpgate(模拟没钱/没人口/cd 中)+ 附近 0 DT
        → 0 warped、ready+warping = 0 → 留在 WARP_UNITS 下 tick 重试。"""
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(60, 60), phasing=True)
        bot = _make_bot(prism, dts=[])  # 无 DT、无 warpgate
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.WARP_UNITS

        await act.execute()

        # 必须留在 WARP_UNITS,不能切 WAIT
        assert act._state == WarpDropState.WARP_UNITS

    @pytest.mark.asyncio
    async def test_transitions_when_enough_warping(self, warp_target, final_target):
        """WARP_UNITS:附近 4 个 DT 正在 warp(build_progress=0.3)
        → ready+warping = 4 >= cargo_count → 切 WAIT_WARP_COMPLETE。"""
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(60, 60), phasing=True)
        # 4 个 warping DT 在 phasing 范围内
        dts = [
            _make_dt(tag=10 + i, pos=_mock_position(61 + i, 60), build_progress=0.3)
            for i in range(4)
        ]
        bot = _make_bot(prism, dts=dts)
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.WARP_UNITS

        await act.execute()

        assert act._state == WarpDropState.WAIT_WARP_COMPLETE


class TestWaitWarpComplete:
    """Task 6 TestWaitWarp: cargo build_progress >= 1 全部 → 切 MORPH_TRANSPORT。"""

    @pytest.mark.asyncio
    async def test_transitions_when_all_dts_complete(self, warp_target, final_target):
        """WAIT_WARP_COMPLETE: 4 个 DT build_progress=1.0 → 切 MORPH_TRANSPORT。"""
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(60, 60), phasing=True)
        # 4 DT 已完成 warp,在 prism 附近
        dts = [
            _make_dt(tag=10 + i, pos=_mock_position(61 + i, 60), build_progress=1.0)
            for i in range(4)
        ]
        bot = _make_bot(prism, dts=dts)
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.WAIT_WARP_COMPLETE

        await act.execute()

        assert act._state == WarpDropState.MORPH_TRANSPORT

    @pytest.mark.asyncio
    async def test_stays_in_wait_when_dts_not_complete(self, warp_target, final_target):
        """WAIT_WARP_COMPLETE: DT 未完成 → 保持 WAIT_WARP_COMPLETE。"""
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(60, 60), phasing=True)
        # 只有 2 个 DT 完成,2 个未完成
        dts = [
            _make_dt(tag=10 + i, pos=_mock_position(61 + i, 60), build_progress=1.0)
            for i in range(2)
        ] + [
            _make_dt(tag=20 + i, pos=_mock_position(63 + i, 60), build_progress=0.5)
            for i in range(2)
        ]
        bot = _make_bot(prism, dts=dts)
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.WAIT_WARP_COMPLETE
        act._warp_timeout_start = bot.time  # 刚开始,未超时

        await act.execute()

        assert act._state == WarpDropState.WAIT_WARP_COMPLETE

    @pytest.mark.asyncio
    async def test_timeout_transitions_to_morph_transport(self, warp_target, final_target):
        """WAIT_WARP_COMPLETE: 超时(60s)→ 切 MORPH_TRANSPORT(防 warp 卡死)。"""
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(60, 60), phasing=True)
        # DT 未完成
        dts = [
            _make_dt(tag=10 + i, pos=_mock_position(61 + i, 60), build_progress=0.3)
            for i in range(4)
        ]
        bot = _make_bot(prism, dts=dts)
        bot.time = 200.0

        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.WAIT_WARP_COMPLETE
        act._warp_timeout_start = 100.0  # 100s 前开始 → 超时

        await act.execute()

        assert act._state == WarpDropState.MORPH_TRANSPORT


class TestMorphTransport:
    """MORPH_TRANSPORT: 调 MORPH_WARPPRISMTRANSPORTMODE → 切 LOAD_CARGO。"""

    @pytest.mark.asyncio
    async def test_morph_to_transport_mode(self, warp_target, final_target):
        from sc2.ids.ability_id import AbilityId

        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(60, 60), phasing=True)
        bot = _make_bot(prism)
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.MORPH_TRANSPORT

        await act.execute()

        prism.assert_called_with(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)

    @pytest.mark.asyncio
    async def test_transitions_to_load_when_transport(self, warp_target, final_target):
        """MORPH_TRANSPORT: prism 已是 transport 形态 → 切 LOAD_CARGO。"""
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(60, 60), phasing=False)
        bot = _make_bot(prism)
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.MORPH_TRANSPORT

        await act.execute()

        assert act._state == WarpDropState.LOAD_CARGO


class TestLoadCargo:
    """LOAD_CARGO: smart-cast 已 warp 的 DT 上船 → cargo 满切 FLY_TO_FINAL。"""

    @pytest.mark.asyncio
    async def test_smart_cast_dts_onto_prism(self, warp_target, final_target):
        """LOAD_CARGO: 每个 DT 应被 smart(prism)。"""
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(60, 60), phasing=False)
        dts = [_make_dt(tag=10 + i, pos=_mock_position(61 + i, 60)) for i in range(4)]
        bot = _make_bot(prism, dts=dts)
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.LOAD_CARGO

        await act.execute()

        for dt in dts:
            dt.smart.assert_called_with(prism)

    @pytest.mark.asyncio
    async def test_transitions_to_fly_final_when_cargo_full(self, warp_target, final_target):
        """LOAD_CARGO: cargo_used = cargo_count 且无散落 DT → 切 FLY_TO_FINAL。"""
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(60, 60), phasing=False)
        prism.cargo_used = 4  # 全上船了
        # 无散落 DT
        bot = _make_bot(prism, dts=[])
        bot.time = 200.0
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.LOAD_CARGO
        act._load_since = 100.0  # 已装载一段时间

        await act.execute()

        assert act._state == WarpDropState.FLY_TO_FINAL


class TestLoadCargoScope:
    """LOAD_CARGO smart-cast 距离过滤(2026-05-24 修 Bug 3):
    只对棱镜附近的 DT 调 smart,不能拉家里和敌方基地骚扰中的 DT。"""

    @pytest.mark.asyncio
    async def test_smart_cast_skips_distant_dts(self, warp_target, final_target):
        """LOAD_CARGO:近的 DT(距 prism < 20)smart;远的 DT(距 > 20)不动。

        典型情况:棱镜在敌方主矿,家里的 DT 应该不动(让 sharpy 别的 plan 管),
        敌方基地骚扰中的 DT 也别拉回(超出 20 即视为"不在棱镜附近")。
        """
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(60, 60), phasing=False)
        near_dts = [_make_dt(tag=10 + i, pos=_mock_position(61 + i, 60)) for i in range(2)]
        # 远 DT:一个在家(127, 119),一个在敌方主矿别处(48, 28)
        far_home = _make_dt(tag=20, pos=_mock_position(127, 119))
        far_harass = _make_dt(tag=21, pos=_mock_position(48, 28))
        all_dts = [*near_dts, far_home, far_harass]
        bot = _make_bot(prism, dts=all_dts)
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.LOAD_CARGO

        await act.execute()

        for dt in near_dts:
            dt.smart.assert_called_with(prism)
        far_home.smart.assert_not_called()
        far_harass.smart.assert_not_called()


class TestFlyToFinal:
    """FLY_TO_FINAL: 飞向 final_drop_pos → 到达切 UNLOAD_FINAL。"""

    @pytest.mark.asyncio
    async def test_moves_toward_final_pos(self, warp_target, final_target):
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(60, 60))  # 距 final (48,28) 较远
        prism.cargo_used = 4
        bot = _make_bot(prism)
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.FLY_TO_FINAL
        act._final_waypoints = [_mock_position(60, 60), _mock_position(48, 28)]
        act._final_wp_idx = 0

        await act.execute()

        prism.move.assert_called()

    @pytest.mark.asyncio
    async def test_transitions_to_unload_when_arrived(self, warp_target, final_target):
        """prism 已在 final_drop_pos 附近 → 切 UNLOAD_FINAL。"""
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(48.5, 28.5))  # 距 (48,28) < 3
        prism.cargo_used = 4
        bot = _make_bot(prism)
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.FLY_TO_FINAL
        act._final_waypoints = [_mock_position(60, 60), _mock_position(48, 28)]
        act._final_wp_idx = 1  # at last waypoint already

        await act.execute()

        assert act._state == WarpDropState.UNLOAD_FINAL


class TestUnloadFinal:
    """Task 6 TestUnloadFinal: 调 UNLOADALLAT_WARPPRISM ability + 切 DONE。"""

    @pytest.mark.asyncio
    async def test_unload_calls_ability(self, warp_target, final_target):
        """UNLOAD_FINAL 且 cargo > 0 → 调 UNLOADALLAT_WARPPRISM。"""
        from sc2.ids.ability_id import AbilityId

        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(48, 28))
        prism.cargo_used = 4
        bot = _make_bot(prism)
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.UNLOAD_FINAL

        await act.execute()

        prism.assert_called_with(AbilityId.UNLOADALLAT_WARPPRISM, prism.position)

    @pytest.mark.asyncio
    async def test_transitions_to_done_when_cargo_empty(self, warp_target, final_target):
        """UNLOAD_FINAL 且 cargo = 0 → 切 DONE。"""
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(48, 28))
        prism.cargo_used = 0  # 已卸空
        bot = _make_bot(prism)
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.UNLOAD_FINAL

        await act.execute()

        assert act._state == WarpDropState.DONE


class TestAttackHandling:
    """prism 受攻击时的处理:phasing 状态被打 → morph 回 transport 撤退。"""

    @pytest.mark.asyncio
    async def test_under_attack_while_phasing_triggers_retreat(self, warp_target, final_target):
        """在 DEPLOY_PHASING/WARP_UNITS/WAIT_WARP_COMPLETE 阶段棱镜被打:
        HP 下降 → _update_under_attack 返回 True → 调 morph transport + move home。
        """
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(60, 60), phasing=True)
        # 模拟棱镜 HP 下降(被攻击)
        prism.health = 50  # 比 _prism_hp_prev 低
        prism.shield = 0

        bot = _make_bot(prism)
        bot.start_location = _mock_position(127, 119)

        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.WARP_UNITS
        act._prism_hp_prev = 300.0  # 上 tick HP 高 → 本 tick 掉血 → 受攻击
        act._last_damage_ts = -1000.0

        await act.execute()

        # 应调了 MORPH_WARPPRISMTRANSPORTMODE(撤退前先 morph)
        # 或 move home,判定受攻击
        # 受攻击时会进入 RETREAT_HOME 状态
        assert act._state == WarpDropState.RETREAT_HOME


class TestLoadCargoPartialLoadedNoRetreat:
    """LOAD_CARGO 半装态不撤退(2026-05-24 修 Bug 2):
    cargo_used >= 1 时被打也不 retreat,先把已装的 DT 带走再说。"""

    @pytest.mark.asyncio
    async def test_does_not_retreat_when_partially_loaded(self, warp_target, final_target):
        """LOAD_CARGO + cargo_used = 2(已装 2 DT)+ 受攻击 → 不 retreat。"""
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(60, 60), phasing=False)
        prism.cargo_used = 2  # 已装 2 个 DT
        prism.health = 50
        prism.shield = 0  # HP 大幅下降 → 受攻击
        bot = _make_bot(prism, dts=[])
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.LOAD_CARGO
        act._prism_hp_prev = 300.0  # 上 tick HP 高 → 本 tick 掉血
        act._last_damage_ts = -1000.0

        await act.execute()

        # 半装态 → 不 retreat,保持 LOAD_CARGO(继续装) 或推进到 FLY_TO_FINAL
        assert act._state != WarpDropState.RETREAT_HOME

    @pytest.mark.asyncio
    async def test_retreats_when_unloaded_and_attacked(self, warp_target, final_target):
        """LOAD_CARGO + cargo_used = 0(空船)+ 受攻击 → retreat(没装东西,救棱镜)。"""
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        prism = _make_prism(_mock_position(60, 60), phasing=False)
        prism.cargo_used = 0  # 空船
        prism.health = 50
        prism.shield = 0
        bot = _make_bot(prism, dts=[])
        act = _make_act(warp_target, final_target)
        _inject(act, bot)
        act._state = WarpDropState.LOAD_CARGO
        act._prism_hp_prev = 300.0
        act._last_damage_ts = -1000.0

        await act.execute()

        assert act._state == WarpDropState.RETREAT_HOME


class TestNoTransport:
    """没有棱镜 → execute 返回 False,不崩溃。"""

    @pytest.mark.asyncio
    async def test_no_prism_returns_false(self, warp_target, final_target):
        from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
            WarpDropState,
        )

        bot = MagicMock()
        bot.time = 100.0

        act = _make_act(warp_target, final_target)
        act.ai = bot
        act.knowledge = MagicMock()

        empty = MagicMock()
        empty.__iter__ = lambda self: iter([])
        empty.__bool__ = lambda self: False
        empty.amount = 0
        bot.units.of_type = lambda types: empty

        result = await act.execute()

        assert result is False
        assert act._state == WarpDropState.IDLE
