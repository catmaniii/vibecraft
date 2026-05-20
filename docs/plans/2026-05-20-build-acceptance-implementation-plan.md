# Build Order 验收测试框架 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 建一套自动验收 build order 的框架 —— always-on 结构化 telemetry log +
acceptance spec + 自动跑 non-realtime SC2 的 runner/verifier，让 build order 的执行
情况可机器判定。

**Architecture:** 三层。(1) `TelemetryLogger` 把游戏内状态写成机读 `telemetry.jsonl`
（离散事件来自 common_bot 已有的 BotAI 钩子，周期快照由 common_bot.on_step 驱动）。
(2) `build_acceptance` 包：spec 模型/loader + verifier，纯函数可单测。(3) `runner`
脚本 spawn SC2、区分 infra-fail / acceptance-fail、调 verifier 出报告。

**Tech Stack:** python-sc2 BotAI 钩子、现有 `GameSession`/`JsonlSink`、现有
`GameProcess` spawn 机制、pydantic、pytest。

**设计文档:** `docs/plans/2026-05-20-build-acceptance-testing-design.md`（§1-§5）。

**实现层面偏离说明:** 设计文档 §1 写"GameTelemetryLogger 是一个 sharpy act"。
实施改为 **common_bot 的方法调用**（钩子 + on_step），不是 sharpy act —— 原因:
(a) sharpy act 放 SequentialList 里有 return-value 阻断后续 act 的坑（本项目踩过
多次）；(b) act 要每个 plan 的 create_plan 手动挂，易漏；(c) common_bot 是所有
race bot 的公共基类，所有 plan 必经，零漏挂。属 CLAUDE.md 允许的实现层面选择。

---

## Task 1: LogStream.TELEMETRY 枚举

新增一个 jsonl 流。`GameSession.__init__` 用 `for stream in LogStream` 自动为每个
枚举值建 sink，所以加一个枚举值就自动产出 `logs/game_*/telemetry.jsonl`。

**Files:**
- Modify: `src/vibecraft/logging_/types.py`（`LogStream` 枚举，约 line 15-23）
- Test: `tests/unit/test_logging_streams.py`（新建）

**Step 1: 写 failing test**

`tests/unit/test_logging_streams.py`:
```python
"""LogStream 枚举包含 telemetry 流。"""
from __future__ import annotations

from vibecraft.logging_.types import LogStream


def test_telemetry_stream_exists():
    assert LogStream.TELEMETRY.value == "telemetry"


def test_game_session_creates_telemetry_sink(tmp_path):
    """GameSession 自动为 TELEMETRY 建一个 sink。"""
    from vibecraft.logging_.session import GameSession, GameSessionConfig

    session = GameSession(GameSessionConfig(root_dir=tmp_path, game_id="t1"))
    session.log(LogStream.TELEMETRY, {"kind": "ping"})
    session.close()
    tel = tmp_path / "t1" / "telemetry.jsonl"
    assert tel.exists()
    assert "ping" in tel.read_text(encoding="utf-8")
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_logging_streams.py -v`
Expected: FAIL — `AttributeError: TELEMETRY`。

注意: `GameSessionConfig` 的字段名先读 `src/vibecraft/logging_/session.py` 确认
（`root_dir` / `game_id` 等），如不一致照实改 test。

**Step 3: 加枚举值**

`src/vibecraft/logging_/types.py` 的 `LogStream` 末尾加一行:
```python
    TELEMETRY = "telemetry"
```

**Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_logging_streams.py -v`
Expected: PASS ×2。

**Step 5: Commit**

```bash
git add src/vibecraft/logging_/types.py tests/unit/test_logging_streams.py
git commit -m "feat(telemetry): 加 LogStream.TELEMETRY jsonl 流"
```

---

## Task 2: TelemetryLogger — record 构造（纯函数）

`TelemetryLogger` 把游戏对象转成 telemetry record dict。本 task 只写**纯转换函数**
（输入 mock 单位/bot，输出 dict），不碰 SC2 / session。下一 task 才接线。

**Files:**
- Create: `src/vibecraft/bot/telemetry.py`
- Test: `tests/unit/test_telemetry.py`

**Step 1: 写 failing test**

`tests/unit/test_telemetry.py`:
```python
"""TelemetryLogger record 构造纯函数测试。"""
from __future__ import annotations

from types import SimpleNamespace

from vibecraft.bot.telemetry import (
    build_event_record,
    build_game_start_record,
    build_snapshot_record,
)


def _pt(x, y):
    return SimpleNamespace(x=x, y=y)


def test_event_record_building():
    rec = build_event_record(
        t=18.3, kind="building_started", unit="GATEWAY", tag=123, pos=_pt(94.4, 104.4)
    )
    assert rec == {
        "t": 18.3, "kind": "building_started", "unit": "GATEWAY",
        "tag": 123, "pos": [94.4, 104.4],
    }


def test_event_record_upgrade_no_pos():
    rec = build_event_record(t=211.0, kind="upgrade_complete", upgrade="WARPGATERESEARCH")
    assert rec == {"t": 211.0, "kind": "upgrade_complete", "upgrade": "WARPGATERESEARCH"}


