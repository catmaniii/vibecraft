"""人族爆死神(Mass Reaper) plan。

用户 2026-07-13 拍板:上来**双气满采** → 2 兵营(加反应堆)**持续爆死神**,死神 kite 自愈保命、
积累数量骚扰逼死对方经济;余钱开气/开矿/补农民/扩张。**气够就一直出死神**。

关键经济(用户经验 + 研究,2 Rax / ByuN 3 Rax mass reaper):
- 死神 = 50 矿 + 50 气,吃气 → **双气满采是命脉**(2 气 ~228 气/min,支撑 2 反应堆兵营 ~4 死神/45s)。
- 反应堆兵营 = 一次出 2 死神 → 2 兵营加反应堆 = 死神量产。
- 微操 kite(死神脱战自愈):复用 `HarassWorkerLineAct({REAPER})`——冷却+敌人逼近就退、绝不站撸,
  尽量别死、持续骚扰积累。

设计参考:strategies/terran/mass_reaper.yaml
"""

from __future__ import annotations

from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import (
    ActUnit,
    BuildGas,
    DefensePosition,
    DefensiveBuilding,
    Expand,
    GridBuilding,
    MineOpenBlockedBase,
    Tech,
)
from sharpy.plans.acts.terran import AutoDepot, MorphOrbitals, TerranUnit
from sharpy.plans.require import RequireCustom, Supply, UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)
from sharpy.plans.tactics.terran import CallMule, LowerDepots, Repair

from vibecraft.bot.auto_combat.harass_act import HarassWorkerLineAct
from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct


