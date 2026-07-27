"""人族火车开矿 plan。

恶火控图 + 2 女妖 + 转 bio：
  depot(14) → BB(15) → 气矿(16) → 死神侦查 → 升轨道 → Factory + Expand(二矿)
  → Factory Reactor（量产恶火，6 个封顶） → 第 2 气矿
  → Starport → Starport TechLab → 2 女妖 + 研隐形
  → 转 bio：BB2 + Marine(~14) + BB TechLab + Stimpack
  → 第 3 气矿 → MorphOrbitals(2) → bio 成型出门推进

设计参考：strategies/terran/hellion_expand.yaml
"""

from __future__ import annotations

from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.terran import AutoDepot, BuildAddon, MorphOrbitals, TerranUnit
from sharpy.plans.require import UnitExists, UnitReady
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


class HellionExpand(KnowledgeBot):  # type: ignore[misc]
    """火车开矿：恶火控图(6 个封顶) + 2 女妖 + 转 bio 推进。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft HellionExpand")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：≥6 Hellion ready + bio 转型 ≥8 Marine（或兜底 6:00）。"""
        hellions = ai.units(UnitTypeId.HELLION).ready.amount
        marines = ai.units(UnitTypeId.MARINE).ready.amount
        if hellions >= 6 and marines >= 8:
            return True
        return bool(ai.time >= 360.0)

    async def create_plan(self) -> BuildOrder:
        # 关掉 attack_on_advantage：运营型，等 bio 成型后手动出门。
        attack = PlanZoneAttack(start_attack_power=24)
        attack.attack_on_advantage = False
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # ── 补给自动化（顶层兄弟，不被任何串行阻塞） ──────────────────────
            AutoDepot(),
            # ── 农民 ramp：双矿阶梯 ──────────────────────────────────────────
            [
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 22),
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 2),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 44),
                ),
            ],
            # ── 早期 critical path（短串行，depot → BB → 第 1 气矿） ───────────
            SequentialList(
                GridBuilding(UnitTypeId.SUPPLYDEPOT, 1),
                GridBuilding(UnitTypeId.BARRACKS, 1),
                BuildGas(1),
            ),
            # ── BB 一好：Factory + 快扩并行（火车开矿核心节奏） ──────────────
            Step(UnitReady(UnitTypeId.BARRACKS, 1), GridBuilding(UnitTypeId.FACTORY, 1)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), Expand(2)),
            # ── 出兵放 Factory/Expand 之后、后续重建筑(Starport/BB2/gas3/Reactor)之前抢资源 ──
            # 2026-06-17 用户：原版 REAPER/HELLION 堆在 plan 最底部 → 排在 Starport/BB2/gas3/
            # Reactor 后面被它们的 reserve 饿死 → BB1 建好就空转、Factory 也产不出恶火。上移到这：
            # BB1 一好出死神侦查、Factory 一好量产恶火（恶火才是本 build 的"连续出兵"，是工厂流）。
            # 注意：**不**在这塞大量 Marine —— 试过加 MARINE 24，它抢光矿把恶火从 4→2、
            # 拖崩 banshee/stim（hellion_expand 是恶火控图，枪兵是 bio 转型后的事，留在底部）。
            Step(UnitReady(UnitTypeId.BARRACKS, 1), TerranUnit(UnitTypeId.REAPER, 1)),
            Step(UnitReady(UnitTypeId.FACTORY, 1), TerranUnit(UnitTypeId.HELLION, 6)),
            # ── VF 一好：第 2 气矿 + Reactor（先出 1 恶火再接 Reactor，加速双产节奏） ──
            # 原设计等 2 恶火（~42s）再挂 Reactor，导致 4:30 时 Reactor 刚完成、
            # Reactor 等 2 恶火出现再挂。试过等 1 恶火提前挂 —— hellion_4 仍只 3 个
            # 没救回，反把 banshee_2 从 2/3 拖到 0/3，得不偿失，回退。
            Step(UnitReady(UnitTypeId.FACTORY, 1), BuildGas(2)),
            Step(
                UnitReady(UnitTypeId.FACTORY, 1),
                BuildAddon(UnitTypeId.FACTORYREACTOR, UnitTypeId.FACTORY, 1),
                skip_until=UnitExists(UnitTypeId.HELLION, 2),
            ),
            # ── 升轨道指挥中心（双矿持续呼 MULE） ────────────────────────────
            MorphOrbitals(2),
            # ── Starport（VF 前置即可建；TechLab 出女妖 + 研隐形） ────────────
            Step(UnitReady(UnitTypeId.FACTORY, 1), GridBuilding(UnitTypeId.STARPORT, 1)),
            Step(
                UnitReady(UnitTypeId.STARPORT, 1),
                BuildAddon(UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORT, 1),
            ),
            # ── 女妖隐形研究（Starport TechLab 一好立刻研） ───────────────────
            Step(
                UnitReady(UnitTypeId.STARPORTTECHLAB, 1),
                Tech(UpgradeId.BANSHEECLOAK),
            ),
            # ── 转 bio：BB2 + BB TechLab + Stimpack ────────────────────────
            # Starport 一好后补 BB2，扩 bio 产能
            Step(UnitReady(UnitTypeId.STARPORT, 1), GridBuilding(UnitTypeId.BARRACKS, 2)),
            # BB1 接 TechLab（Stimpack 前置）
            Step(
                UnitReady(UnitTypeId.BARRACKS, 1),
                BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.STIMPACK),
            ),
            # ── 步兵关键升级:战斗护盾 + +1/+1 攻防 ──────────────────────────
            # vs VeryHard 0/3 根因:枪兵海有兴奋剂但无护盾/攻防 → 被有升级的 AI 换血换死。
            # 护盾(枪兵 45→55hp)+ EB×2 滚 +1 武器/+1 甲,大幅提枪兵换血效率。
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.SHIELDWALL),
            ),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2),
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
            # ── 第 3 气矿（女妖 / bio 后期科技消耗） ─────────────────────────
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 2), BuildGas(3)),
            # ── 单位生产 ────────────────────────────────────────────────────
            # （恶火/死神已上移到 Factory/Expand 之后，见前面）
            # 女妖：2 个（隐形骚扰农民；priority=True 优先消耗 Starport TechLab）
            TerranUnit(UnitTypeId.BANSHEE, 2, priority=True),
            # Marine：bio 转型后持续生产（目标 ~14）—— 留在恶火/女妖之后，不抢早期恶火的矿
            TerranUnit(UnitTypeId.MARINE, 14),
            # ── 家事 + 进攻（tactics 段） ────────────────────────────────────
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                # PlanZoneDefense 会从 free_units 抽兵标 Defending → PlanZoneAttack
                # 看不见这些兵 → 永不出门。bio 成型后 skip，让主力专心出门。
                Step(None, PlanZoneDefense(), skip=lambda ai: ai.supply_army >= 18),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                # 骚扰单位单独拉出去点对方农民 —— 造出兵 ≠ 骚扰到位。
                # 恶火早期就能控图骚扰,无 gate;女妖等隐形研出来再出门(裸女妖会送)。
                # 排在 Gather 之前:先 Reserved 掉它们,Gather 就不会拉进主力。
                # release_after=450:7:30 后放手让恶火/女妖归队 —— 本 build 后期要
                # 凑 bio 一波,骚扰单位长期 Reserved 会饿掉 PlanZoneAttack 的
                # 兵力判定、主力推进被拖晚(attack_moveout 回归)。
                HarassWorkerLineAct({UnitTypeId.HELLION}, release_after=450),
                HarassWorkerLineAct(
                    {UnitTypeId.BANSHEE},
                    wait_upgrade=UpgradeId.BANSHEECLOAK,
                    release_after=450,
                ),
                PlanZoneGather(),
                # PlanZoneAttack 放最后：execute() 永远 return False（源码 "Blocks!"），
                # 放中间会 block 掉后面的 DistributeWorkers / Gather。
                attack,
                PlanFinishEnemy(),
            ),
        )
