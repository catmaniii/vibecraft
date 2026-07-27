"""WarpDTAtPrism: 强制 DT warp-in 在 phasing Warp Prism 处（不是家里 pylon）。

dt_drop_iac 关键 act。sharpy 默认 ProtossUnit(DT) 会把 warp target reset 到
家最近 Nexus，导致 DT 在家 warp、自己走路去 enemy main —— 浪费整个棱镜空投。

本 act 同 forward_warp.py 思路：扫所有 ready WARPGATE，找 phasing prism 作为
power source target，直接 warp_in DT 到 prism 旁；mark cd_manager.used_ability
让 sharpy 后续看到 wg in cooldown 跳过 → 不会双重 warp。

cap 检查：bot.knowledge.vibecraft.dt_trained_count（累计 trained DT 数）≥ 8 时
直接 return True（让 sharpy 默认 ProtossUnit 接管 — 但 ProtossUnit cap 也是 8，
所以等价于"停止 warp"）。
"""

from __future__ import annotations

import logging
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts import ActBase

from vibecraft.bot.auto_combat.protoss.plans.warp_cooldowns import get_warp_cooldown

logger = logging.getLogger(__name__)

# DT WG cd = 32s(LotV Faster,见 warp_cooldowns 表)
_WARP_COOLDOWN_S: float = get_warp_cooldown(UnitTypeId.DARKTEMPLAR)
# DT 累计 trained cap（跟 plan 的 ProtossUnit(DT, 8) 对齐）
_DT_CAP: int = 8


class WarpDTAtPrism(ActBase):  # type: ignore[misc]
    """所有 ready WARPGATE 都 warp DT 到 phasing prism 旁；无 phasing prism 时 yield。"""

    def __init__(self) -> None:
        super().__init__()

    async def execute(self) -> bool:
        # cap：累计 8 DT 已 trained → 不再 warp，act 终止（永久 done）
        try:
            trained = int(self.knowledge.ai.knowledge.vibecraft.dt_trained_count)
        except Exception:
            trained = 0
        if trained >= _DT_CAP:
            return True  # 永久结束（cap 8 已满，sharpy 移除本 act）

        # 找 phasing prism（在 safe_pos warping 时存在）
        phasing_prism: Any = None
        try:
            phasings = self.ai.units(UnitTypeId.WARPPRISMPHASING)
            if phasings:
                phasing_prism = phasings.first
        except Exception:
            pass
        if phasing_prism is None:
            return False  # 没 phasing prism → 本 tick 不动作，下个 tick 再 check
            # （之前是 True 导致 act 永久退出，bug）

        # 遍历所有 ready warpgate
        try:
            warpgates = list(self.ai.structures(UnitTypeId.WARPGATE).ready)
        except Exception:
            warpgates = []
        if not warpgates:
            return False

        # supply 检查（DT = 2 supply）
        try:
            if self.ai.supply_left < 2:
                return False
        except Exception:
            pass

        now = self.ai.time
        warped = 0
        cm = self.knowledge.cooldown_manager
        for wg in warpgates:
            if not self.ai.can_afford(UnitTypeId.DARKTEMPLAR):
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
                    AbilityId.WARPGATETRAIN_DARKTEMPLAR,
                    phasing_prism.position,
                    placement_step=1,
                    max_distance=6,
                )
            except Exception as exc:
                logger.warning("WarpDTAtPrism find_placement fail: %s", exc)
                continue
            if placement is None:
                continue

            wg.warp_in(UnitTypeId.DARKTEMPLAR, placement)
            cm.used_ability(wg.tag, AbilityId.WARPGATETRAIN_ZEALOT)
            warped += 1

        if warped > 0:
            logger.info(
                "WarpDTAtPrism warped %d DT at phasing prism (%.1f, %.1f) game_t=%.1f",
                warped,
                phasing_prism.position.x,
                phasing_prism.position.y,
                now,
            )
        return False
