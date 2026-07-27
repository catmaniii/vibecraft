"""proxy_4rax 枪兵前向集结：头几个枪兵不回家，在野兵营（3 proxy 兵营）锚点附近
集结，攒到 threshold（默认 6）个左右再一起出发。

根因（用户反馈 2026-07-09）：proxy_4rax 用 sharpy `PlanZoneGather()`，出门前会把
idle 枪兵往家拉——枪兵一个个练成就被拉回家又拉出去，拉扯 + 分批送死，凑不齐一波。

实现照抄 `proxy_rax_act.py` 的 SCV 站桩机制（已验证成熟）：
  - `_reassert_reserved` 等价：每帧把枪兵 role 重申为 `Reserved`（sharpy 各 Manager
    /全军暴退拉不走 Reserved 单位——PlanZoneGather 只拉 idle/free 单位）。
  - `_hold_at_anchor` 等价：远了 `move` 过去（不 attack_move，撤退/集结类不恋战——
    CLAUDE.md 控制权模型规则4），到位 `hold_position()` 一次（幂等，不每帧重发，
    防止目标点漂移式追逐——CLAUDE.md「目标坐标一次规划锁定」纪律）。

锚点来源：`knowledge.vibecraft.proxy_anchor`，由 `ProxyBarracksAct` 每帧发布
（3 个 proxy 兵营规划落点的质心）。锚点还没出现（proxy 还没选点）→ `execute()`
直接 `return False`，不拦 BuildOrder 后续 act（顶层兄弟，见 `proxy_4rax.py` 接线）。

释放门（任一满足 → 释放全部被 stage 的枪兵为 Idle，PlanZoneAttack 接管全队出击）：
  A. 场上 ready 枪兵数 ≥ threshold（默认 6，用户"六七个可调"）
  B. `ai.time` ≥ fallback_time（默认 170s，防产能慢/被反攻打断一直攒不够卡死；
     留在 `proxy_4rax._opening_done` 的 180s all-in 兜底之前）
  C. 玩家下达全军 `combat_intent_override`（attack/retreat/defend）→ 立即释放，
     尊重玩家优先于攒兵逻辑（CLAUDE.md 控制权模型规则 2：全军命令只管自由单位，
     这里主动让出 Reserved，别跟玩家拧着）

玩家单位级 claim 的枪兵（在 `ai._llm_controlled_tags` 里）不碰——不 stage、不
release，交给玩家指令（控制权模型规则 1：单位级指令独占，别跟它抢）。

顶层 BuildOrder 兄弟（跟 `ProxyBarracksAct` 同级，见 `proxy_4rax.py`），不放进
`tactics SequentialList`（那会 block 序列后面的 `PlanZoneAttack`）。`execute()`
每帧独立跑，不 gate 别的 act（永远不 raise，返回 True 只表示"这一步不再需要每帧
干预"，不影响其它顶层兄弟）。
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sc2.ids.unit_typeid import UnitTypeId
from sharpy.managers.core.roles import UnitTask
from sharpy.plans.acts import ActBase

_DEFAULT_THRESHOLD = 6  # 用户："攒到 6 个左右一起出发"，六七个可调
_DEFAULT_FALLBACK_TIME = 170.0  # 兜底超时；proxy_4rax._opening_done 180s 兜底之前放行
_STAGE_ARRIVE_DIST = 4.0  # 到锚点判定距离（同 proxy_rax_act._hold_at_anchor）
_PLAYER_INTENTS = ("attack", "retreat", "defend")


class MarineStagingAct(ActBase):  # type: ignore[misc]
    """枪兵前向集结：在 proxy 锚点附近攒够 threshold 再一起释放出发。"""

    def __init__(
        self,
        threshold: int = _DEFAULT_THRESHOLD,
        fallback_time: float = _DEFAULT_FALLBACK_TIME,
    ) -> None:
        super().__init__()
        self.threshold = threshold
        self.fallback_time = fallback_time
        self._released = False
        self._staged_tags: set[int] = set()
        self._held_tags: set[int] = set()  # 已在锚点 hold_position（幂等，防重复发命令/漂移）

    async def execute(self) -> bool:
        if self._released:
            return True  # 已释放，本 act 任务完成，不再干预

        vib = getattr(self.knowledge, "vibecraft", None)
        anchor = getattr(vib, "proxy_anchor", None) if vib is not None else None
        if anchor is None:
            return False  # proxy 还没选点，还没到集结阶段

        marines = self.ai.units(UnitTypeId.MARINE).ready
        player_tags: set[int] = set(getattr(self.ai, "_llm_controlled_tags", None) or set())
        free = [m for m in marines if m.tag not in player_tags]
        # 玩家事后 claim 了已 stage 的枪兵 → 让出管理权（单位级指令独占，规则1）
        self._staged_tags -= player_tags
        self._held_tags -= player_tags

        intent = getattr(vib, "combat_intent_override", None) if vib is not None else None
        if intent in _PLAYER_INTENTS:
            self._release(free, reason=f"player_intent={intent}", marine_count=marines.amount)
            return True

        if marines.amount >= self.threshold or self.ai.time >= self.fallback_time:
            reason = "threshold" if marines.amount >= self.threshold else "fallback_time"
            self._release(free, reason=reason, marine_count=marines.amount)
            return True

        for m in free:
            self.knowledge.roles.set_task(UnitTask.Reserved, m)
            if m.distance_to(anchor) > _STAGE_ARRIVE_DIST:
                m.move(anchor)
                self._held_tags.discard(m.tag)
            elif m.tag not in self._held_tags:
                m.hold_position()
                self._held_tags.add(m.tag)
            self._staged_tags.add(m.tag)

        if free:
            logger.info(f"MARINESTAGE staging n={len(free)} anchor=({anchor.x:.1f},{anchor.y:.1f})")
        return False

    def _release(self, free: list[Any], reason: str, marine_count: int | None = None) -> None:
        for m in free:
            self.knowledge.roles.set_task(UnitTask.Idle, m)
        n = marine_count if marine_count is not None else len(free)
        logger.info(f"MARINESTAGE released n={n} reason={reason}")
        self._staged_tags.clear()
        self._held_tags.clear()
        self._released = True
