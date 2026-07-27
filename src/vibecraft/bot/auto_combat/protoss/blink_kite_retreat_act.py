"""闪追风筝战术撤退 act(2026-05-28 用户需求 3)。

需求:
- 前线 stalker 大部分 blink CD 都没好 + 平均护盾低 → 整体撤退拖 CD
- CD / 护盾恢复后 → 继续推

实施:
- 每 tick 扫前线 attacking stalkers
- 计算 blink ready 比例 + 平均护盾 %
- READY_RATIO_LOW(默认 < 0.3)+ 平均护盾 < SHIELD_LOW_PCT(默认 0.5)→
  set knowledge.vibecraft.kite_retreat = True
- READY_RATIO_RECOVER(默认 > 0.6)→ clear kite_retreat = False
- 中间区域 hysteresis,防 flip-flop

vendor zone_attack.py _should_retreat hook 读 kite_retreat,True 时 return Retreat。
PlanZoneAttack 走 retreat 分支 → 单位回 home → CD 在路上 / home 处自然恢复 →
ratio 回升 → kite_retreat=False → PlanZoneAttack 重新 attack。

加到 blink_stalker plan 的 SequentialList(在 PlanZoneAttack 之前)。
"""

from __future__ import annotations

import logging

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts import ActBase

logger = logging.getLogger(__name__)


# 触发撤退的阈值:blink ready 比例 < ENTER_THRESH 且平均护盾 < SHIELD_LOW
_ENTER_READY_RATIO: float = 0.3
_SHIELD_LOW: float = 0.5
# 退出撤退(回升):blink ready 比例 > EXIT_THRESH
_EXIT_READY_RATIO: float = 0.6
# 最小 stalker 数量,少于不启动撤退(防开局误触)
_MIN_STALKER_COUNT: int = 5
# 与 home 距离阈值:超过这个距离才算"前线",防止已在家也触发
_FORWARD_MIN_DIST: float = 25.0


class BlinkKiteRetreatAct(ActBase):  # type: ignore[misc]
    """检测前线 stalker blink CD 覆盖率 + 护盾,set knowledge.vibecraft.kite_retreat。

    vendor zone_attack._should_retreat hook 消费此 flag。
    """

    async def execute(self) -> bool:
        ai = self.ai
        knowledge = self.knowledge
        vbc = getattr(knowledge, "vibecraft", None)
        if vbc is None:
            return True  # non-blocking
        try:
            stalkers = ai.units(UnitTypeId.STALKER)
            if stalkers.amount < _MIN_STALKER_COUNT:
                if getattr(vbc, "kite_retreat", False):
                    vbc.kite_retreat = False
                    logger.warning(
                        "BlinkKiteRetreat: clear (stalker count < %d)", _MIN_STALKER_COUNT
                    )
                return True

            # 只看前线 stalker(距 home > 阈值)
            home = ai.start_location
            forward_stalkers = stalkers.filter(
                lambda u: u.position.distance_to(home) > _FORWARD_MIN_DIST
            )
            if forward_stalkers.amount < _MIN_STALKER_COUNT:
                # 大部分在家了,清 kite_retreat
                if getattr(vbc, "kite_retreat", False):
                    vbc.kite_retreat = False
                    logger.warning("BlinkKiteRetreat: clear (前线 stalker 不足)")
                return True

            # blink ready 比例
            cd_mgr = getattr(knowledge, "cooldown_manager", None) or getattr(
                knowledge, "_cd_manager", None
            )
            if cd_mgr is None:
                # 没 CD manager 兜底:用 blink 升级研究完了就当 ready(粗略)
                # 实际 sharpy KnowledgeBot 都有 cooldown_manager,这里防 mock 场景
                return True
            ready_count = 0
            for u in forward_stalkers:
                try:
                    if cd_mgr.is_ready(u.tag, AbilityId.EFFECT_BLINK_STALKER):
                        ready_count += 1
                except Exception:
                    pass
            ready_ratio = ready_count / max(1, forward_stalkers.amount)

            # 平均护盾
            avg_shield = (
                sum(u.shield_percentage for u in forward_stalkers) / forward_stalkers.amount
            )

            current_state = bool(getattr(vbc, "kite_retreat", False))

            # 进入撤退:ready 低 + 护盾低
            if not current_state:
                if ready_ratio < _ENTER_READY_RATIO and avg_shield < _SHIELD_LOW:
                    vbc.kite_retreat = True
                    logger.warning(
                        "BlinkKiteRetreat: ENGAGE (ready_ratio=%.0f%% < %.0f%% + avg_shield=%.0f%% < %.0f%%)",
                        ready_ratio * 100,
                        _ENTER_READY_RATIO * 100,
                        avg_shield * 100,
                        _SHIELD_LOW * 100,
                    )
            else:
                # 退出撤退:ready 回升
                if ready_ratio > _EXIT_READY_RATIO:
                    vbc.kite_retreat = False
                    logger.warning(
                        "BlinkKiteRetreat: DISENGAGE (ready_ratio=%.0f%% > %.0f%%)",
                        ready_ratio * 100,
                        _EXIT_READY_RATIO * 100,
                    )
        except Exception as exc:
            logger.debug("BlinkKiteRetreatAct execute fail: %s", exc)
        return True  # non-blocking
