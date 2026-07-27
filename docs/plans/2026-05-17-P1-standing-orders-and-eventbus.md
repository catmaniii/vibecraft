# P1 实施 plan：L3 Standing Orders + EventBus 基建

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现 four-layer plan P1 阶段 —— L3 Standing Orders 完整链路（state + snapshot + UI + 撤销 + 修 M4 schema gap）+ EventBus 基建（11 lifecycle hook publish + 单测）。

**Architecture:** EventBus 是 vibecraft 自建独立 pub/sub 层，`_VibeCraftProtossBot` override 11 个 python-sc2 lifecycle hook 内部 publish 后 `await super()` 让 sharpy 自己跑。Director 加 `standing_orders` list 按 `persistent` flag 路由，新 `revoke_directive` 上行帧让玩家撤销。PWA `StandingOrdersCard.vue` 替换 `M3Placeholder`。

**Tech Stack:** Python 3.11 + Pydantic + pytest（后端）；Vue 3 + TypeScript + Tailwind + Vitest（前端）。

**设计真理源:**
- `docs/plans/2026-05-17-task-completion-and-eventbus-design.md`（7 个决策详解）
- `docs/adr/0010-four-layer-commands.md`（§9 EventBus，§5 数据结构）
- `docs/plans/2026-05-16-four-layer-commands-design.md`（four-layer 总览）

**Verification 通用命令（贯穿全 plan）:**
```bash
uv run --no-sync pytest                          # 全部单测
uv run --no-sync pytest tests/unit/test_xxx.py -v # 单文件
uv run --no-sync ruff check .                    # lint
uv run --no-sync ruff format --check .           # format
uv run --no-sync mypy src/vibecraft              # type check
cd web && npm run typecheck                      # 前端 type
cd web && npm test                               # 前端 vitest
```

每个 sub-task 完成后必须全绿。CLAUDE.md 警告：**`uv sync` / `uv run` 不带 `--extra sc2` 会卸载 ares**，所以全用 `uv run --no-sync`。

---

## Task P1.0a: EventBus core（subscribe / publish / filter / unsubscribe）

**Files:**
- Create: `src/vibecraft/bot/event_bus.py`
- Create: `tests/unit/test_event_bus.py`

**Step 1: 写失败测试**

`tests/unit/test_event_bus.py`：

```python
"""EventBus 核心 pub/sub 单测(P1.0a)。"""

from __future__ import annotations

import pytest

from vibecraft.bot.event_bus import Event, EventBus, EventKind


def _make_event(kind: EventKind = EventKind.UNIT_DESTROYED, **kw) -> Event:
    """构造 Event 的 helper,只填关键字段,其它给默认值。"""
    return Event(
        kind=kind,
        ts=kw.get("ts", 10.0),
        payload=kw.get("payload", {}),
        owner=kw.get("owner"),
        unit_tag=kw.get("unit_tag"),
        unit_type=kw.get("unit_type"),
        position=kw.get("position"),
    )


class TestEventBus:
    def test_subscribe_publish_handler_called(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(EventKind.UNIT_DESTROYED, received.append)
        bus.publish(_make_event(unit_tag=42))
        assert len(received) == 1
        assert received[0].unit_tag == 42

    def test_publish_only_matching_kind(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(EventKind.UNIT_DESTROYED, received.append)
        bus.publish(_make_event(kind=EventKind.UNIT_CREATED))
        assert received == []

    def test_filter_excludes_event(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(
            EventKind.UNIT_DESTROYED,
            received.append,
            filter=lambda e: e.owner == "enemy",
        )
        bus.publish(_make_event(owner="own"))
        bus.publish(_make_event(owner="enemy", unit_tag=99))
        assert len(received) == 1
        assert received[0].unit_tag == 99

    def test_unsubscribe_removes_handler(self):
        bus = EventBus()
        received: list[Event] = []
        sub_id = bus.subscribe(EventKind.UNIT_DESTROYED, received.append)
        bus.publish(_make_event())
        bus.unsubscribe(sub_id)
        bus.publish(_make_event())
        assert len(received) == 1  # 只收到 unsubscribe 前那条

    def test_handler_exception_does_not_break_other_handlers(self):
        bus = EventBus()
        called: list[str] = []

        def bad_handler(e: Event) -> None:
            called.append("bad")
            raise RuntimeError("boom")

        def good_handler(e: Event) -> None:
            called.append("good")

        bus.subscribe(EventKind.UNIT_DESTROYED, bad_handler)
        bus.subscribe(EventKind.UNIT_DESTROYED, good_handler)
        bus.publish(_make_event())
        # 两个 handler 都被 call,bad 抛错不影响 good
        assert called == ["bad", "good"]

    def test_multiple_subscribers_all_get_event(self):
        bus = EventBus()
        a, b = [], []
        bus.subscribe(EventKind.UNIT_DESTROYED, a.append)
        bus.subscribe(EventKind.UNIT_DESTROYED, b.append)
        bus.publish(_make_event())
        assert len(a) == 1 and len(b) == 1
```

**Step 2: 跑测试验证失败**

```bash
uv run --no-sync pytest tests/unit/test_event_bus.py -v
```

Expected: `ImportError: cannot import name 'EventBus' from 'vibecraft.bot.event_bus'`

**Step 3: 实现 EventBus**

`src/vibecraft/bot/event_bus.py`：

