"""人族二本速战巡 BC Rush plan。

二本速战巡 opening：快扩 CC2 + 科技链直奔 FusionCore，首舰 ~4:10-4:40。
早期防守靠兵营 Reactor 双产枪兵 + Cyclone 临时对空，撑到 BC 出场。
BC 出场后 BcRaidSquadAct 自动骚扰敌矿，无限循环直到玩家喊停。

Build order（二本速战巡，调研自 Liquipedia / spawningtool）：
  depot(14)→ rax(15)→ gas1(~0:43)→ orbital + reaper(~1:27 侦察)→
  CC2(~supply20, ~1:42-1:55)→ Factory(~2:00)→ gas2(~2:10)→
  Starport(~2:50)→ FusionCore(~3:25) + Starport TechLab→
  首舰 ~4:10-4:40 → 持续 BC

策略 yaml：strategies/terran/bc_rush.yaml
"""

from __future__ import annotations

import contextlib
from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import (
    ActBase,
    ActUnit,
    BuildGas,
    Expand,
    GridBuilding,
    MineOpenBlockedBase,
    Tech,
)
from sharpy.plans.acts.terran import AutoDepot, BuildAddon, MorphOrbitals, TerranUnit
from sharpy.plans.require import All, Gas, Supply, UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)
from sharpy.plans.tactics.terran import CallMule, LowerDepots, ManTheBunkers, Repair

from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct
from vibecraft.bot.auto_combat.terran.bc_raid_act import BcHomeRepairAct, GroupHarassAct
from vibecraft.bot.auto_combat.terran.bc_supply import bc_depot_target

# 补给楼变体（含降下 / drop）—— 统计已有补给时三种都要算
_DEPOT_TYPES = {
    UnitTypeId.SUPPLYDEPOT,
    UnitTypeId.SUPPLYDEPOTLOWERED,
    UnitTypeId.SUPPLYDEPOTDROP,
}


class BcAutoDepot(AutoDepot):  # type: ignore[misc]
    """bc_rush 专用补给楼自动建造：在 sharpy AutoDepot 的平滑增速模型之上，
    强制保留一段随产能放大的"空余人口 buffer"，确保**绝不卡人口**（用户强规则）。

    为什么父类不够（2026-06-19 实测 5:24 卡 47/47）:
      父类用平滑增速率(rax*2/21 + starport*2/30 …)×1.2 预测,只留 ~3-4 人口冗余。
      但 BC 是**离散 +6 一次性爆发**(单星港一发就吃满冗余),且中期多兵营枪兵 ramp
      爆发速度 > 补给楼 21s 建造延迟 → 父类预测追不上 → 瞬间卡人口。

    本类做法（保留父类结果作下限,再叠加 buffer 下限,取两者大者）:
      buffer = 8 + 2 × (兵营含反应堆 + 工厂 + 星港)  —— 产能越多预留越大,
      覆盖一次 BC(+6) 爆发 + 一轮枪兵 ramp + 补给楼建造延迟。
      目标:保证 (cap_after_pending - supply_used) >= buffer。
    """

    async def pylon_count_calc(self) -> int:
        base = int(await super().pylon_count_calc())

        rax = self.cache.own(UnitTypeId.BARRACKS).ready.amount
        rax += self.cache.own(UnitTypeId.BARRACKSREACTOR).ready.amount
        factory = self.cache.own(UnitTypeId.FACTORY).ready.amount
        starport = self.cache.own(UnitTypeId.STARPORT).ready.amount
        starport += self.cache.own(UnitTypeId.STARPORTREACTOR).ready.amount
        depots_ready = self.cache.own(_DEPOT_TYPES).ready.amount

        return bc_depot_target(
            base=base,
            supply_used=int(self.ai.supply_used),
            supply_cap=int(self.ai.supply_cap),
            rax=int(rax),
            factory=int(factory),
            starport=int(starport),
            depots_ready=int(depots_ready),
        )


