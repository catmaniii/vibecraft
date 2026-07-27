"""vibecraft 虚空骚扰开矿（1 BG + 2 VS，骚扰同时二矿）plan。

战术核心
========
1 BG + 2 VS，攒 4 虚空舰出门骚扰，同时开二矿：
  - PvZ / PvP / PvT 均可用（骚扰 + 经济，通用性强）
  - 2 VS 持续出虚空舰，攒到 4 个 → 派往对面主基地高地空中入侵
  - 家里 1 只追猎保守家
  - 高地边缘利用空军优势慢慢消耗对方地面部队
  - 二矿农民补满后加到 4 BG，然后 4 BG + 2 VS 一起爆兵
  - 看情况开 BF 升攻防（地空双线升级）

关键路径
========
  ~0:22  9 BE
  ~1:20  13 BG × 1（只 1 个！）
  ~1:35  14 BA × 1（第 1 气）
  ~1:50  BY（BG ready 后建）
  ~2:05  15 BE × 2
  ~2:10  19 BA × 2（第 2 气；BY 存在时建）
  ~2:15  研折跃（BY ready 后立刻，chrono）
  ~2:20  VS × 2（BY ready 后立刻建 2 个 — 注意同时建！）
  ~2:30  追猎 × 1（BY ready + 折跃完成后，保家）
  ~3:00  第 1 虚空舰产出（chrono）
  ~3:30  第 2 虚空舰
  ~4:30  第 4 虚空舰 ready → 出门骚扰对面主基地高地
  ~4:30-5:30  水晶 / 气够了就开二矿
  ~6:30-7:30  二矿农民补满 → BG 加到 4，爆兵
  后期:开 BF，地空攻防同步升级

注：追猎只出 1 个（家里守门）；无大量地面部队压制
    4 虚空舰 ready 是出门信号，不是 all-in，持续造虚空补充
    _ZONE_ATTACK_POWER = 0（不靠 supply 启发，由 4 虚空舰显式判定）
"""

from __future__ import annotations

import logging
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.interfaces import IGatherPointSolver
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
from sharpy.plans.acts.protoss import (
    AutoPylon,
    ChronoTech,
    ChronoUnit,
    ProtossUnit,
    RestorePower,
)
from sharpy.plans.require import Supply, UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanCancelBuilding,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)

from vibecraft.bot.auto_combat.intent_gate import AttackGate
from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct

logger = logging.getLogger(__name__)

# 虚空骚扰出门所需虚空舰最小数（用户明确：4 个）
_VR_WAVE_THRESHOLD: int = 4

# 时间兜底：5:30（4 虚空舰若 5:30 还没到，先强制出门骚扰）
_TIME_FALLBACK_S: float = 60 * 5 + 30  # 330s

# attack_on_advantage = True — 骚扰 + 经济 build，优势时顺势推进
# PlanZoneAttack(0) 占位：实际出门由 _ready_to_harass 控制，不靠 supply 数值
_ZONE_ATTACK_POWER: int = 0


def _make_harass_attack_act(start_attack_power: int) -> PlanZoneAttack:
    """构造 PlanZoneAttack，attack_on_advantage=True（骚扰 build 优势时顺势推）。"""
    act = PlanZoneAttack(start_attack_power)
    act.attack_on_advantage = True
    return act


# 第一波骚扰已出动的判定:有虚空在 attacking 且离家 > 此距离(= 出门骚扰,区别于在家防守)
_HARASS_OUT_DIST: float = 25.0

# 集结点交回 bot 默认的时间兜底(2026-06-07 用户:游戏前期过了 → bot 自己选集结点,如分矿外)。
# 第一波虚空出门通常先 latch(~4:30);此为"一直没出门"时的兜底,过此时间也交回 bot。
_RALLY_HANDBACK_TIME_S: float = 60 * 7  # 420s = 7 游戏分钟