```python
"""EventBus —— vibecraft 自建 pub/sub,把 python-sc2 11 个 lifecycle hook 中心化分发。

详 docs/adr/0010-four-layer-commands.md §9 + docs/plans/2026-05-17-task-completion-
and-eventbus-design.md §三。

设计要点:
- handler 同步(sharpy step 是同步调用栈);未来需要 async 再加 subscribe_async
- 一个 handler 抛错不影响其它 handler(try/except + log warning)
- filter 是可选 Callable[[Event], bool]
- subscribe 返回 sub_id,unsubscribe 用 sub_id 精确移除
- 不暴露给 LLM,EventBus 是 vibecraft 内部基础设施
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class EventKind(str, Enum):
    """python-sc2 11 个 lifecycle hook + sc2 alerts 对应的 event kind。"""

    UNIT_CREATED = "unit_created"
    UNIT_DESTROYED = "unit_destroyed"
    UNIT_TYPE_CHANGED = "unit_type_changed"
    BUILDING_STARTED = "building_started"
    BUILDING_COMPLETE = "building_complete"
    UPGRADE_COMPLETE = "upgrade_complete"
    UNIT_TOOK_DAMAGE = "unit_took_damage"
    ENEMY_UNIT_ENTERED_VISION = "enemy_unit_entered_vision"
    ENEMY_UNIT_LEFT_VISION = "enemy_unit_left_vision"
    SC2_ALERT = "sc2_alert"


@dataclass(frozen=True)
class Event:
    """统一 event 信封。`payload` 按 kind 不同字段不同;公共可选字段冗余便于 filter。"""

    kind: EventKind
    ts: float  # bot.time (game time)
    payload: dict[str, Any]
    owner: str | None = None  # "own" / "enemy" / "neutral"
    unit_tag: int | None = None
    unit_type: str | None = None
    position: tuple[float, float] | None = None


Filter = Callable[[Event], bool] | None
Handler = Callable[[Event], None]


@dataclass
class _Subscription:
    sub_id: int
    kind: EventKind
    handler: Handler
    filter: Filter


class EventBus:
    """中心化 pub/sub。线程不安全(sharpy step 单线程跑,够用)。"""

    def __init__(self) -> None:
        self._subs: dict[EventKind, list[_Subscription]] = defaultdict(list)
        self._next_id: int = 1

    def subscribe(
        self, kind: EventKind, handler: Handler, filter: Filter = None
    ) -> int:
        """订阅某 kind 的 event。返回 sub_id,用于 unsubscribe。"""
        sub = _Subscription(self._next_id, kind, handler, filter)
        self._subs[kind].append(sub)
        self._next_id += 1
        return sub.sub_id

    def unsubscribe(self, sub_id: int) -> None:
        """根据 sub_id 移除订阅。不存在的 id 静默忽略。"""
        for subs in self._subs.values():
            subs[:] = [s for s in subs if s.sub_id != sub_id]

    def publish(self, event: Event) -> None:
        """同步派发给所有匹配 subscriber。handler 抛错不影响其它 handler。"""
        for sub in self._subs[event.kind]:
            if sub.filter and not sub.filter(event):
                continue
            try:
                sub.handler(event)
            except Exception as exc:
                logger.warning(
                    "event_handler_error kind=%s sub=%d: %s",
                    event.kind,
                    sub.sub_id,
                    exc,
                )
```

**Step 4: 跑测试验证通过**

```bash
uv run --no-sync pytest tests/unit/test_event_bus.py -v
```

Expected: `6 passed`

**Step 5: lint + type 检查**

```bash
uv run --no-sync ruff check src/vibecraft/bot/event_bus.py tests/unit/test_event_bus.py
uv run --no-sync mypy src/vibecraft/bot/event_bus.py
```

Expected: 全干净

**Step 6: commit**

```bash
git add src/vibecraft/bot/event_bus.py tests/unit/test_event_bus.py
git commit -m "feat(eventbus): EventBus core skeleton (P1.0a)

- EventBus class with subscribe/publish/filter/unsubscribe
- Event dataclass with kind/ts/payload/owner/unit_tag/unit_type/position
- EventKind enum (10 lifecycle + sc2_alert)
- handler 同步;一个 handler 抛错不影响其它
- 6 个单测覆盖核心 pub/sub 路径

设计源 docs/plans/2026-05-17-task-completion-and-eventbus-design.md §三。"
```

---

## Task P1.0b: 把 EventBus wire 到 11 个 bot lifecycle hook

**Files:**
- Modify: `src/vibecraft/bot/auto_combat/protoss/bot.py`（`_VibeCraftProtossBot` override 11 个 hook）
- Modify: `tests/unit/test_event_bus.py`（加 wire 测试）

**Step 1: 在测试里加一组 wire 测试**

追加到 `tests/unit/test_event_bus.py`：

```python
from unittest.mock import MagicMock


class TestBotEventWiring:
    """验 _VibeCraftProtossBot override 的 11 个 hook 正确 publish 到 EventBus。"""

    @pytest.fixture
    def bot_with_bus(self):
        """mock _VibeCraftProtossBot 最小子集 — 只测 hook → publish 这一段。"""
        from vibecraft.bot.auto_combat.protoss.bot import _make_event_publisher

        bus = EventBus()
        bot_self = MagicMock()
        bot_self.time = 12.5
        bot_self.event_bus = bus
        bot_self._enemy_units_dict = {}
        bot_self._own_units_dict = {}
        return bot_self, bus

    @pytest.mark.asyncio
    async def test_on_unit_created_publishes(self, bot_with_bus):
        bot, bus = bot_with_bus
        received: list[Event] = []
        bus.subscribe(EventKind.UNIT_CREATED, received.append)
        unit = MagicMock()
        unit.alliance = 1
        unit.tag = 100
        unit.type_id.__str__ = lambda self: "PROBE"
        unit.position.x = 50.0
        unit.position.y = 60.0
        # call 我们待实现的 publisher
        from vibecraft.bot.auto_combat.protoss.bot import _publish_unit_created
        _publish_unit_created(bot, unit)
        assert len(received) == 1
        e = received[0]
        assert e.kind == EventKind.UNIT_CREATED
        assert e.owner == "own"
        assert e.unit_tag == 100
        assert e.position == (50.0, 60.0)

    @pytest.mark.asyncio
    async def test_on_unit_destroyed_publishes(self, bot_with_bus):
        bot, bus = bot_with_bus
        unit = MagicMock(); unit.alliance = 4; unit.tag = 200
        unit.type_id.__str__ = lambda self: "STALKER"
        unit.position.x = 10.0; unit.position.y = 20.0
        bot._enemy_units_dict = {200: unit}
        received: list[Event] = []
        bus.subscribe(EventKind.UNIT_DESTROYED, received.append)
        from vibecraft.bot.auto_combat.protoss.bot import _publish_unit_destroyed
        _publish_unit_destroyed(bot, 200)
        assert received[0].owner == "enemy"
        assert received[0].unit_type == "STALKER"

    @pytest.mark.asyncio
    async def test_on_upgrade_complete_publishes(self, bot_with_bus):
        bot, bus = bot_with_bus
        received: list[Event] = []
        bus.subscribe(EventKind.UPGRADE_COMPLETE, received.append)
        upgrade = MagicMock(); upgrade.__str__ = lambda self: "BLINKTECH"
        from vibecraft.bot.auto_combat.protoss.bot import _publish_upgrade_complete
        _publish_upgrade_complete(bot, upgrade)
        assert received[0].kind == EventKind.UPGRADE_COMPLETE
        assert received[0].payload["upgrade_id"] == "BLINKTECH"

    # ... 类似 8 个 hook 各 1 个测试
```

