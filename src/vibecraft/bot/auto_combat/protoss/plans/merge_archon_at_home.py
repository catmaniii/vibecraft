"""MergeArchonAtHome: 只把"在家附近"的 DT 合成 Archon。

dt_drop_iac 专用（用户战术 2026-05-21）。在敌方家里没被发现、还在偷的 DT 留着
继续骚扰，**不**召回合球 —— 合成白球会现形、变脆、反而挨打。只有：
- 撤回家的 DT（被发现 → VibeCraftMicroDarkTemplar 把它撤回家）
- 家里本来就在的 DT
才就近合 Archon。

判据：DT 距任一我方基地 < _NEAR_HOME_DIST 才参与合球。敌方家里的骚扰 DT
离家远、不会被合。合球 act 由 plan 卡在"进入主力期"后才启用，所以也不会
误合棱镜首波装载中的 DT。

合并机制 mirror 自 sharpy 的 Archon act（raw MORPH_ARCHON 命令）。
"""

from __future__ import annotations

import logging
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.unit import Unit
from sharpy.managers.core.roles import UnitTask
from sharpy.plans.acts.act_base import ActBase

logger = logging.getLogger(__name__)

# DT 距我方基地小于这个距离才算"在家"、参与合球
_NEAR_HOME_DIST: float = 30.0


class MergeArchonAtHome(ActBase):  # type: ignore[misc]
    """只合并在家附近的 DT 成 Archon；敌方家里的骚扰 DT 不动。"""

    def __init__(self) -> None:
        # 已发出合并命令的 DT tag（防重复下令；合完变 idle 后清除）
        self.already_merging_tags: list[int] = []
        super().__init__()

    async def start(self, knowledge: Any) -> None:
        await super().start(knowledge)
        knowledge.register_on_unit_destroyed_listener(self.on_unit_destroyed)

    def _near_home(self, dt: Unit) -> bool:
        try:
            return any(dt.distance_to(th) < _NEAR_HOME_DIST for th in self.ai.townhalls)
        except Exception:
            return False

    async def execute(self) -> bool:
        try:
            dts = self.cache.own(UnitTypeId.DARKTEMPLAR).ready
        except Exception:
            return True

        # 合并完成（变 idle）的 DT → 清 tag + 解除 Reserved
        for dt in dts:
            if dt.is_idle and dt.tag in self.already_merging_tags:
                self.roles.clear_task(dt)
                self.already_merging_tags.remove(dt.tag)

        # 只挑：在家附近 + 未在合并中
        candidates = dts.tags_not_in(self.already_merging_tags).filter(self._near_home)
        if candidates.amount > 1:
            unit: Unit = candidates[0]
            self.already_merging_tags.append(unit.tag)
            target: Unit = candidates.tags_not_in(self.already_merging_tags).closest_to(unit)
            self.already_merging_tags.append(target.tag)

            # Reserve，防被其它 state 抢走
            self.roles.set_task(UnitTask.Reserved, unit)
            self.roles.set_task(UnitTask.Reserved, target)

            from s2clientprotocol import raw_pb2 as raw_pb
            from s2clientprotocol import sc2api_pb2 as sc_pb

            command = raw_pb.ActionRawUnitCommand(
                ability_id=AbilityId.MORPH_ARCHON.value,
                unit_tags=[unit.tag, target.tag],
                queue_command=False,
            )
            action = raw_pb.ActionRaw(unit_command=command)
            await self.ai._client._execute(
                action=sc_pb.RequestAction(actions=[sc_pb.Action(action_raw=action)])
            )
            logger.info("MergeArchonAtHome merging 2 home DT → Archon")

        return False

    def on_unit_destroyed(self, event: Any) -> None:
        if event.unit_tag in self.already_merging_tags:
            self.already_merging_tags.remove(event.unit_tag)


class MergeArchon(ActBase):  # type: ignore[misc]
    """通用合白球（2026-06-02 用户）：把 ≥2 个 ready 的指定 templar(DT 或 HT)
    合成 Archon —— **战场 + 家里都合**（无位置/能量限制）。

    用于 immortal_archon（HT 合白球）/ immortal_archon_no_ht（DT 合白球）—— 这俩
    build 原本没有任何合白球 act，HT/DT 永远堆着不合，名不副实。区别于：
    - MergeArchonAtHome：DT 专用 + 仅家里（dt_drop_iac 偷家场景，前线 DT 不召回合）
    - ArchonAfterStorm：HT 专用 + 战场放完 Storm 能量低才合（iac_2base 叉球场景）
    本 act 是"无脑合"：只要有 ≥2 个该 templar 就近合，给死球凑白球 AoE。
    """

    def __init__(self, templar_type: UnitTypeId) -> None:
        self.templar_type = templar_type
        self.already_merging_tags: list[int] = []
        super().__init__()

    async def start(self, knowledge: Any) -> None:
        await super().start(knowledge)
        knowledge.register_on_unit_destroyed_listener(self.on_unit_destroyed)

    async def execute(self) -> bool:
        try:
            templars = self.cache.own(self.templar_type).ready
        except Exception:
            return True

        # 合并完成（变 idle）的 templar → 清 tag + 解除 Reserved
        for tp in templars:
            if tp.is_idle and tp.tag in self.already_merging_tags:
                self.roles.clear_task(tp)
                self.already_merging_tags.remove(tp.tag)

        candidates = templars.tags_not_in(self.already_merging_tags)
        if candidates.amount > 1:
            unit: Unit = candidates[0]
            self.already_merging_tags.append(unit.tag)
            target: Unit = candidates.tags_not_in(self.already_merging_tags).closest_to(unit)
            self.already_merging_tags.append(target.tag)

            self.roles.set_task(UnitTask.Reserved, unit)
            self.roles.set_task(UnitTask.Reserved, target)

            from s2clientprotocol import raw_pb2 as raw_pb
            from s2clientprotocol import sc2api_pb2 as sc_pb

            command = raw_pb.ActionRawUnitCommand(
                ability_id=AbilityId.MORPH_ARCHON.value,
                unit_tags=[unit.tag, target.tag],
                queue_command=False,
            )
            action = raw_pb.ActionRaw(unit_command=command)
            await self.ai._client._execute(
                action=sc_pb.RequestAction(actions=[sc_pb.Action(action_raw=action)])
            )
            logger.info("MergeArchon merging 2 %s → Archon", self.templar_type.name)

        return False

    def on_unit_destroyed(self, event: Any) -> None:
        if event.unit_tag in self.already_merging_tags:
            self.already_merging_tags.remove(event.unit_tag)
