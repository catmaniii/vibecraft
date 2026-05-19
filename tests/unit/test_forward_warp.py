"""ForwardWarpStalker 单测：在 forward WARPGATE 上 warp stalker。

直接验证 _find_forward_warpgate / _find_forward_pylon 的"forward"判定 — 距敌方
< 距家 * 0.7 才算 forward。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# sharpy path（forward_warp 顶 import sharpy）
_VENDOR_SHARPY = Path(__file__).resolve().parents[2] / "vendor" / "sharpy"
if str(_VENDOR_SHARPY) not in sys.path:
    sys.path.insert(0, str(_VENDOR_SHARPY))

pytest.importorskip("sc2.ids.unit_typeid")
pytest.importorskip("sharpy.plans.acts")


def _make_struct(pos, type_id, tag=0):
    """构造 mock structure(WARPGATE/PYLON)。"""
    from sc2.position import Point2

    s = MagicMock()
    s.position = Point2(pos)
    s.type_id = type_id
    s.tag = tag
    s.is_ready = True
    s.distance_to = lambda other, _self_pos=Point2(pos): _self_pos.distance_to(
        other.position if hasattr(other, "position") else other
    )
    return s


class _MockUnits:
    def __init__(self, units):
        self._units = list(units)

    @property
    def ready(self):
        return _MockUnits([u for u in self._units if getattr(u, "is_ready", True)])

    def __iter__(self):
        return iter(self._units)

    def __len__(self):
        return len(self._units)


def _make_instance(
    home=(127.5, 119.5),
    enemy=(48.5, 28.5),
    warpgates=(),
    pylons=(),
    minerals=500,
    vespene=200,
    supply_left=8,
):
    """构造 ForwardWarpStalker 实例 + 注入 mock state。

    默认资源足够 warp 4 个 stalker(500 矿 / 200 气 / 8 supply)。
    要测"钱不够"等场景显式传 minerals/vespene/supply_left。
    """
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.position import Point2
    from vibecraft.bot.auto_combat.protoss.plans import forward_warp

    inst = forward_warp.ForwardWarpStalker()
    ai = MagicMock()
    ai.start_location = Point2(home)
    ai.enemy_start_locations = [Point2(enemy)]
    ai.minerals = minerals
    ai.vespene = vespene
    ai.supply_left = supply_left
    # 默认 game_time 100s,部分测试会显式覆盖。不设的话会是 MagicMock,
    # 诊断日志 `now - _last_skip_log_t` 算术挂掉。
    ai.time = 100.0

    def _structures_call(type_id):
        if type_id == UnitTypeId.WARPGATE:
            return _MockUnits(list(warpgates))
        if type_id == UnitTypeId.PYLON:
            return _MockUnits(list(pylons))
        return _MockUnits([])

    ai.structures = MagicMock(side_effect=_structures_call)

    # 默认 can_place batch query 返回全 True(grid 上所有 spot 都合法)。
    # 测试要测 "no placement" 时显式覆盖。
    async def _mock_can_place(_ability, positions):
        return [True for _ in positions]

    ai.can_place = _mock_can_place

    inst.ai = ai
    return inst


class TestFindForwardWarpgate:
    def test_home_warpgate_not_forward(self):
        from sc2.ids.unit_typeid import UnitTypeId

        home_wg = _make_struct((125, 115), UnitTypeId.WARPGATE)  # 距家 4 距敌方 ~117 → 远非 forward
        inst = _make_instance(warpgates=[home_wg])
        assert (
            inst._find_forward_warpgate(inst.ai.start_location, inst.ai.enemy_start_locations[0])
            is None
        )

    def test_forward_warpgate_picked(self):
        """距敌方 < 距家 * 0.7 → forward。"""
        from sc2.ids.unit_typeid import UnitTypeId

        # 自家 (127.5, 119.5), 敌方 (48.5, 28.5) — 全距 ~120
        # forward WG (89.3, 29.7) 距敌方 ~41,距家 ~97 → 41 < 97*0.7=68 ✓
        forward_wg = _make_struct((89.3, 29.7), UnitTypeId.WARPGATE)
        inst = _make_instance(warpgates=[forward_wg])
        result = inst._find_forward_warpgate(
            inst.ai.start_location, inst.ai.enemy_start_locations[0]
        )
        assert result is forward_wg

    def test_picks_first_forward_when_multiple(self):
        from sc2.ids.unit_typeid import UnitTypeId

        home_wg = _make_struct((125, 115), UnitTypeId.WARPGATE)
        forward_wg = _make_struct((89.3, 29.7), UnitTypeId.WARPGATE)
        inst = _make_instance(warpgates=[home_wg, forward_wg])
        result = inst._find_forward_warpgate(
            inst.ai.start_location, inst.ai.enemy_start_locations[0]
        )
        assert result is forward_wg

    def test_no_warpgate_returns_none(self):
        inst = _make_instance(warpgates=[])
        assert (
            inst._find_forward_warpgate(inst.ai.start_location, inst.ai.enemy_start_locations[0])
            is None
        )


class TestFindForwardPylon:
    def test_forward_pylon_picked(self):
        from sc2.ids.unit_typeid import UnitTypeId

        forward_py = _make_struct((83.5, 28.5), UnitTypeId.PYLON)
        inst = _make_instance(pylons=[forward_py])
        result = inst._find_forward_pylon(inst.ai.start_location, inst.ai.enemy_start_locations[0])
        assert result is forward_py

    def test_home_pylon_not_forward(self):
        from sc2.ids.unit_typeid import UnitTypeId

        home_py = _make_struct((127, 119), UnitTypeId.PYLON)
        inst = _make_instance(pylons=[home_py])
        assert (
            inst._find_forward_pylon(inst.ai.start_location, inst.ai.enemy_start_locations[0])
            is None
        )


class TestExecuteShortCircuits:
    async def test_returns_false_when_minerals_zero(self):
        """钱为 0 时 return False(等下帧再 try),不调 warp_in。

        2026-05-20 重写:旧版用 can_afford False 短路,新版本地预扣资源 — 直接
        让 ai.minerals=0 走资源不足分支。
        """
        from sc2.ids.unit_typeid import UnitTypeId

        forward_wg = _make_struct((89.3, 29.7), UnitTypeId.WARPGATE)
        forward_py = _make_struct((83.5, 28.5), UnitTypeId.PYLON)
        inst = _make_instance(
            warpgates=[forward_wg], pylons=[forward_py], minerals=0, vespene=0
        )

        result = await inst.execute()
        assert result is False  # 等下帧再 try
        forward_wg.warp_in.assert_not_called()

    async def test_returns_true_when_no_forward_pylon(self):
        """没 forward PYLON → return True 让 sharpy 默认 WarpUnit 接管家里 warp。

        新语义:forward PYLON 是"是否一波"的信号 — 有就所有 warp 到前线,
        没有就让 sharpy 默认行为接管。
        """
        from sc2.ids.unit_typeid import UnitTypeId

        # 有 warpgate 但没 forward pylon → fallback
        home_wg = _make_struct((125, 115), UnitTypeId.WARPGATE)
        inst = _make_instance(warpgates=[home_wg], pylons=[])

        result = await inst.execute()
        assert result is True
        home_wg.warp_in.assert_not_called()

    async def test_supply_full_does_not_mark_cooldown(self):
        """supply 满时不调 warp_in 也不 mark _last_warp_ts(防浪费 28s 窗口)。

        Regression: warp_in 在 supply 满时被 sc2 reject 不开 cooldown,
        但我们如果 mark _last_warp_ts 会阻塞 28s 后重试 — 即使 supply 后来够。
        """
        from sc2.ids.unit_typeid import UnitTypeId

        wg = _make_struct((89, 30), UnitTypeId.WARPGATE, tag=42)
        forward_py = _make_struct((83.5, 28.5), UnitTypeId.PYLON)
        inst = _make_instance(warpgates=[wg], pylons=[forward_py])
        inst.ai.can_afford = MagicMock(return_value=True)
        inst.ai.time = 100.0
        inst.ai.supply_left = 0  # 人口满

        result = await inst.execute()
        assert result is False
        wg.warp_in.assert_not_called()
        # 关键:不能 mark cooldown
        assert 42 not in inst._last_warp_ts

    async def test_uses_home_warpgate_when_forward_pylon_exists(self):
        """有 forward PYLON 时,家里 warpgate 也 warp 到 forward PYLON(全员一波)。

        2026-05-20:placement 改 batch can_place 池,实际 spot 是 PYLON 周围
        网格中随机一个。验证 warp_in 被调用 + spot 在 forward 附近(距 PYLON < 6.5)。
        """
        from sc2.ids.unit_typeid import UnitTypeId

        home_wg = _make_struct((125, 115), UnitTypeId.WARPGATE, tag=200)
        forward_py = _make_struct((83.5, 28.5), UnitTypeId.PYLON)
        inst = _make_instance(warpgates=[home_wg], pylons=[forward_py])
        inst.ai.time = 100.0

        inst.knowledge = MagicMock()
        inst.knowledge.cooldown_manager.is_ready = MagicMock(return_value=True)
        inst.knowledge.cooldown_manager.used_ability = MagicMock()

        result = await inst.execute()
        assert result is False
        home_wg.warp_in.assert_called_once()
        call_args = home_wg.warp_in.call_args
        # 落点应在 forward PYLON 周围 power radius(6.5)内
        placement = call_args[0][1]
        d = placement.distance_to(forward_py.position)
        assert d <= 6.5, f"placement {placement} too far from pylon (d={d})"


class TestHardCooldownTracking:
    """ForwardWarpStalker 自带 cooldown 限速:同 warpgate 28s 内不重复 warp。

    Regression: sharpy cd_manager.is_ready 在 forward warpgate tag 不在
    available_dict 时可能返回不准,实战 log 显示 ForwardWarpStalker 反复 log
    "warp STALKER" 但 sc2 只 spawn 1 个 — 浪费 log 噪音。
    """

    async def test_recent_warp_blocks_next_call(self):
        """同一 warpgate tag 上次 warp < 28s game-time → 不再调 warp_in。"""
        from sc2.ids.unit_typeid import UnitTypeId

        forward_wg = _make_struct((89.3, 29.7), UnitTypeId.WARPGATE, tag=999)
        forward_py = _make_struct((83.5, 28.5), UnitTypeId.PYLON)
        inst = _make_instance(warpgates=[forward_wg], pylons=[forward_py])
        inst.ai.can_afford = MagicMock(return_value=True)
        inst.ai.time = 100.0
        # 模拟 1s 前刚 warp 过
        inst._last_warp_ts[999] = 99.0

        result = await inst.execute()
        assert result is False
        forward_wg.warp_in.assert_not_called()

    async def test_warp_allowed_after_cooldown(self):
        """28s+ 后允许再 warp。"""
        from sc2.ids.unit_typeid import UnitTypeId
        from sc2.position import Point2

        forward_wg = _make_struct((89.3, 29.7), UnitTypeId.WARPGATE, tag=999)
        forward_py = _make_struct((83.5, 28.5), UnitTypeId.PYLON)
        inst = _make_instance(warpgates=[forward_wg], pylons=[forward_py])
        inst.ai.time = 130.0
        inst._last_warp_ts[999] = 100.0  # 30s 前 — cooldown 已过

        # mock find_placement 返回合法位置
        async def _mock_find(*a, **kw):
            return Point2((85, 30))

        inst.ai.find_placement = _mock_find
        # mock knowledge.cooldown_manager(允许通过)
        inst.knowledge = MagicMock()
        inst.knowledge.cooldown_manager.is_ready = MagicMock(return_value=True)
        inst.knowledge.cooldown_manager.used_ability = MagicMock()

        result = await inst.execute()
        assert result is False
        forward_wg.warp_in.assert_called_once()
        # _last_warp_ts 被更新
        assert inst._last_warp_ts[999] == 130.0


# ============================================================================
# Tests: 一次刷 4 个(2026-05-20 用户反馈)
# ============================================================================


class TestMultiWarpResourceTracking:
    """新行为:循环里本地预扣 minerals/vespene/supply,免得同 tick 多次 warp_in 时
    sc2 server 只接受头一个。用户反馈"每次只刷一个"= 旧 ai.minerals 快照 bug。
    """

    async def test_warps_four_when_all_resources_sufficient(self):
        """500 矿 / 200 气 / 8 supply + 4 warpgate → 一次性 warp 4 个 stalker。"""
        from sc2.ids.unit_typeid import UnitTypeId
        from sc2.position import Point2

        wgs = [
            _make_struct((89, 30), UnitTypeId.WARPGATE, tag=i + 100) for i in range(4)
        ]
        forward_py = _make_struct((83.5, 28.5), UnitTypeId.PYLON)
        inst = _make_instance(
            warpgates=wgs,
            pylons=[forward_py],
            minerals=500,
            vespene=200,
            supply_left=8,
        )
        inst.ai.time = 100.0

        async def _mock_find(*a, **kw):
            return Point2((84, 30))

        inst.ai.find_placement = _mock_find
        inst.knowledge = MagicMock()
        inst.knowledge.cooldown_manager.is_ready = MagicMock(return_value=True)
        inst.knowledge.cooldown_manager.used_ability = MagicMock()

        result = await inst.execute()
        assert result is False
        # 4 个 wg 都 warp_in 一次
        for wg in wgs:
            wg.warp_in.assert_called_once()

    async def test_warps_two_when_only_money_for_two(self):
        """250 矿 / 100 气 → 只够 2 stalker(各 125 矿 / 50 气);剩 2 个 wg 不发 warp。"""
        from sc2.ids.unit_typeid import UnitTypeId
        from sc2.position import Point2

        wgs = [
            _make_struct((89, 30), UnitTypeId.WARPGATE, tag=i + 200) for i in range(4)
        ]
        forward_py = _make_struct((83.5, 28.5), UnitTypeId.PYLON)
        inst = _make_instance(
            warpgates=wgs,
            pylons=[forward_py],
            minerals=250,
            vespene=100,
            supply_left=8,
        )
        inst.ai.time = 100.0

        async def _mock_find(*a, **kw):
            return Point2((84, 30))

        inst.ai.find_placement = _mock_find
        inst.knowledge = MagicMock()
        inst.knowledge.cooldown_manager.is_ready = MagicMock(return_value=True)
        inst.knowledge.cooldown_manager.used_ability = MagicMock()

        await inst.execute()
        called = sum(1 for wg in wgs if wg.warp_in.called)
        assert called == 2, f"expected 2 warps, got {called}"

    async def test_each_warpgate_gets_unique_spot(self):
        """4 个 WG 同 tick warp,每个 WG 拿到**不同**的 placement spot
        (避免之前 4 个打到同点 SC2 只接受 1 个的 bug)。"""
        from sc2.ids.unit_typeid import UnitTypeId

        wgs = [
            _make_struct((89, 30), UnitTypeId.WARPGATE, tag=i + 400) for i in range(4)
        ]
        forward_py = _make_struct((83.5, 28.5), UnitTypeId.PYLON)
        inst = _make_instance(
            warpgates=wgs,
            pylons=[forward_py],
            minerals=500,
            vespene=200,
            supply_left=8,
        )
        inst.ai.time = 100.0

        inst.knowledge = MagicMock()
        inst.knowledge.cooldown_manager.is_ready = MagicMock(return_value=True)
        inst.knowledge.cooldown_manager.used_ability = MagicMock()

        await inst.execute()

        # 4 个 WG 各被调用 warp_in 一次
        warped_spots = []
        for wg in wgs:
            wg.warp_in.assert_called_once()
            warped_spots.append(wg.warp_in.call_args[0][1])

        # 4 个 spot 互不相同
        unique_spots = {(s.x, s.y) for s in warped_spots}
        assert len(unique_spots) == 4, (
            f"expected 4 unique spots, got {len(unique_spots)}: {warped_spots}"
        )

    async def test_falls_back_to_home_pylon_when_no_forward(self):
        """没 forward PYLON → fallback warp 到家里 PYLON(用户 2026-05-20:
        "有野水晶和bg,没有就刷家里")。"""
        from sc2.ids.unit_typeid import UnitTypeId

        # 只有 home PYLON,没 forward PYLON
        home_py = _make_struct((127, 119), UnitTypeId.PYLON)  # 家里
        wg = _make_struct((125, 115), UnitTypeId.WARPGATE, tag=600)
        inst = _make_instance(warpgates=[wg], pylons=[home_py])
        inst.ai.time = 100.0

        inst.knowledge = MagicMock()
        inst.knowledge.cooldown_manager.is_ready = MagicMock(return_value=True)
        inst.knowledge.cooldown_manager.used_ability = MagicMock()

        result = await inst.execute()
        assert result is False
        # warp 命令应该被发出,落点在 home PYLON 附近
        wg.warp_in.assert_called_once()
        placement = wg.warp_in.call_args[0][1]
        d = placement.distance_to(home_py.position)
        assert d <= 6.5, f"home-fallback placement {placement} too far from home pylon (d={d})"

    async def test_no_warps_when_can_place_returns_all_false(self):
        """所有 candidate spot 都被占/不合法 → 不发 warp 命令。"""
        from sc2.ids.unit_typeid import UnitTypeId

        wgs = [
            _make_struct((89, 30), UnitTypeId.WARPGATE, tag=i + 500) for i in range(2)
        ]
        forward_py = _make_struct((83.5, 28.5), UnitTypeId.PYLON)
        inst = _make_instance(
            warpgates=wgs,
            pylons=[forward_py],
            minerals=500,
            vespene=200,
            supply_left=8,
        )
        inst.ai.time = 100.0

        # 覆盖 can_place 返回全 False(模拟所有 spot 都被占)
        async def _all_false(_ability, positions):
            return [False] * len(positions)

        inst.ai.can_place = _all_false

        inst.knowledge = MagicMock()
        inst.knowledge.cooldown_manager.is_ready = MagicMock(return_value=True)
        inst.knowledge.cooldown_manager.used_ability = MagicMock()

        await inst.execute()
        for wg in wgs:
            wg.warp_in.assert_not_called()

    async def test_warps_three_when_supply_blocks_fourth(self):
        """500 矿 / 200 气 但 supply_left=7 → 只能 warp 3 个(每个占 2 supply,4 个要 8)。"""
        from sc2.ids.unit_typeid import UnitTypeId
        from sc2.position import Point2

        wgs = [
            _make_struct((89, 30), UnitTypeId.WARPGATE, tag=i + 300) for i in range(4)
        ]
        forward_py = _make_struct((83.5, 28.5), UnitTypeId.PYLON)
        inst = _make_instance(
            warpgates=wgs,
            pylons=[forward_py],
            minerals=500,
            vespene=200,
            supply_left=7,
        )
        inst.ai.time = 100.0

        async def _mock_find(*a, **kw):
            return Point2((84, 30))

        inst.ai.find_placement = _mock_find
        inst.knowledge = MagicMock()
        inst.knowledge.cooldown_manager.is_ready = MagicMock(return_value=True)
        inst.knowledge.cooldown_manager.used_ability = MagicMock()

        await inst.execute()
        called = sum(1 for wg in wgs if wg.warp_in.called)
        assert called == 3, f"expected 3 warps (supply gated), got {called}"
