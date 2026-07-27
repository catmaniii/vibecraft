"""vibecraft 凤凰控场 persistent doctrine plan。

凤凰控空 + 升空抓关键单位 + 反骚扰，地面巨像 + 追猎跟进，地图控制型运营。
从 phoenix_2base / iac_2base 开局转入最顺 —— VS 已就位，追加 VF + VR→VB 即可。

不像开局是固定 supply build，persistent doctrine 是**后期组合驱动**：玩家从
中期开局转过来时已经有 2-3 矿 + CYBERNETICSCORE + 可能有 VS，剧本要做的是
把建筑链补完整 + 持续 train 关键单位 + 升空军/地面攻防。

core target composition:
  12 凤凰 + 4 巨像 + 8 追猎 + 2 Observer

关键路径:
  1. 经济：ProtossPersistentMacro（3 矿 / 满农）
  2. 建筑链：VS×2（凤凰产能）/ VF（凤凰射程前置）/ VR → VB（巨像前置）/ 2 BF / 4 BG
  3. 升级：凤凰射程 + 巨像射程 + 空军武器 1/2/3 + 地面武器 1/2/3（两条并行）
  4. 单位：凤凰 12 / 巨像 4 / 追猎 8 / Observer 2
  5. chrono：VS 完成后持续 chrono 凤凰（空军核心产能）
  6. 战斗：6+ 凤凰 + 凝聚 → PlanZoneAttack 强 timing 推；巨像随后跟进

假定转入时基础经济已就位；若缺早期建筑（如 CYBERNETICSCORE 都没），各 Step
的 require 会卡住等先决。

2026-05-30: 加 PhoenixSquadAct + 出门门改 >=6 + 凝聚。
"""

from __future__ import annotations

from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import BuildGas, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.protoss import ChronoUnit, ProtossUnit, RestorePower
from sharpy.plans.require import UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanCancelBuilding,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)

from vibecraft.bot.auto_combat.persistent_macro import MacroConfig, ProtossPersistentMacro
from vibecraft.bot.auto_combat.protoss.phoenix_squad_micro import REJOIN_DIST
from vibecraft.bot.auto_combat.protoss.plans.phoenix_squad_act import PhoenixSquadAct


