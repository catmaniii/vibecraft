"""sharpy PlanZoneAttack 的 vibecraft 子类。

优先读 `knowledge.vibecraft.{attack_target_override, combat_intent_override}`
（由 SharpyFacade.set_attack_target_override / set_combat_intent_override 写入），
无覆盖时走 sharpy 默认决策。

L2 attack/defend/hold/retreat/vision 指令的执行端点 —— Director 调 facade，
facade 写 knowledge namespace，本类下一 tick 在 _get_target / _should_attack 内读到。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sc2.position import Point2
from sharpy.plans.tactics import PlanZoneAttack

if TYPE_CHECKING:
    pass  # ExtendedPower import 留给未来（sharpy 路径未稳定）


class VibeCraftZoneAttack(PlanZoneAttack):  # type: ignore[misc]
    """覆盖 sharpy 默认 attack target / should_attack 决策。"""

    def _get_target(self) -> Any:
        override = getattr(self.knowledge.vibecraft, "attack_target_override", None)
        if override is not None:
            # tuple → Point2；已经是 Point2 直接返回
            if isinstance(override, tuple) and len(override) == 2:
                return Point2(override)
            return override
        return super()._get_target()

    def _should_attack(self, power: Any) -> bool:
        """优先读 combat_intent_override；无 override 透传给 sharpy 默认逻辑。

        sharpy 父类签名：_should_attack(self, power: ExtendedPower) -> bool。
        power 用 Any 避免 sharpy 无 stub 时的 import 问题。
        """
        intent = getattr(self.knowledge.vibecraft, "combat_intent_override", None)
        if intent == "attack":
            return True
        if intent in ("defend", "hold", "retreat", "vision"):
            return False
        return super()._should_attack(power)  # type: ignore[no-any-return]
