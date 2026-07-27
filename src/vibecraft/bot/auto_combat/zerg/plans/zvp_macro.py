"""虫族 ZvP 运营流 plan — Hatch First 经济宏观，对标 spawningtool ZvP Standard Hatch First。

与 macro_hatch（ZvT 向）的关键区别 = **针对神族**：
  - **孢子匍匐者（SporeCrawler）防空**：主矿 + 二矿各 2 个，~3:30 落地 —— 神族 ZvP 标配，
    防先知（Oracle）骚扰农民 / 暗使（DT）偷家 / 凤凰，并提供反隐探测。这是 ZvP 的命根子。
  - **快三矿 + 多蜂后**：hatch-first 经济，~2:43 三矿，蜂后补到 5（注卵 + 防空双职）。
  - **蟑螂 + 刺蛇运营**：蟑螂前排扛 + 刺蛇后排射，对神族追猎/不朽/巨像稳健过渡。

开局路线（spawningtool ZvP Standard Hatch First, build/199494）：
    14 BE（气，抽农民 trick）/ 15 OL / 16 BH（二矿，hatch first）/ 17 BS（母池）
    18 BE（二气）/ 21 蜂后×2 + 提速 / 31 蜂后（二矿）/ 34 BH（三矿）
    47 孢子×4（主+二矿，防 Oracle/DT）/ 60 BR 蟑螂窝 / 71 三气 / 75 Lair → 暴农 → 运营

建筑 hotkey（SC2 Standard Layout，虫族）：
    BH=孵化场 BE=气矿 BS=母池 BR=蟑螂窝 BV=进化腔 BA=孢子匍匐者(防空)
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
    DefensePosition,
    DefensiveBuilding,
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
    """ZvP 运营主力 attack：start_attack_power=22 + 关 attack_on_advantage。

    同 macro_hatch 修复模式（经济强/军队中等时 sharpy 默认龟防不出门）。ZvP 出门
    阈值略高于 ZvT（22 vs 20）：神族单位质量高，蟑刺需更多兵力才敢推。
    """
    attack = PlanZoneAttack(start_attack_power=22)
    attack.attack_on_advantage = False
    return attack


class ZvpMacro(KnowledgeBot):  # type: ignore[misc]
    """ZvP 运营流：16 BH hatch-first → 快三矿 → 孢子防空 → 蟑刺运营。

    针对神族：孢子匍匐者防 Oracle/DT 骚扰是核心，蟑螂+刺蛇稳健过渡中后期。
    """

    def __init__(self) -> None:
        super().__init__("VibeCraft ZvpMacro")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：三矿（townhalls 含 LAIR/HIVE）≥3 且蟑螂 ≥10 → 通知 Director 切持续 doctrine。

        同 macro_hatch：用 townhalls（合并 HATCH+LAIR+HIVE）而非 HATCHERY 计数，
        防主基升 Lair 后 hatch<3 永不触发（#530 同类 bug）。
        """
        try:
            townhalls = ai.townhalls.amount
            roach = ai.units(UnitTypeId.ROACH).amount
        except Exception:
            return False
        return bool(townhalls >= 3 and roach >= 10)

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            EmitOpeningCompleteAct(self._opening_done),
            # ── 顺序段：hatch-first 前期建筑顺序锁定 ──────────────────────────
            # spawningtool ZvP Standard Hatch First (build/199494)
            # 16 农 → 16 BH（二矿，先扩）→ 17 BS（母池）→ 第一气
            SequentialList(
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 16),
                # 16 BH：二矿 hatch-first（≈0:45），ZvP 经济开局核心
                Expand(2),
                # 17 BS：母池，BS 后才能蜂后 + ling speed + 孢子（孢子只需母池）
                GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),
                # 18 BE：第一气（Lair / 提速 / 蟑螂都吃气）
                BuildGas(1),
            ),
            # ── 并行段：中后期科技 + 量产 ────────────────────────────────────
            BuildOrder(
                AutoOverLord(),
                # 蜂后 ×2：BS 完工即训（≈1:57），两矿注卵 —— 放在所有结构投资**之前**，
                # 保证蜂后/注卵先于孢子/三矿拿矿，否则蜂后被结构挤到 5min 后(经济崩，#550 实测过)。
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 2),
                ),
                # 早期暴农到 20：hatch-first 经济命脉，但降一档(原 24)——vs VeryHard 实测 24 会把
                # larva/矿全喂农民、蜂后拖到 321s、军队远晚于敌时机被压死(F45/F46/F47/I27/D31)。
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 20),
                # ling speed：BS 后研，守家小虫提速应对 adept/zealot 骚扰
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    Tech(UpgradeId.ZERGLINGMOVEMENTSPEED),
                ),
                # 守家小虫 ×8：ZvP 早期防 adept 闪现 / zealot
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    ZergUnit(UnitTypeId.ZERGLING, 8),
                ),
                # 三矿 BH：≈2:43（二矿建筑存在后触发，避免 worker 竞争）
                Step(
                    UnitExists(UnitTypeId.HATCHERY, 2),
                    Expand(3),
                ),
                # ★ ZvP 命根子：孢子匍匐者防空（主矿 1 + 二矿 1）≈3:30
                # 防 Oracle 骚扰农民 / DT 偷家 / 凤凰 + 反隐探测。**gate 在双蜂后已出之后**
                # (UnitExists QUEEN 2)：保证蜂后/注卵先拿矿，孢子不抢早期经济(#550 实测：孢子早于
                # 蜂后会把矿吃光、蜂后拖到 5min)。每矿 1 个够探测 + 基础防空，省矿。
                Step(
                    UnitExists(UnitTypeId.QUEEN, 2),
                    DefensiveBuilding(
                        UnitTypeId.SPORECRAWLER, DefensePosition.BehindMineralLineCenter, 0, 1
                    ),
                ),
                Step(
                    UnitExists(UnitTypeId.QUEEN, 2),
                    DefensiveBuilding(
                        UnitTypeId.SPORECRAWLER, DefensePosition.BehindMineralLineCenter, 1, 1
                    ),
                ),
                # 三气：三矿落地后补（Lair + 升级 + 蟑螂吃气）
                Step(
                    UnitExists(UnitTypeId.HATCHERY, 3),
                    BuildGas(3),
                ),
                # BR 蟑螂窝：二矿存在后开建（≈3:40）
                Step(
                    UnitExists(UnitTypeId.HATCHERY, 2),
                    GridBuilding(UnitTypeId.ROACHWARREN, 1),
                ),
                # BV 进化腔 ×2：攻防升级
                Step(
                    UnitExists(UnitTypeId.HATCHERY, 2),
                    GridBuilding(UnitTypeId.EVOLUTIONCHAMBER, 2),
                ),
                # Lair：BS 后升，Glial / 刺蛇巢前置
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    MorphLair(),
                ),
                # +1 导弹武器 / +1 地甲：BV 完工后研
                Step(
                    UnitReady(UnitTypeId.EVOLUTIONCHAMBER, 1),
                    Tech(UpgradeId.ZERGMISSILEWEAPONSLEVEL1),
                ),
                Step(
                    UnitReady(UnitTypeId.EVOLUTIONCHAMBER, 1),
                    Tech(UpgradeId.ZERGGROUNDARMORSLEVEL1),
                ),
                # Glial（蟑螂速）：Lair 后研，蟑螂走位过追猎/不朽
                Step(
                    UnitReady(UnitTypeId.LAIR, 1),
                    Tech(UpgradeId.GLIALRECONSTITUTION),
                ),
                # 蟑螂量产（BR 完工后持续，cap 80 = sharpy 自然约束在 supply 内）
                Step(
                    UnitReady(UnitTypeId.ROACHWARREN, 1),
                    ZergUnit(UnitTypeId.ROACH, 80),
                ),
                # 小狗持续生产 24：补战线 + 探路 + 吃零散 LARVA（只吃矿，不与蟑螂抢气）
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    ZergUnit(UnitTypeId.ZERGLING, 24),
                ),
                # 暴农到 48(原 66)：3 矿 base×16 饱和线，超了就是拿 larva 挤军队(F47/D31)——
                # 降到 48 让多出的 larva/矿更早转蟑螂,军队在 VeryHard 时机前成型。
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 48),
                # 蜂后补到 5：ZvP 多蜂后（注卵 + 防空兼职，神族空中骚扰多）
                ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 5),
            ),
            # ── 家事段：每帧持续任务（顺序同 macro_hatch）──────────────────────
            SequentialList(
                InjectLarva(),
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                _make_macro_attack(),
                PlanFinishEnemy(),
            ),
        )