def test_game_start_record():
    rec = build_game_start_record(
        t=0.0, home=_pt(127.5, 119.5), enemy_main=_pt(48.5, 28.5),
        natural=_pt(145.5, 98.5), active_recipe="dt_drop_iac", my_race="Protoss",
    )
    assert rec["kind"] == "game_start"
    assert rec["home"] == [127.5, 119.5]
    assert rec["enemy_main"] == [48.5, 28.5]
    assert rec["natural"] == [145.5, 98.5]
    assert rec["active_recipe"] == "dt_drop_iac"


def test_snapshot_record():
    rec = build_snapshot_record(
        t=120.0, supply_used=24, supply_cap=39, workers=22, army_supply=4,
        minerals=150, vespene=80, bases=2, army_center=_pt(100, 110),
        units={"STALKER": 2, "ZEALOT": 0},
        key_units={"WARPPRISM": [_pt(114, 115)]},
        active_recipe="dt_drop_iac",
    )
    assert rec["kind"] == "snapshot"
    assert rec["army_center"] == [100.0, 110.0]
    assert rec["units"] == {"STALKER": 2, "ZEALOT": 0}
    assert rec["key_units"] == {"WARPPRISM": [[114.0, 115.0]]}
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_telemetry.py -v`
Expected: FAIL — `ModuleNotFoundError: vibecraft.bot.telemetry`。

**Step 3: 实现 record 构造函数**

`src/vibecraft/bot/telemetry.py`:
```python
"""TelemetryLogger: 把 SC2 游戏内状态写成机读 telemetry.jsonl。

两路:
- 离散事件 (build_event_record) — 由 common_bot 的 BotAI 钩子调用
- 周期快照 (build_snapshot_record) — 由 common_bot.on_step 每 ~2s 调用一次

record 构造是纯函数(本模块上半部),便于单测;接线在 common_bot。
"""

from __future__ import annotations

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
    units: dict[str, int], key_units: dict[str, list[Any]],
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
        "key_units": {k: [_xy(p) for p in v] for k, v in key_units.items()},
        "active_recipe": active_recipe,
    }
```

**Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_telemetry.py -v`
Expected: PASS ×4。

**Step 5: Commit**

```bash
git add src/vibecraft/bot/telemetry.py tests/unit/test_telemetry.py
git commit -m "feat(telemetry): TelemetryLogger record 构造纯函数"
```

---

## Task 3: TelemetryLogger 采集类 + common_bot 接线

加一个 `TelemetryLogger` 类负责"从 bot 读状态 + 写 session"，并在 common_bot 的
钩子/on_step 里接线。这部分是集成代码（依赖真实 bot 对象），单测靠 mock，最终靠
smoke test 验证编译。

**Files:**
- Modify: `src/vibecraft/bot/telemetry.py`（加 `TelemetryLogger` 类）
- Modify: `src/vibecraft/bot/auto_combat/common_bot.py`
  - `on_start`（约 line 615）—— init telemetry + 写 game_start
  - `on_step`（约 line 734）—— 每 ~2s 调 snapshot
  - 5 个钩子（约 line 896-948）—— 写离散事件
- Test: `tests/unit/test_telemetry.py`（追加 `TelemetryLogger` 测试）

**Step 1: 写 failing test（TelemetryLogger 类 — snapshot 节流 + 写 sink）**

追加到 `tests/unit/test_telemetry.py`:
```python
def test_telemetry_logger_snapshot_throttle():
    """maybe_snapshot 每 ~2s 才真正写一次。"""
    from vibecraft.bot.telemetry import TelemetryLogger

    written: list[dict] = []
    tl = TelemetryLogger(sink_fn=written.append, snapshot_interval_s=2.0)
    snap = {"kind": "snapshot", "t": 0.0}
    tl.maybe_write_snapshot(now=0.0, record=snap)      # 第一次:写
    tl.maybe_write_snapshot(now=1.0, record=snap)      # 1s:节流跳过
    tl.maybe_write_snapshot(now=2.5, record=snap)      # 2.5s:写
    assert len(written) == 2


def test_telemetry_logger_event_passthrough():
    """write_event 直接落 sink,不节流。"""
    from vibecraft.bot.telemetry import TelemetryLogger

    written: list[dict] = []
    tl = TelemetryLogger(sink_fn=written.append)
    tl.write_event({"kind": "building_started", "t": 1.0})
    tl.write_event({"kind": "building_complete", "t": 2.0})
    assert len(written) == 2
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_telemetry.py::test_telemetry_logger_snapshot_throttle -v`
Expected: FAIL — `TelemetryLogger` 不存在。

**Step 3: 实现 TelemetryLogger 类**

追加到 `src/vibecraft/bot/telemetry.py`:
```python
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

# 周期快照间隔（game-second）
_SNAPSHOT_INTERVAL_S: float = 2.0

# 快照里 units 计数的固定单位集（神族为主，含三族常见单位）
_SNAPSHOT_UNIT_TYPES: tuple[str, ...] = (
    "PROBE", "ZEALOT", "STALKER", "SENTRY", "ADEPT", "DARKTEMPLAR",
    "HIGHTEMPLAR", "ARCHON", "IMMORTAL", "COLOSSUS", "WARPPRISM",
    "OBSERVER", "VOIDRAY", "PHOENIX", "CARRIER", "TEMPEST", "MOTHERSHIP",
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
        except Exception as exc:  # noqa: BLE001
            logger.warning("telemetry write_event fail: %s", exc)

    def maybe_write_snapshot(self, now: float, record: dict) -> None:
        """节流:距上次 snapshot >= interval 才写。"""
        if now - self._last_snapshot_t < self._snapshot_interval_s:
            return
        self._last_snapshot_t = now
        try:
            self._sink(record)
        except Exception as exc:  # noqa: BLE001
            logger.warning("telemetry snapshot fail: %s", exc)
```

**Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_telemetry.py -v`
Expected: PASS（全部 6 个）。

**Step 5: 接线 common_bot —— on_start 初始化**

在 `common_bot.py` 的 `on_start`（约 line 615，`super().on_start()` 之后、设
`knowledge.vibecraft` 附近）加:
```python
# telemetry: always-on 游戏内状态采集(项目开发期默认开)
self._telemetry = None
try:
    import contextlib
    from functools import partial

    from vibecraft.bot.telemetry import TelemetryLogger, build_game_start_record
    from vibecraft.logging_.types import LogStream

    session = getattr(self.director, "session", None) if self.director else None
    if session is not None:
        self._telemetry = TelemetryLogger(
            sink_fn=partial(session.log, LogStream.TELEMETRY)
        )
        # game_start record:home / enemy_main / natural 锚点
        home = self.start_location
        enemy_main = self.enemy_start_locations[0]
        natural = None
        with contextlib.suppress(Exception):
            exps = list(self.expansion_locations_list)
            cands = sorted(exps, key=lambda p: p.distance_to(home))
            for p in cands:
                if p.distance_to(home) > 1.0:
                    natural = p
                    break
        self._telemetry.write_event(
            build_game_start_record(
                t=float(self.time), home=home, enemy_main=enemy_main,
                natural=natural, active_recipe=str(getattr(self, "active_recipe", "")),
                my_race=str(self.race).rsplit(".", 1)[-1],
            )
        )
except Exception as exc:  # noqa: BLE001
    logger.warning("telemetry init fail: %s", exc)
```
注意: `self.director` 在 on_start 里此刻可能尚未创建（director 在 on_start
后段才 `director_factory(...)`）。**把这段 telemetry init 挪到 director 创建之后**
（common_bot.py 约 line 651 `self.director = director_factory(...)` 之后）。执行时
先读 common_bot 确认 director 创建的确切行，把 init 放其后。

**Step 6: 接线 common_bot —— on_step 周期快照**

在 `on_step`（约 line 734）末尾、`super().on_step(...)` 之前加:
```python
if getattr(self, "_telemetry", None) is not None:
    import contextlib
    with contextlib.suppress(Exception):
        self._write_telemetry_snapshot()
```
并在 bot 类里加方法 `_write_telemetry_snapshot`（从 `self` 读状态 → 调
`build_snapshot_record` → `self._telemetry.maybe_write_snapshot`）:
```python
def _write_telemetry_snapshot(self) -> None:
    from vibecraft.bot.telemetry import (
        _KEY_UNIT_TYPES, _SNAPSHOT_UNIT_TYPES, build_snapshot_record,
    )
    from sc2.ids.unit_typeid import UnitTypeId

    now = float(self.time)
    units_count: dict[str, int] = {}
    for name in _SNAPSHOT_UNIT_TYPES:
        ut = getattr(UnitTypeId, name, None)
        units_count[name] = self.units(ut).amount if ut is not None else 0
    key_units: dict[str, list] = {}
    for name in _KEY_UNIT_TYPES:
        ut = getattr(UnitTypeId, name, None)
        if ut is not None:
            ku = self.units(ut)
            if ku:
                key_units[name] = [u.position for u in ku]
    army = self.units.exclude_type(
        {UnitTypeId.PROBE, UnitTypeId.OBSERVER, UnitTypeId.WARPPRISM}
    ).filter(lambda u: not u.is_structure)
    army_center = army.center if army else None
    army_supply = max(0, int(self.supply_army))
    rec = build_snapshot_record(
        t=now, supply_used=int(self.supply_used), supply_cap=int(self.supply_cap),
        workers=int(self.supply_workers), army_supply=army_supply,
        minerals=int(self.minerals), vespene=int(self.vespene),
        bases=int(self.townhalls.amount), army_center=army_center,
        units=units_count, key_units=key_units,
        active_recipe=str(getattr(self, "active_recipe", "")),
    )
    self._telemetry.maybe_write_snapshot(now, rec)
```

**Step 7: 接线 common_bot —— 5 个离散事件钩子**

在已有的 5 个钩子里各加一行写事件。例 `on_building_construction_started`（约 line 935）:
```python
async def on_building_construction_started(self, unit: Any) -> None:
    _publish_building_started(self, unit)
    self._tel_event("building_started", unit)
    if hasattr(super(), "on_building_construction_started"):
        await super().on_building_construction_started(unit)
```
同样在 `on_building_construction_complete` → `_tel_event("building_complete", unit)`、
`on_unit_created` → `_tel_event("unit_created", unit)`、
`on_unit_destroyed` → `_tel_event_destroyed(unit_tag)`、
`on_upgrade_complete` → `_tel_event_upgrade(upgrade)`。
并加 3 个 helper:
```python
def _tel_event(self, kind: str, unit: Any) -> None:
    tel = getattr(self, "_telemetry", None)
    if tel is None or getattr(unit, "alliance", 1) != 1:
        return  # 只记己方
    import contextlib
    with contextlib.suppress(Exception):
        from vibecraft.bot.telemetry import build_event_record
        tel.write_event(build_event_record(
            t=float(self.time), kind=kind,
            unit=str(unit.type_id).rsplit(".", 1)[-1],
            tag=int(unit.tag), pos=unit.position,
        ))

