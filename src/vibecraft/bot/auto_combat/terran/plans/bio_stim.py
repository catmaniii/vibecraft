"""人族生化兴奋剂中期 plan。

三矿 MMM timing：Marine + Marauder + Medivac 三件套。
Stimpack 完成（~5:00）+ 4 医疗船 ready（~7:00）→ 8:00 出门。
工程湾步兵攻防各 +1，Factory TechLab 出 1-2 坦克，5 兵营量产。

build order 参考：
  - https://liquipedia.net/starcraft2/MMM_Timing_Push
  - https://lotv.spawningtool.com/build/85561/ （Reaper Expand into Stim Medivac）
  - https://terrancraft.com/2019/08/06/builds-for-beginners-and-intermediate-players/

设计参考：strategies/terran/bio_stim.yaml
"""

from __future__ import annotations

from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.terran import AutoDepot, BuildAddon, MorphOrbitals, TerranUnit
from sharpy.plans.require import All, Supply, SupplyType, UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)
from sharpy.plans.tactics.terran import CallMule, LowerDepots, Repair

from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct
from vibecraft.bot.auto_combat.terran.siege_idle_tanks import SiegeIdleTanksAct


class BioStim(KnowledgeBot):  # type: ignore[misc]
    """生化兴奋剂中期：MMM timing 压制，三矿运营，8:00 出门。工程湾攻防 +1，坦克支援。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft BioStim")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：Stim 完成 + ≥5 BB + ≥12 Marine + ≥4 Medivac。"""
        stim_done = UpgradeId.STIMPACK in ai.state.upgrades
        if not stim_done:
            return False
        barracks = (
            ai.structures(UnitTypeId.BARRACKS).amount
            + ai.structures(UnitTypeId.BARRACKSFLYING).amount
        )
        if barracks < 5:
            return False
        marines = ai.units(UnitTypeId.MARINE).ready.amount
        if marines < 12:
            return False
        medivacs = ai.units(UnitTypeId.MEDIVAC).ready.amount
        return bool(medivacs >= 4)

    async def create_plan(self) -> BuildOrder:
        # 关掉 attack_on_advantage：sharpy 默认「经济领先 + 军队劣势」龟防
        # （zone_attack.py _should_attack），MMM timing 必须按战术出门压制。
        attack = PlanZoneAttack(start_attack_power=18)
        attack.attack_on_advantage = False
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # ── 补给自动化（顶层兄弟，不被任何串行阻塞） ──────────────────────
            AutoDepot(),
            # ── BB TechLab：必须排在 Marine 产线**前面** ───────────────────────
            # 2026-06-18 修「TechLab 永不挂」根因：BuildAddon 只在 `.ready.idle` 兵营上挂挂件
            # （build_addon.py:46）。Marine(priority) 排在前面时，每帧先抢空闲兵营塞枪兵 →
            # 兵营永不空闲 → TechLab 挂不上，直到 ~650s 钱 flood 兵营才偶尔空闲挂上 →
            # stim 拖到 749s、0 掠夺者、气 flood 2000(无 TechLab 消耗)。把 2 个 TechLab 挂件
            # 排到 Marine 前：兵营产完一发枪兵那一帧空闲时，BuildAddon 先抢到手挂 TechLab。
            # 只前置 TechLab(科技关键，供 stim+掠夺者)；Reactor(量产枪兵)仍留 Marine 后。
            Step(
                UnitReady(UnitTypeId.BARRACKS, 1),
                BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 2),
                BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 2),
            ),
            # ── MMM 单位生产：已下移到所有建筑步**之后**（见 create_plan 末尾的产兵集群） ──
            # 2026-06-18 经验：任何重矿单位(掠夺者 100 矿/发)产线排在建筑步**前**都会把矿
            # 抽干 → 二矿/BB3-5 饿死、经济崩(2 兵营 0 二矿早亡)。统一把产兵放建筑后，让
            # 建筑(开矿/5 兵营/挂件)先吃矿成型，再产兵。附带好处：建筑期兵营天然空闲 →
            # TechLab/Reactor 挂件好挂上(BuildAddon 只在 .ready.idle 兵营挂)。
            # ── 农民 ramp：三矿三档阶梯 ──────────────────────────────────────
            # 一矿 → 22 农；**3 兵营齐**再爬 44（不再 CC2-好就爬）；三矿好 → 爬 58
            # 2026-06-18：二矿提早(BB1-ready)后,SCV-44 档(原 CC2-exists ~190 触发)排在 BB2-5
            # 前面把矿抽走 → 兵营卡 1 个到 330s。改成"3 兵营齐了再爬 44":22 农先把核心 3 兵营
            # 拉起来(~240),再爆农到 44。兵营是产能命脉,优先于第 2 波农民;但只 hold 到 3(不是 5),
            # 否则中期农民太少(实测 hold 到 5 时 6min 才 28 农、经济偏软),3 兵营起后就放开爆农。
            [
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 22),
                Step(
                    UnitReady(UnitTypeId.BARRACKS, 3),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 44),
                ),
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 3),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 58),
                ),
            ],
            # ── 早期 critical path（短串行，只含最不可分的前三步） ─────────────
            # depot → BB → 第 1 气矿；严守顺序保证 BB ~1:20 完成
            SequentialList(
                GridBuilding(UnitTypeId.SUPPLYDEPOT, 1),
                GridBuilding(UnitTypeId.BARRACKS, 1),
                BuildGas(1),
            ),
            # ── BB 一好：二矿照常早开（用户要的就是二矿早），但延后三矿 ──────────
            # 2026-06-18：二矿(CC2)BB1-ready 即开（标准快扩，spec 窗口 1:50-3:10）。早期矿荒
            # 不靠延后二矿解决（用户明确"二矿延后不是我的需求"），而是把**产兵下移到建筑步后**
            # （见末尾产兵集群）+ 三矿延后到有军队再开（见下）——这样早期只有 1 个 400 的 CC2 跟
            # TechLab/stim 抢矿，stim 仍 ~250s 出；不会像"CC2+CC3 连开"那样把 stim 拖到 417s。
            # Factory/gas2 同 BB1-ready；gas3 推到 Factory-exists（3 口气喂枪兵海 flood,延后采矿）。
            Step(UnitReady(UnitTypeId.BARRACKS, 1), Expand(2)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), GridBuilding(UnitTypeId.FACTORY, 1)),
            # Starport 紧跟 Factory（air-tech 路径）：必须排在 BB3-5 / 三矿**前面**，否则
            # 抢不到 SCV/矿(实测被 BB3-5+CC2+CC3 卡到 422s,医疗船跟着晚)。Factory 一放置就建。
            Step(UnitExists(UnitTypeId.FACTORY, 1), GridBuilding(UnitTypeId.STARPORT, 1)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), BuildGas(2)),
            Step(UnitExists(UnitTypeId.FACTORY, 1), BuildGas(3)),
            # 工程湾 +1：脱离 CC2-exists 门(原来链在晚到的 CC2 后→EngBay 330s→+1 攻击 487s 超窗)。
            # 改 Factory-exists 触发 + 提到 BB4/5 前，+1 攻防早出。
            Step(UnitExists(UnitTypeId.FACTORY, 1), GridBuilding(UnitTypeId.ENGINEERINGBAY, 1)),
            # ── 三矿：5 兵营齐 + 一小股防守部队再开（用户拍板折中：别没几个兵就开）─────────
            # 2026-06-18：原 CC2-exists 一好就连开三矿 → 没几个兵就 2-3 矿(#537 投诉根因)
            # + CC2+CC3 双 400 连抽把 stim 拖到 417s。门改 `5 兵营 ready + combat supply ≥ 6`:
            #   - **放高优先级建筑块**(Expand2 之后):否则排在产兵后抢不到 SCV/矿,gate 开了还拖到 7:40。
            #   - 但**必须等 5 兵营齐**才放(BARRACKS,5 ready ~330)再开:否则高优先级 400 的 CC3
            #     会抢走 BB5 的 SCV → 只 2 兵营(实测)。等 BB5 好时 army 也 ~10 了,自然满足"别没几个兵"。
            # CC3 ~6:40 落地(折中:比老 5:30 晚、又不像纯 army≥10 那样拖到 7:40 伤经济)。
            Step(All([UnitReady(UnitTypeId.BARRACKS, 5), Supply(6, SupplyType.Combat)]), Expand(3)),
            # ── 升轨道指挥中心（三矿各升 Orbital，持续呼 MULE） ─────────────
            MorphOrbitals(3),
            # ── BB 扩产能：BB2 → BB5（标准 bio 5 兵营） ──────────────────────
            # BB1 ready 后尽快补 BB2；BB2 ready 接着补 BB3（兵营 ramp 与开矿解耦，
            # 不再等 CC2 落地 → 矿荒期也能持续加兵营）；二矿好后补 BB4/BB5
            Step(UnitReady(UnitTypeId.BARRACKS, 1), GridBuilding(UnitTypeId.BARRACKS, 2)),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 2),
                GridBuilding(UnitTypeId.BARRACKS, 3),
            ),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2),
                GridBuilding(UnitTypeId.BARRACKS, 4),
            ),
            # BB5：改为 CC2 exists 触发（原为 CC3 exists，导致 5BB 要等三矿落地才触发，
            # barracks_5 check 实测只有 4 BB）；二矿好就可以上 BB4/BB5 预备产能。
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2),
                GridBuilding(UnitTypeId.BARRACKS, 5),
            ),
            # ── 三矿：已上移到 Expand2 之后的高优先级建筑块（见上方），保证 gate 一开就立刻建 ──
            # ── BB1/BB2 接 TechLab：已上移到 Marine 产线前（见上方注释） ──────────
            # ── BB3/BB4/BB5 接 Reactor（量产 Marine） ─────────────────────────
            Step(
                UnitReady(UnitTypeId.BARRACKS, 3),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 3),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 4),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 4),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 5),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 5),
            ),
            # ── Factory 接 TechLab（出坦克前置） ─────────────────────────────
            Step(
                UnitReady(UnitTypeId.FACTORY, 1),
                BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 1),
            ),
            # ── Starport / 工程湾1：已上移到 Factory 之后（见上方注释） ──────────
            # ── 工程湾2（攻防 +2 链，三矿后）─────────────────────────────────
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 3),
                GridBuilding(UnitTypeId.ENGINEERINGBAY, 2),
            ),
            # ── 三升级（各独立 Step，不串行互堵） ─────────────────────────
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.STIMPACK),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.SHIELDWALL),  # Combat Shield
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.PUNISHERGRENADES),  # Concussive Shells
            ),
            # ── 步兵攻击 +1 / 护甲 +1（各放独立 Step） ──────────────────────
            Step(
                UnitReady(UnitTypeId.ENGINEERINGBAY, 1),
                Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1),
            ),
            Step(
                UnitReady(UnitTypeId.ENGINEERINGBAY, 1),
                Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL1),
            ),
            # ── 步兵攻击 +2 / 护甲 +2（需 Armory；三矿后有条件研究） ───────────
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL2),
            ),
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL2),
            ),
            # ── Starport 接 Reactor（双产 Medivac） ──────────────────────────
            Step(
                UnitReady(UnitTypeId.STARPORT, 1),
                BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1),
            ),
            # ── MMM 单位生产（放所有建筑步之后：建筑先吃矿成型，再产兵） ─────────
            # Marauder/Medivac 排在 Marine **前**(execution order)：兵营产完一发那帧空闲时，
            # 掠夺者先抢到 TechLab 兵营档期(否则 Marine 把所有兵营塞满 → 0 掠夺者 + 气 flood)。
            # 三者都 priority=True：此时建筑步已在它们**前面**先 reserve 过矿，产兵不会饿死建筑。
            TerranUnit(UnitTypeId.MARAUDER, 16, priority=True),
            TerranUnit(UnitTypeId.MEDIVAC, 8, priority=True),
            TerranUnit(UnitTypeId.MARINE, 60, priority=True),
            # ── 坦克：Factory TechLab 好后出 1-2 辆（priority 保住气） ──────────
            # 坦克提供远程压制，支援 MMM 推进或防守；出厂即可架炮（SC2 无 Siege Tech）
            Step(
                UnitReady(UnitTypeId.FACTORYTECHLAB, 1),
                TerranUnit(UnitTypeId.SIEGETANK, 2, priority=True),
            ),
            # ── 家事 + 进攻（tactics 段，原样保留结构） ──────────────────────
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                # PlanZoneDefense 会从 free_units 抽兵标 Defending → PlanZoneAttack
                # 看不见这些兵 → 永不出门（见 dt_drop_iac.py / gate4_pressure.py 注释）。
                # 军队成型后 skip 掉，让主力专心出门。
                Step(None, PlanZoneDefense(), skip=lambda ai: ai.supply_army >= 20),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                SiegeIdleTanksAct(),
                # PlanZoneAttack 放最后：execute() 永远 return False（源码 "Blocks!"），
                # 放中间会 block 掉后面的 DistributeWorkers / Gather。
                attack,
                PlanFinishEnemy(),
            ),
        )
