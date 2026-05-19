"""ForwardWarpStalker：在 forward WARPGATE 上 warp 兵到 forward PYLON 附近。

存在意义
========
sharpy 自带的 `WarpUnit` 在 ``warp_unit.py:71`` 强制把 target_point 重置到
最近 NEXUS 的位置（"Reset position to nexus to reduce the possibility of
warping stuck units in"），导致 forward PYLON（在敌方一侧、距家 NEXUS 100+
距离）**永远不会被选中** warp 兵 — 实战 log(game_20260518_043437) forward
PYLON+GATEWAY 修好了但 0 个 stalker 从 forward warpgate 出。

这个 act 专门绕过 sharpy 限制：扫描所有 WARPGATE 找位置偏敌方一侧的（"forward"），
直接 warp_in 到 forward PYLON 附近，与 sharpy 自带的 ProtossUnit warp 流并行。
"""

from __future__ import annotations

import logging
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

logger = logging.getLogger(__name__)

# "forward"判定：距敌方主基地的距离 < 距家 * 此比率 → 视为 forward
# 0.7 = 比中点(0.5)稍偏敌方,确保家里 PYLON/WARPGATE 不被误判 forward
_FORWARD_RATIO: float = 0.7

# WARPGATE warp 冷却(game-second)。sharpy cd_manager.is_ready 在 forward warpgate
# tag 不在 available_dict 时可能返回不准,我们自己 track game.time 作为兜底。
# 实际 cooldown ~28s,留 1s 容差给重复调用。
_WARP_COOLDOWN_S: float = 28.0

# 单位成本表(minerals, vespene, supply)。同 tick 内多次调用 wg.warp_in 时
# python-sc2 BotAI.minerals/vespene/supply_left 不会立刻反映"已发出但还没结算"
# 的命令(它们是 game-state 快照,只在 step 末由 sc2 服务端确认后更新)。
# 不预扣 → 本帧 4 个 warp_in 全发出,sc2 只处理头一个有钱的,其余 silent reject
# → 单刷(用户反馈 2026-05-20)。预扣后能正确连发到耗尽资源 / supply / CD。
_UNIT_COST: dict[UnitTypeId, tuple[int, int, int]] = {
    UnitTypeId.STALKER: (125, 50, 2),
    UnitTypeId.ZEALOT: (100, 0, 2),
    UnitTypeId.DARKTEMPLAR: (125, 125, 2),
    UnitTypeId.SENTRY: (50, 100, 2),
    UnitTypeId.HIGHTEMPLAR: (50, 150, 2),
    UnitTypeId.ADEPT: (100, 25, 2),
}