class VoidRayStageRallyAct(ActBase):  # type: ignore[misc]
    """虚空骚扰前:把全局集结点设到 **离对方主基地最近的 VS(野星门)**,虚空一出来就近集结、
    方便出门骚扰(而非被拉回家门口)。latch 后不再 override → 恢复 sharpy 默认集结(bot 自己选,
    如分矿外),后续 VS 出的虚空回家防守/regroup。

    latch 触发(任一):
    - 第一波虚空真出门骚扰(attacking 虚空离家 > _HARASS_OUT_DIST);
    - 游戏前期过了(time > _RALLY_HANDBACK_TIME_S,兜底:一直没出门也交回 bot)。
    (2026-06-07 用户:有野 VS → 集结到离敌最近的那个;前期过了 → 默认交回 bot。)

    玩家显式设了集结点(knowledge.vibecraft.player_rally_point)→ 本 act 让位(玩家 > bot)。

    放在 PlanZoneGather **之前**(每 tick set gather_point,PlanZoneGather 据此 rally)。
    PlanZoneAttack 触发出门时 attack 命令自然覆盖集结(同 ForwardRallyStalker)。
    """

    def __init__(self) -> None:
        super().__init__()
        self._launched = False  # latch:不再 override 集结点
        self._staged_logged = False  # 集结日志只打一次

    async def execute(self) -> bool:
        if self._launched:
            return True  # 已 latch → 不 override,bot 默认集结(分矿外/后续虚空回家)
        # 玩家显式设了集结点 → 让位(玩家 > bot),不覆盖
        vbc = getattr(self.knowledge, "vibecraft", None)
        if getattr(vbc, "player_rally_point", None) is not None:
            return True
        try:
            home = self.ai.start_location
            # latch 兜底:游戏前期过了 → 交回 bot 默认集结点(自己选,如分矿外)
            if self.ai.time > _RALLY_HANDBACK_TIME_S:
                self._launched = True
                logger.info(
                    "VOIDRAYRALLY 过 %.0fs(前期结束)→ 集结点交回 bot 默认",
                    _RALLY_HANDBACK_TIME_S,
                )
                return True
            attacking = self.knowledge.roles.attacking_units
            # 有虚空在 attacking 且离家远 = 第一波真出门骚扰了(区别于在家防守的 attacking)
            if any(
                u.type_id == UnitTypeId.VOIDRAY and u.distance_to(home) > _HARASS_OUT_DIST
                for u in attacking
            ):
                self._launched = True
                logger.info("VOIDRAYRALLY 第一波骚扰已出门 → 集结点交回 bot 默认")
                return True
        except Exception:
            pass
        # 还没 latch → 集结到"离对方主基地最近的 VS"(最前的野星门,虚空就近待命方便出击)
        try:
            stargates = self.ai.structures(UnitTypeId.STARGATE).ready
            if stargates.exists:
                solver = self.knowledge.get_required_manager(IGatherPointSolver)
                # 离敌方主基地最近的 VS(野 VS 优先;没野 VS 则家里 VS 也行)
                enemy_main = self.ai.enemy_start_locations[0]
                vs = stargates.closest_to(enemy_main)
                solver.set_gather_point(vs.position)
                if not self._staged_logged:
                    self._staged_logged = True
                    logger.info(
                        "VOIDRAYRALLY 集结点设到离敌最近 VS(%.1f,%.1f),虚空就近待命",
                        vs.position.x,
                        vs.position.y,
                    )
        except Exception:
            pass
        return True


