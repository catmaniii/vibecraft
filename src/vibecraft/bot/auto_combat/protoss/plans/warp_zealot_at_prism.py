"""WarpZealotAtPrism: 大部队出门一波后，在前线 phasing Warp Prism 处 warp 叉子增援。

dt_drop_iac 专用（用户战术改进点 3）。**大部队真出门一波后**，棱镜飞到主力前沿
展开 phasing；本 act 让所有 ready warpgate 把叉子直接 warp 在前线棱镜处，持续
给一波部队补兵 —— 不用从家里走路赶过来。

触发时机（用户 2026-05-21 修正）
================================
**只在大部队真出门一波后**才动作（main_army_is_attacking）。前期 DT 骚扰阶段
完全不介入 —— 那时棱镜只运 / warp DT（WarpDTAtPrism 负责），不抢去 warp 叉子。
不再用 "dt_trained_count≥8" 误触发（那会在骚扰刚结束、大部队还没出门时就
乱 warp 叉子）。

同 WarpDTAtPrism 思路：扫所有 ready WARPGATE，warp_in 到 phasing prism 旁，
mark cd_manager.used_ability 让 sharpy 后续看到 wg 在 cd 跳过、不双重 warp。
"""

from __future__ import annotations

import logging
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts import ActBase

from vibecraft.bot.auto_combat.protoss.plans.dt_micro import main_army_is_attacking
from vibecraft.bot.auto_combat.protoss.plans.warp_cooldowns import get_warp_cooldown

logger = logging.getLogger(__name__)

# Zealot WG cd = 20s(LotV Faster,见 warp_cooldowns 表)
_WARP_COOLDOWN_S: float = get_warp_cooldown(UnitTypeId.ZEALOT)


class WarpZealotAtPrism(ActBase):  # type: ignore[misc]
    """大部队出门后：所有 ready warpgate 把叉子 warp 在前线 phasing 棱镜处。"""

    def __init__(self) -> None:
        super().__init__()

    async def execute(self) -> bool:
        # 只在大部队真出门一波后动作；前期 DT 骚扰阶段完全不介入
        # （让 WarpDTAtPrism 独占 phasing 棱镜，棱镜骚扰期只 warp DT）。
        if not main_army_is_attacking(self.ai):
            return False

        # 找前线 phasing 棱镜
        phasing_prism: Any = None
        try:
            phasings = self.ai.units(UnitTypeId.WARPPRISMPHASING)
            if phasings:
                phasing_prism = phasings.first
        except Exception:
            pass
        if phasing_prism is None:
            return False

        try:
            warpgates = list(self.ai.structures(UnitTypeId.WARPGATE).ready)
        except Exception:
            warpgates = []
        if not warpgates:
            return False

        now = self.ai.time
        warped = 0
        cm = self.knowledge.cooldown_manager
        for wg in warpgates:
            if not self.ai.can_afford(UnitTypeId.ZEALOT):
                break
            try:
                if self.ai.supply_left < 2:
                    break
            except Exception:
                pass

            # CD 判定:走 sharpy cd_manager cooldown 模式(查 used_dict)。
            # 不用默认模式 — SC2 get_available_abilities 对 WG warp ability 不过滤 cd。
            if not cm.is_ready(wg.tag, AbilityId.WARPGATETRAIN_ZEALOT, cooldown=_WARP_COOLDOWN_S):
                continue

            # find_placement near phasing prism
            try:
                placement = await self.ai.find_placement(
                    AbilityId.WARPGATETRAIN_ZEALOT,
                    phasing_prism.position,
                    placement_step=1,
                    max_distance=6,
                )
            except Exception as exc:
                logger.warning("WarpZealotAtPrism find_placement fail: %s", exc)
                continue
            if placement is None:
                continue

            wg.warp_in(UnitTypeId.ZEALOT, placement)
            cm.used_ability(wg.tag, AbilityId.WARPGATETRAIN_ZEALOT)
            warped += 1

        if warped > 0:
            logger.info(
                "WarpZealotAtPrism warped %d zealot at front prism (%.1f, %.1f) game_t=%.1f",
                warped,
                phasing_prism.position.x,
                phasing_prism.position.y,
                now,
            )
        return False
