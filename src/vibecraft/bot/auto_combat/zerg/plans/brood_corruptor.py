"""虫族巢虫领主腐化者后期 plan。

后期巢虫领主 + 腐化者空军组合：BL 出小虫消耗对方，腐化者护卫 + 对空。
搭配感染虫 / 毒蛇控场，慢推胜利。

科技链：BS(母池) → Lair → VI(感染坑) → Hive → MorphGreaterSpire(大刺翼)
建筑：BH(孵化场)×4, BS(母池), VI(感染坑), Spire→GreaterSpire
升级：飞行攻防 1-3

设计参考：strategies/zerg/brood_corruptor.yaml
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
from sharpy.plans.acts.zerg import (
    AutoOverLord,
    MorphBroodLord,
    MorphGreaterSpire,
    MorphHive,
    MorphLair,
    ZergUnit,
)
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


def _make_attack_act() -> PlanZoneAttack:
    """主力 attack act:start_attack_power=40 + 关 attack_on_advantage。

    2026-05-27: 同 macro_hatch/terran mech-bc_late-bio_max 修复模式。
    zerg 后期 doctrine 经济强但军队中等,sharpy 默认 attack_on_advantage=True
    在"经济优势 + 军队劣势"时龟防累积不出门(实测 macro_hatch attack_moveout
    1179s → 426s 验证)。
    brood_corruptor: 早期 zergling/agriculture units 即触发 start=22 太早出门
    (实测 VeryEasy attack_moveout 174s 完全没等 corruptor/BL)。调到 40 让
    8 腐化者×2.5 + 4 BL×4 = 36 不够,凑 8 腐化者 + 6 BL = 44 才推。
    """
    attack = PlanZoneAttack(start_attack_power=40)
    attack.attack_on_advantage = False
    return attack


class BroodCorruptor(KnowledgeBot):  # type: ignore[misc]
    """巢虫领主腐化者后期：BL + 腐化者 + 感染虫控场。

    顺序段（sequential boot）只保留必须串行的科技链地基：
      36 农 → BS → Lair → VI(感染坑) → Hive → Spire → GreaterSpire
    其余扩张/出兵/升级全部进并行段（BuildOrder），避免 SequentialList 卡顿。
    """

    def __init__(self) -> None:
        super().__init__("VibeCraft BroodCorruptor")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：大刺翼完工 + 8 腐化者 ready + 4 BL ready → 通知 Director 切持续。

        GreaterSpire ready + CORRUPTOR ≥ 8 + BROODLORD ≥ 4：
        后期空军框架到位，Director 推荐切持续攻推。
        """
        try:
            greater_spire_ready = ai.structures(UnitTypeId.GREATERSPIRE).ready.exists
            corruptor_count = ai.units(UnitTypeId.CORRUPTOR).amount
            brood_lord_count = ai.units(UnitTypeId.BROODLORD).amount
        except Exception:
            return False
        return bool(greater_spire_ready and corruptor_count >= 8 and brood_lord_count >= 4)

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # ── 后期科技树 build order ────────────────────────────────────────
            #
            # 顺序段只保留科技链 "地基" 步骤（每步是后续所有步骤的唯一前置）：
            #   36农 → BS → MorphLair → VI(感染坑) → MorphHive → Spire → MorphGreaterSpire
            # 其余 Expand / BuildGas / 出兵 / 升级全进并行段，避免串行阻塞。
            #
            BuildOrder(
                # 36 农先稳经济
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 36),
                # BS(母池)：Lair 前置，早建保防守
                GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),
                # Lair：BS 完成后立即升，VI / Hive 的唯一前置。
                # MorphLair 内部等 HATCHERY，用 UnitReady(BS) gate 精确触发。
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    MorphLair(),
                ),
                # VI(感染坑)：Hive 前置（需 Lair 完成）
                Step(
                    UnitReady(UnitTypeId.LAIR, 1),
                    GridBuilding(UnitTypeId.INFESTATIONPIT, 1),
                ),
                # Hive：Lair 形态升级（需 VI 感染坑完成）—— 必须用 MorphHive
                Step(
                    UnitReady(UnitTypeId.INFESTATIONPIT, 1),
                    MorphHive(),
                ),
                # Spire(刺翼)：BL/腐化者前置（也需 Lair；Hive 完成后立即开建）
                Step(
                    UnitReady(UnitTypeId.LAIR, 1),
                    GridBuilding(UnitTypeId.SPIRE, 1),
                ),
                # MorphGreaterSpire(大刺翼)：BL 唯一前置（需 Hive + Spire ready）
                Step(
                    UnitReady(UnitTypeId.SPIRE, 1),
                    MorphGreaterSpire(),
                ),
                # ── 并行段：科技链到位后所有事同步推进 ──────────────────────
                BuildOrder(
                    AutoOverLord(),
                    # 四矿后期经济 + 气矿
                    Expand(4),
                    BuildGas(4),
                    # 44 农上限（四矿经济）
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 44),
                    # 持续升级飞行攻防 1/2/3（BL + 腐化者都是飞行单位）
                    # 单个 Spire（Greater Spire 自动升级）能并行研 weapons + armor。
                    Tech(UpgradeId.ZERGFLYERWEAPONSLEVEL1),
                    Tech(UpgradeId.ZERGFLYERWEAPONSLEVEL2),
                    Tech(UpgradeId.ZERGFLYERWEAPONSLEVEL3),
                    Tech(UpgradeId.ZERGFLYERARMORSLEVEL1),
                    Tech(UpgradeId.ZERGFLYERARMORSLEVEL2),
                    Tech(UpgradeId.ZERGFLYERARMORSLEVEL3),
                    # 先积累腐化者，再转化 BL（MorphBroodLord 需要腐化者）
                    Step(
                        UnitReady(UnitTypeId.SPIRE, 1),
                        ZergUnit(UnitTypeId.CORRUPTOR, 8),
                    ),
                    Step(
                        UnitExists(UnitTypeId.CORRUPTOR, 6),
                        MorphBroodLord(10),
                    ),
                    # 感染虫控场（VI 完成后）
                    Step(
                        UnitReady(UnitTypeId.INFESTATIONPIT, 1),
                        ZergUnit(UnitTypeId.INFESTOR, 4),
                    ),
                    # 毒蛇（Hive 科技，Viper 需 Hive）
                    Step(
                        UnitReady(UnitTypeId.HIVE, 1),
                        ZergUnit(UnitTypeId.VIPER, 3),
                    ),
                    # 女王保注射
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ZergUnit(UnitTypeId.QUEEN, 6),
                    ),
                ),
            ),
            # ── 家事 + 进攻 ─────────────────────────────────────────────────
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
                _make_attack_act(),
                PlanFinishEnemy(),
            ),
        )