class PhoenixControl(KnowledgeBot):  # type: ignore[misc]  # sharpy 无类型,KnowledgeBot=Any
    """凤凰控场 — 凤凰控空 + 升空抓关键单位，地面巨像 + 追猎跟进。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Phoenix Control")
        # 2026-05-28 Issue 4:AttackGate 处理 retreat/defend intent + latch
        from vibecraft.bot.auto_combat.intent_gate import AttackGate

        self._attack_gate = AttackGate(self._ready_to_push)

    async def create_plan(self) -> BuildOrder:
        # 3 矿稳运营；ProtossPersistentMacro 给 probe chrono + AutoPylon + Expand
        macro = ProtossPersistentMacro(MacroConfig(expansion_cap=3))
        return BuildOrder(
            # ---------- 经济基线（probe + AutoPylon + Expand 3 矿）----------
            *macro.acts(),
            # ---------- 气矿：6 个（3 矿满气，凤凰 + 巨像都吃气）----------
            BuildGas(6),
            # ---------- 建筑链 ----------
            # CC 前置兜底（从无 robo 开局切入时可能缺）
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            # VS×2（凤凰双线产能；已有 1 VS 时只补第 2 个）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.STARGATE, 2),
            ),
            # VF（凤凰射程前置；在 VS 1 好时就立刻开建，与补 VS×2 并行）
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                GridBuilding(UnitTypeId.FLEETBEACON, 1),
            ),
            # VR（巨像前置；与 VF 并行开建）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.ROBOTICSFACILITY, 1),
            ),
            # VB（巨像前置；VR 完成后立刻上）
            Step(
                UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                GridBuilding(UnitTypeId.ROBOTICSBAY, 1),
            ),
            # 2 BF（空军武器 / 地面武器双线并行研）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.FORGE, 2)),
            # 4 BG（追猎 + 追猎补充产能）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.GATEWAY, 4)),
            # ---------- 升级 ----------
            # 凤凰射程（VF 完成立刻研）
            Step(UnitReady(UnitTypeId.FLEETBEACON, 1), Tech(UpgradeId.PHOENIXRANGEUPGRADE)),
            # 巨像射程（VB 完成立刻研）
            Step(UnitReady(UnitTypeId.ROBOTICSBAY, 1), Tech(UpgradeId.EXTENDEDTHERMALLANCE)),
            # 空军武器：weapons 1/2/3（gate VS ready；1 → 2 gate VF ready）
            SequentialList(
                Step(
                    UnitReady(UnitTypeId.STARGATE, 1),
                    Tech(UpgradeId.PROTOSSAIRWEAPONSLEVEL1),
                ),
                Step(
                    UnitReady(UnitTypeId.FLEETBEACON, 1),
                    Tech(UpgradeId.PROTOSSAIRWEAPONSLEVEL2),
                ),
                Step(
                    UnitReady(UnitTypeId.FLEETBEACON, 1),
                    Tech(UpgradeId.PROTOSSAIRWEAPONSLEVEL3),
                ),
            ),
            # 地面武器：weapons 1/2/3（gate BF；与空军升级线并行，用第 2 个 BF）
            SequentialList(
                Step(
                    UnitReady(UnitTypeId.FORGE, 1),
                    Tech(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1),
                ),
                Step(
                    UnitReady(UnitTypeId.FORGE, 1),
                    Tech(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL2),
                ),
                Step(
                    UnitReady(UnitTypeId.FORGE, 1),
                    Tech(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL3),
                ),
            ),
            # ---------- 单位训练 ----------
            # 凤凰（核心控空）；gate VS ready 即开始
            Step(UnitReady(UnitTypeId.STARGATE, 1), ProtossUnit(UnitTypeId.PHOENIX, 12)),
            # 巨像（地面 AoE 核心）；gate VB ready
            Step(UnitReady(UnitTypeId.ROBOTICSBAY, 1), ProtossUnit(UnitTypeId.COLOSSUS, 4)),
            # 追猎（地面补充 / 拉扯）；gate CC ready
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), ProtossUnit(UnitTypeId.STALKER, 8)),
            # Observer（反隐）；gate VR ready
            Step(UnitReady(UnitTypeId.ROBOTICSFACILITY, 1), ProtossUnit(UnitTypeId.OBSERVER, 2)),
            # ---------- chrono：持续 chrono 凤凰（VS 完成后空军产能核心）----------
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                ChronoUnit(UnitTypeId.PHOENIX, UnitTypeId.STARGATE),
            ),
            # ---------- 战术 / 维护 / 战斗触发 ----------
            SequentialList(
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                PlanZoneDefense(),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                # PhoenixSquadAct：抱团 + 永动 kite + 智能 lift。
                # wave_threshold=6：与 _ready_to_push 一致（6 凤凰起锁）。
                # 2026-05-30: 从 sharpy 默认 micro 切换到 squad-aware 控制。
                # 骚扰=持久指令卡：玩家可×随时归队；harass_duration=240s 硬性兜底
                # （控场 doctrine 凤凰更多随军，骚扰窗口短一点）。
                PhoenixSquadAct(release_after=None, wave_threshold=6, harass_duration=240.0),
                PlanZoneGather(),
                # 6+ 凤凰 + 凝聚 → 出门推；凤凰控空后巨像跟进；玩家强制 attack 直接绕过
                Step(
                    self._attack_gate,
                    PlanZoneAttack(8),
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_push(ai: Any) -> bool:
        """出门条件: >=6 凤凰 ready + squad 凝聚（最远凤凰距 center < REJOIN_DIST）。

        2026-05-30 改: 从单纯 >=8 凤凰 改成 >=6 + 凝聚 gate，避免凤凰各自飞。
        REJOIN_DIST 从 phoenix_squad_micro 常量取，跟随 micro 阈值一起调。
        巨像 / 追猎在 free_army 里随大军走，不必等齐；
        凤凰是本 doctrine 的前置制空力量，不够时不强推。
        """
        phoenixes = ai.units(UnitTypeId.PHOENIX).ready
        if phoenixes.amount < 6:
            return False
        center = phoenixes.center
        max_dist = max(p.distance_to(center) for p in phoenixes)
        return bool(max_dist < REJOIN_DIST)
