"""vibecraft 两矿凤凰（Phoenix 2-base opener）plan。

战术核心
========
双星门持续 chrono 凤凰，以骚扰 + 吊资源打乱对方节奏：
  - PvZ：飞虫（Mutalisk）出现前先下手，吊 Overlord + 骚扰 drone line
  - PvT：吊 SCV + 骚扰 bio 集结，配合地面 Stalker 守家

关键路径
========
1. 快速双矿（~1:24 NX）
2. 第 1 VS（BY ready 后建，~2:19）→ 凤凰开始产出
3. 1 号 VS 完成后立刻建第 2 VS（双星门拉产能）
4. 5 凤凰 ready → 出门骚扰（PhoenixSquadAct 永不归队）
5. VR（6 凤凰后建，Observer 反隐）
6. 8 凤凰后三矿延续

注：**无 Warpgate research**（折跃研究后置/跳过，优先凤凰产能，不抢 gas/chrono）
   无 VC / Blink（与 Phoenix build 不符，白烧约 300 gas）
   无 Warp Prism（两矿凤凰阶段用不上）

Build 节奏（参考 spawningtool.com 126982，HuShang Double Stargate Phoenix PvZ）
=============================================================================
  1:24  NX（双矿）
  1:34  BY（CyberneticsCore）
  1:43  BA x2（第 2 气）
  2:01  Adept @chrono（保家侦察）
  2:19  VS x1（**第一星门，先建 1 个**）
  3:31  Phoenix 第 1 个
  3:47  BA x3（三气）
  3:59  VS x2（1 号星门 ready 就连下，双星门满产能）
  ~4:30 三气满采
  ~4:30 5 凤凰 ready → 出门骚扰（PhoenixSquadAct 整局锁定，不归队）
  ~5:00 NX（三矿，等 8 凤凰后）
  ~5:30 VR（Robo，Observer 反隐）

2026-05-30: 切换到 PhoenixSquadAct，抱团 + 永动 kite + 智能 lift。
"""

from __future__ import annotations

from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.protoss import (
    AutoPylon,
    ChronoUnit,
    ProtossUnit,
    RestorePower,
)
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanCancelBuilding,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)

from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct
from vibecraft.bot.auto_combat.protoss.plans.phoenix_squad_act import PhoenixSquadAct


