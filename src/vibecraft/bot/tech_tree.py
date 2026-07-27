"""三族单位/建筑/科技完整依赖图 —— 数据源:python-sc2 内置。

设计目的(2026-05-23 用户:依赖树自动补齐扩到三族):
  Director._auto_build_prereqs_for 需要查任意 unit/structure/upgrade 的完整
  prereq chain。之前手写的 `_REQUIRED_STRUCTURE` 只覆盖神族,虫族/人族 silent
  bug(prereq check 直接 pass)。这里改用 python-sc2 内置数据,三族通用零维护。

数据源(全部权威,Blizzard game data):
  - sc2.constants.PROTOSS_TECH_REQUIREMENT / ZERG_TECH_REQUIREMENT /
    TERRAN_TECH_REQUIREMENT:unit/structure → 直接 required structure
    (defaultdict 返回 NOTAUNIT 表示无前置)
  - sc2.dicts.upgrade_researched_from.UPGRADE_RESEARCHED_FROM:
    upgrade → researching building (e.g. BLINKTECH → TWILIGHTCOUNCIL,
    STIMPACK → BARRACKSTECHLAB, ZERGLINGMOVEMENTSPEED → SPAWNINGPOOL)
  - sc2.constants.EQUIVALENTS_FOR_TECH_PROGRESS:morph 形态等价
    (Gateway ↔ Warpgate,Supplydepot ↔ SupplyDepotLowered 等)

核心 API:
  required_for(target_id, race) -> UnitTypeId | None
      单层查询:目标 → 直接前置(None 表示无前置)
  prereq_chain(target_id, race) -> list[UnitTypeId]
      递归展开:从最浅(无前置的根)到目标的直接 required structure
      过滤基础建筑(Nexus/CC/Hatchery/Pylon/SupplyDepot/Larva,玩家已有)
  equivalent_structures(struct_id) -> set[UnitTypeId]
      morph 等价(Gateway ↔ Warpgate),用于 already_pending/structures 检查
"""

from __future__ import annotations

from sc2.constants import (
    EQUIVALENTS_FOR_TECH_PROGRESS,
    PROTOSS_TECH_REQUIREMENT,
    TERRAN_TECH_REQUIREMENT,
    ZERG_TECH_REQUIREMENT,
)
from sc2.data import Race
from sc2.dicts.unit_train_build_abilities import TRAIN_INFO
from sc2.dicts.upgrade_researched_from import UPGRADE_RESEARCHED_FROM
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

_RACE_TECH_REQ: dict[Race, dict[UnitTypeId, UnitTypeId]] = {
    Race.Protoss: PROTOSS_TECH_REQUIREMENT,
    Race.Zerg: ZERG_TECH_REQUIREMENT,
    Race.Terran: TERRAN_TECH_REQUIREMENT,
}

# 基础建筑 + supply 类:不需要自动补建(玩家天然有,或 sharpy 自动管)。
# Larva 是 unit 不是 structure,但虫族 unit trained_from 可能含 Larva,过滤掉。
_BASE_STRUCTURES: frozenset[UnitTypeId] = frozenset(
    {
        UnitTypeId.NEXUS,
        UnitTypeId.HATCHERY,
        UnitTypeId.COMMANDCENTER,
        UnitTypeId.PYLON,
        UnitTypeId.SUPPLYDEPOT,
        UnitTypeId.LARVA,
    }
)

# Worker:作为 target 时无 prereq(玩家天然有 base 生产);
# 作为 producer 时也要跳过(避免 SpawningPool→Drone 之类错链)。
_WORKER_UNITS: frozenset[UnitTypeId] = frozenset(
    {
        UnitTypeId.PROBE,
        UnitTypeId.SCV,
        UnitTypeId.DRONE,
        UnitTypeId.MULE,
    }
)

# 非建筑 producer:TRAIN_INFO fallback 反查时跳过(防误判 prereq)。
# Queen 能造 CreepTumor;Larva 能 morph 单位 — 都不是 prereq。
# 但 Queen / Larva 作为 target 时仍走正常 TECH_REQUIREMENT 查询。
_NON_PREREQ_PRODUCERS: frozenset[UnitTypeId] = _WORKER_UNITS | {UnitTypeId.LARVA, UnitTypeId.QUEEN}


