"""EventBus —— vibecraft 自建 pub/sub,把 python-sc2 11 个 lifecycle hook 中心化分发。

详 docs/adr/0010-four-layer-commands.md §9（task completion +
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
from dataclasses import dataclass, field
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
    filter: Filter = field(default=None)


class EventBus:
    """中心化 pub/sub。线程不安全(sharpy step 单线程跑,够用)。"""

    def __init__(self) -> None:
        self._subs: dict[EventKind, list[_Subscription]] = defaultdict(list)
        self._next_id: int = 1

    def subscribe(self, kind: EventKind, handler: Handler, filter: Filter = None) -> int:
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
        for sub in list(self._subs[event.kind]):
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
