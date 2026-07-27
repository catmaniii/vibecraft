"""菌毯扩张的共用几何 —— 女王种菌毯瘤的选点（敌方家 / 自家前线两处共用）。

女王的菌毯瘤**只能种在菌毯上**，所以选点永远是"当前菌毯的外沿"：
  - 敌方家（`nydus_raid_act`）：坑道虫落地自带一圈菌毯，钻出来的女王往外沿种 → 菌毯朝敌方
    矿线扩，后续增援有视野、单位加速，虫被拆了地盘还在。
  - 自家前线（`spare_queen_act`）：从自家菌毯往最外分矿方向扩，把防线连成一片。
两处只有"往哪个方向优先扩"不同，几何完全一样，故抽在这里，别抄两份。
"""

from __future__ import annotations

import contextlib
import math
from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

CREEP_TUMOR_ENERGY: float = 25.0  # 菌毯瘤耗能
CREEP_TUMOR_TYPES: tuple[UnitTypeId, ...] = (
    UnitTypeId.CREEPTUMOR,
    UnitTypeId.CREEPTUMORBURROWED,
    UnitTypeId.CREEPTUMORQUEEN,
)


def existing_tumors(cache: Any) -> list[Point2]:
    """己方已有菌毯瘤位置（三种形态都算：刚种的 / 潜地的 / 女王种的）。"""
    out: list[Point2] = []
    with contextlib.suppress(Exception):
        for t in CREEP_TUMOR_TYPES:
            out.extend(u.position for u in cache.own(t))
    return out


def pick_creep_tumor_spot(
    ai: Any,
    origin: Point2,
    existing: list[Point2],
    *,
    max_r: int = 6,
    spacing: float = 7.0,
    toward: Point2 | None = None,
) -> Point2 | None:
    """在 `origin` 周围的菌毯**外沿**挑一个可种菌毯瘤的点；挑不到返回 None。

    由远及近扫（先试外沿，扩得最远），每圈按角度取 8 个方向；`toward` 给定时按"离该点更近"
    排序，让菌毯优先朝那个方向长（敌方矿线 / 自家最外分矿）。要求该点在菌毯上、在可放置
    格里、且离已有菌毯瘤 ≥ `spacing`（别挤一起白费能量）。
    """
    for r in range(max_r, 0, -1):
        cands: list[Point2] = []
        for k in range(8):
            ang = math.pi * 2 * k / 8
            cands.append(Point2((origin.x + r * math.cos(ang), origin.y + r * math.sin(ang))))
        if toward is not None:
            cands.sort(key=lambda p: p.distance_to(toward))
        for p in cands:
            try:
                if not ai.has_creep(p) or not ai.in_placement_grid(p):
                    continue
            except Exception:
                continue
            if any(p.distance_to(e) < spacing for e in existing):
                continue
            return p
    return None