class RampBunkerAct(ActBase):  # type: ignore[misc]
    """在主基**斜坡口高地边缘**建 1 座地堡（防 all-in，2026-06-19 用户）。

    落点 = `main_base_ramp.barracks_in_middle`（人族墙体中点，紧贴高地斜坡边缘，卡口防守），
    一次 find_placement 算好**锁住**不每帧重选（CLAUDE.md 强规则，防落点漂移）。
    已有地堡（含 pending）即 done。SCV 被 sharpy 拽走时下帧 already_pending 掉 → 自动重发。
    """

    def __init__(self) -> None:
        super().__init__()
        self._placement: Point2 | None = None

    async def execute(self) -> bool:
        # 已有地堡（ready 或在建）→ 完成，不再建
        ready = self.cache.own(UnitTypeId.BUNKER).amount
        pending = 0
        with contextlib.suppress(Exception):
            pending = int(self.ai.already_pending(UnitTypeId.BUNKER))
        if ready + pending >= 1:
            return True
        if not self.ai.can_afford(UnitTypeId.BUNKER):
            return True  # 等钱（non-blocking，不阻塞后续 build）

        # 落点一次锁定：斜坡墙体中点（高地边缘），find_placement 找最近合法格
        if self._placement is None:
            near: Point2 | None = None
            with contextlib.suppress(Exception):
                near = self.ai.main_base_ramp.barracks_in_middle
            if near is None:
                with contextlib.suppress(Exception):
                    ramp = self.ai.main_base_ramp
                    near = ramp.top_center.towards(self.ai.start_location, 3.0)
            if near is None:
                return True
            place = None
            with contextlib.suppress(Exception):
                place = await self.ai.find_placement(UnitTypeId.BUNKER, near=near, placement_step=1)
            if place is None:
                return True
            self._placement = place
            with contextlib.suppress(Exception):
                import logging

                logging.getLogger(__name__).info(
                    "RAMPBUNKER placement=(%.1f,%.1f) ramp_mid=(%.1f,%.1f) top=(%.1f,%.1f)",
                    place.x,
                    place.y,
                    near.x,
                    near.y,
                    self.ai.main_base_ramp.top_center.x,
                    self.ai.main_base_ramp.top_center.y,
                )

        # 派最近的采矿 SCV 去建（每帧重发到 already_pending，压过 sharpy 抢人）
        with contextlib.suppress(Exception):
            workers = self.ai.workers.gathering or self.ai.workers
            if workers:
                scv = workers.closest_to(self._placement)
                scv.build(UnitTypeId.BUNKER, self._placement)
        return True