def _tel_event_destroyed(self, unit_tag: int) -> None:
    tel = getattr(self, "_telemetry", None)
    if tel is None:
        return
    import contextlib
    with contextlib.suppress(Exception):
        from vibecraft.bot.telemetry import build_event_record
        tel.write_event(build_event_record(
            t=float(self.time), kind="unit_destroyed", tag=int(unit_tag),
        ))

def _tel_event_upgrade(self, upgrade: Any) -> None:
    tel = getattr(self, "_telemetry", None)
    if tel is None:
        return
    import contextlib
    with contextlib.suppress(Exception):
        from vibecraft.bot.telemetry import build_event_record
        tel.write_event(build_event_record(
            t=float(self.time), kind="upgrade_complete",
            upgrade=str(upgrade).rsplit(".", 1)[-1],
        ))
```

**Step 8: 跑测试 + lint**

```bash
uv run pytest tests/unit/test_telemetry.py tests/unit/test_plan_create_plan_smoke.py -v
uv run ruff check src/vibecraft/bot/telemetry.py src/vibecraft/bot/auto_combat/common_bot.py
```
Expected: telemetry 测试全 PASS；smoke 测试全 PASS（编译没坏）；lint 干净。

**Step 9: Commit**

```bash
git add src/vibecraft/bot/telemetry.py src/vibecraft/bot/auto_combat/common_bot.py tests/unit/test_telemetry.py
git commit -m "feat(telemetry): TelemetryLogger 采集类 + common_bot 钩子/on_step 接线"
```

---

## Task 4: Acceptance spec 模型 + loader

`tests/build_acceptance/<id>.yaml` 的 pydantic 模型 + yaml loader。

**Files:**
- Create: `src/vibecraft/build_acceptance/__init__.py`（空）
- Create: `src/vibecraft/build_acceptance/spec.py`
- Test: `tests/unit/test_build_acceptance_spec.py`

**Step 1: 写 failing test**

`tests/unit/test_build_acceptance_spec.py`:
```python
"""Acceptance spec 模型 + loader。"""
from __future__ import annotations

import pytest

from vibecraft.build_acceptance.spec import AcceptanceSpec, parse_mmss


def test_parse_mmss():
    assert parse_mmss("0:35") == 35.0
    assert parse_mmss("3:14") == 194.0
    assert parse_mmss("10:06") == 606.0


def test_spec_loads_from_dict():
    spec = AcceptanceSpec.model_validate({
        "strategy_id": "demo",
        "my_race": "Protoss",
        "checks": [
            {"id": "g1", "type": "building_started", "unit": "GATEWAY", "by": "0:35"},
            {"id": "ds", "type": "building_complete", "unit": "DARKSHRINE",
             "at": "3:14", "tol": 25},
        ],
    })
    assert spec.strategy_id == "demo"
    assert len(spec.checks) == 2
    # by/at 解析成秒
    assert spec.checks[0].by_s == 35.0
    assert spec.checks[1].at_s == 194.0
    assert spec.checks[1].tol == 25