**Step 2: 跑测试验证失败**

```bash
uv run --no-sync pytest tests/unit/test_event_bus.py::TestBotEventWiring -v
```

Expected: `ImportError: cannot import name '_publish_unit_created'`

**Step 3: 在 bot.py 加 11 个 _publish_xxx helper + 在 _VibeCraftProtossBot hook 内调**

读 `src/vibecraft/bot/auto_combat/protoss/bot.py` 找当前 `_VibeCraftProtossBot` 类 + 现有 `on_unit_destroyed` override 位置（line ~795 / 321）。

在 class 顶部 module level 加 helpers：

```python
# === EventBus publishing helpers ===
# 每个对应 python-sc2 一个 lifecycle hook。bot.event_bus.publish 调用包装在
# helper 是为方便单测(mock bot + bus,不需要起完整 sharpy)。

from vibecraft.bot.event_bus import Event, EventKind


def _publish_unit_created(bot_self, unit) -> None:
    owner = "own" if getattr(unit, "alliance", 0) == 1 else "enemy"
    bot_self.event_bus.publish(Event(
        kind=EventKind.UNIT_CREATED,
        ts=bot_self.time,
        payload={"unit_tag": unit.tag, "unit_obj": unit},
        owner=owner,
        unit_tag=unit.tag,
        unit_type=str(unit.type_id),
        position=(float(unit.position.x), float(unit.position.y)),
    ))


def _publish_unit_destroyed(bot_self, unit_tag: int) -> None:
    # 死亡时 unit 对象可能已 invalid,从 cached dicts 找
    unit = (bot_self._enemy_units_dict.get(unit_tag)
            or bot_self._own_units_dict.get(unit_tag))
    owner = None
    unit_type = None
    position = None
    if unit is not None:
        owner = "own" if getattr(unit, "alliance", 0) == 1 else "enemy"
        unit_type = str(unit.type_id)
        position = (float(unit.position.x), float(unit.position.y))
    bot_self.event_bus.publish(Event(
        kind=EventKind.UNIT_DESTROYED,
        ts=bot_self.time,
        payload={"unit_tag": unit_tag, "unit_obj": unit},
        owner=owner,
        unit_tag=unit_tag,
        unit_type=unit_type,
        position=position,
    ))


def _publish_unit_type_changed(bot_self, unit, previous_type) -> None:
    bot_self.event_bus.publish(Event(
        kind=EventKind.UNIT_TYPE_CHANGED,
        ts=bot_self.time,
        payload={
            "unit_tag": unit.tag,
            "previous_type": str(previous_type),
            "current_type": str(unit.type_id),
        },
        owner="own" if getattr(unit, "alliance", 0) == 1 else "enemy",
        unit_tag=unit.tag,
        unit_type=str(unit.type_id),
    ))


def _publish_building_started(bot_self, unit) -> None:
    bot_self.event_bus.publish(Event(
        kind=EventKind.BUILDING_STARTED,
        ts=bot_self.time,
        payload={"unit_tag": unit.tag, "unit_obj": unit},
        owner="own",  # 只有自方触发
        unit_tag=unit.tag,
        unit_type=str(unit.type_id),
        position=(float(unit.position.x), float(unit.position.y)),
    ))


def _publish_building_complete(bot_self, unit) -> None:
    bot_self.event_bus.publish(Event(
        kind=EventKind.BUILDING_COMPLETE,
        ts=bot_self.time,
        payload={"unit_tag": unit.tag, "unit_obj": unit},
        owner="own",
        unit_tag=unit.tag,
        unit_type=str(unit.type_id),
        position=(float(unit.position.x), float(unit.position.y)),
    ))


def _publish_upgrade_complete(bot_self, upgrade) -> None:
    bot_self.event_bus.publish(Event(
        kind=EventKind.UPGRADE_COMPLETE,
        ts=bot_self.time,
        payload={"upgrade_id": str(upgrade)},
        owner="own",
    ))


def _publish_unit_took_damage(bot_self, unit, amount) -> None:
    bot_self.event_bus.publish(Event(
        kind=EventKind.UNIT_TOOK_DAMAGE,
        ts=bot_self.time,
        payload={"unit_tag": unit.tag, "amount": float(amount)},
        owner="own",  # 只有自方触发
        unit_tag=unit.tag,
        unit_type=str(unit.type_id),
        position=(float(unit.position.x), float(unit.position.y)),
    ))


def _publish_enemy_unit_entered_vision(bot_self, unit) -> None:
    bot_self.event_bus.publish(Event(
        kind=EventKind.ENEMY_UNIT_ENTERED_VISION,
        ts=bot_self.time,
        payload={"unit_tag": unit.tag, "unit_obj": unit},
        owner="enemy",
        unit_tag=unit.tag,
        unit_type=str(unit.type_id),
        position=(float(unit.position.x), float(unit.position.y)),
    ))


def _publish_enemy_unit_left_vision(bot_self, unit_tag: int) -> None:
    bot_self.event_bus.publish(Event(
        kind=EventKind.ENEMY_UNIT_LEFT_VISION,
        ts=bot_self.time,
        payload={"unit_tag": unit_tag},
        owner="enemy",
        unit_tag=unit_tag,
    ))
```

然后 `_VibeCraftProtossBot` 类内 override 11 个 hook（如果已 override 的扩，否则新加）：