class ForwardWarpStalker(ActBase):  # type: ignore[misc]
    """**一波流模式**:所有 ready WARPGATE(家里+forward)都 warp 兵到 forward PYLON。

    意图:用户反馈"4bg 决定一波时,只要 forward BE+BG 能刷兵,全部都刷过去"。
    把家里 4 BG 的折跃也指向 forward PYLON,实现"集结成大波团 attack 敌方家"。

    每 step:
      1. 找 forward PYLON。没有 → return True(让 sharpy ProtossUnit 接管家里 warp)
      2. 遍历**所有** ready WARPGATE
      3. 对每个 wg:检查 self._last_warp_ts 硬 cooldown + cd_manager,通过则
         find_placement near forward PYLON + warp_in
      4. mark cd_manager.used_ability → sharpy 后续 WarpUnit 看到 cooldown 跳过

    Fallback(forward PYLON 不存在):return True 让 sharpy 默认 WarpUnit 接管,
    家里 warpgate 走"target=最近 NEXUS"的默认行为,等价于"取消一波转防守"语义。
    """

    def __init__(self, unit_type: UnitTypeId = UnitTypeId.STALKER) -> None:
        super().__init__()
        self.unit_type = unit_type
        # 每个 warpgate tag 上次成功 warp 的 game.time,兜底 cd_manager 不准时的硬限速
        self._last_warp_ts: dict[int, float] = {}

    async def execute(self) -> bool:
        try:
            home = self.ai.start_location
            enemy = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return True

        # 找 forward PYLON。没有 → 不一波,让 sharpy 默认 WarpUnit 接管
        forward_pylon = self._find_forward_pylon(home, enemy)
        if forward_pylon is None:
            return True

        # 遍历所有 ready WARPGATE
        try:
            warpgates = list(self.ai.structures(UnitTypeId.WARPGATE).ready)
        except Exception:
            warpgates = []
        if not warpgates:
            return True  # 折跃没研完 / 还没 morph

        # 取该单位成本。表里没有的 → STALKER 兜底
        cost_min, cost_gas, cost_supply = _UNIT_COST.get(
            self.unit_type, _UNIT_COST[UnitTypeId.STALKER]
        )

        # 顶层 supply check:1 单位都放不下 → 等 AutoPylon 补人口
        try:
            if self.ai.supply_left < cost_supply:
                return False
        except Exception:
            pass

        # —— 关键:本地预扣资源/supply ——
        # python-sc2 的 ai.minerals/vespene/supply_left 是 game-state 快照,
        # 同 tick 内多次 wg.warp_in 不会让它们立刻变化。不预扣 → 4 个 warp_in
        # 都看到同一个数字全发出,sc2 实际只处理头一个 → 用户反馈"每次只刷一个"。
        try:
            avail_min = int(self.ai.minerals)
            avail_gas = int(self.ai.vespene)
            avail_supply = int(self.ai.supply_left)
        except Exception:
            avail_min, avail_gas, avail_supply = 0, 0, 0

        now = self.ai.time
        warped_count = 0
        for wg in warpgates:
            # 本地账户能不能再 warp 一个
            if avail_min < cost_min or avail_gas < cost_gas:
                break  # 钱不够,下帧再试
            if avail_supply < cost_supply:
                break  # supply 不够

            # 自家追踪 cooldown 兜底(sharpy cd_manager 偶尔不准)
            last = self._last_warp_ts.get(wg.tag, -1000.0)
            if now - last < _WARP_COOLDOWN_S:
                continue

            # sharpy cd_manager 检查
            try:
                if not self.knowledge.cooldown_manager.is_ready(
                    wg.tag, AbilityId.WARPGATETRAIN_ZEALOT
                ):
                    continue
            except Exception:
                pass

            # find_placement near forward PYLON
            try:
                placement = await self.ai.find_placement(
                    AbilityId.WARPGATETRAIN_STALKER,
                    forward_pylon.position,
                    placement_step=1,
                    max_distance=8,
                )
            except Exception as exc:
                logger.warning("ForwardWarpStalker find_placement fail: %s", exc)
                continue
            if placement is None:
                continue

            wg.warp_in(self.unit_type, placement)
            # 本地预扣 — 下次循环用扣后的余额判断
            avail_min -= cost_min
            avail_gas -= cost_gas
            avail_supply -= cost_supply
            self._last_warp_ts[wg.tag] = now
            try:
                self.knowledge.cooldown_manager.used_ability(wg.tag, AbilityId.WARPGATETRAIN_ZEALOT)
            except Exception:
                pass
            warped_count += 1

        if warped_count > 0:
            logger.info(
                "ForwardWarpStalker warped %d %s to forward PYLON (%.1f, %.1f) game_t=%.1f",
                warped_count,
                self.unit_type.name,
                forward_pylon.position.x,
                forward_pylon.position.y,
                now,
            )
        return False

    def _find_forward_warpgate(self, home: Point2, enemy: Point2) -> Any:
        """找一个 ready 的 forward WARPGATE。"""
        try:
            wgs = self.ai.structures(UnitTypeId.WARPGATE).ready
        except Exception:
            return None
        for wg in wgs:
            d_home = wg.distance_to(home)
            d_enemy = wg.distance_to(enemy)
            if d_enemy < d_home * _FORWARD_RATIO:
                return wg
        return None

    def _find_forward_pylon(self, home: Point2, enemy: Point2) -> Any:  # noqa: D401
        """找一个 ready 的 forward PYLON。"""
        try:
            pylons = self.ai.structures(UnitTypeId.PYLON).ready
        except Exception:
            return None
        for py in pylons:
            d_home = py.distance_to(home)
            d_enemy = py.distance_to(enemy)
            if d_enemy < d_home * _FORWARD_RATIO:
                return py
        return None