class Phoenix2Base(KnowledgeBot):  # type: ignore[misc]
    """两矿凤凰（Double Stargate Phoenix）— 双星门 chrono 凤凰骚扰，PvZ/PvT。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Phoenix 2-base")
        # 2026-05-28 Issue 4:AttackGate 处理 retreat/defend intent + latch
        from vibecraft.bot.auto_combat.intent_gate import AttackGate

        self._attack_gate = AttackGate(self._ready_to_pressure)

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 开局完成（_ready_to_pressure 首次满足）→ 通知 Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._ready_to_pressure),
            # ---------- chrono ----------
            # 探机 chrono 直到 BY 出现
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.CYBERNETICSCORE, 1),
                skip_until=UnitExists(UnitTypeId.ASSIMILATOR, 1),
            ),
            # 凤凰 chrono：VS 完成后持续 chrono（核心骚扰单位产速）
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                ChronoUnit(UnitTypeId.PHOENIX, UnitTypeId.STARGATE),
            ),
            # ---------- 早期主线 ----------
            SequentialList(
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14),
                GridBuilding(UnitTypeId.PYLON, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 15),
                GridBuilding(UnitTypeId.GATEWAY, 1),
                BuildGas(1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 19),
            ),
            # ---------- 快速双矿（~1:24）+ BY ----------
            Step(UnitReady(UnitTypeId.GATEWAY, 1), Expand(2)),
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            # ---------- 双矿气矿（凤凰吃气多）----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(2)),
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(3)),
            # ---------- 第 1 VS（BY ready 后建，~2:19）----------
            # 标准 build：先建 1 VS，等凤凰产出后再加第 2 VS
            # 不同时建 2 VS（矿资源竞争导致凤凰延后）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.STARGATE, 1)),
            # ---------- 第 2 VS（1 号星门一好就连下，撑满双星门产能）----------
            # 2026-05-20：原触发 UnitExists(PHOENIX,4)（~4:35 才下 2 号星门）→ 5:30
            # 只 6 凤凰。改 UnitReady(STARGATE,1)：1 号星门好就连下 2 号（~64s 提前）。
            # 实测 3 跑：stargate_2 稳定达成、phoenix_eight 6→7、首批凤凰 phoenix_four
            # 仍稳过 —— 双星门拉产能有效，"过早挤矿"代价可接受。
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                GridBuilding(UnitTypeId.STARGATE, 2),
            ),
            # ---------- 三气（第 2 VS 建造期间补）----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(4)),
            # ---------- VR（Robotics，6 凤凰后建 Observer 反隐）----------
            # 标准 build：5:30 才建 VR；早期 VR 挤占凤凰矿资源
            Step(
                UnitExists(UnitTypeId.PHOENIX, 6),
                GridBuilding(UnitTypeId.ROBOTICSFACILITY, 1),
            ),
            # ---------- 三矿延续（8 凤凰后开，约 ~5:00）----------
            # 过早三矿（二矿存在就开）会在凤凰集结前守家压力过大
            Step(UnitExists(UnitTypeId.PHOENIX, 8), Expand(3)),
            # ---------- 三气（三矿后补）----------
            Step(UnitExists(UnitTypeId.NEXUS, 3), BuildGas(5)),
            # ---------- 四矿 + 六气（2026-07-19 用户「靠运营提升胜率」）----------
            # 凤凰控场杀农民 = 压制对方经济、保自己扩张安全 → 我方多开矿暴农、把经济
            # 优势滚起来,后期用经济优势的重兵碾过去。四矿 gate 在 12 凤凰(控场成型、
            # 扩张安全)。六气支撑后期不朽/重兵的气。
            Step(UnitExists(UnitTypeId.PHOENIX, 12), Expand(4)),
            Step(UnitExists(UnitTypeId.NEXUS, 4), BuildGas(6)),
            # ---------- 单位训练 ----------
            # Observer（VR 完成立刻，反隐必须）
            Step(
                UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                ProtossUnit(UnitTypeId.OBSERVER, 2),
            ),
            # 凤凰主力（双星门 chrono，target 12）
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                ProtossUnit(UnitTypeId.PHOENIX, 12),
            ),
            # Stalker 两阶段（2026-05-30 实战调）：
            #   早期只出 1 追猎保家，避免追猎抢凤凰的气矿（追猎 50 气 / 凤凰 100 气）
            #   等 6 凤凰 ready 后补到 4 追猎（凤凰主力到位再撑地面）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ProtossUnit(UnitTypeId.STALKER, 1, priority=True),
            ),
            Step(
                UnitExists(UnitTypeId.PHOENIX, 6),
                ProtossUnit(UnitTypeId.STALKER, 4, priority=True),
            ),
            # ---------- 叉子(Zealot)：吃富余的矿 + 补地面军(2026-07-19 用户)----------
            # 凤凰是纯气兵(150 矿/100 气),打起来气吃紧、矿囤到 600-870(真机实测)。
            # 叉子 100 矿 / 0 气,soak 富余矿、不抢凤凰的气；凤凰是空军守不了地,叉子撑地面
            # 正面 + 守家。晚点上(8 凤凰后)不耽误凤凰开局。**不 priority**:只在矿富余
            # (凤凰/探机/追猎都满足后)才出,绝不挤占凤凰产能。
            # 议会 + 冲锋:裸叉子会被风筝死,冲锋(一次性 100/100 气,气够)才让叉子能贴上去。
            Step(
                UnitExists(UnitTypeId.PHOENIX, 8),
                GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1),
            ),
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                Tech(UpgradeId.CHARGE),
            ),
            Step(
                UnitExists(UnitTypeId.PHOENIX, 8),
                ProtossUnit(UnitTypeId.ZEALOT, 14),
            ),
            # ---------- 后期重兵:不朽(Immortal)(2026-07-19 用户「后期加点别的兵种」)----------
            # 凤凰控场 + 运营滚出经济优势后,后期要有能收尾的重兵。不朽从现有重工(VR)出、
            # 强 vs 重甲(坦克/蟑螂/不朽),给凤凰+叉子+追猎的军队一个正面拳头。gate 在三矿
            # (经济起来、气够)。非 priority:不抢凤凰早期的气,靠多矿富余的气产。
            Step(
                UnitExists(UnitTypeId.NEXUS, 3),
                ProtossUnit(UnitTypeId.IMMORTAL, 6),
            ),
            # ---------- 经济 ----------
            AutoPylon(),
            # 农民 60→76:四矿运营,多基地满采(~19/矿),经济优势是靠运营赢的根本
            ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 76),
            # ---------- 战术 / 维护 / 攻击触发 ----------
            SequentialList(
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                PlanZoneDefense(),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                # release_after=600(2026-07-19 用户「后期加兵种收尾/靠运营提升」):
                # 凤凰早-中期锁定骚扰(控场杀农民、保自己扩张),到 ~10 分(game-time 600s)
                # 释放归队,参加后期决战。根因:原 release_after=None 让凤凰整局锁死骚扰、
                # 决战时地面军(叉子+追猎+不朽)单打 VeryHard 全军 → 12局有5局"发展了却正面
                # 输"(胜局凤凰24-45参战 vs 败局12-22);凤凰归队能吊关键单位+杀空军+加 DPS。
                # wave_threshold=5:第一波攒 5 凤凰才出门骚扰。切换 PhoenixSquadAct(抱团+永动
                # kite+智能 lift)。玩家仍可×卡片提前归队。
                # recall_threshold=6(2026-07-19 用户):不光按时间(600s),敌方大部队来攻
                # (我方基地 30 格内 ≥6 敌方战斗单位)也召回凤凰回防/参战。
                PhoenixSquadAct(
                    release_after=600.0,
                    wave_threshold=5,
                    harass_duration=300.0,
                    recall_threshold=6,
                ),
                PlanZoneGather(),
                # 凤凰主力集结出门 —— PhoenixHarassAct release_after=None，
                # ZoneAttack 永远不会拿到凤凰（Reserved 全程），由地面 Stalker 撑场。
                # 玩家强制 attack 仍可随时绕过。
                Step(
                    self._attack_gate,
                    PlanZoneAttack(8),
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_pressure(ai: Any) -> bool:
        """两矿凤凰出门骚扰：至少 1 VS ready + 5 凤凰 ready。

        5 凤凰即出门骚扰 —— 双星门产速下约 4:30 可达到，比原来 8 凤凰提早 ~1min，
        骚扰窗口更早，对手农民线被骚扰时间更长。
        """
        # 至少 1 VS ready
        stargate_ready = ai.structures(UnitTypeId.STARGATE).ready.exists
        if not stargate_ready:
            return False
        # 5 凤凰 ready
        phoenix_count = ai.units(UnitTypeId.PHOENIX).ready.amount
        return bool(phoenix_count >= 5)
