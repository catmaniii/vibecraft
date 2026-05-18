"""保证 plan 文件内的 attack act 实际是 VibeCraftZoneAttack 而非裸 PlanZoneAttack 实例。

调研结论（Step 0）：
  - gate4_pressure.py：有 PlanZoneAttack 实例化，需换成 VibeCraftZoneAttack
  - sustain.py：无 PlanZoneAttack，只有注释提及，跳过
  - forward_proxy.py：无 PlanZoneAttack，跳过
  - 1g_robo_immortal.py / iac_2base.py / skytoss.py：文件不存在，跳过

测试策略：grep 源文件而非 import 模块（避免 sharpy 复杂 init 链 import 失败）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PLANS_DIR = (
    Path(__file__).parent.parent.parent
    / "src"
    / "vibecraft"
    / "bot"
    / "auto_combat"
    / "protoss"
    / "plans"
)

# 需要换的 plan 文件（有 PlanZoneAttack 实例化）
_PLANS_WITH_ATTACK_ACT = [
    "gate4_pressure.py",
    "skytoss.py",
    "robo_1gate.py",
    "iac_2base.py",
    "dt_rush.py",
    "phoenix_2base.py",
    "blink_stalker.py",
]


@pytest.mark.parametrize("plan_file", _PLANS_WITH_ATTACK_ACT)
def test_plan_uses_vibecraft_zone_attack_not_bare_plan(plan_file: str) -> None:
    """plan 源文件应 import VibeCraftZoneAttack 且不直接实例化裸 PlanZoneAttack。"""
    src_path = _PLANS_DIR / plan_file
    assert src_path.exists(), f"plan 文件不存在: {src_path}"
    src = src_path.read_text(encoding="utf-8")

    assert "VibeCraftZoneAttack" in src, (
        f"{plan_file} 未引用 VibeCraftZoneAttack（应 import 并实例化子类）"
    )
    assert "PlanZoneAttack(" not in src, (
        f"{plan_file} 仍直接实例化 PlanZoneAttack(...)（应换成 VibeCraftZoneAttack(...)）"
    )
