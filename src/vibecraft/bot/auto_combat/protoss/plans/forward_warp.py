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

Placement 池策略(2026-05-20 用户设计)
=====================================
之前每只 WG 单独 `find_placement(forward_pylon, max_distance=12)`,问题:
- 4 个 WG 同 tick 4 次 find_placement,可能返回**同一坐标**(`random_alternative`
  从有限合法 spot 池随机,样本小 → 撞同点)
- 4 个 wg.warp_in 都打到同点 → SC2 server 只接受第一个 → 没刷满

新策略:
1. PYLON ready 后 `_build_candidate_grid` 一次性生成 PYLON power radius(6.5)
   内的 grid spot 列表(~80 个候选)
2. 每 tick `_query_valid_placements` 调 `ai.can_place(STALKER, grid)` **一次批量
   query**,拿当前 valid spot 子集(被占用/被挡的过滤掉)
3. 给每只 WG **唯一坐标**(`used_spot_idx`),保证 N 个 WG 4 个不同 spot
4. 每 tick `random.shuffle` valid spots,避免位置可预测被对手针对
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
        # 2026-05-20 用户设计:预计算 forward PYLON 周围 candidate grid,然后批量 batch
        # validate 拿到当前 valid spots,**给每个 WG 分配唯一 spot**,避免 4 个 warp_in
        # 全打到同一坐标导致 SC2 server 只接受第一个 → 没刷满。
        # Grid 不变(PYLON tag 没变),只 cache 网格本身;valid 由每 tick batch 查。
        self._candidate_grid: list[Point2] | None = None
        self._candidate_grid_pylon_tag: int | None = None

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

        # 2026-05-20 用户设计:预计算 forward PYLON 周围 candidate grid + 每帧
        # batch can_place 拿当前 valid spots。给每个 WG 分配**唯一** spot,避免
        # 多 WG warp 到同坐标导致 SC2 只接受第一个 → 没刷满。
        valid_spots = await self._query_valid_placements(forward_pylon)
        # 随机打乱,避免每帧选同样位置 → bot 行为可预测/被针对
        import random

        random.shuffle(valid_spots)

        now = self.ai.time
        warped_count = 0
        used_spot_idx: set[int] = set()
        # 诊断:统计每只 WG 的 skip 原因(用户反馈"有钱有CD不刷兵")
        skip_money = skip_supply = skip_my_cd = skip_placement = skip_warp_fail = 0
        for wg in warpgates:
            # 本地账户能不能再 warp 一个
            if avail_min < cost_min or avail_gas < cost_gas:
                skip_money += 1
                break  # 钱不够,下帧再试
            if avail_supply < cost_supply:
                skip_supply += 1
                break  # supply 不够

            # 自家追踪 cooldown — 唯一的 CD 判定。
            # 2026-05-20:之前还检查 sharpy `cooldown_manager.is_ready` 作为附加 gate,
            # 但 sharpy `available_dict` 对 forward WARPGATE 经常不准 → False 误报。
            # 现在只信本地 `_last_warp_ts`(精确到我们自己上次 wg.warp_in 的 game-time)。
            last = self._last_warp_ts.get(wg.tag, -1000.0)
            if now - last < _WARP_COOLDOWN_S:
                skip_my_cd += 1
                continue

            # 从 valid_spots 池选一个还没用的(给每个 WG 唯一坐标)
            placement: Point2 | None = None
            for i, cand in enumerate(valid_spots):
                if i in used_spot_idx:
                    continue
                placement = cand
                used_spot_idx.add(i)
                break
            if placement is None:
                # 池子用光了(WG 数 > valid spot 数,或 spot 全被占)
                skip_placement += 1
                continue

            # Issue warp。pre-validated 过,不该 fail,但仍 defensive 处理。
            try:
                result = wg.warp_in(self.unit_type, placement, can_afford_check=True)
            except TypeError:
                # 老版本 python-sc2 没 can_afford_check kwarg → fallback
                result = wg.warp_in(self.unit_type, placement)
            if result is False:
                # python-sc2 do() 钱不够返 False(can_afford_check=True)
                skip_warp_fail += 1
                continue

            # 本地预扣 — 下次循环用扣后的余额判断
            avail_min -= cost_min
            avail_gas -= cost_gas
            avail_supply -= cost_supply
            self._last_warp_ts[wg.tag] = now
            import contextlib

            with contextlib.suppress(Exception):
                self.knowledge.cooldown_manager.used_ability(wg.tag, AbilityId.WARPGATETRAIN_ZEALOT)
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
        elif warpgates:
            # 有 WG 但没 warp:dump 原因,debug 用
            # 节流:每 ~3s 最多 log 一次
            if now - getattr(self, "_last_skip_log_t", -1000.0) > 3.0:
                logger.debug(
                    "ForwardWarpStalker no warps t=%.1f WG=%d money=%d supply=%d my_cd=%d placement=%d warp_fail=%d (M%d G%d S%d)",
                    now, len(warpgates), skip_money, skip_supply, skip_my_cd,
                    skip_placement, skip_warp_fail,
                    avail_min, avail_gas, avail_supply,
                )
                self._last_skip_log_t = now
        return False

    def _build_candidate_grid(self, forward_pylon: Any) -> list[Point2]:
        """生成 forward PYLON 周围 power radius 内的候选 spot 网格。

        PYLON power radius 6.5(LotV)。grid 步长 1.0,扣掉 PYLON 占地 ±2 的
        中心区(否则跟 PYLON 本身重叠)。
        Cache 按 PYLON tag — PYLON 不会动,网格只算 1 次。
        """
        if (
            self._candidate_grid is not None
            and self._candidate_grid_pylon_tag == forward_pylon.tag
        ):
            return self._candidate_grid

        grid: list[Point2] = []
        cx, cy = forward_pylon.position.x, forward_pylon.position.y
        for dx_int in range(-6, 7):
            for dy_int in range(-6, 7):
                if dx_int == 0 and dy_int == 0:
                    continue
                spot = Point2((cx + dx_int, cy + dy_int))
                d = spot.distance_to(forward_pylon.position)
                if d > 6.0:  # 留 0.5 buffer 离 power 边缘
                    continue
                if d < 2.0:  # PYLON 自己占 2x2
                    continue
                grid.append(spot)
        self._candidate_grid = grid
        self._candidate_grid_pylon_tag = forward_pylon.tag
        logger.info(
            "ForwardWarpStalker built candidate grid: %d spots near pylon (%.1f, %.1f)",
            len(grid), cx, cy,
        )
        return grid

    async def _query_valid_placements(self, forward_pylon: Any) -> list[Point2]:
        """批量 can_place 查 forward PYLON 周围当前哪些 spot 可以 warp 兵。

        一次 SC2 round-trip 查所有 candidate(`can_place(positions: list[Point2])`),
        比每只 WG 单独 `find_placement` 快很多 + 结果更准(SC2 server 实时判断
        terrain/unit 占用/power)。
        """
        grid = self._build_candidate_grid(forward_pylon)
        if not grid:
            return []
        try:
            ability = AbilityId.WARPGATETRAIN_STALKER
            results = await self.ai.can_place(ability, grid)
        except Exception as exc:
            logger.warning("ForwardWarpStalker can_place batch query fail: %s", exc)
            return []
        return [grid[i] for i, ok in enumerate(results) if ok]

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
