"""虫族宏观双孵 plan — ZvT 标准 Macro Hatch，pro timing 对标。

开局路线（Spawning Tool Ref: Grim ZvT 16/18/17 Quick 3rd）：
  顺序段（SequentialList）确保前期建筑顺序精确：
    17 BH  ≈ 0:51  — 二矿先于母池（先扩后池）
    17 BE  ≈ 1:08  — 开第一气
    18 BS  ≈ 1:16  — 母池
  并行段（BuildOrder）负责中后期科技 + 量产，不卡死顺序：
    蜂后 ×2  ≈ 2:07（BS 完工后）
    ling speed ≈ 2:09 开始研，3:25 完成
    小虫 ×6 守家
    三矿 BH ≈ 2:30
    BR 蟑螂窝 ≈ 3:47
    BV 进化腔 ×2 ≈ 3:48
    Lair ≈ 4:05（升在自然矿 BH）
    +1 导弹武器 ≈ 4:29
    +1 地甲 ≈ 4:29
    Glial（蟑螂速）—— ZvT 最优先，Lair 后立刻研
    蟑螂 ×28 量产 → 出门 7-9 min
    暴农 目标 60 农民
    蜂后补至 4 只（三矿后）

建筑 hotkey 备注（SC2 Standard Layout，虫族）：
  BH=孵化场  BE=气矿  BS=母池  BR=蟑螂窝  BV=进化腔
单位：农民(Drone) / 蜂后(Queen) / 小虫(Zergling) / 蟑螂(Roach) / 霸主(Overlord)
升级：ling speed=Metabolic Boost / 蟑螂速=Glial Reconstitution
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
    Expand,
    GridBuilding,
    MineOpenBlockedBase,
    Tech,
)
from sharpy.plans.acts.zerg import AutoOverLord, MorphLair, ZergUnit
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)
from sharpy.plans.tactics.zerg import InjectLarva

from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct


def _make_macro_attack() -> PlanZoneAttack:
    """macro_hatch 主力 attack act:start_attack_power=20 + 关 attack_on_advantage。

    2026-05-27: 同 terran mech/bc_late/bio_max A2 修复模式。zerg macro 三矿后期
    经济强但军队中等,sharpy 默认 attack_on_advantage=True 在"经济优势 + 军队
    劣势"时龟防累积不出门(实测 macro_hatch vs VeryEasy 兵 28 蟑螂到位但
    attack_moveout 卡 20 min)。
    """
    attack = PlanZoneAttack(start_attack_power=20)
    attack.attack_on_advantage = False
    return attack


class MacroHatch(KnowledgeBot):  # type: ignore[misc]
    """宏观双孵开局：17 BH → 17 BE → 18 BS，三矿 + 蟑螂出门 7-9 min。

    ZvT 标准 Macro Hatch，对标 Grim / Serral 节奏。
    慢节奏宏观流，7-9 分钟出门需 28 蟑螂 + 1/1 攻防。
    """

    def __init__(self) -> None:
        super().__init__("VibeCraft MacroHatch")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：三矿(BH+LAIR+HIVE 合计) ≥3 且蟑螂 ≥10 → 通知 Director 切持续 doctrine。

        3 矿宏观运营落地 + 蟑螂量产到位（ROACH ≥10）表示
        macro_hatch 开局成型，Director 可推荐 toast 转后期持续运营。

        2026-05-28: HATCHERY → townhalls(含 LAIR/HIVE)修「Lair 升级后
        HATCHERY 数 < 3 永远不触发 opening_completed」bug。
        实测 game_20260528_030401_8a7af6 t=536 时 HATCH=2 LAIR=1(总 3 矿但
        plan MorphLair 升了主基地)，原条件 hatch≥3 永远 False →
        OpeningSustainAct 120s 超时永远不触发 → 蟑螂卡 28 摆烂。
        """
        try:
            townhalls = ai.townhalls.amount  # 自动合并 HATCH+LAIR+HIVE
            roach = ai.units(UnitTypeId.ROACH).amount
        except Exception:
            return False
        return bool(townhalls >= 3 and roach >= 10)

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # ── 顺序段：前期建筑顺序严格锁定 ──────────────────────────────────
            # Spawning Tool Ref: lotv.spawningtool.com/build/124625/
            # 17 供 凑农民 → 17 BH（≈0:51）→ 17 BE（≈1:08）→ 18 BS（≈1:16）
            SequentialList(
                # 凑到 17 农民（含开局 12），准备派探机走二矿
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 17),
                # 17 BH：二矿孵化场，先扩后池是 Macro Hatch 的核心区别于 Pool First
                # Ref: Grim 16/18/17 — 二矿落地 ≈ 0:51
                Expand(2),
                # 17 BE：第一气矿，Lair + 提速 + 蟑螂速都需要气
                # Ref: ≈ 1:08，同一探机或另派一个
                BuildGas(1),
                # 18 BS：母池，BS 完工后才能出蜂后 + 研 ling speed
                # Ref: ≈ 1:16
                GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),
            ),
            # ── 并行段：中后期科技 + 量产同步推进 ────────────────────────────
            BuildOrder(
                # 霸主自动补供应（防止卡供应导致农民 / 蜂后出不来）
                AutoOverLord(),
                # 蜂后 ×2：BS 完工即训，两矿各一只注卵
                # Ref: ≈ 2:07（BS 完工约 2:05，建造 28s，出生 ≈ 2:30）
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 2),
                ),
                # ling speed（Metabolic Boost）：BS 完工后研
                # Ref: 开始 ≈ 2:09，完成 ≈ 3:25；小虫守家必须提速
                # Step gate：BS 没 ready 前 Tech 无 builders，不 reserve，但加
                # gate 后更精确触发 timing，和 mutalisk_harass 保持一致。
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    Tech(UpgradeId.ZERGLINGMOVEMENTSPEED),
                ),
                # 小虫 ×6：防守用，提速后可应对早期骚扰（bio rush / hellion）
                # 不需要太多，宏观流不靠小虫打仗
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    ZergUnit(UnitTypeId.ZERGLING, 6),
                ),
                # 三矿 BH：≈ 2:30，三矿稳定后才有 60 农民经济基础
                # Ref: Grim / Serral —— 快三矿是 ZvT Macro 核心
                # Step gate：二矿建筑存在后再触发三矿，防止 Expand(3) 从
                # 第 1 帧起就抢 worker + reserve 300 矿，与 SequentialList 里的
                # Expand(2) 产生 worker 竞争，导致二矿/母池延误。
                # 参考 mutalisk_harass.py:Step(UnitExists(HATCHERY,2), Expand(3))
                Step(
                    UnitExists(UnitTypeId.HATCHERY, 2),
                    Expand(3),
                ),
                # 第二、三气矿：三矿落地后补气
                # Lair + Glial + BV 升级 + 蟑螂量产同时吃气，2 气不够
                # Ref: ≈ 3:41-3:49 开第 2-3 气
                Step(
                    UnitExists(UnitTypeId.HATCHERY, 3),
                    BuildGas(3),
                ),
                # BR 蟑螂窝：二矿孵化场存在后即可开建
                # Ref: ≈ 3:47（Grim ZvT）
                Step(
                    UnitExists(UnitTypeId.HATCHERY, 2),
                    GridBuilding(UnitTypeId.ROACHWARREN, 1),
                ),
                # BV 进化腔 ×2：和蟑螂窝同步开建，升级要时间，早开早完
                # Ref: ≈ 3:48，两个进化腔并行研 +1 攻 + +1 防
                Step(
                    UnitExists(UnitTypeId.HATCHERY, 2),
                    GridBuilding(UnitTypeId.EVOLUTIONCHAMBER, 2),
                ),
                # Lair 升级：BS 完工后即可升，建在自然矿 BH
                # Ref: ≈ 4:05；Glial 前置是 Lair，不升 Lair 蟑螂速研不了
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    MorphLair(),
                ),
                # +1 导弹武器（Zerg Missile Weapons Lv1）：BV 完工后立研
                # Ref: ≈ 4:29（Grim ZvT）—— 蟑螂 1/1 出门是 7-9 min 的目标
                Step(
                    UnitReady(UnitTypeId.EVOLUTIONCHAMBER, 1),
                    Tech(UpgradeId.ZERGMISSILEWEAPONSLEVEL1),
                ),
                # +1 地面护甲（Zerg Ground Armors Lv1）：攻防同步
                # Ref: ≈ 4:29（第二进化腔研防）
                Step(
                    UnitReady(UnitTypeId.EVOLUTIONCHAMBER, 1),
                    Tech(UpgradeId.ZERGGROUNDARMORSLEVEL1),
                ),
                # Glial Reconstitution（蟑螂速）：ZvT 核心升级，Lair 后最优先
                # 没蟑螂速的蟑螂走不过 bio 的 stim；Ref: ≈ 4:30 开始研，5:30 完成
                Step(
                    UnitReady(UnitTypeId.LAIR, 1),
                    Tech(UpgradeId.GLIALRECONSTITUTION),
                ),
                # 蟑螂量产：BR 完工后持续出 → cap 满 (80 蟑螂 = 160 supply)
                # 2026-05-28: 28 → 80 修「钱多不出兵」bug。
                # 原 ZergUnit(ROACH, 28) 设计是 7-9 min 出门一波 = Serral ZvT
                # 标准编制；问题：达到 28 后停训，supply 卡 127/200(余 70 浪费)
                # 矿/气持续攒到 20000+ 没消化(实测 macro_hatch__retreat
                # game_20260528_030401_8a7af6 t=536s 后 ROACH=28 一直不变)。
                # 80 上限：60 农民 + 80 蟑螂 + 4 queen + OL 已超 supply cap,
                # 实际 sharpy 自然约束在 200 内停。EmitOpeningCompleteAct 仍
                # 在 hatch≥3 + roach≥10 触发(toast 推 doctrine);
                # 玩家不 confirm 时 plan 兜底继续刷兵不摆烂。
                Step(
                    UnitReady(UnitTypeId.ROACHWARREN, 1),
                    ZergUnit(UnitTypeId.ROACH, 80),
                ),
                # 小狗持续生产 30:零散 LARVA 出狗,蟑螂吃气,小狗只吃矿,
                # 互不冲突。原 6 是开局守家用,中后期多出小狗补战线 + 探路。
                # 2026-05-28: 6 → 30 配合 ROACH 80 全消化 supply。
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    ZergUnit(UnitTypeId.ZERGLING, 30),
                ),
                # 农民暴到 60：宏观流经济目标，60 农民 = 三矿满采
                # Macro Hatch 额外孵化场节点（Ref: Serral ~6 min，主基地补建）
                # 由 AutoOverLord + InjectLarva + 这条 act 驱动
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 60),
                # 蜂后补至 4 只：三矿后增加注卵频次，确保三矿幼虫不浪费
                ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 4),
            ),
            # ── 家事段：每帧执行的持续性任务 ──────────────────────────────────
            SequentialList(
                InjectLarva(),
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                # DistributeWorkers / SpeedMining / Gather 必须排在 PlanZoneAttack
                # 之前：PlanZoneAttack.execute() 正常对局每帧 return False（sharpy
                # 源码 zone_attack.py:123 "Blocks!"），SequentialList 一旦遇 False
                # 就停 —— 排在它后面的 act 整局不执行。
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # 宏观流（7-9 min 出门）：start_attack_power=20 确保积攒够兵力才出门
                # 比 ling_bane(8) 高很多，避免单独几只蟑螂送死
                # 2026-05-27: 加 attack_on_advantage=False — 同 terran mech/bc_late/
                # bio_max A2 修复模式。zerg macro 三矿后期经济强但军队中等,sharpy
                # 默认 logic 在 "经济优势 + 军队劣势" 时龟防累积不出门 (实测 macro_hatch
                # vs VeryEasy 兵 28 蟑螂到位但 attack_moveout 卡 20 min)。
                _make_macro_attack(),
                PlanFinishEnemy(),
            ),
        )
