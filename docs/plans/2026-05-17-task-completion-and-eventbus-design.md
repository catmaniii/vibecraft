# 任务完成判定 + EventBus 设计

> 创建于 2026-05-17。four-layer plan §7 P3 (L2 Tactics) + ADR 0010 配套设计。
> 本文档定下 L2/L4 directive 怎么自动判定完成、EventBus 在 vibecraft 端的形态。

## 一、问题

four-layer plan 里 L2 `TacticalObjective`（如「攻击自然」）和 L4 `ProductionOverride`
（如「下个 BG 出 2 哨兵」）需要 `status: pending/executing/completed/failed`，但
**谁来更新 status?怎么判断完成?**

- L1 strategy_set：现有 phase 机制 + `_pending_recommendation`，**不在本文档范围**
- L3 standing_order：`persistent`，**玩家撤销才完，没 auto-done**
- **L2/L4：本文档解决**

## 二、关键决策（已拍）

### 决策 1：bot 内闭环判定，不上 supervisor agent

排除"加 supervisor agent 调 LLM 判定"路径。原因：
- 底层 sharpy bot 是 deterministic step loop（45ms/step），LLM call 1-3s 等不起
- vibecraft 本来就靠玩家 10s 一令做"高级 reasoning"，supervisor 抢玩家角色
- bot 内闭环 = 一个 process 一套代码，可测可重放

### 决策 2：LLM 输出 structured done_when（discriminated union），不是 DSL 字符串

LLM 解析话语时**一次输出** directive + done_when：

```jsonc
{
  "type": "tactical_objective",
  "payload": { "verb": "attack", "target_area": "enemy_natural" },
  "done_when": { "kind": "any_of", "conditions": [
    {"kind": "target_destroyed", "target_kind": "natural"},
    {"kind": "own_army_size_ratio", "op": "<=", "value": 0.3}
  ]},
  "timeout_s": 90
}
```

bot 端**纯 Python dispatcher**（没 DSL eval、没 LLM call）：

```python
def task_check(directive, game_state):
    return DONE_CHECKERS[directive.done_when.kind](directive.done_when, directive, game_state)
```

**为什么不用 DSL**：
- vibecraft 现有 DSL 是给**剧本 YAML 字符串**用的（沙箱安全 + 可重放）
- LLM 即时生成完成条件场景下，pydantic discriminated union 更优势：
  - schema 严格（编译期 validate，不用 retry-runtime-fail）
  - 跟现有 Directive schema 同形态
  - 玩家可见性好（PWA 直接显示 nested dict）
  - 是 AI app 主流模式（function calling / tool use）

DSL 保留剧本 YAML 阵地。

### 决策 3：8 个 condition kind 起步 + `any_of`/`all_of` 复合

| kind | 用途 | params |
|---|---|---|
| `unit_count_built_since` | L4 出 N 兵 | unit_type, op, value |
| `tech_done` | L4 研升级 | upgrade_id |
| `expansion_count` | L4 开矿 | op, value |
| `target_destroyed` | L2 attack | target_kind, target_param |
| `own_army_size_ratio` | L2 安全撤 | op, value |
| `vision_acquired` | L2 scout | area, hold_seconds |
| `enemy_killed_in_area` | L2 harass | area, unit_type, op, value |
| `time_elapsed_since` | 通用 timing | seconds, ref |
| `any_of` / `all_of` | 复合 | conditions: list |

实施中不够再加。覆盖 L2 11 verb × 典型 done 约 90%。

### 决策 4：LLM 输出 done_when 后 validate + retry 1 次（最严格）

`IntentParser` 在 LLM 返回后：
1. Pydantic validate done_when（discriminated union 自动 check kind + params）
2. 不通过 → 把 `validation_error` 回灌 LLM retry 1 次
3. 仍不通过 → 设 `done_when=None`（EPHEMERAL，一次执行后失效）+ echo 告诉玩家
   "完成条件无效已降级"

worst case 2x LLM call，token 翻倍。**正确性优先于成本**。

### 决策 5：每个 done_when 必带 `timeout_s` 兜底

避免 LLM 写的 condition 永不成立 → bot 永不让位。默认 by verb：

| verb | timeout_s |
|---|---|
| attack | 120 |
| defend | 180 |
| scout | 60 |
| harass | 90 |
| expand | 60 |
| production | 60 |
| tech | by upgrade（研究时间 + 30s buffer）|
| vision | 30 |

玩家明示（"打 5 分钟不行就撤"）→ LLM 在 done_when 加 `time_elapsed_since` OR
override `timeout_s`。

### 决策 6：EventBus 是 vibecraft 自建独立层，不复用 sharpy listener registry

