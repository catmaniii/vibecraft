"""单位类别判据 —— **不要再用 `unit.is_worker`,python-sc2 没有这个属性**。

踩坑(2026-07-27 真机查出)：代码里多处写 `getattr(u, "is_worker", False)` 或 `u.is_worker`
判"这是不是农民"。但 `sc2.unit.Unit` **根本没有 `is_worker`**（只有 `is_mine` /
`is_mineral_field` / `is_carrying_minerals` 等），且它没有 `__getattr__` 兜底：

  - 写成 `getattr(u, "is_worker", False)` → **恒 False**，农民永远查不出来，代码静默走错分支；
  - 写成 `u.is_worker` → 每次 **AttributeError**，被外层 `contextlib.suppress` / on_step 兜底
    吞掉，整段逻辑静默失效。

真机症状：坑道虫钻出后 `tgt=worker` 恒 0%（从不扑农民、只拆建筑打军队），而 telemetry 的
`enemy_workers_harassed` 却有几十——因为后者走的是**伤害回调**、不看这个假属性，两边对不上。

判据统一走单位类型 id（这是引擎真实存在的字段）。新增"是不是农民/是不是军队"的判断一律用本模块。
"""

from __future__ import annotations

from typing import Any

from sc2.ids.unit_typeid import UnitTypeId

WORKER_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE, UnitTypeId.MULE}
)
# 比对用的是**名字**不是 enum 成员本身：测试 conftest 会重导 sc2 模块，重导后同名 enum 成员
# 分属两个不同的类、`in` 比对为假（全量跑时才复现的假失败）。按名字比免疫这种身份问题，
# 语义完全一致。
_WORKER_NAMES: frozenset[str] = frozenset(t.name for t in WORKER_TYPES)


def _type_name(unit: Any) -> str:
    try:
        return str(unit.type_id.name)
    except Exception:
        return ""


def is_worker(unit: Any) -> bool:
    """这个单位是不是农民（含 MULE）。取不到 type_id 一律当"不是"。"""
    return _type_name(unit) in _WORKER_NAMES


def is_army(unit: Any) -> bool:
    """这个单位是不是"能打的机动部队"：非农民、非建筑。"""
    name = _type_name(unit)
    if not name or name in _WORKER_NAMES:
        return False
    # is_structure 真 Unit 一定有;取不到时当"不是建筑"(别因为缺一个字段就让整条判据失效)
    return not bool(getattr(unit, "is_structure", False))
