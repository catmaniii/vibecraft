"""坦克不动(idle)时自动架起来(siege)的分层微操 act。

2026-06-17 用户:坦克不动的时候尽量架着。

背景:vibecraft 的 Terran 用 **generic `PlanZoneGather`**(不是带 siege 逻辑的
`PlanZoneGatherTerran`,后者是 sharpy 给它自带 dummy bot 用的、vibecraft 没接),所以坦克默认
**不会自动架**。这个独立 act 把"不动就架"作为**分层微操**补上,不改共享的 gather/attack 内核
(符合"通用核心 + 种族微操分层"的架构)。

行为:
- 未架坦克(`SIEGETANK`)处于 **idle**(没有移动/攻击命令)→ 架起来(`SIEGEMODE`)。
- 坦克被 gather/attack plan 下**移动/攻击**命令时,SC2 引擎会自动先解架再走(sieged tank 收到
  move/attack 命令即 unsiege),所以**进攻/集结/撤退途中的坦克不会被架住卡死** —— 到位停下变 idle
  后,本 act 下一帧再把它架起来。配合撤退滞回(大军不再抖),不会 siege/unsiege 反复抽搐。
- 门:**不在主基斜坡上下口架**(架在坡口会挡自家进出),沿用 `PlanZoneGatherTerran` 的 ramp 判定。

挂载:Terran 用坦克的 plan(mech / two_base_tanks / two_one_one / one_one_one / bio_stim)的 tactics
段。无坦克时本 act 是 no-op,放哪个 plan 都安全。
"""

from __future__ import annotations

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts import ActBase


class SiegeIdleTanksAct(ActBase):  # type: ignore[misc]
    """idle 的攻城坦克自动架起来(non-blocking,每帧执行)。"""

    def __init__(self) -> None:
        super().__init__()

    async def execute(self) -> bool:
        try:
            ramp = self.zone_manager.own_main_zone.ramp
        except Exception:
            ramp = None
        for tank in self.cache.own(UnitTypeId.SIEGETANK):
            # 非 idle = 有移动/攻击命令(集结/进攻/撤退途中)→ 别架,让它走完
            if not getattr(tank, "is_idle", False):
                continue
            # 不架在主基斜坡口(挡自家进出);ramp 缺失则跳过该门
            if ramp is not None:
                try:
                    if (
                        tank.distance_to(ramp.bottom_center) < 5.0
                        or tank.distance_to(ramp.top_center) < 4.0
                    ):
                        continue
                except Exception:
                    pass
            tank(AbilityId.SIEGEMODE_SIEGEMODE)
        return True  # 永不阻塞后续 act