排除"扩 sharpy `_on_unit_destroyed_listeners` pattern 到 11 个 hook"路径，原因：
- 要碰 `vendor/sharpy/`（vendor 是 read-only，fork 维护成本）
- sharpy listener 只是 `func(event)`，没 filter；EventBus 要"by unit_type / by area /
  by owner"过滤
- vibecraft 已有 polling 风格 `DecisionWatcher`，未来 unify 到 EventBus 自然

## 三、EventBus 设计

### 3.1 形态

`src/vibecraft/bot/event_bus.py` 新文件：

```python
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

class EventKind(str, Enum):
    UNIT_CREATED = "unit_created"
    UNIT_DESTROYED = "unit_destroyed"
    UNIT_TYPE_CHANGED = "unit_type_changed"
    BUILDING_STARTED = "building_started"
    BUILDING_COMPLETE = "building_complete"
    UPGRADE_COMPLETE = "upgrade_complete"
    UNIT_TOOK_DAMAGE = "unit_took_damage"
    ENEMY_UNIT_ENTERED_VISION = "enemy_unit_entered_vision"
    ENEMY_UNIT_LEFT_VISION = "enemy_unit_left_vision"
    SC2_ALERT = "sc2_alert"  # 核弹 / building under attack / 自然 alerts

@dataclass(frozen=True)
class Event:
    kind: EventKind
    ts: float                      # bot.time (game time)
    payload: dict[str, Any]        # 按 kind 不同的字段
    # 公共可选字段（payload 里也有,这里冗余便于 filter）:
    owner: str | None = None       # "own" / "enemy" / "neutral"
    unit_tag: int | None = None
    unit_type: str | None = None
    position: tuple[float, float] | None = None

# Filter = Callable[[Event], bool],由 subscriber 自己写 lambda
Filter = Callable[[Event], bool] | None

@dataclass
class Subscription:
    sub_id: int
    kind: EventKind
    handler: Callable[[Event], None]
    filter: Filter

class EventBus:
    def __init__(self) -> None:
        self._subs: dict[EventKind, list[Subscription]] = defaultdict(list)
        self._next_id: int = 1

    def subscribe(
        self, kind: EventKind, handler: Callable[[Event], None],
        filter: Filter = None,
    ) -> int:
        sub = Subscription(self._next_id, kind, handler, filter)
        self._subs[kind].append(sub)
        self._next_id += 1
        return sub.sub_id

    def unsubscribe(self, sub_id: int) -> None:
        for kind, subs in self._subs.items():
            subs[:] = [s for s in subs if s.sub_id != sub_id]

    def publish(self, event: Event) -> None:
        for sub in self._subs[event.kind]:
            if sub.filter and not sub.filter(event):
                continue
            try:
                sub.handler(event)
            except Exception as exc:
                # 一个 handler 挂不影响其它
                logger.warning("event_handler_error kind=%s sub=%d: %s",
                               event.kind, sub.sub_id, exc)
```

**handler 同步**（不 async），因为 sharpy step 是同步 Python 调用栈，async handler
要 schedule task 不直观。如果未来需要 async handler，加 `subscribe_async`。

### 3.2 wire 到 bot

`_VibeCraftProtossBot.on_<xxx>` 11 个 hook 内部 publish：

```python
class _VibeCraftProtossBot(KnowledgeBot):
    def __init__(self, ...):
        super().__init__(...)
        self.event_bus = EventBus()

    async def on_unit_destroyed(self, unit_tag: int) -> None:
        # publish 到 bus
        unit = self._enemy_units_dict.get(unit_tag) or self._own_units_dict.get(unit_tag)
        self.event_bus.publish(Event(
            kind=EventKind.UNIT_DESTROYED,
            ts=self.time,
            payload={"unit_tag": unit_tag, "unit_obj": unit},
            owner=("own" if unit and unit.alliance == 1 else "enemy" if unit else None),
            unit_tag=unit_tag,
            unit_type=str(unit.type_id) if unit else None,
            position=(unit.position.x, unit.position.y) if unit else None,
        ))
        await super().on_unit_destroyed(unit_tag)   # 让 sharpy 自己跑

    async def on_unit_created(self, unit) -> None:
        self.event_bus.publish(Event(
            kind=EventKind.UNIT_CREATED, ...))
        await super().on_unit_created(unit)

    # ... 11 个 hook 类似
```

### 3.3 subscriber 谁

**首批 subscribers**:

1. **task_monitor**（本文档主角）订阅 8 个 done_when kind 需要的 event：
   - `unit_count_built_since` 订阅 `UNIT_CREATED` 累加 counter
   - `enemy_killed_in_area` 订阅 `UNIT_DESTROYED` 累加 counter
   - `tech_done` 订阅 `UPGRADE_COMPLETE`
   - 其余靠 game_state polling（vision / army_ratio / target_destroyed / expansion_count
     / time_elapsed）—— 不需要 event
