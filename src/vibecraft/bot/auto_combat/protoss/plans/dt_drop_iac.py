"""vibecraft 空投隐刀转叉球一波（Stats DT/Archon drop into IAC push）plan。

战术意图（2026-05-19 用户重新定义）
====================================
  首批 4 DT 家里折跃 → 棱镜装船运到敌方矿区 → 卸下 + 原地展开再 warp 4 DT →
  8 DT 骚扰对方矿区 → DT 合 Archon → 跟家里持续暴的 chargelot 汇合 →
  一波 attack（棱镜跟到前线展开、持续 warp 叉子增援）

vs vanilla iac_2base 关键差异
==============================
  - **多了 WarpPrism**（VR 唯一产品，不出不朽）
  - DT 用法不同：iac_2base 全 DT 在家 warp + 立刻合 archon；
    本路线 DT 全员去敌方矿区 warp-in 骚扰，活下来再合 Archon
  - 出门 timing 晚 ~30s（要等 DT 撤回 + archon 合）

vs Stats 原版 (spawningtool /68902)
====================================
  - Stats 原版有不朽：4:04/4:51 出 2 不朽 + Archon。本变体**完全不出不朽**
  - Stats 原版 DT 分两波（4 + 4）按 timing 出。本变体 DT 同样 2 波但全去骚扰
  - 出门 timing ~8:00（同 Stats）

Build 节奏（Stats spawningtool 完整）
=====================================
  Supply  Time   Action
  ------------------------------------------------------------
  13      ~0:18  Pylon
  14      ~0:30  Gateway #1
  16      ~0:45  Assimilator #1
  18      ~1:25  Natural Nexus (二矿)
  20      ~1:35  CyberneticsCore (BY)
  21      ~1:45  Pylon #2
  22      ~2:05  Assimilator #2
  24      ~2:10  Warpgate research
  31      ~2:38  ★ Twilight Council  (VC)
  34      ~3:05  ★ Robotics Facility (VR)
  36      ~3:14  ★★ Dark Shrine     (VD)  -- DT 前置
  51      ~4:38  ★★ Dark Templar ×4  -- 首波 DT
  60      ~5:26  ★★ Dark Templar ×4  -- 第 2 批 DT
  68      ~5:29  ★★ Archon ×2 (DT 合)
  —       ~5:00  ★ 2×Forge → 攻防并行升级（一攻一防 → 2/2 → 3/3）
  —       ~5:38  Charge research
  —       ~3:14  Gateway 补到 4（DT 两波折跃产能；骚扰期不超过 4）
  —       8 DT 后 Gateway 补过 4（→6，不抢 DT warp 的矿）
  —       后期   满运营 / 钱多 → 动态补 BG 到 10
  ~140    ~8:00  ★ 出门 attack（满 chargelot + 6 Archon，带一攻一防）

注意：
  - 不用 7 BG（vanilla IAC 的暴产能）,因为 archon 已经顶住单位伤害,产能 4 BG 够用
  - 不研 PsiStorm（要 TA + HT，~8:00 timing 来不及,且 archon 已经 splash）
"""

from __future__ import annotations

from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step, StepBuildGas
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.protoss import (
    AutoPylon,
    ChronoTech,
    ChronoUnit,
    ProtossUnit,
    RestorePower,
)
from sharpy.plans.require import All, TechReady, Time, UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanCancelBuilding,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)

from vibecraft.bot.auto_combat.protoss.plans.chargelot_archon_producer import (
    ChargelotArchonProducer,
)
from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct
from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import PrismWarpDropAct
from vibecraft.bot.auto_combat.protoss.plans.warp_dt_at_prism import WarpDTAtPrism
from vibecraft.bot.auto_combat.protoss.plans.warp_zealot_at_prism import WarpZealotAtPrism