```python
class _VibeCraftProtossBot(KnowledgeBot):
    def __init__(self, ...):
        super().__init__(...)
        from vibecraft.bot.event_bus import EventBus
        self.event_bus = EventBus()

    async def on_unit_created(self, unit) -> None:
        _publish_unit_created(self, unit)
        await super().on_unit_created(unit)

    async def on_unit_destroyed(self, unit_tag: int) -> None:
        _publish_unit_destroyed(self, unit_tag)
        # 已有的 LLM_CONTROLLED tag 清理逻辑保留
        self._llm_controlled_tags.discard(unit_tag)
        await super().on_unit_destroyed(unit_tag)

    async def on_unit_type_changed(self, unit, previous_type) -> None:
        _publish_unit_type_changed(self, unit, previous_type)
        await super().on_unit_type_changed(unit, previous_type)

    async def on_building_construction_started(self, unit) -> None:
        _publish_building_started(self, unit)
        await super().on_building_construction_started(unit)

    async def on_building_construction_complete(self, unit) -> None:
        _publish_building_complete(self, unit)
        await super().on_building_construction_complete(unit)

    async def on_upgrade_complete(self, upgrade) -> None:
        _publish_upgrade_complete(self, upgrade)
        await super().on_upgrade_complete(upgrade)

    async def on_unit_took_damage(self, unit, amount_damage_taken) -> None:
        _publish_unit_took_damage(self, unit, amount_damage_taken)
        await super().on_unit_took_damage(unit, amount_damage_taken)

    async def on_enemy_unit_entered_vision(self, unit) -> None:
        _publish_enemy_unit_entered_vision(self, unit)
        await super().on_enemy_unit_entered_vision(unit)

    async def on_enemy_unit_left_vision(self, unit_tag: int) -> None:
        _publish_enemy_unit_left_vision(self, unit_tag)
        await super().on_enemy_unit_left_vision(unit_tag)
```

注：`SC2_ALERT` 在 P1 不接（sc2 alerts 从 `bot.state.alerts` 读，需要 polling 或专门 wire，留 P3）。

**Step 4: 跑测试验证通过**

```bash
uv run --no-sync pytest tests/unit/test_event_bus.py -v
```

Expected: `~14 passed`（核心 6 + wire 8）

**Step 5: 跑全部单测确保没 regress**

```bash
uv run --no-sync pytest
```

Expected: `403+ passed`（原 389 + 新增）

**Step 6: lint + type**

```bash
uv run --no-sync ruff check . && uv run --no-sync mypy src/vibecraft
```

Expected: 全干净

**Step 7: commit**

```bash
git add src/vibecraft/bot/auto_combat/protoss/bot.py tests/unit/test_event_bus.py
git commit -m "feat(eventbus): wire 11 lifecycle hooks → EventBus (P1.0b)

- _VibeCraftProtossBot.__init__ 加 self.event_bus = EventBus()
- override 9 个 python-sc2 hook (on_unit_created/destroyed/type_changed/
  building_started/complete/upgrade_complete/unit_took_damage/
  enemy_unit_entered_vision/enemy_unit_left_vision):每个 publish + super
- on_unit_destroyed 保留已有 _llm_controlled_tags.discard 逻辑
- 11 个 _publish_xxx module-level helpers 方便单测 mock
- SC2_ALERT 暂不接(state.alerts polling,留 P3)

设计源 docs/plans/2026-05-17-task-completion-and-eventbus-design.md §3.2。"
```

---

## Task P1.1: 修 M4 e2e schema gap + UnitClaimPayload 加 `persistent: bool`

**Files:**
- Modify: `src/vibecraft/directives/models.py`
- Modify: `src/vibecraft/llm/prompt.py`
- Modify: `tests/unit/test_directives.py`
- Modify: `tests/unit/test_llm_anthropic.py`（如果有 standing order 例子）

**前置：先读现有 schema 看 `UnitClaimPayload`、`UnitSelector`、`Target` 当前定义**

```bash
grep -n "UnitClaimPayload\|UnitSelector\|class Target" src/vibecraft/directives/models.py
```

**Step 1: 写失败测试 — persistent=True 应进 schema**

追加到 `tests/unit/test_directives.py`：

```python
class TestStandingOrderSchema:
    """L3 standing order schema (P1.1)。"""

    def test_unit_claim_payload_persistent_default_false(self):
        from vibecraft.directives.models import UnitClaimPayload, UnitSelector, UnitTask
        payload = UnitClaimPayload(
            selector=UnitSelector(unit_type="Phoenix"),
            task=UnitTask(primary_action={"verb": "patrol", "target": {"kind": "named_spot", "named_spot": "natural"}}),
        )
        assert payload.persistent is False

    def test_unit_claim_payload_persistent_true(self):
        from vibecraft.directives.models import UnitClaimPayload, UnitSelector, UnitTask
        payload = UnitClaimPayload(
            selector=UnitSelector(unit_type="Zealot"),
            task=UnitTask(primary_action={"verb": "hold", "target": {"kind": "named_spot", "named_spot": "main_ramp"}}),
            persistent=True,
        )
        assert payload.persistent is True

    def test_target_kind_building_tag(self):
        """修 M4 e2e 发现的 schema gap:target.kind 支持 building_tag。"""
        from vibecraft.directives.models import Target
        t = Target(kind="building_tag", building_tag=12345)
        assert t.kind == "building_tag"
        assert t.building_tag == 12345

    def test_target_kind_named_spot(self):
        """守气矿等场景用 named_spot。"""
        from vibecraft.directives.models import Target
        t = Target(kind="named_spot", named_spot="enemy_main_gas")
        assert t.kind == "named_spot"
        assert t.named_spot == "enemy_main_gas"

    def test_selector_no_count_field(self):
        """修 M4 e2e schema gap:UnitSelector 不接 count(已弃用)。"""
        from vibecraft.directives.models import UnitSelector
        with pytest.raises(Exception):  # pydantic ValidationError
            UnitSelector(unit_type="Probe", count=1)
```

**Step 2: 跑测试看失败**

```bash
uv run --no-sync pytest tests/unit/test_directives.py::TestStandingOrderSchema -v
```

Expected: 测试 fail（`persistent` field 不存在 / `Target` 不支持新 kind / `count` 字段还能传）

**Step 3: 修 schema**

`src/vibecraft/directives/models.py`：

```python
# UnitClaimPayload 加字段:
class UnitClaimPayload(BaseModel):
    selector: UnitSelector
    task: UnitTask
    # P1.1 新增:true 进 Director.standing_orders,false 一次性
    persistent: bool = False

# Target 改:加 building_tag / named_spot 到 kind enum,加对应字段
class Target(BaseModel):
    kind: Literal["point", "unit_tag", "building_tag", "named_spot", "unit_type"]
    point: tuple[float, float] | None = None
    unit_tag: int | None = None
    building_tag: int | None = None  # P1.1 新增
    named_spot: str | None = None    # P1.1 新增 (如 "natural" / "main_ramp")
    unit_type: str | None = None

    @model_validator(mode="after")
    def _check_field_for_kind(self):
        # 每个 kind 必填对应字段
        required = {
            "point": "point", "unit_tag": "unit_tag",
            "building_tag": "building_tag", "named_spot": "named_spot",
            "unit_type": "unit_type",
        }[self.kind]
        if getattr(self, required) is None:
            raise ValueError(f"Target.kind={self.kind} 需要字段 {required}")
        return self

# UnitSelector 移除 count 字段(原来若有):
class UnitSelector(BaseModel):
    model_config = ConfigDict(extra="forbid")  # 拒绝 count 等 extra inputs
    unit_type: str | None = None
    unit_tag: int | None = None
    # ...其它合法 selector 字段
```