2. **DecisionWatcher 未来重构**：现有 polling diff 模式可改为订阅相关 event
3. **standing_order revoke trigger**（P5 范围）：standing order 也可以 trigger "敌方
   某事件 → 自动 release"

### 3.4 task_monitor 用 EventBus 的具体形态

```python
class TaskMonitor:
    def __init__(self, bot, board, event_bus):
        self.bot = bot
        self.board = board
        self.event_bus = event_bus
        # event-driven counters,O(1) 增量更新:
        self._unit_built_counts: dict[directive_id, dict[unit_type, int]] = ...
        self._enemy_killed_counts: dict[directive_id, dict[area, int]] = ...

    def attach_directive(self, directive):
        """directive 进 board 时调,设 listener"""
        if not directive.done_when:
            return
        kind = directive.done_when.kind
        if kind == "unit_count_built_since":
            ut = directive.done_when.unit_type
            self.event_bus.subscribe(
                EventKind.UNIT_CREATED,
                handler=lambda e: self._inc_unit_built(directive.id, ut),
                filter=lambda e: e.unit_type == ut and e.owner == "own"
                                  and e.ts >= directive.issued_at,
            )
        # ... 类似处理其它 event-driven kind

    def tick(self, now):
        """on_step 调用,polling 那部分 + check completion"""
        for d in self.board.in_flight():
            if d.done_when is None:
                continue
            if self._is_done(d, now):
                self.board.complete(d.id, now)
            elif now - d.issued_at > d.timeout_s:
                self.board.expire(d.id, now, reason="timeout")
```

### 3.5 schema 暴露给 LLM？

**不暴露**。LLM 看到的还是 8 个 done_when kind（决策 3 列的）。EventBus 是 vibecraft
内部基础设施，是 done_when kind 的"高效实现技术"，玩家 / LLM 都不需要知道。

未来如果要让 LLM 表达"对方造 Starport 就转空军"，**加新 done_when kind**
`enemy_building_appeared`（params: `unit_type`, `area`），内部用 EventBus 订阅
`BUILDING_COMPLETE` filter `owner=enemy`。

## 四、与 four-layer plan / ADR 0010 的对齐

- **plan §2 Director 数据结构**：`Director` 加 `self.task_monitor: TaskMonitor`
- **plan §6 snapshot 新字段**：每个 directive view 透传 `done_when` + 当前 progress
  （e.g. unit_count_built_since 显示 "1 / 2 完成"），玩家 PWA 可见
- **ADR 0010**：加一节"EventBus 设计"引本文档
- **plan §7 P3 实施**：本文档定义了 P3 的核心机制（done_when schema + LLM 教法 +
  task_monitor + EventBus）

## 五、跟 sharpy 的边界

| sharpy 自己有的 | vibecraft 这边怎么用 |
|---|---|
| 11 个 lifecycle hook | **vibecraft override 并 publish 到 EventBus**，然后 `await super()` 让 sharpy 自己的逻辑跑 |
| `register_on_unit_destroyed_listener`（sharpy 自带 registry）| **vibecraft 不用**，自己的 EventBus 覆盖了 |
| Managers 内部 polling state（如 `enemy_units_manager`）| **vibecraft 不动**，sharpy 自治 |
| KnowledgeBot 抽象 | vibecraft 继承，不替换 |

## 六、实施分期（在 four-layer plan P1-P6 框架内）

| 时点 | 内容 |
|---|---|
| P1 一起加 | EventBus skeleton + `_VibeCraftProtossBot` 11 个 hook publish + 单测 |
| P1 | `unit_count_built_since` 用 EventBus 实现作为 reference |
| P2 | L4 production override 走 done_when（决策 D 同 L2） |
| P3 主菜 | task_monitor 完整实现 + 8 个 kind dispatcher + validation retry + timeout |
| P3 | LLM prompt 加 done_when few-shot + schema |
| P5 | sharpy plan 让位机制，directive completed → release `LLM_CONTROLLED` tags |
| P6 | headless smoke 验证：inject "切 4BG，打到对方自然 OR 损失 70% 撤"，看 task_monitor 完成 |

## 七、风险

1. **LLM 写 done_when 不合理**（如 `vision_acquired hold_seconds: 9999`）→ timeout
   兜底，但玩家体验差。**缓解**：prompt 教典型值范围 + PWA 显示 done_when 给玩家
   review 机会
2. **EventBus handler 异常拖慢 step**：handler 同步执行，慢 handler 会拖。**缓解**：
   try/except + 慢 handler log warning，未来支持 async handler
3. **directive 完成后 listener 没 unsubscribe → 内存泄漏**：`TaskMonitor.attach_directive`
   要记 sub_id，`complete/expire` 时统一 `unsubscribe`
4. **EventBus + DecisionWatcher 双写**：P3 前期 DecisionWatcher 不动（polling），
   P6 之后再考虑 unify