class DtDropIac(KnowledgeBot):  # type: ignore[misc]
    """空投隐刀转叉球一波 — Stats DT/Archon drop into IAC push。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft DT-Drop IAC")
        # 2026-05-28 Issue 4:AttackGate 处理 retreat/defend intent + latch
        from vibecraft.bot.auto_combat.intent_gate import AttackGate

        self._attack_gate = AttackGate(self._iac_ready_to_pressure)

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 开局完成（_iac_ready_to_pressure 首次满足）→ 通知 Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._iac_ready_to_pressure),
            # ---------- chrono（永久后台）----------
            # 农民 chrono：早期持续 chrono PROBE,到 ASSIMILATOR 起就停
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.PROBE, 44, include_pending=True),
                skip_until=UnitExists(UnitTypeId.ASSIMILATOR, 1),
            ),
            # 折跃 chrono：BY 出现后全力 chrono 折跃,99% 停
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ChronoTech(AbilityId.RESEARCH_WARPGATE, UnitTypeId.CYBERNETICSCORE),
                skip_until=UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
            ),
            # 2026-05-19 修正：VR 不出不朽,只出 Warp Prism（用户要求）
            # Warp Prism chrono：VR 一好就 chrono Warp Prism，越快出越好（飞前线开棱镜空投）
            Step(
                None,
                ChronoUnit(UnitTypeId.WARPPRISM, UnitTypeId.ROBOTICSFACILITY),
                skip=UnitExists(UnitTypeId.WARPPRISM, 1, include_pending=True),
            ),
            # DT chrono：VD 一好后 chrono DT 出门,够 8 个停
            Step(
                UnitReady(UnitTypeId.DARKSHRINE, 1),
                ChronoUnit(UnitTypeId.DARKTEMPLAR, UnitTypeId.GATEWAY),
                skip=UnitExists(UnitTypeId.DARKTEMPLAR, 8, include_pending=True),
            ),
            # ---------- 早期 critical path ----------
            SequentialList(
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 13),
                GridBuilding(UnitTypeId.PYLON, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14),
                GridBuilding(UnitTypeId.GATEWAY, 1),
                BuildGas(1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 16),
            ),
            # ---------- BG 一好的并行触发（NX + BY）----------
            Step(UnitReady(UnitTypeId.GATEWAY, 1), Expand(2)),
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            # 2026-05-20 用户修正:加三矿。原来只 Expand(2),整局两矿,撑不住
            # 8:00 chargelot+archon 一波的产能。5:00 game-time 开三矿(DT 骚扰期间
            # 趁机扩,Expand 内部判断已有 NEXUS 数,够了不会重复开)。
            Step(Time(60 * 5), Expand(3)),
            # ---------- 第二气矿 ----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(2)),
            # ---------- BY 一好：研折跃 + 起 VC（Stats 2:38）----------
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), Tech(UpgradeId.WARPGATERESEARCH)),
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1),
            ),
            # ---------- VC 一好：起 VR（3:05）+ Dark Shrine（3:14）----------
            # Stats 关键路径：VC 不研 Charge,先让 VR + VD 并行起
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                GridBuilding(UnitTypeId.ROBOTICSFACILITY, 1),
            ),
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                GridBuilding(UnitTypeId.DARKSHRINE, 1),
            ),
            # ---------- 防身（2026-05-20 用户修正：只出 1 追猎，之后直接刷影刀）----------
            # 用户："上来出一个追猎之后就直接刷影刀就行了"。资源全留给 DT 科技 +
            # WarpPrism。哨兵也砍掉(见下方 — 移到 Charge 主力期才出)。
            ProtossUnit(UnitTypeId.STALKER, 1, priority=True),
            # ---------- VR 一好：出 Warp Prism（最多 2 个，再死改补 Observer）----------
            # 2026-05-19：VR 不出不朽，只出 Warp Prism（运 DT 做空投）。
            # 2026-05-21 用户修正：棱镜最多补 1 个 —— 累计造过 2 个（原版 + 替补）
            # 后不再补棱镜，改补 Observer（侦察 + 反隐，比一直扔棱镜划算）。
            Step(
                lambda ai: (
                    self._prism_quota_left(ai)
                    and bool(ai.structures(UnitTypeId.ROBOTICSFACILITY).ready)
                ),
                ActUnit(UnitTypeId.WARPPRISM, UnitTypeId.ROBOTICSFACILITY, 1, priority=True),
            ),
            Step(
                lambda ai: (
                    (not self._prism_quota_left(ai))
                    and bool(ai.structures(UnitTypeId.ROBOTICSFACILITY).ready)
                ),
                ActUnit(UnitTypeId.OBSERVER, UnitTypeId.ROBOTICSFACILITY, 1),
            ),
            # ---------- VD 一好：出 8 个 DT（首批 4 家里折跃，第二批 4 在棱镜处）----------
            # 2026-05-21 用户修正：首批 4 DT 在家折跃（棱镜装船运过去），第二批 4 DT
            # 在前线 phasing 棱镜处 warp（WarpDTAtPrism）。`_dt_batch_active` 门控：
            # trained < 4 开（家里折跃首批）；4 ≤ trained < 8 只在有 phasing 棱镜时
            # 开（棱镜飞行途中关掉 → 不会在家漏 warp 第二批让它们走路过去）；
            # trained ≥ 8 关（进攻阶段把 warpgate 让给叉子增援）。
            Step(
                lambda ai: self._dt_batch_active(ai),
                ProtossUnit(UnitTypeId.DARKTEMPLAR, 8, priority=True),
            ),
            # ---------- ★ DT 合 Archon —— 整合到 ChargelotArchonProducer ----------
            # 2026-05-24 用户:"叉子刷得有点多,气多时优先刷 DT 合白球。直接放到
            # 叉球一波的 act 里"。
            # MergeArchonAtHome 跟 ProtossUnit(ZEALOT, 60) 两个 step 整合到下方
            # 单 act ChargelotArchonProducer:home DT pair → MORPH_ARCHON / 气多
            # 时 train DT / 主力 zealot 暴(cap 60)。
            # ---------- ★ 进攻期持续刷 DT 喂 archon merge（rollback 2026-05-24）----------
            # 实测 cap 16 自动刷 DT 进攻期跟 chargelot/archon merge 抢资源,
            # build_acceptance 退步:20/21 → 16/21(ground_weapon 510s,worker 38,
            # attack_moveout 早 100s 兵力不够已被推)。用户期望 BOT 自动持续
            # 合 archon 的诉求,改由 player voice "合白球" 触发(director 的
            # _exec_archon_item 智能 merge,2026-05-24 已实现)。
            # plan 自动刷 DT cap 仍是 8(原 _dt_batch_active 控制)。
            # ---------- ★ 棱镜运输空投行为（PrismWarpDropAct 二段空投）----------
            # FLY_TO_WARP_SPOT → DEPLOY_PHASING → WARP_UNITS(首批 4 DT)→
            # WAIT_WARP_COMPLETE → MORPH_TRANSPORT → LOAD_CARGO →
            # FLY_TO_FINAL → UNLOAD_FINAL → DONE
            # warp_pos="enemy_main:safe_edge": 敌方主矿沿最近地图边推到边缘留 2 格,
            #   corner spawn 距 nexus 26-40 grid,贴边远离 nexus 视野(12)+ 巡逻范围。
            #   2026-05-24:演进路径:
            #     - ramp_outside (斜坡下方,敌方主力集结点) → 棱镜立即被打死
            #     - mineral (behind_mineral 投影到边缘,距 nexus ~14 grid) → 仍在
            #       敌方巡逻范围,棱镜 ~21s 被发现打死
            #     - safe_edge (nexus 推到地图边,距 nexus 26-40 grid) → 真正贴边
            # final_drop_pos="enemy_main:production": 二段空投到敌方主矿 nexus(打农民)。
            # 2026-05-23 改用 PrismWarpDropAct(A 方式: plan 内直接 instantiate)
            PrismWarpDropAct(
                cargo_unit=UnitTypeId.DARKTEMPLAR,
                cargo_count=4,
                warp_pos="enemy_main:safe_edge",
                final_drop_pos="enemy_main:production",
                after_unload="attack_workers",
            ),
            # ---------- ★ 第二批 DT 在 phasing prism 处 warp ----------
            # 配合 PrismWarpDropAct WAIT_WARP_COMPLETE 状态（棱镜在 warp_spot 展开）。
            # 无 phasing prism 时 yield。cap：dt_trained_count ≥ 8 终止 act。
            WarpDTAtPrism(),
            # ---------- ★ 进攻阶段在前线 phasing 棱镜处 warp 叉子增援 ----------
            # 只在 macro_attack ready 后动作；骚扰阶段不介入（让 WarpDTAtPrism 独占
            # phasing 棱镜）。
            WarpZealotAtPrism(),
            # ---------- 5:00 起 2 个 Forge（一攻一防并行升级）----------
            # 2026-05-21 用户修正：补第 2 个 Forge —— 单 Forge 串行升 +1 攻 / +1 防
            # 要 ~256s，8:00 一波来不及；2 个 Forge 并行各升一项，~8:00 前一攻一防
            # 都完成。提前到 5:00（比 Charge 早 30s）给升级留够跑道；Forge 非
            # priority，不抢 priority 的 DT / chargelot 的矿。
            Step(Time(60 * 5), GridBuilding(UnitTypeId.FORGE, 2)),
            # 2026-05-20 修:Charge 加 Time(5:30) gate。原来只 `UnitReady(TWILIGHTCOUNCIL)`
            # → VC ~3:38 完成就立刻研 Charge,跟 DT 科技(VR/VD)+ WarpPrism 抢矿,拖慢
            # 棱镜骚扰 timing。Stats 标准 Charge 5:38 才研(资源先给 DT)。加 All([Time,
            # UnitReady]) 把 Charge 推到 5:30,跟 Forge 同期,不抢 DT 阶段的矿。
            Step(
                All([Time(60 * 5 + 30), UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1)]),
                Tech(UpgradeId.CHARGE),
            ),
            # Charge chrono（VC 上 chrono,加速出门 timing）
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                ChronoTech(AbilityId.RESEARCH_CHARGE, UnitTypeId.TWILIGHTCOUNCIL),
            ),
            # ---------- 攻防升级：2 个 Forge 并行，一路升到 3 攻 3 防 ----------
            # 用户要求：一攻一防（8:00 一波前完成）后**继续**升 2/2、3/3 —— 不能
            # 升完 +1/+1 就停。2 个 Forge 并行：一路升攻、一路升防；每一级 gate
            # 在「Twilight Council ready + 前一级完成」。8:00 一波 gate 只卡 +1/+1，
            # +2/+3 后台继续，给后期运营战补强。
            # 2026-05-24 用户:攻防各级都加星空加速。原本只 charge 有 chrono,
            # 攻防裸研究太慢,1/2/3 攻防完成 timing 拖后,导致出门兵力没 +1/+1。
            Step(UnitReady(UnitTypeId.FORGE, 1), Tech(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1)),
            Step(
                UnitReady(UnitTypeId.FORGE, 1),
                ChronoTech(AbilityId.FORGERESEARCH_PROTOSSGROUNDWEAPONSLEVEL1, UnitTypeId.FORGE),
            ),
            Step(UnitReady(UnitTypeId.FORGE, 2), Tech(UpgradeId.PROTOSSGROUNDARMORSLEVEL1)),
            Step(
                UnitReady(UnitTypeId.FORGE, 2),
                ChronoTech(AbilityId.FORGERESEARCH_PROTOSSGROUNDARMORLEVEL1, UnitTypeId.FORGE),
            ),
            Step(
                All(
                    [
                        UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                        TechReady(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1, 1),
                    ]
                ),
                Tech(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL2),
            ),
            Step(
                All(
                    [
                        UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                        TechReady(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1, 1),
                    ]
                ),
                ChronoTech(AbilityId.FORGERESEARCH_PROTOSSGROUNDWEAPONSLEVEL2, UnitTypeId.FORGE),
            ),
            Step(
                All(
                    [
                        UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                        TechReady(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL2, 1),
                    ]
                ),
                Tech(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL3),
            ),
            Step(
                All(
                    [
                        UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                        TechReady(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL2, 1),
                    ]
                ),
                ChronoTech(AbilityId.FORGERESEARCH_PROTOSSGROUNDWEAPONSLEVEL3, UnitTypeId.FORGE),
            ),
            Step(
                All(
                    [
                        UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                        TechReady(UpgradeId.PROTOSSGROUNDARMORSLEVEL1, 1),
                    ]
                ),
                Tech(UpgradeId.PROTOSSGROUNDARMORSLEVEL2),
            ),
            Step(
                All(
                    [
                        UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                        TechReady(UpgradeId.PROTOSSGROUNDARMORSLEVEL1, 1),
                    ]
                ),
                ChronoTech(AbilityId.FORGERESEARCH_PROTOSSGROUNDARMORLEVEL2, UnitTypeId.FORGE),
            ),
            Step(
                All(
                    [
                        UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                        TechReady(UpgradeId.PROTOSSGROUNDARMORSLEVEL2, 1),
                    ]
                ),
                Tech(UpgradeId.PROTOSSGROUNDARMORSLEVEL3),
            ),
            Step(
                All(
                    [
                        UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                        TechReady(UpgradeId.PROTOSSGROUNDARMORSLEVEL2, 1),
                    ]
                ),
                ChronoTech(AbilityId.FORGERESEARCH_PROTOSSGROUNDARMORLEVEL3, UnitTypeId.FORGE),
            ),
            # ---------- Sentry × 2（力场切阵）----------
            # 2026-05-20 用户修正:哨兵移到 Charge 主力期才出(Time 6:00),前期不出,
            # 资源全给 DT 科技。priority=False — 力场是 nice-to-have,不抢 chargelot 产能。
            Step(
                Time(60 * 6),
                ProtossUnit(UnitTypeId.SENTRY, 2),
            ),
            # ---------- ★ 主力期产能：chargelot 主力 + 气多自动合白球 ----------
            # 2026-05-24 用户:整合到一个 act。ChargelotArchonProducer 替代
            # MergeArchonAtHome + ProtossUnit(ZEALOT, 60)。每 tick:
            # - home DT(距 townhall<30) >= 2 → MORPH_ARCHON pair(merge 帧不 train)
            # - < 2 home DT + 资源够(125M + 125V) → train 1 DT 补充
            # - zealot < 60 cap + 矿够(100M) → train 1 zealot
            # - home DT 每帧 set Reserved 防 sharpy 派前线
            # 优先级:merge > train DT > train zealot。气多时 DT 优先吃 vespene,
            # 气少时全资源给 chargelot 主力。
            Step(
                lambda ai: self._army_phase_active(ai),
                ChargelotArchonProducer(),
            ),
            # ---------- 经济 ----------
            AutoPylon(),
            [
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 22),
                Step(
                    UnitExists(UnitTypeId.NEXUS, 2),
                    ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 44),
                ),
                # 2026-05-21 用户修正：三矿后 44 农民填不满 3 个矿 —— 单矿满采
                # 16 矿 + 6 气 = 22，三矿 ≈ 66。priority=True 优先填满农民（运营
                # 体系基石），66 是硬上限，填满后全部转产兵。
                Step(
                    UnitExists(UnitTypeId.NEXUS, 3),
                    ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 66, priority=True),
                ),
                # 2026-05-24 用户:DT 空投后开矿时气矿起得太慢,后期 archon+
                # chargelot+攻防升级吃 vespene。改为三矿后无条件补到 6 气矿
                # (3 个 NEXUS 各 2 气),不再 cap on vespene 阈值。
                Step(UnitExists(UnitTypeId.NEXUS, 3), StepBuildGas(6)),
            ],
            # ---------- DT 骚扰期 4 BG（DT 两波 8 个用 4 门折跃）----------
            # 2026-05-21 用户修正：DT 两波需要 4 BG 来 warp；但骚扰前 / 期间**不超过
            # 4 BG** —— 再多的 BG 建造成本会抢第一波 DT 折跃的矿、拖慢骚扰。
            # DARKSHRINE 一好就补到 4，~4:38 第一波 DT 前 morph 成 warpgate。
            Step(UnitExists(UnitTypeId.DARKSHRINE, 1), GridBuilding(UnitTypeId.GATEWAY, 4)),
            # ---------- 补 BG 过 4（DT 两波 8 个 warp 完后才补超过 4 的 BG）----------
            # 4 以上的 BG 卡 _army_phase_active —— 不抢 DT warp 的矿。
            Step(
                lambda ai: self._army_phase_active(ai),
                GridBuilding(UnitTypeId.GATEWAY, 6),
            ),
            # ---------- 动态补 BG：进入主力期 + 钱多 / 满运营 → 砸 chargelot 产能 ----------
            # 同样卡 _army_phase_active —— 骚扰期就算余钱堆积也不补 BG（否则又抢
            # DT warp 的矿）。封顶 10，GridBuilding 自带 can_afford 节流。
            Step(
                lambda ai: self._army_phase_active(ai) and self._should_macro_gateways(ai),
                GridBuilding(UnitTypeId.GATEWAY, 10),
            ),
            # ---------- 战术 / 维护 / 攻击触发 ----------
            SequentialList(
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                # PlanZoneDefense.get_defenders 会从 Idle/Moving/Fighting/Attacking
                # 各 task 抽最近的兵标 Defending → 不在 free_units → PlanZoneAttack
                # 看不见。IAC 主力 ready 后必须 skip，否则主力被持续抽走，
                # PlanZoneAttack(20) 永远 "No attacking units" → 第二波出不了门
                # → 拖局 200+ 分钟 Tie（与 4bg 同源问题，见 gate4_pressure.py）。
                Step(
                    None,
                    PlanZoneDefense(),
                    skip=lambda ai: self._iac_ready_to_pressure(ai),
                ),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # ★ IAC 大军一波：Charge + Archon 到位（或 8:30 兜底）→ 一波推。
                # 2026-05-20 修拖局 Tie：原来这条前面还串了一条 DT 骚扰用的
                # `Step(_dt_harass_ready, PlanZoneAttack(4))`。sharpy
                # PlanZoneAttack.execute() 只在「敌方已知基地全灭」(target=None) 时
                # return True，其余一律 return False（源码注释 "Blocks!"）。两个
                # PlanZoneAttack 串进同一个 SequentialList → 第一个永远 block 第二个。
                # 且 `_dt_harass_ready` 是瞬态条件（DT 死光 / 7:00 合 Archon 后
                # DT 数 <3 → 永久转 False），requirement False 时 Step 直接
                # return False → SequentialList 停 → PlanZoneAttack(20) +
                # PlanFinishEnemy 永不执行 → bot 暴到 200 人口也不出门 → 拖局
                # 200+ 分钟判 Tie。
                # 修法：DT 骚扰本就由 PrismWarpDropAct + WarpDTAtPrism 全权微操
                # （含 macro_attack / follow_army 收尾），删掉冗余且会 block 的
                # PlanZoneAttack(4)，只留主力一波。结构对齐能稳胜的
                # 4bg / dt_rush：单个 gated ZoneAttack + PlanFinishEnemy。
                # 玩家显式 attack override 透传到这条主力 trigger。
                # 2026-05-28 Issue 4:AttackGate(含 retreat/defend/hold + latch)
                Step(self._attack_gate, PlanZoneAttack(20)),  # 20 supply（Charge 叉主力 + Archon）
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _iac_ready_to_pressure(ai: Any) -> bool:
        """大军一波 timing（2026-05-19：不要求 Immortal，本路线不出不朽）.

        触发条件（任一即推）：
        - 8:30 game time 兜底 —— **无条件**，到点必推
        - 满人口（supply_used >= 190）—— 再不推就拖局
        - Charge done + 一攻一防完成 + 2 Archon ready + army_supply >= 30 —— 正常 timing

        2026-05-20 修拖局 Tie：原来 `archons < 2 → return False` 写在时间兜底
        **之前**，DT 骚扰全灭（0 残 DT → 0 Archon）时把 8:30 兜底也一起堵死 →
        主力一波永不触发。改成时间 / 人口兜底先判，确保「到点 / 满人口必推」。
        """
        # 时间 / 人口兜底：无条件触发（拖局保险）
        if ai.time >= 60 * 8.5:
            return True
        if ai.supply_used >= 190:
            return True
        # 正常 timing：Charge 完成 + 2 Archon + 兵力够
        charge_done = (
            ai.already_pending_upgrade(UpgradeId.CHARGE) >= 1.0
            or UpgradeId.CHARGE in ai.state.upgrades
        )
        if not charge_done:
            return False
        # 用户要求（2026-05-21）：最后一波必须带 +1 攻 / +1 防（一攻一防升级完成）。
        # 只 gate 正常 timing —— 上方 8:30 / 满人口兜底仍无条件，不会因升级没好拖局。
        upgrades = ai.state.upgrades
        if (
            UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1 not in upgrades
            or UpgradeId.PROTOSSGROUNDARMORSLEVEL1 not in upgrades
        ):
            return False
        # 2026-05-24 用户:出发条件至少 3 个 archon(原 2 个)。
        # 8:30 / 200 supply 兜底仍无条件触发,避免合不出 3 archon 时拖局。
        archons = ai.units(UnitTypeId.ARCHON).ready.amount
        if archons < 3:
            return False
        unit_supply = {
            UnitTypeId.ZEALOT: 2,
            UnitTypeId.STALKER: 2,
            UnitTypeId.SENTRY: 2,
            UnitTypeId.ARCHON: 4,
            UnitTypeId.DARKTEMPLAR: 2,
        }
        army_supply = sum(ai.units(ut).ready.amount * sup for ut, sup in unit_supply.items())
        return bool(army_supply >= 30)

    @staticmethod
    def _should_macro_gateways(ai: Any) -> bool:
        """是否该动态补 BG：余钱多 或 满运营（三矿 + 农民到位）。

        2 框 / 3 框满运营后 6 BG 喂不饱 chargelot 产能、余钱堆积 —— 把钱砸进
        更多 BG 持续刷叉子。任一条件满足即触发，GridBuilding 自身 can_afford
        节流，不会一次吃空矿。
        - rich：余钱 >= 350（满运营自然会堆钱，这条本身就覆盖大部分情形）。
        - saturated：三矿到位 + 农民 >= 38，提前于钱堆起来就开始铺产能。
        """
        rich = ai.minerals >= 350
        saturated = ai.townhalls.amount >= 3 and ai.supply_workers >= 38
        return bool(rich or saturated)

    @staticmethod
    def _dt_batch_active(ai: Any) -> bool:
        """DT 折跃产线是否该开 —— 控制首批家里折跃、棱镜飞行途中不漏 warp。

        - DARKSHRINE 没好 → 关。
        - dt_trained_count < 4 → 开：家里折跃首批 4 DT（无 phasing 棱镜，sharpy
          默认在家 warp；棱镜随后装船运走）。
        - 4 ≤ dt_trained_count < 8 → 只在有 phasing 棱镜时开：第二批，
          WarpDTAtPrism 在棱镜处 warp，ProtossUnit 只做 priority 矿预留；棱镜
          运输飞行途中（无 phasing 棱镜）关掉，否则会在家漏 warp 第二批让它们
          走路赶过去，浪费整个空投。
        - dt_trained_count ≥ 8 → 关：8 DT 够了，进攻阶段把 warpgate 让给叉子增援。
        """
        try:
            if not ai.structures(UnitTypeId.DARKSHRINE).ready:
                return False
            trained = int(ai.knowledge.vibecraft.dt_trained_count)
            if trained >= 8:
                return False
            if trained < 4:
                return True
            return bool(ai.units(UnitTypeId.WARPPRISMPHASING).exists)
        except Exception:
            return False

    @staticmethod
    def _prism_quota_left(ai: Any) -> bool:
        """棱镜替补额度是否还有 —— 累计造过 < 2 个（原版 + 1 替补）。"""
        try:
            return int(ai.knowledge.vibecraft.prism_built_count) < 2
        except Exception:
            return True

    @staticmethod
    def _army_phase_active(ai: Any) -> bool:
        """是否进入主力期 —— DT 两波 8 个骚扰 warp 完之后。

        门控补 BG + chargelot 产能：DT 骚扰阶段折跃门 + 矿全留给 DT 折跃，
        进入主力期才补 BG、暴 chargelot（否则建造 / 产能成本抢 DT warp 的矿，
        拖慢骚扰 —— 用户 2026-05-21 反馈）。
        触发：DARKSHRINE ready 且（8 DT trained 满 或 7:00 兜底）。7:00 兜底防
        骚扰卡住 → 永远不进主力期、不出主力军。
        """
        try:
            if not ai.structures(UnitTypeId.DARKSHRINE).ready:
                return False
            trained = int(ai.knowledge.vibecraft.dt_trained_count)
            return trained >= 8 or ai.time >= 60 * 7
        except Exception:
            return False
