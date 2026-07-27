"""VibeCraftMicroZealots：去掉 sharpy 叉子 group 级聚团。

问题（用户 2026-06-02）
======================
sharpy ``MicroZealots.group_solve_combat`` 在 Assault / SearchAndDestroy 等行军
move_type 下，只要 ``engage_ratio > 0.25`` 就把整团叉子拉向 ``closest_group.center``
（团重心）。行军逼近敌人时前排叉子进入敌方射程 → engage_ratio 反复 >0.25 → 整团
每帧回缩重心 → 前排冲不进去，表现为"行军中不停把叉子聚团"。

（engage_ratio 含义见 default_micro_methods.init_group：某叉子落在任一敌人射程内即
计入 engage_count，engage_ratio = engage_count / 单位数。前排 1/4 进射程即 >0.25。）

修复
====
叉子是近战冲锋单位，直接 attack-move 推进即可，不需要 group 级聚团。重写
``group_solve_combat`` 统一返回 ``current_command``（= 上层 combat manager 给的推进
目标）。其余行为（``unit_solve_combat`` 的冲锋 NoAction / melee_focus_fire / Push
前压）全部继承 ``MicroZealots``，不变。

注入
====
common_bot 在神族 bot on_start 时::

    self.combat.rules.unit_micros[UnitTypeId.ZEALOT] = VibeCraftMicroZealots()

走和 ``VibeCraftMicroDarkTemplar`` 一样的 subclass-swap 模式（不改 vendor sharpy）。
"""

from __future__ import annotations

from sc2.units import Units
from sharpy.combat import Action
from sharpy.combat.protoss.micro_zealots import MicroZealots


class VibeCraftMicroZealots(MicroZealots):  # type: ignore[misc]
    """叉子 micro：继承 sharpy MicroZealots，但去掉 group 级聚团（回缩重心）。"""

    def group_solve_combat(self, units: Units, current_command: Action) -> Action:
        # 去掉原版"engage_ratio>0.25 → Action(closest_group.center) 整团回缩重心"的聚团。
        # 行军逼近时前排进敌方射程反复触发，叉子永远被拉回重心冲不进 → 直接推进即可。
        # retreat/push 等其它 move_type 原版本就 return current_command，这里统一返回。
        return current_command