class BcRush(KnowledgeBot):  # type: ignore[misc]
    """二本速战巡：快扩 CC2 → FusionCore + Starport TechLab → BC 骚扰流。

    首舰 ~4:10-4:40；BcRaidSquadAct 自动骚扰敌矿。
    """

    def __init__(self) -> None:
        super().__init__("VibeCraft BcRush")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：FusionCore ready + 第一艘 BC ready。

        FusionCore 是 BC 双前置之一；条件满足即代表科技链已经建好。
        """
        fusion_ready = ai.structures(UnitTypeId.FUSIONCORE).ready.amount >= 1
        if not fusion_ready:
            return False
        bc_count: int = ai.units(UnitTypeId.BATTLECRUISER).ready.amount
        return bool(bc_count >= 1)

    async def create_plan(self) -> BuildOrder:
        # BcRush 是 timing 一波流（BC 出场即开始骚扰），关掉 attack_on_advantage
        # 避免因经济领先而龟防不出门（参考 two_one_one.py 注释）。
        attack = PlanZoneAttack(start_attack_power=15)
        attack.attack_on_advantage = False

        return BuildOrder(
            # 开局完成信号 → Director 推荐转持续 doctrine（persistent_skyterran）
            EmitOpeningCompleteAct(self._opening_done),
            # ---- 持续后台（不被 build 串行阻塞）----
            # BcAutoDepot：父类增速模型 + 随产能放大的人口 buffer，绝不卡人口（用户强规则）
            BcAutoDepot(),
            # 枪兵：**首舰前只出 4 个保命**（2026-06-20 用户：首舰出来前出了 10 来个枪兵，抢矿拖慢首舰）。
            # 优先级 < VF/重工/BC（non-priority，只吃科技链 + BC 之后的余矿）。首舰出来后再放开枪兵海。
            TerranUnit(UnitTypeId.MARINE, 4),
            # 首舰出来后才放开枪兵海（钱多了的主出口，不抢首舰前的矿）
            Step(UnitExists(UnitTypeId.BATTLECRUISER, 1), TerranUnit(UnitTypeId.MARINE, 90)),
            # Cyclone：工厂出，防神谕者 / 临时对空，不需 TechLab
            TerranUnit(UnitTypeId.CYCLONE, 2),
            # Reaper：兵营开了就出 1 个侦察敌方分矿路线（~1:27）
            TerranUnit(UnitTypeId.REAPER, 1),
            # SCV 生产见下方 BC 步骤之后（#582 用户 2026-07-04 拍板改）：non-priority + 无上限
            # —— 只吃"科技链/BC(priority) 之后"的余钱，**绝不占大件的钱**；大件永远先造、农民随便多少、
            # 永不硬停；开二矿后 DistributeWorkers 自动把过饱和农民分流到二矿。
            # （旧法 priority=True 封顶 23/44 被摆在科技链之前 → 抢在工厂前预留矿把工厂饿死，见 #582 复盘。）
            # ---- 早期 critical path（严守顺序）: depot → rax → **两口气尽早开满** ----
            # bc_rush 最缺气：rax 一好就把两口气**连着**开(gas1 完→立刻 gas2)，不再把 gas2 拆出去
            # 等兵营 exists(用户实测:二气太晚→首舰晚、重工没气下不出)。BuildGas(2) 在 sequential 里
            # 紧接 rax，gas1/gas2 ~0:50/1:00 背靠背开 → 气第一时间满。
            SequentialList(
                # 第一个补给楼卡在 **supply 14** 下（2026-06-20 用户：14 农民才下第一个房子，
                # 否则提前花 100 矿 → 卡 SCV 生产/停农民）。BcAutoDepot 也已在 supply<14 时返 0 不抢建。
                Step(Supply(14), GridBuilding(UnitTypeId.SUPPLYDEPOT, 1)),
                GridBuilding(UnitTypeId.BARRACKS, 1),
                BuildGas(2),  # 两口气背靠背开满（不等兵营 ready/exists）
            ),
            # ---- 开矿：**前 3 个大舰连续出来之后**才开二矿（2026-06-19 用户铁律）----
            # 用户多次强调:核心链 兵营→重工→机场→聚变芯 + 机场科技挂件 → 第一时间出大舰,
            # **至少攒够前 3 个大舰、它们有绝对优先权**;3 个大舰连续出来、有多余的钱了,才开二矿/补兵营。
            # 之前 gate=FusionCore ready 仍太早:气限速下大舰慢、矿物 float → CC2 一见余矿就开(二矿太早)。
            # 现在 gate=BATTLECRUISER>=3:核心三建筑 priority=True 预留资源、BC priority=True 预留钱
            # (提前按建造进度留够下一发的钱)→ 前 3 大舰连续出;之后余钱才开二矿。
            Step(UnitExists(UnitTypeId.BATTLECRUISER, 3), Expand(2)),
            # 双 CC 升轨道见下方 BC 步骤之后（#582：别在科技链之前占 150 矿饿死工厂；
            # 配合 morph_building 只对空闲 CC 占矿的 patch）。
            # ---- 兵营 Reactor（双枪兵产能，早期防守用）----
            Step(
                UnitReady(UnitTypeId.BARRACKS, 1),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 1),
            ),
            # ---- 碉堡兜底（**科技链优先，地堡推后**，2026-06-19 用户）----
            # 地堡 100 矿会拖慢核心解锁链 → gate 推到 **FusionCore 修好(科技链完成)之后**才建，
            # 早期防守靠那 1-4 个枪兵 + Cyclone。在主基斜坡口高地边缘(RampBunkerAct，紧贴卡口)，
            # 下方 ManTheBunkers 自动塞兵。（早 all-in 防守此后靠二矿迁移方案补，见 #556 批 2。）
            Step(UnitReady(UnitTypeId.FUSIONCORE, 1), RampBunkerAct()),
            # ---- Factory（Starport 前置，~2:00）priority=True：预留资源，绝不被二矿/兵营/出兵抢矿耽误 ----
            # 注意：这里不挂 TechLab（Cyclone 不需 TechLab，BC 前置走 Starport TechLab）
            Step(
                UnitReady(UnitTypeId.BARRACKS, 1),
                GridBuilding(UnitTypeId.FACTORY, 1, priority=True),
            ),
            # （gas2 已并入上面早期 sequential 的 BuildGas(2)，两口气背靠背开，不再单列）
            # ---- Starport（Factory ready 即建，~2:50）priority=True：核心链预留资源 ----
            Step(
                UnitReady(UnitTypeId.FACTORY, 1),
                GridBuilding(UnitTypeId.STARPORT, 1, priority=True),
            ),
            # ---- Fusion Core（Starport 存在即下，~3:25）priority=True：核心链预留资源 ----
            # BC 需要 FusionCore + Starport TechLab 两个前置；下 VC 同时下方挂 Starport TechLab。
            Step(
                UnitExists(UnitTypeId.STARPORT, 1),
                GridBuilding(UnitTypeId.FUSIONCORE, 1, priority=True),
            ),
            # ---- Starport TechLab（Starport ready 即挂）----
            Step(
                UnitReady(UnitTypeId.STARPORT, 1),
                BuildAddon(UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORT, 1),
            ),
            # ---- BC 持续生产（FusionCore + TechLab 双前置满足后）----
            # priority=True：预留 400 矿 / 300 气给 BC（抢资源优先级）
            Step(
                UnitReady(UnitTypeId.FUSIONCORE, 1),
                TerranUnit(UnitTypeId.BATTLECRUISER, 10, priority=True),
            ),
            # ---- SCV 无上限生产（#582 用户 2026-07-04：大件永远先吃钱、农民吃余钱、绝不占大件的钱）----
            # non-priority → 永远排在所有 priority(科技链/BC)之后，绝不占大件的钱；上面 critical path
            # (depot/兵营/气) 列表更靠前，余量优先级高于 SCV → 也不被 SCV 抢。cap=70≈不封顶：农民随便多少、
            # 永不硬停；开二矿后 DistributeWorkers 自动分流过饱和农民到二矿。
            ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 70, priority=False),
            # 双 CC 升轨道（放 BC 之后 = 不与科技链抢钱；morph_building patch 保证只对空闲 CC 占矿）
            MorphOrbitals(2),
            # ---- gas 3-4（二矿开了(CC2 存在)再扩气，供持续 BC 生产；前 3 大舰靠主矿两口气满采）----
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 2), BuildGas(4)),
            # ==== 无玩家干预时的自动资源级联（2026-06-19 用户拍板，按优先级排列）====
            # 总则：BC 永远 priority=True 先吃矿+气。下面全是 non-priority，只花 BC 之后的余量，
            # 在 BuildOrder 里**按列表顺序**抢余量 → 列表越靠前 = 余量优先级越高。
            #
            # 【气的去处】① BC → ② 气余了再开一个星港(双星港出 BC,最先吃余气) →
            #             ③ 还余 → 升级空军(战巡)攻防(军火库,最后的气兜底)。
            # 【矿的去处】① BC → ② 余钱开二矿(上方 Minerals gate) → ③ 钱多了出枪兵海(主出口) →
            #             ④ 大舰保证后还有潜力 → 升级枪兵攻防。
            #
            # ② 气余了 → 第二个星港 + TechLab（双星港出 BC）。gate: 首舰已出 + 气堆到 150
            #    （门槛**低于**军火库的 300 → 气先喂第二星港,喂不完才轮到空军升级）。
            Step(
                All([UnitExists(UnitTypeId.BATTLECRUISER, 1), Gas(150)]),
                GridBuilding(UnitTypeId.STARPORT, 2),
            ),
            Step(
                UnitReady(UnitTypeId.STARPORT, 2),
                BuildAddon(UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORT, 2),
            ),
            # ③(矿) 加兵营到 4 出枪兵海（钱多了的主出口）。gate=**前 3 大舰出来后**（绝不在攒前 3 大舰
            #    期间出"一堆兵营"抢矿 —— 2026-06-19 用户铁律）。枪兵纯吃矿,不碰 BC 的气。
            Step(UnitExists(UnitTypeId.BATTLECRUISER, 3), GridBuilding(UnitTypeId.BARRACKS, 4)),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 2),
                BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1),  # 供 Stim/Shield
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 3),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 3),  # 双产枪兵烧矿
            ),
            # ④(潜力) 枪兵攻防：兴奋剂/盾牌 + 步兵攻防（大舰保证后的余量，矿+气都吃一点）
            Step(
                UnitExists(UnitTypeId.BATTLECRUISER, 1), GridBuilding(UnitTypeId.ENGINEERINGBAY, 1)
            ),
            Step(UnitReady(UnitTypeId.BARRACKSTECHLAB, 1), Tech(UpgradeId.STIMPACK)),
            Step(UnitReady(UnitTypeId.BARRACKSTECHLAB, 1), Tech(UpgradeId.SHIELDWALL)),
            Step(
                UnitReady(UnitTypeId.ENGINEERINGBAY, 1), Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1)
            ),
            Step(
                UnitReady(UnitTypeId.ENGINEERINGBAY, 1), Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL1)
            ),
            # ③(气) 气**还**发不出去 → 军火库 + 空军(战巡)攻防（最后的气兜底，放列表最末 = 最低优先）。
            #    gate: 首舰已出 + 气堆到 300(高于第二星港的 150 → 第二星港先吃,实在喂不完才升空军)。
            Step(
                All([UnitExists(UnitTypeId.BATTLECRUISER, 1), Gas(300)]),
                GridBuilding(UnitTypeId.ARMORY, 1),
            ),
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                # 战巡武器走 SHIP weapons（SC2 武器分车/空两条，没有合并的"车空武器"；
                # TERRANVEHICLEANDSHIPWEAPONS 不存在于 UPGRADE_RESEARCHED_FROM → Tech 构造即 KeyError）
                Tech(UpgradeId.TERRANSHIPWEAPONSLEVEL1),  # 战巡空军武器 +1
            ),
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                # 护甲是车空合并的"镀层"(Vehicle and Ship Plating)，这条合并 ID 是真实存在的
                Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL1),  # 战巡空军护甲 +1
            ),
            # ---- 家事 + 进攻（tactics SequentialList）----
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),  # SCV 自动修 BC（Repair 不看 UnitTask，reserved BC 照修）
                # 碉堡装填：把最近的枪兵塞进碉堡(每个最多 4)，撑早期 all-in（2026-06-19 用户）
                ManTheBunkers(),
                MineOpenBlockedBase(),
                # 早期守家（BC 出场前供守）；军队成型后 skip
                Step(None, PlanZoneDefense(), skip=lambda ai: ai.supply_army >= 12),
                # min_gas=6：强制主矿两口气**满采**(2 geysers × 3)。bc_rush 最缺气,默认公式
                # (free_workers-8)/2 早期只放 ~4 个采气 → 气严重不足拖慢核心链/大舰。优先采气(用户铁律)。
                DistributeWorkers(min_gas=6),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # 在家修理（non-blocking，放骚扰 act 前；#583 单一归属：GroupHarassAct 已删 _ensure_repair）
                BcHomeRepairAct(),
                # BC 骚扰小队 act（放 Gather 后、ZoneAttack 前；non-blocking）
                GroupHarassAct(),
                # PlanZoneAttack 放最后（execute() 永远 return False，放中间 block 后续）
                attack,
                PlanFinishEnemy(),
            ),
        )
