"""兵种 roster 静态提取（M7 准入闸，2026-06-15 用户）。

硬约束：优化变体**不引入/不删除兵种类型**。校验 = 静态读 build 的 plan 代码，提取它训练的
army 兵种集合；变体改完静态 diff，roster 变了 → 跑 A/B 前直接否决（不实测）。

army 判定（离线，无需游戏）：用 sc2.dicts.unit_trained_from —
- 可训练（在 UNIT_TRAINED_FROM 里）
- 且生产者不是农民（农民造的 = 建筑，排除）
- 且自身不是农民/补给/larva 等非战斗单位
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[3]


def _army_unit_names() -> set[str]:
    """SC2 全部 army 兵种的 UnitTypeId 名集合（离线算一次）。"""
    from sc2.dicts.unit_trained_from import UNIT_TRAINED_FROM
    from sc2.ids.unit_typeid import UnitTypeId

    workers = {UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE}
    non_army = workers | {
        UnitTypeId.OVERLORD,
        UnitTypeId.OVERLORDTRANSPORT,
        UnitTypeId.OVERSEER,
        UnitTypeId.LARVA,
        UnitTypeId.EGG,
        UnitTypeId.MULE,
        UnitTypeId.BROODLING,
        UnitTypeId.CHANGELING,
    }
    out: set[str] = set()
    for unit, producers in UNIT_TRAINED_FROM.items():
        if unit in non_army:
            continue
        if producers & workers:  # 农民造的 = 建筑
            continue
        out.add(unit.name)
    return out


def _plan_source_path(sharpy_dummy_class: str) -> Path | None:
    """ "pkg.mod:Class" → 模块源文件路径。"""
    mod = sharpy_dummy_class.split(":", 1)[0]
    rel = Path(*mod.split(".")).with_suffix(".py")
    p = _ROOT / "src" / rel
    return p if p.exists() else None


def unit_roster(strategy_id: str, root: Path | None = None) -> set[str]:
    """提取一个 build 训练的 army 兵种集合（UnitTypeId 名，大写）。

    读 strategy YAML 的 sharpy_dummy_class → 扫 plan 源码里的 UnitTypeId.X →
    与全部 army 兵种取交集（过滤掉建筑/农民/补给 + 非训练的偶发引用）。
    找不到 plan 源 → 返回空集（调用方据此降级：跳过 roster 闸或报警）。
    """
    base = root or _ROOT
    yaml_path = None
    for race in ("protoss", "zerg", "terran"):
        cand = base / "strategies" / race / f"{strategy_id}.yaml"
        if cand.exists():
            yaml_path = cand
            break
    if yaml_path is None:
        return set()
    data: dict[str, Any] = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    sdc = data.get("sharpy_dummy_class", "")
    src = _plan_source_path(sdc) if sdc else None
    if src is None:
        return set()
    text = src.read_text(encoding="utf-8")
    referenced = set(re.findall(r"UnitTypeId\.([A-Z_0-9]+)", text))
    return referenced & _army_unit_names()


def roster_diff(base_id: str, variant_id: str, root: Path | None = None) -> dict[str, set[str]]:
    """变体 vs 原版的 roster 差异。added/removed 任一非空 → M7 否决。"""
    base_r = unit_roster(base_id, root)
    var_r = unit_roster(variant_id, root)
    return {"added": var_r - base_r, "removed": base_r - var_r, "base": base_r, "variant": var_r}