**Step 4: 跑测试看通过**

```bash
uv run --no-sync pytest tests/unit/test_directives.py::TestStandingOrderSchema -v
```

Expected: `5 passed`

**Step 5: 改 LLM prompt few-shot**

读 `src/vibecraft/llm/prompt.py` 找现有 unit_claim 例子（line ~ "凤凰举不朽"）。改成用新字段：

```python
# few-shot 例 5 改:
"""例 5：「凤凰举不朽」
→ unit_claim: selector={unit_type:"Phoenix"}, task={primary_action:{verb:"lift_target", target:{kind:"unit_type", unit_type:"Immortal"}}}, persistent=false

例 5b(新):「那个农民守气矿别动」
→ unit_claim: selector={unit_type:"Probe"}, task={primary_action:{verb:"hold", target:{kind:"named_spot", named_spot:"enemy_main_gas"}}}, persistent=true
"""
```

**Step 6: 跑全部单测看 regress**

```bash
uv run --no-sync pytest
```

Expected: 全过

**Step 7: lint + type + commit**

```bash
uv run --no-sync ruff check . && uv run --no-sync mypy src/vibecraft
git add src/vibecraft/directives/models.py src/vibecraft/llm/prompt.py tests/unit/test_directives.py
git commit -m "fix(directives): 修 M4 e2e schema gap + 加 persistent 字段 (P1.1)

修 v0.1.0a3 M4 e2e 发现的 LLM prompt ↔ Pydantic schema 不匹配:
- Target.kind 加 'building_tag' / 'named_spot' enum,加对应字段
- UnitSelector 设 extra='forbid' 明确拒绝 count(已弃用)
- model_validator 强制 Target 按 kind 填对应字段

新增:
- UnitClaimPayload.persistent: bool = False (按 ADR 0010 §3 / 决策 3)
  true 进 Director.standing_orders 列表,false 一次性

LLM prompt few-shot 加 standing order 例子(persistent=true 守 named_spot)。

5 个新单测覆盖 schema 路径。"
```

---

## Task P1.2: Director state — `standing_orders` 列表 + `_submit_directives` 按 persistent 路由

**Files:**
- Modify: `src/vibecraft/bot/director.py`
- Modify: `tests/unit/test_director.py`

**Step 1: 写失败测试**

追加到 `tests/unit/test_director.py`：

```python
class TestStandingOrderRouting:
    """P1.2 Director 按 persistent 路由 directive 到 standing_orders 或 _in_flight。"""

    def test_persistent_false_goes_to_in_flight(self, director):
        from vibecraft.directives.models import Directive, UnitClaimPayload, UnitSelector, UnitTask
        from vibecraft.directives.types import DirectiveType
        d = Directive(
            id="d_test_1",
            type=DirectiveType.UNIT_CLAIM,
            payload={"unit_claim": UnitClaimPayload(
                selector=UnitSelector(unit_type="Phoenix"),
                task=UnitTask(primary_action={"verb": "lift_target",
                                              "target": {"kind": "unit_type", "unit_type": "Immortal"}}),
                persistent=False,
            )},
            issued_at=10.0,
        )
        director._submit_directives([d], now=10.0)
        assert d.id in director._in_flight
        assert not any(s.id == d.id for s in director.standing_orders)

    def test_persistent_true_goes_to_standing_orders(self, director):
        # 同上,persistent=True
        d = Directive(...persistent=True...)
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.standing_orders)
        assert d.id not in director._in_flight

    def test_revoke_standing_order_removes(self, director):
        d = Directive(...persistent=True...)
        director._submit_directives([d], now=10.0)
        director.revoke_standing_order(d.id, now=15.0)
        assert not any(s.id == d.id for s in director.standing_orders)
```

**Step 2: 跑测试看失败**

```bash
uv run --no-sync pytest tests/unit/test_director.py::TestStandingOrderRouting -v
```

Expected: `AttributeError: 'Director' object has no attribute 'standing_orders'`

**Step 3: 修 Director**

`src/vibecraft/bot/director.py` __init__ 加：

```python
# P1.2 L3 standing orders (persistent directives 不走 _in_flight)
self.standing_orders: list[Directive] = []
```

修 `_submit_directives` 方法（找 `self._in_flight[submitted.id] = submitted` 那行）：

```python
def _submit_directives(self, directives: list[Directive], now: float) -> None:
    from vibecraft.directives.types import IssuedBy
    from vibecraft.directives.models import UnitClaimPayload  # P1.2

    for d in directives:
        d_with_ts = ...  # 已有逻辑
        if is_view_directive(d_with_ts.type):
            ...
            continue
        submitted = self.board.submit(d_with_ts, now=now)
        # P1.2: persistent=True 的 unit_claim 进 standing_orders
        is_persistent = (
            isinstance(submitted.payload, UnitClaimPayload)
            and submitted.payload.persistent
        )
        if is_persistent:
            self.standing_orders.append(submitted)
        else:
            self._in_flight[submitted.id] = submitted
```

新增方法：

```python
def revoke_standing_order(self, directive_id: str, now: float) -> bool:
    """玩家通过 revoke_directive 上行帧撤销 standing order。"""
    before = len(self.standing_orders)
    self.standing_orders = [s for s in self.standing_orders if s.id != directive_id]
    if len(self.standing_orders) < before:
        # 通知 board (用于 sharpy 让位 release tag —— P5 接)
        self.board.revoke(directive_id, now)
        self._push_snapshot(now)
        return True
    return False
```

**Step 4: 跑测试看通过 + 全部单测**

```bash
uv run --no-sync pytest tests/unit/test_director.py -v
uv run --no-sync pytest
```

Expected: 全过

**Step 5: lint + type + commit**