class VoidRayHarass(KnowledgeBot):  # type: ignore[misc]
    """虚空骚扰开矿（1 BG + 2 VS）— 攒 4 虚空舰骚扰高地 + 二矿运营，全对手通用。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Void Ray Harass")
        # AttackGate：_ready_to_harass 满足或玩家 intent → True
        # latch 防虚空舰被打死后 gate 永久关闭
        self._attack_gate = AttackGate(self._ready_to_harass)

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 开局完成（_ready_to_harass 首次满足）→ 通知 Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._ready_to_harass),
            # ---------- chrono ----------
            # 探机 chrono 直到 BY 出现（优先经济 + 快速 BY）
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.CYBERNETICSCORE, 1),
                skip_until=UnitExists(UnitTypeId.ASSIMILATOR, 1),
            ),
            # 折跃研究 chrono（折跃研究本身延后到第一波虚空出完才研，见下方 Tech；chrono 同步
            # 延后，早期 chrono 全给虚空舰 timing，2026-06-07 用户）
            Step(
                UnitExists(UnitTypeId.VOIDRAY, 4),
                ChronoTech(AbilityId.RESEARCH_WARPGATE, UnitTypeId.CYBERNETICSCORE),
            ),
            # 虚空舰 chrono（VS ready 后持续 chrono，加快虚空舰产出 timing）
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                ChronoUnit(UnitTypeId.VOIDRAY, UnitTypeId.STARGATE),
            ),
            # ---------- 早期主线（严守顺序）----------
            SequentialList(
                # 9 BE：标准 LotV 开局
                GridBuilding(UnitTypeId.PYLON, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 13),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14),
                # BG × 1：supply 13，只建 1 个（用户明确：1 BG 保守家 + 1 追猎）
                GridBuilding(UnitTypeId.GATEWAY, 1),
                # 第 1 气：supply 14
                BuildGas(1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 17),
            ),
            # ---------- BY（BG ready 后立刻）----------
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            # ---------- 第 2 BE（supply 15，不等 BY）----------
            Step(Supply(15), GridBuilding(UnitTypeId.PYLON, 2)),
            # ---------- 第 2 气（BY 存在时建，supply 19 附近）----------
            Step(UnitExists(UnitTypeId.CYBERNETICSCORE, 1), BuildGas(2)),
            # ---------- 折跃研究（延后到第一波虚空出完、开始补 BG 时再研，2026-06-07 用户）----------
            # 早研抢虚空的 chrono/钱、压慢虚空;折跃要到补 BG 爆兵阶段才用得上,VOIDRAY≥4(第一波出完)
            # 再研都来得及(此时正好开始补 2 BG)。
            Step(UnitExists(UnitTypeId.VOIDRAY, 4), Tech(UpgradeId.WARPGATERESEARCH)),
            # ---------- VS × 2（BY ready 后立刻同时建 2 个）----------
            # 这是 build 核心：2 VS 保证足够虚空舰产能攒到 4 个
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.STARGATE, 2)),
            # ---------- 单位训练 ----------
            # 追猎保家：折跃完成后只出 1 个（用户明确：1 追猎守门，不出多）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ProtossUnit(UnitTypeId.STALKER, 1),
            ),
            # 虚空舰主力（2 VS 持续产，target 12：骚扰 + 后期规模扩充）
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                ProtossUnit(UnitTypeId.VOIDRAY, 12),
            ),
            # ---------- 二矿（水晶 / 气足够时开）----------
            # 二矿触发条件：第 1 VS ready 后（约 3:30+，开始有虚空舰的时候顺势开矿）
            # 注：不提前开矿，等 VS ready 表示经济基础已建好
            Step(UnitReady(UnitTypeId.STARGATE, 1), Expand(2)),
            # ---------- 中期 2 BG（2026-06-02 用户调节奏）----------
            # 出虚空的同时有富余水晶 → 补到 2 BG（2BG2VS：一边虚空一边叉子，
            # 虚空 gas-heavy，矿用不完，2 BG 把富余矿变成叉子，不浪费）。
            Step(UnitExists(UnitTypeId.VOIDRAY, 3), GridBuilding(UnitTypeId.GATEWAY, 2)),
            # 2 BG 富余水晶出叉子（中期肉盾 + 地面存在）
            Step(UnitReady(UnitTypeId.GATEWAY, 2), ProtossUnit(UnitTypeId.ZEALOT, 4)),
            # ---------- 后期 BG 扩张（至少 6 虚空后再补 4 BG）----------
            # 2026-06-02 用户明确："至少虚空出到 6-8 个以后再补 bg，别二矿一落地就摊
            # 地面兵导致不出虚空在刷叉子"。改从 NEXUS≥2(~3:30) 延后到 VOIDRAY≥6(~6:00)。
            Step(UnitExists(UnitTypeId.VOIDRAY, 6), GridBuilding(UnitTypeId.GATEWAY, 4)),
            # ---------- 后期气矿扩充（二矿后补满气矿）----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(3)),
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(4)),
            # ---------- 后期叉子（4 BG 爆兵，叉子 + 追猎混合地面部队）----------
            Step(
                UnitReady(UnitTypeId.GATEWAY, 4),
                ProtossUnit(UnitTypeId.ZEALOT, 8),
            ),
            # 追猎后期扩充（4 BG 后加追猎，地面阵容配合虚空舰）
            Step(
                UnitReady(UnitTypeId.GATEWAY, 4),
                ProtossUnit(UnitTypeId.STALKER, 8),
            ),
            # ---------- 后期 BF 攻防升级（看情况开）----------
            # 用户明确："看情况同时升级攻防"——地空双线，BF ready 后自动研究
            # 地面攻击升级
            Step(
                UnitExists(UnitTypeId.GATEWAY, 4),
                GridBuilding(UnitTypeId.FORGE, 1),
            ),
            # ---------- 经济 ----------
            AutoPylon(),
            # 骚扰 + 运营 build：持续补农到双矿饱和（约 44 探机）
            ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 44),
            # ---------- 战术 / 维护 / 攻击触发 ----------
            SequentialList(
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                PlanZoneDefense(),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                # 第一波骚扰前:虚空集结到 VS 附近(方便出门);出门后恢复家里集结。必须在
                # PlanZoneGather 之前 set gather_point。
                VoidRayStageRallyAct(),
                PlanZoneGather(),
                # 出门条件：4 虚空舰 ready（_ready_to_harass）
                # 或玩家任意 intent（attack/retreat/defend/hold）
                # latch 防虚空舰死光 gate 永久关闭
                # attack_on_advantage=True — 骚扰 build，优势时顺势推
                Step(
                    self._attack_gate,
                    _make_harass_attack_act(_ZONE_ATTACK_POWER),
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_harass(ai: Any) -> bool:
        """虚空骚扰出门：至少 1 VS ready + 4 虚空舰 ready。

        核心条件：
        - 至少 1 VS 完成（防 VS 还在建时误触发）
        - 至少 4 虚空舰 ready（用户明确：攒到 4 个出门）

        时间兜底 5:30：骚扰 build 若 5:30 仍无 4 虚空舰，先强制出门，
        不是 all-in 兜底，是防异常卡进度。
        """
        # VS 必须完成
        stargate_ready = ai.structures(UnitTypeId.STARGATE).ready.exists
        if not stargate_ready:
            return False
        # 至少 4 虚空舰 ready（_VR_WAVE_THRESHOLD = 4）
        vr_count = ai.units(UnitTypeId.VOIDRAY).ready.amount
        if vr_count >= _VR_WAVE_THRESHOLD:
            return True
        # 时间兜底：5:30
        return bool(ai.time >= _TIME_FALLBACK_S)
