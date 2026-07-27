"""单位类别判据单测 + **禁用假属性 `is_worker` 的静态审计**（2026-07-27 真机查出的坑）。

背景：代码里多处用 `unit.is_worker` 判农民，但 `sc2.unit.Unit` **没有这个属性**，且没有
`__getattr__` 兜底：写成 `getattr(u, "is_worker", False)` 恒 False、写成 `u.is_worker` 直接
AttributeError 被外层吞掉。两种写法都让"找农民"的逻辑静默失效（真机症状：坑道虫钻出后
`tgt=worker` 恒 0%，从不扑农民）。

这里两道门：
  ① 行为门：`is_worker` / `is_army` 对三族农民、军队、建筑判得对。
  ② **静态门**：全仓 src/ 不许再出现 `.is_worker` / "is_worker" 字面量的属性用法。行为门
     只能证明新 helper 对，挡不住别处再写一遍那个假属性 —— 这是那类 bug 的复发点。
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from sc2.ids.unit_typeid import UnitTypeId

from vibecraft.bot.unit_kind import is_army, is_worker

_SRC = Path(__file__).resolve().parents[2] / "src" / "vibecraft"


def _u(type_id: UnitTypeId, structure: bool = False) -> SimpleNamespace:
    return SimpleNamespace(type_id=type_id, is_structure=structure)


@pytest.mark.parametrize(
    "tid", [UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE, UnitTypeId.MULE]
)
def test_is_worker_true_for_all_races(tid: UnitTypeId) -> None:
    assert is_worker(_u(tid)) is True


@pytest.mark.parametrize(
    "tid", [UnitTypeId.ZERGLING, UnitTypeId.STALKER, UnitTypeId.MARINE, UnitTypeId.QUEEN]
)
def test_is_worker_false_for_army(tid: UnitTypeId) -> None:
    assert is_worker(_u(tid)) is False


def test_is_worker_false_when_no_type_id() -> None:
    """拿不到 type_id 一律当"不是农民"，不能抛异常（调用点常在 suppress 里）。"""
    assert is_worker(SimpleNamespace()) is False
    assert is_army(SimpleNamespace()) is False


def test_is_army_excludes_workers_and_structures() -> None:
    assert is_army(_u(UnitTypeId.ZERGLING)) is True
    assert is_army(_u(UnitTypeId.DRONE)) is False
    assert is_army(_u(UnitTypeId.HATCHERY, structure=True)) is False


def test_sc2_unit_really_has_no_is_worker() -> None:
    """钉住这个坑的前提：python-sc2 的 Unit 确实没有 is_worker。

    哪天上游加了这个属性，这条会红 —— 那时可以考虑改回原生写法，但**别默认它存在**。
    """
    from sc2.unit import Unit

    assert not hasattr(Unit, "is_worker"), "上游加了 is_worker？复核 unit_kind 是否还需要"


def test_no_source_uses_fake_is_worker_attribute() -> None:
    """静态门：src/ 里不许再出现 `.is_worker` 或 "is_worker" 字面量的属性用法。

    允许的只有本模块自己的定义/导入（`is_worker(` 函数调用、`import ... is_worker`）。
    """
    pat_attr = re.compile(r"\.is_worker\b")
    pat_str = re.compile(r'["\']is_worker["\']')
    offenders: list[str] = []
    for path in _SRC.rglob("*.py"):
        if path.name == "unit_kind.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), start=1):
            if pat_attr.search(line) or pat_str.search(line):
                offenders.append(f"{path.relative_to(_SRC)}:{i}: {line.strip()}")
    assert not offenders, (
        "这些地方用了 python-sc2 并不存在的 `is_worker` 属性（恒 False 或 AttributeError，"
        "会让找农民的逻辑静默失效）。改用 vibecraft.bot.unit_kind.is_worker：\n  "
        + "\n  ".join(offenders)
    )