```bash
uv run --no-sync ruff check . && uv run --no-sync mypy src/vibecraft
git add src/vibecraft/bot/director.py tests/unit/test_director.py
git commit -m "feat(director): 加 standing_orders 列表 + persistent 路由 (P1.2)

按 ADR 0010 §5 + 决策 3:
- Director.standing_orders: list[Directive] 新字段
- _submit_directives:persistent=True 的 unit_claim 进 standing_orders,
  其它进 _in_flight (原有路径)
- revoke_standing_order(id, now) 新方法,玩家撤销时调

3 个单测覆盖路由 + 撤销路径。"
```

---

## Task P1.3: Snapshot 加 `standing_orders` 字段

**Files:**
- Modify: `src/vibecraft/bot/director.py`（`build_snapshot`）
- Modify: `web/src/types.ts`（`StandingOrderView` + `SnapshotFrame`）
- Modify: `tests/unit/test_cockpit_sync.py`

**Step 1: 写失败测试**

追加到 `tests/unit/test_cockpit_sync.py`：

```python
class TestSnapshotStandingOrders:
    def test_snapshot_includes_standing_orders_field(self, director):
        snap = director.build_snapshot(now=10.0)
        assert "standing_orders" in snap
        assert isinstance(snap["standing_orders"], list)

    def test_snapshot_standing_order_view_fields(self, director):
        # 先 submit 一个 standing order
        d = Directive(...persistent=True...)
        director._submit_directives([d], now=10.0)
        snap = director.build_snapshot(now=11.0)
        assert len(snap["standing_orders"]) == 1
        view = snap["standing_orders"][0]
        assert view["id"] == d.id
        assert view["display"]  # 中文人话(如 "凤凰 守 enemy_main_gas")
        assert view["issued_at"] == 10.0
```

**Step 2: 跑测试看失败**

```bash
uv run --no-sync pytest tests/unit/test_cockpit_sync.py::TestSnapshotStandingOrders -v
```

Expected: `KeyError: 'standing_orders'`

**Step 3: 修 build_snapshot**

`src/vibecraft/bot/director.py`：

```python
def build_snapshot(self, now: float) -> dict[str, Any]:
    snap = {
        "strategy": {...},  # 已有
        "recent_commands": [...],
        # P1.3 新增:
        "standing_orders": [self._standing_order_view(s) for s in self.standing_orders],
        # ...其它已有
    }
    return snap

def _standing_order_view(self, d: Directive) -> dict[str, Any]:
    payload = d.payload
    display = self._format_standing_order_display(payload)
    return {
        "id": d.id,
        "display": display,
        "issued_at": d.issued_at,
        "selector": payload.unit_claim.selector.model_dump() if hasattr(payload, "unit_claim") else {},
        "task_summary": str(payload.unit_claim.task.primary_action.get("verb", "?"))
                        if hasattr(payload, "unit_claim") else "",
    }

def _format_standing_order_display(self, payload) -> str:
    """中文人话:'凤凰 × 巡逻 enemy_main' 这种格式。"""
    if not hasattr(payload, "unit_claim"):
        return "未知 standing"
    uc = payload.unit_claim
    unit_type = uc.selector.unit_type or "单位"
    verb = uc.task.primary_action.get("verb", "?")
    target = uc.task.primary_action.get("target", {})
    target_display = target.get("named_spot") or target.get("unit_type") or "?"
    return f"{unit_type} {verb} {target_display}"
```

**Step 4: 加前端 type**

`web/src/types.ts`：

```typescript
// P1.3 新增
export interface StandingOrderView {
  id: string
  display: string         // 中文人话,如 "凤凰 patrol natural"
  issued_at: number
  selector: Record<string, unknown>
  task_summary: string
}

// SnapshotFrame 加字段
export interface SnapshotFrame {
  type: 'snapshot'
  ts: number
  strategy: {...}  // 已有
  recent_commands: {...}[]
  standing_orders: StandingOrderView[]  // P1.3 新增
  // ...其它已有
}
```

**Step 5: 跑后端单测看通过**

```bash
uv run --no-sync pytest tests/unit/test_cockpit_sync.py -v
```

Expected: `3+ passed`

**Step 6: 前端 type check**

```bash
cd web && npm run typecheck
```

Expected: clean

**Step 7: commit**

```bash
git add src/vibecraft/bot/director.py tests/unit/test_cockpit_sync.py web/src/types.ts
git commit -m "feat(snapshot): 加 standing_orders 字段透传 (P1.3)

- director.build_snapshot 加 standing_orders: list[StandingOrderView]
- _standing_order_view + _format_standing_order_display helpers
  生成中文人话(如 '凤凰 patrol natural')
- web/src/types.ts:StandingOrderView interface + SnapshotFrame 加字段

3 个单测覆盖字段透传 + display 格式。"
```

---

## Task P1.4: `revoke_directive {id}` 上行帧 + ws handler

**Files:**
- Modify: `web/src/types.ts`（`RevokeDirectiveFrame`）
- Modify: `src/vibecraft/server/ws.py`（handler）
- Modify: `src/vibecraft/server/game_process.py`（down_q 消费）
- Modify: `src/vibecraft/bot/auto_combat/protoss/bot.py`（_tick_view_channel 加 revoke_directive 处理）
- Modify: `tests/unit/test_server_ws.py`

**Step 1: 写失败测试**

```python
class TestRevokeDirectiveFrame:
    async def test_ws_revoke_directive_sent_to_game(self, mock_game_process):
        ws_conn = ...  # 已有 setup
        await ws_conn._handle_frame({"type": "revoke_directive", "directive_id": "d_abc123"})
        mock_game_process.send_command.assert_called_once_with(
            {"type": "revoke_directive", "directive_id": "d_abc123"}
        )

    async def test_ws_revoke_directive_missing_id_rejected(self, mock_game_process):
        ws_conn = ...
        await ws_conn._handle_frame({"type": "revoke_directive"})  # 没 directive_id
        mock_game_process.send_command.assert_not_called()
```

```python
# bot.py 端测试:
class TestBotRevokeDirective:
    async def test_revoke_directive_calls_director(self, mock_bot):
        director = MagicMock()
        mock_bot.director = director
        msg = {"type": "revoke_directive", "directive_id": "d_abc123"}
        # 模拟 down_q 收到这条 msg
        await mock_bot._tick_view_channel(now_s=10.0)  # 内部 from down_q.get_nowait()
        director.revoke_standing_order.assert_called_once_with("d_abc123", 10.0)
```

**Step 2: 跑测试看失败**

**Step 3: 实现各处**

`web/src/types.ts`：