def required_for(target_id: UnitTypeId | UpgradeId, race: Race) -> UnitTypeId | None:
    """单位/建筑/升级 → 直接 required structure。None 表示无 prereq。

    - UpgradeId:走 UPGRADE_RESEARCHED_FROM
    - UnitTypeId:走 race 对应的 _RACE_TECH_REQ;若 TECH_REQUIREMENT 没列,
      fallback 到 TRAIN_INFO 找 producer 建筑(2026-05-27 修复)。

    2026-05-27 用户:OBSERVER/IMMORTAL/WARPPRISM 等"producer 即唯一 prereq"
    类单位,sc2.constants.PROTOSS_TECH_REQUIREMENT 漏列(它只记额外科技要求,
    不记 producer)。结果 `required_for(OBSERVER)=None` → _check_prereq_ready
    判定 ready → bot.train(OBSERVER) 因无 Robo 静默返 0,auto_prereq 也不触发。

    fallback 逻辑:在 TRAIN_INFO 里反查 — 哪个建筑能生产此单位,该建筑就是
    prereq。对 OBSERVER 找到 ROBOTICSFACILITY(唯一生产者)。
    """
    if isinstance(target_id, UpgradeId):
        return UPGRADE_RESEARCHED_FROM.get(target_id)
    # UnitTypeId
    # workers 没 prereq(玩家天然有 base 生产);Queen/Larva 仍走正常 TECH_REQ
    if target_id in _WORKER_UNITS:
        return None
    req = _RACE_TECH_REQ[race].get(target_id, UnitTypeId.NOTAUNIT)
    if req == UnitTypeId.NOTAUNIT:
        # fallback: TRAIN_INFO 反查 producer
        # TRAIN_INFO[producer_id][produced_unit_id] = {ability, required_building?, ...}
        for producer_id, train_dict in TRAIN_INFO.items():
            if target_id in train_dict:
                # 跳过基础建筑(Nexus/Hatchery/CC):那是 worker 来源,玩家天然有
                if producer_id in _BASE_STRUCTURES:
                    continue
                # 跳过 worker / 非建筑 producer(Drone 能 build SpawningPool 但
                # Drone 不是 prereq;Pool 在 ZERG_TECH_REQUIREMENT 也无前置 → 真无 prereq)
                if producer_id in _NON_PREREQ_PRODUCERS:
                    continue
                return producer_id
        return None
    return req


def prereq_chain(target_id: UnitTypeId | UpgradeId, race: Race) -> list[UnitTypeId]:
    """完整 prereq chain:从最浅(无前置的根)到目标的直接 required structure。

    不含 target 自身,只含 prereq 链。基础建筑(Nexus/CC/Hatchery/Pylon/SupplyDepot)
    被过滤(玩家已有 / sharpy 自动管)。

    例:
      prereq_chain(DARKTEMPLAR, Protoss) → [GATEWAY, CYBERNETICSCORE,
                                            TWILIGHTCOUNCIL, DARKSHRINE]
      prereq_chain(ZERGLING, Zerg)       → [SPAWNINGPOOL]
      prereq_chain(BANELING, Zerg)       → [SPAWNINGPOOL, BANELINGNEST]
      prereq_chain(MUTALISK, Zerg)       → [SPAWNINGPOOL, LAIR, SPIRE]
      prereq_chain(THOR, Terran)         → [BARRACKS, FACTORY, ARMORY]
      prereq_chain(BLINKTECH, Protoss)   → [GATEWAY, CYBERNETICSCORE,
                                            TWILIGHTCOUNCIL]
    """
    chain: list[UnitTypeId] = []
    seen: set[UnitTypeId] = set()

    def _walk(curr: UnitTypeId | UpgradeId) -> None:
        req = required_for(curr, race)
        if req is None:
            return
        if req in _BASE_STRUCTURES:
            return
        if req in seen:
            return
        seen.add(req)
        _walk(req)  # 先递归更深层(后序遍历:深的在前)
        chain.append(req)

    _walk(target_id)
    return chain


def equivalent_structures(struct_id: UnitTypeId) -> set[UnitTypeId]:
    """morph 等价形态(Gateway ↔ Warpgate)。用于 already_pending/structures ready 检查。

    含 struct_id 自身。例:equivalent_structures(GATEWAY) → {GATEWAY, WARPGATE}。
    """
    equiv: set[UnitTypeId] = {struct_id}
    extra = EQUIVALENTS_FOR_TECH_PROGRESS.get(struct_id)
    if extra:
        equiv.update(extra)
    return equiv