def test_spec_check_needs_at_or_by():
    with pytest.raises(ValueError):
        AcceptanceSpec.model_validate({
            "strategy_id": "demo", "my_race": "Protoss",
            "checks": [{"id": "bad", "type": "building_started", "unit": "GATEWAY"}],
        })
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_build_acceptance_spec.py -v`
Expected: FAIL — 模块不存在。

**Step 3: 实现 spec 模型**

`src/vibecraft/build_acceptance/__init__.py`: 空文件。

`src/vibecraft/build_acceptance/spec.py`:
```python
"""Acceptance spec 模型 + yaml loader。

spec 文件 tests/build_acceptance/<strategy_id>.yaml — 每个 build 一份,记录
deep research 出的标准 timing 节点。verifier 据此判定 telemetry。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_CHECK_TYPES = (
    "building_started", "building_complete", "upgrade_complete",
    "worker_count", "unit_count", "key_unit_at", "army_gather", "attack_moveout",
)


def parse_mmss(s: str) -> float:
    """'M:SS' → 秒。"""
    parts = str(s).split(":")
    if len(parts) != 2:
        raise ValueError(f"时间格式应为 M:SS, got {s!r}")
    return int(parts[0]) * 60 + int(parts[1])


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal[
        "building_started", "building_complete", "upgrade_complete",
        "worker_count", "unit_count", "key_unit_at", "army_gather", "attack_moveout",
    ]
    # 时间:at(窗口中心)或 by(上界),至少一个
    at: str | None = None
    by: str | None = None
    tol: float = 20.0  # at 模式的 ±窗口秒
    # 目标参数(按 type 取用)
    unit: str | None = None
    upgrade: str | None = None
    min: int | None = None
    near: str | None = None       # 命名锚点 home/enemy_main/natural
    within: float | None = None   # 距锚点容差

    @property
    def at_s(self) -> float | None:
        return parse_mmss(self.at) if self.at else None

    @property
    def by_s(self) -> float | None:
        return parse_mmss(self.by) if self.by else None

    @model_validator(mode="after")
    def _need_time(self) -> Check:
        if self.at is None and self.by is None:
            raise ValueError(f"check {self.id}: 必须有 at 或 by")
        return self


class AcceptanceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    my_race: str
    checks: list[Check]


def load_spec(path: str | Path) -> AcceptanceSpec:
    """从 yaml 文件读 AcceptanceSpec。"""
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return AcceptanceSpec.model_validate(raw)
```

**Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_build_acceptance_spec.py -v`
Expected: PASS ×3。

**Step 5: Commit**

```bash
git add src/vibecraft/build_acceptance/ tests/unit/test_build_acceptance_spec.py
git commit -m "feat(build_acceptance): acceptance spec 模型 + yaml loader"
```

---

## Task 5: Verifier — 解析 telemetry 对比 spec

核心逻辑。输入 telemetry record 列表 + spec，输出每条 check 的 pass/fail。纯函数，
完整单测。

**Files:**
- Create: `src/vibecraft/build_acceptance/verifier.py`
- Test: `tests/unit/test_build_acceptance_verifier.py`

**Step 1: 写 failing test**

`tests/unit/test_build_acceptance_verifier.py`:
```python
"""Verifier: telemetry × spec → pass/fail。"""
from __future__ import annotations

from vibecraft.build_acceptance.spec import AcceptanceSpec
from vibecraft.build_acceptance.verifier import verify

_GAME_START = {
    "kind": "game_start", "t": 0.0,
    "home": [127.5, 119.5], "enemy_main": [48.5, 28.5], "natural": [145.5, 98.5],
}


def _spec(checks):
    return AcceptanceSpec.model_validate(
        {"strategy_id": "t", "my_race": "Protoss", "checks": checks}
    )


def test_building_started_by_pass():
    tel = [_GAME_START,
           {"kind": "building_started", "t": 20.0, "unit": "GATEWAY"}]
    spec = _spec([{"id": "g1", "type": "building_started",
                   "unit": "GATEWAY", "by": "0:35"}])
    report = verify(tel, spec, opponent="veryeasy")
    assert report.passed
    assert report.results[0].ok


def test_building_started_by_fail_too_late():
    tel = [_GAME_START,
           {"kind": "building_started", "t": 50.0, "unit": "GATEWAY"}]
    spec = _spec([{"id": "g1", "type": "building_started",
                   "unit": "GATEWAY", "by": "0:35"}])
    report = verify(tel, spec, opponent="veryeasy")
    assert not report.passed
    assert not report.results[0].ok


def test_building_complete_at_window():
    tel = [_GAME_START,
           {"kind": "building_complete", "t": 200.0, "unit": "DARKSHRINE"}]
    # at 3:14 (194s) ± 25 → [169, 219]; 200 命中
    spec = _spec([{"id": "ds", "type": "building_complete",
                   "unit": "DARKSHRINE", "at": "3:14", "tol": 25}])
    assert verify(tel, spec, opponent="veryeasy").passed


def test_worker_count_at():
    tel = [_GAME_START,
           {"kind": "snapshot", "t": 240.0, "workers": 42},
           {"kind": "snapshot", "t": 242.0, "workers": 44}]
    spec = _spec([{"id": "w", "type": "worker_count", "at": "4:00", "min": 40}])
    assert verify(tel, spec, opponent="veryeasy").passed


def test_key_unit_at_near_anchor():
    # 棱镜 4:30 在 enemy_main 25 距内
    tel = [_GAME_START,
           {"kind": "snapshot", "t": 270.0,
            "key_units": {"WARPPRISM": [[55.0, 35.0]]}}]
    spec = _spec([{"id": "p", "type": "key_unit_at", "unit": "WARPPRISM",
                   "at": "4:30", "near": "enemy_main", "within": 25}])
    assert verify(tel, spec, opponent="veryeasy").passed


def test_cheatmoney_skips_position_checks():
    """CheatMoney 档跳过位置类断言(抗压下位置必乱)。"""
    tel = [_GAME_START,
           {"kind": "snapshot", "t": 270.0,
            "key_units": {"WARPPRISM": [[999.0, 999.0]]}}]  # 位置很离谱
    spec = _spec([{"id": "p", "type": "key_unit_at", "unit": "WARPPRISM",
                   "at": "4:30", "near": "enemy_main", "within": 25}])
    report = verify(tel, spec, opponent="cheatmoney")
    # 位置类被 skip → 不算 fail
    assert report.results[0].skipped
    assert report.passed


def test_attack_moveout_detects_army_leaving_home():
    tel = [_GAME_START,
           {"kind": "snapshot", "t": 400.0, "army_center": [120.0, 110.0]},  # 在家
           {"kind": "snapshot", "t": 500.0, "army_center": [70.0, 70.0]}]     # 出门
    spec = _spec([{"id": "out", "type": "attack_moveout", "by": "9:00"}])
    assert verify(tel, spec, opponent="veryeasy").passed
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_build_acceptance_verifier.py -v`
Expected: FAIL — `verifier` 模块不存在。

**Step 3: 实现 verifier**

`src/vibecraft/build_acceptance/verifier.py`:
```python
"""Verifier: 解析 telemetry record 列表,对比 AcceptanceSpec,出 pass/fail。