```typescript
export interface RevokeDirectiveFrame {
  type: 'revoke_directive'
  directive_id: string
}

export type UpFrame =
  | StartGameFrame | CommandFrame | ViewMoveFrame
  | ConfirmRecommendationFrame | DismissRecommendationFrame
  | ConfirmForceStrategyFrame | CancelForceStrategyFrame
  | RevokeDirectiveFrame   // P1.4 新增
```

`src/vibecraft/server/ws.py` 找处理 confirm_recommendation 那段（line ~229），加：

```python
elif frame_type == "revoke_directive":
    directive_id = frame.get("directive_id")
    if not directive_id:
        self._log.warning("revoke_directive_missing_id")
        return
    self._game_process.send_command(
        {"type": "revoke_directive", "directive_id": directive_id}
    )
```

`bot.py` `_tick_view_channel`（line ~713 已有的 while loop）加分支：

```python
elif msg_type == "revoke_directive":
    directive_id = msg.get("directive_id")
    if directive_id and self.director is not None:
        self.director.revoke_standing_order(directive_id, now_s)
```

**Step 4: 跑测试看通过**

**Step 5: commit**

```bash
git add web/src/types.ts src/vibecraft/server/ws.py src/vibecraft/bot/auto_combat/protoss/bot.py tests/unit/test_server_ws.py
git commit -m "feat(ws): revoke_directive 上行帧 + 端到端 wire (P1.4)

- web/types.ts:RevokeDirectiveFrame interface 加入 UpFrame union
- ws.py _handle_frame 处理 revoke_directive 帧 → game_process.send_command
- bot.py _tick_view_channel down_q 消费 revoke_directive →
  director.revoke_standing_order(id, now)
- 缺 directive_id 时 ws warning 拒绝

测试:ws → game_process 路径 + bot → director 路径各 2 个。"
```

---

## Task P1.5: PWA `StandingOrdersCard.vue` + CockpitView 装载

**Files:**
- Create: `web/src/components/StandingOrdersCard.vue`
- Create: `web/src/components/__tests__/StandingOrdersCard.test.ts`
- Modify: `web/src/views/CockpitView.vue`（替换 `M3Placeholder "Standing Orders"`）
- Modify: `web/src/composables/useWs.ts`（透传 `standingOrders` + `revokeDirective` 函数）

**Step 1: 写组件失败测试**

`web/src/components/__tests__/StandingOrdersCard.test.ts`：

```typescript
import { mount } from '@vue/test-utils'
import { describe, it, expect } from 'vitest'
import StandingOrdersCard from '../StandingOrdersCard.vue'

describe('StandingOrdersCard', () => {
  it('renders empty state when no orders', () => {
    const wrapper = mount(StandingOrdersCard, {
      props: { orders: [] },
    })
    expect(wrapper.text()).toContain('暂无 standing order')
  })

  it('renders each standing order with display + revoke button', () => {
    const wrapper = mount(StandingOrdersCard, {
      props: {
        orders: [
          { id: 'd_1', display: '凤凰 patrol natural', issued_at: 100,
            selector: {}, task_summary: 'patrol' },
          { id: 'd_2', display: 'Probe hold enemy_main_gas', issued_at: 200,
            selector: {}, task_summary: 'hold' },
        ],
      },
    })
    expect(wrapper.text()).toContain('凤凰 patrol natural')
    expect(wrapper.text()).toContain('Probe hold enemy_main_gas')
    expect(wrapper.findAll('button.revoke-btn')).toHaveLength(2)
  })

  it('emits revoke event with id on button click', async () => {
    const wrapper = mount(StandingOrdersCard, {
      props: {
        orders: [{ id: 'd_1', display: '凤凰 patrol', issued_at: 100,
                   selector: {}, task_summary: 'patrol' }],
      },
    })
    await wrapper.find('button.revoke-btn').trigger('click')
    expect(wrapper.emitted('revoke')).toEqual([['d_1']])
  })
})
```

**Step 2: 跑测试看失败**

```bash
cd web && npm test -- StandingOrdersCard
```

Expected: `Cannot find module '../StandingOrdersCard.vue'`

**Step 3: 实现组件**

`web/src/components/StandingOrdersCard.vue`：

```vue
<script setup lang="ts">
import type { StandingOrderView } from '@/types'

defineProps<{
  orders: readonly StandingOrderView[]
}>()

const emit = defineEmits<{
  revoke: [id: string]
}>()
</script>

<template>
  <div class="rounded-xl bg-surface-2 border border-border p-4">
    <div class="flex items-center justify-between mb-2">
      <p class="text-sm font-semibold text-muted uppercase tracking-wider">
        Standing Orders
      </p>
      <span class="text-xs text-muted">{{ orders.length }}</span>
    </div>
    <p v-if="orders.length === 0" class="text-xs text-muted italic">
      暂无 standing order
    </p>
    <ul v-else class="space-y-2">
      <li v-for="order in orders" :key="order.id"
          class="flex items-center justify-between bg-surface-3 rounded px-3 py-2">
        <span class="text-sm text-white">{{ order.display }}</span>
        <button class="revoke-btn text-xs text-danger hover:text-danger/80"
                @click="emit('revoke', order.id)">
          × 撤销
        </button>
      </li>
    </ul>
  </div>
</template>
```

**Step 4: 跑组件测试看通过**

```bash
cd web && npm test -- StandingOrdersCard
```

Expected: `3 passed`

**Step 5: 修 `CockpitView.vue` 替换 M3Placeholder**

找现有 `<M3Placeholder label="Standing Orders" .../>`（已知存在），换成：

```vue
<StandingOrdersCard
  :orders="standingOrders"
  @revoke="onRevokeStanding"
/>
```

在 script 顶部 import：

```typescript
import StandingOrdersCard from '@/components/StandingOrdersCard.vue'
```

加 props（从 App.vue 传过来）+ emit：

```typescript
const props = defineProps<{
  // ...已有
  standingOrders: readonly StandingOrderView[]
}>()

const emit = defineEmits<{
  // ...已有
  revokeStanding: [id: string]
}>()

function onRevokeStanding(id: string) {
  emit('revokeStanding', id)
}
```

**Step 6: 修 `useWs.ts` 透传**

加：

```typescript
const standingOrders = ref<StandingOrderView[]>([])

// snapshot 解析时
function applySnapshot(frame: SnapshotFrame) {
  // ...已有
  standingOrders.value = frame.standing_orders ?? []
}

function revokeDirective(id: string) {
  send({ type: 'revoke_directive', directive_id: id })
}

return {
  // ...已有
  standingOrders: readonly(standingOrders),
  revokeDirective,
}
```