class MassReaper(KnowledgeBot):  # type: ignore[misc]
    """爆死神开局:双气满采 + 2 兵营反应堆持续死神 + kite 积累。"""

    # ── 防空检测词表(死神纯对地,敌人转空军时补枪兵;用户 2026-07-13 指定)────────
    # 敌方"威胁性"空军单位:见到就判定走空。**排除** overlord/overseer/medivac/warp prism/
    # observer/raven 这些无威胁的支援飞行(尤其虫族常驻 overlord,不能一刀切 .flying;raven 的
    # 自动炮台是地面单位、死神打得了)。VIKINGFIGHTER=飞行态维京(检测用,不训练)。
    _AIR_THREAT_UNITS = frozenset(
        {
            UnitTypeId.MUTALISK,
            UnitTypeId.CORRUPTOR,
            UnitTypeId.BROODLORD,
            UnitTypeId.VOIDRAY,
            UnitTypeId.PHOENIX,
            UnitTypeId.ORACLE,
            UnitTypeId.CARRIER,
            UnitTypeId.TEMPEST,
            UnitTypeId.MOTHERSHIP,
            UnitTypeId.BANSHEE,
            UnitTypeId.BATTLECRUISER,
            UnitTypeId.LIBERATOR,
            UnitTypeId.LIBERATORAG,
            UnitTypeId.VIKINGFIGHTER,
        }
    )
    # 明确产空建筑(提前预警;**不含** terran STARPORT——它也产 medivac/raven,信号噪)。
    # FUSIONCORE = BC 铁证,零误报。
    _AIR_TECH_STRUCTURES = frozenset(
        {
            UnitTypeId.SPIRE,
            UnitTypeId.GREATERSPIRE,
            UnitTypeId.STARGATE,
            UnitTypeId.FLEETBEACON,
            UnitTypeId.FUSIONCORE,
        }
    )
    # 主力舰/重型空军:见到把枪兵上限从 20 提到 35(Fable5:裸枪兵对高甲主力舰需更多数量)。
    _AIR_CAPITAL = frozenset(
        {
            UnitTypeId.BATTLECRUISER,
            UnitTypeId.CARRIER,
            UnitTypeId.MOTHERSHIP,
            UnitTypeId.TEMPEST,
            UnitTypeId.BROODLORD,
        }
    )

    def __init__(self) -> None:
        super().__init__("VibeCraft MassReaper")
        # 防空 latch:一旦判定敌人走空就锁死(空军会离开视野,latch 防枪兵产量抖动;
        # 只让上限升、不降 —— 见过 = 永久威胁,20 枪兵总价 1000 矿反正矿在飘、零成本)。
        self._seen_enemy_air = False
        self._seen_air_capital = False

    def _enemy_going_air(self, ai: Any) -> bool:
        """敌人是否在走空军(死神打不了空 → 补枪兵)。latch:见过威胁空军/产空建筑就锁定。"""
        if self._seen_enemy_air:
            return True
        if (
            ai.enemy_units(self._AIR_THREAT_UNITS).exists
            or ai.enemy_structures(self._AIR_TECH_STRUCTURES).exists
        ):
            self._seen_enemy_air = True
        return self._seen_enemy_air

    def _enemy_air_capital(self, ai: Any) -> bool:
        """敌方是否有主力舰/重型空军(枪兵上限提到 35)。同样 latch(只升不降)。"""
        if self._seen_air_capital:
            return True
        if ai.enemy_units(self._AIR_CAPITAL).exists:
            self._seen_air_capital = True
        return self._seen_air_capital

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成:≥8 死神成规模 + 二矿 CC done(死神压制住、开始滚雪球)。"""
        if ai.units(UnitTypeId.REAPER).amount < 8:
            return False
        cc = (
            ai.structures(UnitTypeId.COMMANDCENTER).amount
            + ai.structures(UnitTypeId.ORBITALCOMMAND).amount
            + ai.structures(UnitTypeId.PLANETARYFORTRESS).amount
        )
        return bool(cc >= 2)

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # 补给自动化
            AutoDepot(),
            # ── 农民阶梯:单矿 22 → 二矿后 44(死神吃气,农民也要供矿造死神 + 采气)──
            [
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 22),
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 2),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 44),
                ),
            ],
            # ── 早期 critical path:depot → BB → **双气背靠背开满**(用户"上来就气满采")──
            SequentialList(
                Step(Supply(12), GridBuilding(UnitTypeId.SUPPLYDEPOT, 1)),
                GridBuilding(UnitTypeId.BARRACKS, 1),
                BuildGas(2),  # 两口气背靠背,不等兵营 —— 死神吃气,气满采是命脉
            ),
            # 星轨(经济,呼 MULE 补矿)
            MorphOrbitals(1),
            # ── 死神量产骨架(Fable5 修正 + 2026-07-13 真机调试)──────────────────
            # 死神建造 **32s**(faster,不是 45s)→ 1 裸兵营 2死神/64s... 实际 1 兵营连续 = 1死神/32s≈93
            # 气/min。2 气满采 ~226气/min → 撑得起 ~2.4 裸兵营。一基期 **3 裸兵营** 持续死神(贴住 2 气 +
            # 星轨 MULE 补矿)。反应堆先不上(真机调试:reactor 挂件 + priority 组合让兵营停产、矿堆
            # 1180 不产死神;裸兵营范式对齐能工作的 reaper_expand)。
            # ── 敌人出空军 → 补枪兵防空(死神纯对地打不了空;用户 2026-07-13 指定)──────────
            # 死神打不了空,敌方转空军(muta/void/banshee/BC/oracle/BL…)时纯死神干瞪眼——这是
            # 100% 必败分支。见到威胁空军/产空建筑就 latch,补枪兵:**50 矿 0 气**,恰好花掉死神
            # 花不掉、一路飘着的矿(死神瓶颈是气),经济上净赚、不抢死神的气。20 保底;敌有主力舰
            # (BC/航母/母舰/风暴舰/BL)→ 提到 35。枪兵吃同样 3/3 步兵升级。**放在死神产能之前** →
            # air-latch 后枪兵优先拿兵营 slot、补满自动还给死神(靠 BuildOrder 顺序给优先级,不用
            # priority flag,避开"Step+priority 停产"坑)。这是玩家指定的防空反应,**非转生化**
            # (转生化仍走玩家确认的 lategame_transition)。
            Step(RequireCustom(self._enemy_going_air), TerranUnit(UnitTypeId.MARINE, 20)),
            Step(RequireCustom(self._enemy_air_capital), TerranUnit(UnitTypeId.MARINE, 35)),
            # 敌走空 → **提前补 EB**(导弹炮塔的前置;banshee 常 4-5min 来骚扰,别等三矿后才有 EB
            # → 那时隐形 banshee 已经杀穿农民)。air-latch 一触发就建,顺带 L1 攻防也能早点开。
            Step(RequireCustom(self._enemy_going_air), GridBuilding(UnitTypeId.ENGINEERINGBAY, 1)),
            # **每个矿区矿后放导弹炮塔**——解枪兵解决不了的两半:①炮塔=**检测器**,照见隐形 banshee
            # (枪兵没检测,隐形面前 20 枪兵=0);②**静态对空**守矿区,muta 绕后骚扰分矿时枪兵追不上、
            # 炮塔原地就打。`to_base_index=None`→所有基地矿后各放 to_count 个,随新开矿自动补。枪兵
            # (机动跟军队打)+ 炮塔(静态守矿区+检测)覆盖 Fable5 说的两半。全是**防御建筑**,死神仍
            # 是主力,**非转兵种**(转生化仍走玩家确认)。gate 在 air-latch + EB ready(炮塔需 EB)。
            Step(
                RequireCustom(
                    lambda ai: (
                        self._enemy_going_air(ai)
                        and ai.structures(UnitTypeId.ENGINEERINGBAY).ready.exists
                    )
                ),
                DefensiveBuilding(
                    UnitTypeId.MISSILETURRET, DefensePosition.BehindMineralLineCenter, to_count=1
                ),
            ),
            # 死神产能放【top-level 无 priority】(对齐 reaper_expand 的 TerranUnit(REAPER,4) 范式;
            # 之前放 Step + priority=True 导致停产)。
            TerranUnit(UnitTypeId.REAPER, 50),
            # ── 兵营随**气**量 scale(死神吃气,兵营多寡由气撑;每 ~1 口气撑 ~1.2 裸兵营)────
            # 1 基 3 裸兵营;4 口气 →5;6 口气 →7。别提前堆兵营(裸兵营吃矿不产 = 卡产能/囤矿),
            # 让兵营数追着气长 → 气到位就有兵营立刻把气变成死神。
            Step(UnitReady(UnitTypeId.BARRACKS, 1), GridBuilding(UnitTypeId.BARRACKS, 3)),
            Step(
                RequireCustom(lambda ai: ai.gas_buildings.ready.amount >= 4),
                GridBuilding(UnitTypeId.BARRACKS, 5),
            ),
            Step(
                RequireCustom(lambda ai: ai.gas_buildings.ready.amount >= 6),
                GridBuilding(UnitTypeId.BARRACKS, 7),
            ),
            # ── 滚雪球:死神成规模 → 开二矿(用累计/时间兜底防"战损死锁",Fable5)──────────
            # 用 RequireCustom(死神≥6 **或** time>3:45):kite 失手死几只后存活数永远不到 6 → Expand
            # 死锁(同 nydus 条件门永不触发的病)。时间兜底保证 3:45 一定开矿。
            Step(
                RequireCustom(lambda ai: ai.units(UnitTypeId.REAPER).amount >= 6 or ai.time > 225),
                Expand(2),
            ),
            # 二矿 CC 一放下(含在建)就去二矿点开两口气(refinery 不需 CC 完工即可建,Fable5:让气与 CC
            # 差不多同时好、气工即刻转入,别等 CC 完工再开气)。二矿=townhalls≥2(主基已 morph 星轨,
            # 不能只数 COMMANDCENTER)。
            Step(
                RequireCustom(lambda ai: ai.townhalls.amount >= 2),
                MorphOrbitals(2),
            ),
            Step(
                RequireCustom(lambda ai: ai.townhalls.amount >= 2),
                BuildGas(4),
            ),
            # ── 持续扩张(修"矿飘 3-5 万"根因)────────────────────────────────────
            # 死神 = 50 矿 50 气(1:1),气永远是瓶颈 → 矿花不掉、一路飘到几万。用户规格:多余矿
            # **开新矿/开气/扩张**。这里:矿存>400 且**无在建 CC** → 再开一矿,一次一个,滚到 6 矿。
            # 多矿=多气矿=多气=多兵营=多死神 → 矿真花得掉(良性循环,替代之前只开到 3 矿就囤死)。
            Step(
                RequireCustom(lambda ai: ai.minerals > 400 and ai.townhalls.not_ready.amount == 0),
                Expand(6),
            ),
            # 气随基地 scale:每矿 2 口。3 矿 →6 口气,4 矿 →8 口气(喂满 5/7 兵营量产死神)。
            Step(
                RequireCustom(lambda ai: ai.townhalls.ready.amount >= 3),
                BuildGas(6),
            ),
            Step(
                RequireCustom(lambda ai: ai.townhalls.ready.amount >= 4),
                BuildGas(8),
            ),
            # ── 3 矿后地面攻防升级(用户 2026-07-13:开到三矿以上、余钱多了才补;三矿前纯死神)──
            # 死神是**轻甲步兵**,吃 TERRANINFANTRY 攻防升级 → 直接 buff 死神(+2/+2 让死神战斗力质变、
            # 战损再降),**这是强化当前兵种、不是转兵种战略**(转枪兵/生化仍走玩家确认的 lategame_
            # transition,见 yaml)。全部 gate 在 townhalls.ready>=3:**三矿前一分钱不分给科技**,保证前
            # 期一直爆死神。攻防也是后续转生化的共享科技(顺带备好转型)。
            Step(
                RequireCustom(lambda ai: ai.townhalls.ready.amount >= 3),
                GridBuilding(UnitTypeId.ENGINEERINGBAY, 2),
            ),
            Step(
                UnitReady(UnitTypeId.ENGINEERINGBAY, 1),
                Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1),
            ),
            Step(
                UnitReady(UnitTypeId.ENGINEERINGBAY, 1),
                Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL1),
            ),
            # +2 需军火库(Armory);3 矿 + EB 就位后补。
            Step(
                RequireCustom(
                    lambda ai: (
                        ai.townhalls.ready.amount >= 3
                        and ai.structures(UnitTypeId.ENGINEERINGBAY).ready.amount >= 1
                    )
                ),
                GridBuilding(UnitTypeId.ARMORY, 1),
            ),
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL2),
            ),
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL2),
            ),
            # ── 持续升到 3/3(用户 2026-07-13:后期持续升级到三攻三防)──────────────────
            # L3 是 L2 的自然延续(军火库已为 L2 建好,无新前置;游戏自动 L2→L3 排序)。死神双枪
            # 每级 +1 → 每轮齐射 8→10→12→14,**+3 = 基础伤害 +75%**(死神吃步兵武器升级性价比极高);
            # +3 防对 60HP 死神配合脱战回血也是质变,战损再降。定位:巩固 Medium、争取 Hard 翻盘
            # (VeryHard 的坦克/AoE 硬克不吃 +3,别指望;Fable5)。三矿后 6 气一次性吸收得动。
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL3),
            ),
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL3),
            ),
            # ── 家事 + 死神微操 + 进攻(tactics)──────────────────────────────
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                # DistributeWorkers/SpeedMining 必须排在 PlanZoneAttack 之前(它每帧 return False 会
                # 截断 SequentialList)。
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                # 死神 kite 微操(基线 hit-and-run)+ **release_after 攒军队收尾**(2026-07-13 autonomous
                # 迭代 v1):真机诊断——8 兵营满负荷产死神(~15只/min),但全被 harass 喂去自杀、死神永远卡
                # 2-9、攒不成军队 → 没兵防守 → 连 Medium 都一波推空家(0/6)。修:harass 只干**前 4 分钟**
                # (早期骚扰压制),到点 release → 死神不再被 Reserved、交给 PlanZoneDefense/Gather/Attack
                # **攒成死神军队**防守家 + 够强再一波推。让死神"积累数量"真的发生。
                HarassWorkerLineAct(
                    {UnitTypeId.REAPER},
                    bail_hp_ratio=0.5,
                    recover_hp_ratio=0.85,
                    release_after=240.0,
                ),
                PlanZoneGather(),
                PlanZoneAttack(),
                PlanFinishEnemy(),
            ),
        )
