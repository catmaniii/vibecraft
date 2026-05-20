"""TelemetryLogger: 把 SC2 游戏内状态写成机读 telemetry.jsonl。

两路:
- 离散事件 (build_event_record) — 由 common_bot 的 BotAI 钩子调用
- 周期快照 (build_snapshot_record) — 由 common_bot.on_step 每 ~2s 调用一次

record 构造是纯函数(本模块上半部),便于单测;接线在 common_bot。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any


def _xy(pos: Any) -> list[float]:
    """Point2-like → [x, y] float 列表。"""
    return [float(pos.x), float(pos.y)]


def build_event_record(
    t: float,
    kind: str,
    unit: str | None = None,
    tag: int | None = None,
    pos: Any | None = None,
    upgrade: str | None = None,
) -> dict[str, Any]:
    """离散事件 record。kind ∈ building_started/building_complete/
    unit_created/unit_destroyed/upgrade_complete。"""
    rec: dict[str, Any] = {"t": round(float(t), 2), "kind": kind}
    if unit is not None:
        rec["unit"] = unit
    if tag is not None:
        rec["tag"] = int(tag)
    if pos is not None:
        rec["pos"] = _xy(pos)
    if upgrade is not None:
        rec["upgrade"] = upgrade
    return rec


def build_game_start_record(
    t: float, home: Any, enemy_main: Any, natural: Any | None,
    active_recipe: str, my_race: str,
) -> dict[str, Any]:
    """开局 record — 记地图锚点供 verifier 解析命名位置。"""
    return {
        "t": round(float(t), 2),
        "kind": "game_start",
        "home": _xy(home),
        "enemy_main": _xy(enemy_main),
        "natural": _xy(natural) if natural is not None else None,
        "active_recipe": active_recipe,
        "my_race": my_race,
    }


def build_snapshot_record(
    t: float, supply_used: int, supply_cap: int, workers: int, army_supply: int,
    minerals: int, vespene: int, bases: int, army_center: Any | None,
    units: dict[str, int], buildings: dict[str, int], key_units: dict[str, list[Any]],
    active_recipe: str,
) -> dict[str, Any]:
    """周期快照 record。"""
    return {
        "t": round(float(t), 2),
        "kind": "snapshot",
        "supply_used": int(supply_used),
        "supply_cap": int(supply_cap),
        "workers": int(workers),
        "army_supply": int(army_supply),
        "minerals": int(minerals),
        "vespene": int(vespene),
        "bases": int(bases),
        "army_center": _xy(army_center) if army_center is not None else None,
        "units": {k: int(v) for k, v in units.items()},
        "buildings": {k: int(v) for k, v in buildings.items()},
        "key_units": {k: [_xy(p) for p in v] for k, v in key_units.items()},
        "active_recipe": active_recipe,
    }


# -----------------------------------------------------------------------
# TelemetryLogger：采集 + 写 telemetry（接线在 common_bot）
# -----------------------------------------------------------------------

_logger = logging.getLogger(__name__)

# 周期快照间隔（game-second）
_SNAPSHOT_INTERVAL_S: float = 2.0

# 快照里 units 计数的固定单位集（神族为主，含三族常见单位）
_SNAPSHOT_UNIT_TYPES: tuple[str, ...] = (
    "PROBE", "ZEALOT", "STALKER", "SENTRY", "ADEPT", "DARKTEMPLAR",
    "HIGHTEMPLAR", "ARCHON", "IMMORTAL", "COLOSSUS", "WARPPRISM",
    "OBSERVER", "VOIDRAY", "PHOENIX", "CARRIER", "TEMPEST", "MOTHERSHIP",
)
# 快照里建筑计数的固定建筑集（神族为主，含三族常见。verifier building_count 用）
_SNAPSHOT_BUILDING_TYPES: tuple[str, ...] = (
    "NEXUS", "PYLON", "GATEWAY", "WARPGATE", "CYBERNETICSCORE", "ASSIMILATOR",
    "FORGE", "TWILIGHTCOUNCIL", "ROBOTICSFACILITY", "STARGATE", "DARKSHRINE",
    "TEMPLARARCHIVE", "ROBOTICSBAY", "FLEETBEACON", "PHOTONCANNON", "SHIELDBATTERY",
)
# 快照里要记坐标的关键单位
_KEY_UNIT_TYPES: tuple[str, ...] = ("WARPPRISM", "WARPPRISMPHASING")


class TelemetryLogger:
    """采集 + 写 telemetry。sink_fn 接收一个 dict record(通常 = session.log 的偏函数)。"""

    def __init__(
        self,
        sink_fn: Callable[[dict], None],
        snapshot_interval_s: float = _SNAPSHOT_INTERVAL_S,
    ) -> None:
        self._sink = sink_fn
        self._snapshot_interval_s = snapshot_interval_s
        self._last_snapshot_t: float = -1000.0

    def write_event(self, record: dict) -> None:
        """离散事件直接落盘,不节流。"""
        try:
            self._sink(record)
        except Exception as exc:
            _logger.warning("telemetry write_event fail: %s", exc)

    def maybe_write_snapshot(self, now: float, record: dict) -> None:
        """节流:距上次 snapshot >= interval 才写。"""
        if now - self._last_snapshot_t < self._snapshot_interval_s:
            return
        self._last_snapshot_t = now
        try:
            self._sink(record)
        except Exception as exc:
            _logger.warning("telemetry snapshot fail: %s", exc)