CheatMoney 档:tol×2 + 跳过位置类断言(key_unit_at/army_gather) +
其余只验 by 类。VeryEasy 档:按 spec 精确判定。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from vibecraft.build_acceptance.spec import AcceptanceSpec, Check

# 部队"离家"判定阈值:army_center 距 home 超过此值算出门
_MOVEOUT_HOME_DIST: float = 60.0
# 位置类 check(CheatMoney 跳过)
_POSITION_TYPES = frozenset({"key_unit_at", "army_gather"})


@dataclass
class CheckResult:
    check_id: str
    ok: bool
    skipped: bool = False
    detail: str = ""


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.ok or r.skipped for r in self.results)

    def summary(self) -> str:
        n_pass = sum(1 for r in self.results if r.ok)
        n_skip = sum(1 for r in self.results if r.skipped)
        n_total = len(self.results)
        lines = [f"{n_pass}/{n_total} passed ({n_skip} skipped)"]
        for r in self.results:
            tag = "SKIP" if r.skipped else ("PASS" if r.ok else "FAIL")
            lines.append(f"  [{tag}] {r.check_id}  {r.detail}")
        return "\n".join(lines)


def _dist(a: list[float], b: list[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _anchors(telemetry: list[dict]) -> dict[str, list[float]]:
    for rec in telemetry:
        if rec.get("kind") == "game_start":
            return {
                "home": rec.get("home"),
                "enemy_main": rec.get("enemy_main"),
                "natural": rec.get("natural"),
            }
    return {}


def _snapshot_at(telemetry: list[dict], t: float) -> dict | None:
    """取 t 时刻最近的 snapshot record。"""
    best: dict | None = None
    best_dt = 1e9
    for rec in telemetry:
        if rec.get("kind") != "snapshot":
            continue
        dt = abs(rec.get("t", -1e9) - t)
        if dt < best_dt:
            best_dt = dt
            best = rec
    return best


def _check_one(
    check: Check, telemetry: list[dict], anchors: dict, tol_mult: float
) -> CheckResult:
    tol = check.tol * tol_mult
    ctype = check.type

    if ctype in ("building_started", "building_complete"):
        evs = [r for r in telemetry
               if r.get("kind") == ctype and r.get("unit") == check.unit]
        if not evs:
            return CheckResult(check.id, False, detail=f"{check.unit} 无 {ctype} 事件")
        actual = min(r["t"] for r in evs)
        return _judge_time(check, actual, tol)

    if ctype == "upgrade_complete":
        evs = [r for r in telemetry
               if r.get("kind") == "upgrade_complete"
               and r.get("upgrade") == check.upgrade]
        if not evs:
            return CheckResult(check.id, False, detail=f"{check.upgrade} 未完成")
        return _judge_time(check, min(r["t"] for r in evs), tol)

    if ctype in ("worker_count", "unit_count"):
        t = check.at_s if check.at_s is not None else check.by_s
        snap = _snapshot_at(telemetry, t)
        if snap is None:
            return CheckResult(check.id, False, detail="无 snapshot")
        if ctype == "worker_count":
            actual = int(snap.get("workers", 0))
        else:
            actual = int(snap.get("units", {}).get(check.unit, 0))
        ok = actual >= (check.min or 0)
        return CheckResult(check.id, ok,
                           detail=f"actual={actual} need>={check.min} @ {t:.0f}s")

    if ctype == "key_unit_at":
        t = check.at_s
        snap = _snapshot_at(telemetry, t)
        anchor = anchors.get(check.near)
        if snap is None or anchor is None:
            return CheckResult(check.id, False, detail="无 snapshot/锚点")
        positions = snap.get("key_units", {}).get(check.unit, [])
        if not positions:
            return CheckResult(check.id, False, detail=f"{check.unit} 不在场")
        nearest = min(_dist(p, anchor) for p in positions)
        ok = nearest <= (check.within or 0)
        return CheckResult(check.id, ok,
                           detail=f"距 {check.near} {nearest:.1f} (need<={check.within})")

    if ctype == "army_gather":
        t = check.at_s
        snap = _snapshot_at(telemetry, t)
        anchor = anchors.get(check.near)
        if snap is None or anchor is None or snap.get("army_center") is None:
            return CheckResult(check.id, False, detail="无 army_center/锚点")
        d = _dist(snap["army_center"], anchor)
        ok = d <= (check.within or 0)
        return CheckResult(check.id, ok,
                           detail=f"army 距 {check.near} {d:.1f}")

    if ctype == "attack_moveout":
        home = anchors.get("home")
        if home is None:
            return CheckResult(check.id, False, detail="无 home 锚点")
        moveout_t: float | None = None
        for rec in sorted(
            (r for r in telemetry if r.get("kind") == "snapshot"),
            key=lambda r: r.get("t", 0),
        ):
            ac = rec.get("army_center")
            if ac and _dist(ac, home) > _MOVEOUT_HOME_DIST:
                moveout_t = rec["t"]
                break
        if moveout_t is None:
            return CheckResult(check.id, False, detail="部队从未出门")
        return _judge_time(check, moveout_t, tol)

    return CheckResult(check.id, False, detail=f"未知 check type {ctype}")


def _judge_time(check: Check, actual: float, tol: float) -> CheckResult:
    """按 at±tol 或 by 上界判定一个时间值。"""
    if check.by_s is not None:
        ok = actual <= check.by_s
        return CheckResult(check.id, ok,
                           detail=f"actual {actual:.0f}s, by {check.by_s:.0f}s")
    lo, hi = check.at_s - tol, check.at_s + tol
    ok = lo <= actual <= hi
    return CheckResult(check.id, ok,
                       detail=f"actual {actual:.0f}s, want {check.at_s:.0f}±{tol:.0f}s")


def verify(
    telemetry: list[dict], spec: AcceptanceSpec, opponent: str = "veryeasy"
) -> Report:
    """主入口。opponent ∈ veryeasy / cheatmoney。"""
    cheat = opponent.lower() == "cheatmoney"
    tol_mult = 2.0 if cheat else 1.0
    anchors = _anchors(telemetry)
    report = Report()
    for check in spec.checks:
        if cheat and check.type in _POSITION_TYPES:
            report.results.append(
                CheckResult(check.id, ok=True, skipped=True,
                            detail="CheatMoney 档跳过位置类")
            )
            continue
        report.results.append(_check_one(check, telemetry, anchors, tol_mult))
    return report
```

**Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_build_acceptance_verifier.py -v`
Expected: PASS ×7。

**Step 5: Commit**

```bash
git add src/vibecraft/build_acceptance/verifier.py tests/unit/test_build_acceptance_verifier.py
git commit -m "feat(build_acceptance): verifier — telemetry × spec 判定"
```

---

## Task 6: Test runner 脚本

`scripts/build_acceptance.py` —— spawn non-realtime SC2、infra-fail 自动 retry、
跑完读 telemetry.jsonl、调 verifier、出报告。这是集成 glue，不写单测（verifier
已单测，runner 靠真实运行验证）。

**Files:**
- Create: `scripts/build_acceptance.py`
- 参考: `scripts/headless_smoke.py`（spawn 模式）、`src/vibecraft/server/game_process.py`
  （`GameConfig` / `GameProcess`）

**Step 1: 读参考文件**

执行前先读 `scripts/headless_smoke.py` 全文 + `game_process.py` 的 `GameConfig`、
`GameProcess.start` / 状态查询 API，确认:
- 怎么拿到本局 `logs/game_*/` 目录路径（telemetry.jsonl 在里面）
- 怎么轮询子进程状态拿到 `crashed` / `error` / game 结束信号
- `VIBECRAFT_FORCE_INITIAL_OPENING` 环境变量强制开局 recipe

**Step 2: 实现 runner**

`scripts/build_acceptance.py` 骨架（细节按 Step 1 实测调整）:
```python
"""Build order 验收 runner。

用法:
    uv run python scripts/build_acceptance.py <strategy_id> [--opponent veryeasy|cheatmoney]

流程:spawn non-realtime SC2 → 跑到 game-time 上限 → 收 telemetry.jsonl →
verifier 判定 → 出报告。infra-fail(watchdog hang / SC2 崩溃)自动 retry ≤3 次。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.build_acceptance.spec import load_spec
from vibecraft.build_acceptance.verifier import verify
from vibecraft.server.game_process import GameConfig, GameProcess

_MAX_INFRA_RETRY = 3
_GAME_TIME_LIMIT_S = 600  # 验收只需覆盖到出门攻击
_OPPONENT_DIFFICULTY = {"veryeasy": "VeryEasy", "cheatmoney": "CheatMoney"}


def _run_one_game(strategy_id: str, opponent: str) -> Path | None:
    """跑一局,返回 telemetry.jsonl 路径;infra-fail 返回 None。"""
    os.environ["VIBECRAFT_FORCE_INITIAL_OPENING"] = strategy_id
    cfg = GameConfig(
        map_name="DaybreakLE",
        opponent_race="Random",
        opponent_difficulty=_OPPONENT_DIFFICULTY[opponent],
        realtime=False,  # non-realtime 全速
    )
    gp = GameProcess()
    gp.start(cfg)
    # 轮询子进程状态直到:游戏结束 / infra-fail / 到 game-time 上限对应的 wall-clock 兜底
    # —— 具体状态查询 API 按 Step 1 实测填。infra-fail(crashed/error)→ return None。
    # 正常结束 → 定位本局 logs/game_*/ 目录,返回其下 telemetry.jsonl。
    ...
    raise NotImplementedError("按 Step 1 实测的 GameProcess API 填充")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy_id")
    ap.add_argument("--opponent", default="veryeasy",
                    choices=["veryeasy", "cheatmoney"])
    args = ap.parse_args()

    spec_path = _ROOT / "tests" / "build_acceptance" / f"{args.strategy_id}.yaml"
    if not spec_path.exists():
        print(f"ERROR: 没有 acceptance spec: {spec_path}")
        return 2
    spec = load_spec(spec_path)

    telemetry_path: Path | None = None
    for attempt in range(1, _MAX_INFRA_RETRY + 1):
        print(f"[runner] {args.strategy_id} vs {args.opponent} — 第 {attempt} 次")
        telemetry_path = _run_one_game(args.strategy_id, args.opponent)
        if telemetry_path is not None:
            break
        print(f"[runner] infra-fail (第 {attempt} 次),retry...")
    if telemetry_path is None:
        print("INFRA BROKEN: 连续 3 次基础设施失败,无法验收。需人工排查。")
        return 3

    telemetry = [
        json.loads(line)
        for line in telemetry_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = verify(telemetry, spec, opponent=args.opponent)
    out = "\n".join([
        f"=== Build Acceptance: {args.strategy_id} vs {args.opponent} ===",
        report.summary(),
    ])
    print(out)
    ts = time.strftime("%Y%m%d_%H%M%S")
    rep_dir = _ROOT / "logs" / "build_acceptance"
    rep_dir.mkdir(parents=True, exist_ok=True)
    (rep_dir / f"{args.strategy_id}_{args.opponent}_{ts}.txt").write_text(
        out, encoding="utf-8"
    )
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

**Step 3: 手动验证 runner 能起来**

先确保有一个 spec 文件（Task 8 会建 4bg 的；本步可临时建一个最小 `4bg.yaml`
只含 1 条 `building_started GATEWAY by 0:45` 的 check）。
Run: `uv run python scripts/build_acceptance.py 4bg --opponent veryeasy`
Expected: SC2 弹窗 → non-realtime 跑完 → 打印报告。若 `_run_one_game` 的
`NotImplementedError` 触发,说明 Step 1 的 API 没填对,回去补。

**Step 4: Commit**

```bash
git add scripts/build_acceptance.py
git commit -m "feat(build_acceptance): runner — spawn SC2 + infra retry + verifier 报告"
```

---

## Task 7: Process doc

把 1-6 跑通的经验固化成"引入新开局策略"的可复用流程。

**Files:**
- Create: `docs/process/new-opening-strategy.md`

**Step 1: 写 process doc**

`docs/process/new-opening-strategy.md`,内容 = 设计文档 §5 的 7 步流程展开:
1. **Deep research** — 搜 spawningtool / Liquipedia / TeamLiquid / 高手录像,
   收集该 build 的标准 timing 节点（建筑/科技/农民数/出兵/集结/出门）
2. **写 acceptance spec** — `tests/build_acceptance/<id>.yaml`，文件头注释记 research
   来源链接
3. **写 / 改 plan 代码** — `<id>.py`（sharpy plan）+ strategy 定义 `<id>.yaml`
4. **跑 runner（VeryEasy）** — `uv run python scripts/build_acceptance.py <id>`
5. **读报告修循环** — acceptance-fail → 读 `logs/game_*/telemetry.jsonl` 分析 →
   改 plan → 重跑,直到全 PASS
6. **跑 CheatMoney ×3** — `--opponent cheatmoney`,看 3 局通过率
7. **沉淀** — 确认 spec 头注释的 research 来源完整

附:infra-fail（runner 自动 retry）vs acceptance-fail（改 plan）的区别说明。

**Step 2: Commit**

```bash
git add docs/process/new-opening-strategy.md
git commit -m "docs: 引入新开局策略的 7 步可复用流程"
```

---

## Task 8: 首个示范 — 4bg acceptance spec + 跑通

用完整流程跑通第一个 build（4bg），证明 pipeline 端到端可用。

**Files:**
- Create: `tests/build_acceptance/4bg.yaml`

**Step 1: Deep research 4bg 标准节奏**

WebSearch + WebFetch 搜 "4 gate warpgate pressure protoss build order timing"
（SC2 wiki Liberty / TeamLiquid 4 Gate guide）。提取标准节点:
PYLON / 首 BG / BY / 折跃研究完成 / 4 BG 全 ready / 首波出门 timing。
（参考 `gate4_pressure.py` 文件头已有的 build order 注释 + 在线交叉验证。）

**Step 2: 写 4bg.yaml**

`tests/build_acceptance/4bg.yaml`,例（具体数值按 Step 1 research 填）:
```yaml
strategy_id: 4bg
my_race: Protoss
# research 来源: <Step 1 搜到的链接>

checks:
  - id: pylon_1
    type: building_started
    unit: PYLON
    by: "0:30"
  - id: gateway_1
    type: building_complete
    unit: GATEWAY
    at: "1:35"
    tol: 20
  - id: cyber_core
    type: building_complete
    unit: CYBERNETICSCORE
    at: "2:05"
    tol: 25
  - id: warpgate_research
    type: upgrade_complete
    upgrade: WARPGATERESEARCH
    by: "4:00"
  - id: four_gates
    type: unit_count
    unit: STALKER
    at: "4:30"
    min: 4
  - id: attack_moveout
    type: attack_moveout
    by: "5:00"
```

**Step 3: 跑 runner**

Run: `uv run python scripts/build_acceptance.py 4bg --opponent veryeasy`
Expected: 端到端跑通 —— 出 N/M passed 报告。若有 acceptance-fail,**先判断**
是 spec 数值不准（research 偏差）还是 plan 真有问题；按 process doc 第 5 步处理。

**Step 4: Commit**

```bash
git add tests/build_acceptance/4bg.yaml
git commit -m "test(build_acceptance): 4bg 首个验收 spec — pipeline 端到端示范"
```

---

## 收尾

8 个 task 完成后:
- `uv run pytest` 全绿（新增 telemetry / spec / verifier 单测）
- `uv run ruff check .` 干净
- 4bg 验收 pipeline 端到端跑通

后续（不在本 plan）:按 process doc 逐个审计现存 build（iac_2base / dt_drop_iac /
1g_robo / blink / dt_rush / phoenix / cannon → zerg / terran），每个一次
research + spec + 修 plan + 验收。