**Step 7: App.vue wire CockpitView** —— 传 `standingOrders` 当 prop + handle `revokeStanding` emit：

```vue
<CockpitView
  ...
  :standing-orders="standingOrders"
  @revoke-standing="revokeDirective"
/>
```

**Step 8: 前端 type + 测试**

```bash
cd web && npm run typecheck && npm test
```

Expected: 全过

**Step 9: build PWA assets 给后端 serve**

```bash
cd web && npm run build
```

Expected: 输出新 hash 的 assets 到 `src/vibecraft/server/static/`

**Step 10: commit**

```bash
git add web/
git commit -m "feat(pwa): StandingOrdersCard 替换 M3Placeholder + revoke wire (P1.5)

- 新增 web/src/components/StandingOrdersCard.vue:列表 + × 撤销按钮
- CockpitView 替换 'Standing Orders' M3Placeholder
- useWs:standingOrders 响应式 + revokeDirective(id) 发上行帧
- App.vue wire prop + emit handler

3 个 vitest 组件测试覆盖空态 / 列表渲染 / revoke emit。

重新 build PWA 资源到 src/vibecraft/server/static/(新 hash)。"
```

---

## Task P1.6: e2e smoke verify

**Files:**
- 仅跑命令 + 看 output

**Step 1: 重启 service 让新 build 生效（可选 — 只跑 headless smoke 不需要）**

**Step 2: 跑 headless smoke 验证 schema gap 已修**

```bash
uv run --no-sync python scripts/headless_smoke.py --fast \
  --initial-opening 1g_robo_immortal \
  --inject "那个农民守气矿别动" \
  --inject-after 5 --seconds 60
```

Expected output 关键行：

- `INJECTING '那个农民守气矿别动'`
- ECHO 不含 `[解析失败]` —— 应是中文 description "已将一个农民设为守气矿的 standing order"
- EVENT 含 `directive.committed` payload directive_id
- 完成时 `total snapshots: N` + `standing_orders: 1`（如果 collect 函数 dump 了）

**Step 3: 验 snapshot 真的 push 了 standing_orders**

读最新 log：

```bash
latest=$(ls -t logs/ | grep game_ | head -1)
cat "logs/$latest/llm_calls/call_001.json" | python -c "
import json, sys
d = json.load(sys.stdin)
print('user_text:', d['user_text'])
print('directives:', json.dumps(d['response_raw']['directives'], indent=2, ensure_ascii=False))
"
```

Expected：directives 含 `type=unit_claim` + `payload.unit_claim.persistent=true` + `selector.unit_type=Probe` + `target.kind=named_spot`。

**Step 4: 跑全部单测确保没 regress**

```bash
uv run --no-sync pytest
cd web && npm test && npm run typecheck
```

Expected: 全过

**Step 5: 不需要 commit**（只跑验证）

如果 step 2-3 失败，回去对应 P1.1-P1.4 修，不要继续 P1.7。

---

## Task P1.7: 更新 ADR 0010 Implementation Notes corner case

**Files:**
- Modify: `docs/adr/0010-four-layer-commands.md`（Implementation Notes 段）

**Step 1: 整理 P1 实施过程中发现的 corner case**

可能的 corner case（实际填实施时发现的）：
- LLM 生成 `named_spot` 时引用了不在 sharpy zone manager 里的 spot name（e.g., `enemy_third_gas`）→ 暂时 fallback `closest_to(point)`，留 P5 实施 spot name 注册
- standing_order 的 unit 被 attack 死亡时 → P1 直接 mark 该 unit invalid，未来 P5 加"unit 死亡自动 release standing"
- 玩家发同样 standing order 两次 → P1 简单 append（双倍生效），未来加 dedup
- ...

**Step 2: 追加到 ADR 0010 末尾**

```markdown
## Implementation Notes

P1 实施（2026-05-17 ~ XX）发现的 corner case：

- **named_spot 注册**：LLM 用 `target.kind="named_spot"` 时引用的 spot name 必须
  在 sharpy zone manager 里有对应。P1 实现支持 4 个 spot：natural / main_ramp /
  enemy_main / enemy_natural。其它 name 走 fallback `closest_to(known_spot)`，
  P5 时机做完整 spot name registry。
- **standing order 单位死亡**：P1 不自动 release，单位 invalid 后 sharpy 自然 skip。
  P5 加 EventBus 订阅 `UNIT_DESTROYED` filter `unit_tag in standing.assigned_tags`
  → 自动 release standing。
- **同样 standing order 重复发**：P1 简单 append 列表。未来加 dedup
  （`(selector, task)` 相同视为同一条，覆盖 issued_at）。
- ...（实施时持续补）
```

**Step 3: commit**

```bash
git add docs/adr/0010-four-layer-commands.md
git commit -m "docs(adr-0010): 补 P1 实施 corner case (P1.7)"
```

---

## Task P1.8: 更新 TASKS.md 标记 P1 done + 准备 P2

**Files:**
- Modify: `TASKS.md`

**Step 1: 把 P1.0-P1.7 八项打勾,P1 标 ✅ done**

**Step 2: 更新顶部「当前状态」段**

- HEAD 改为最后一个 commit
- 「最近几个 commit」加 P1.0-P1.7 commit
- 「下一步」改 P2 在最前

**Step 3: commit + push**

```bash
git add TASKS.md
git commit -m "TASKS.md:P1 done,准备 P2"
git push origin main
```

---

## 全 plan 收尾 verification

P1 全部 done 后：

```bash
# 后端
uv run --no-sync pytest                                    # 全过
uv run --no-sync pytest --cov=src/vibecraft --cov-report=term-missing
                                                            # 看新加代码覆盖率
uv run --no-sync ruff check . && uv run --no-sync ruff format --check .
uv run --no-sync mypy src/vibecraft

# 前端
cd web && npm test && npm run typecheck && npm run build

# e2e
uv run --no-sync python scripts/headless_smoke.py --fast \
  --initial-opening 1g_robo_immortal \
  --inject "那个农民守气矿别动" \
  --inject-after 5 --seconds 60
# 验 schema 不再 fail + standing_orders 含 1 条
```

全绿 = P1 完成，进 P2（L4 Production Overrides，详 ADR 0010 phasing 表）。
