"""TaskMonitor —— 每 sharpy step 检查 in-flight directive 是否完成。

设计文档: docs/plans/2026-05-17-task-completion-and-eventbus-design.md §3.4
ADR 0010 §8 done_when 决策摘要

职责:
- attach_directive: directive 进 board 时注册 EventBus listener + 初始化状态
- detach: directive complete/expire 时 unsubscribe 全部 listener + 清状态
- tick: 每 sharpy step 调, 检查 done_when 条件 + timeout, 返回 completed directive_id list

done_when 当前用 dict 形态 (过渡期, P3.1 补 pydantic discriminated union 后换强类型):
  {"kind": "time_elapsed_since", "seconds": 90, "ref": "directive_issued"}
  {"kind": "unit_count_built_since", "unit_type": "Sentry", "op": ">=", "value": 2}

P3.2 wire: Director._submit_directives 调 attach_directive,
           Director.on_tick 尾调 tick, 返回 id 列表后 board.complete()。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from vibecraft.bot.event_bus import Event, EventBus, EventKind

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# done_when checker registry
# ---------------------------------------------------------------------------

# kind → checker(done_when_dict, directive_id, game_state, monitor) -> bool
DONE_CHECKERS: dict[str, Callable[..., bool]] = {}


def register(kind: str) -> Callable[[Callable[..., bool]], Callable[..., bool]]:
    """注册 done_when checker 的装饰器。"""

    def decorator(fn: Callable[..., bool]) -> Callable[..., bool]:
        DONE_CHECKERS[kind] = fn
        return fn

    return decorator


# ---------------------------------------------------------------------------
# TaskMonitor
# ---------------------------------------------------------------------------


class TaskMonitor:
    """每 sharpy step 检查 in-flight directive 是否完成。

    board: DirectiveBoard (任意 duck-type 兼容即可, 单测里传 None 也 OK)
    event_bus: EventBus 实例
    """

    def __init__(self, board: Any, event_bus: EventBus) -> None:
        self.board = board
        self.event_bus = event_bus
        # directive_id → list of sub_id, detach 时统一 unsubscribe
        self._sub_ids: dict[str, list[int]] = {}
        # directive_id → accumulated unit_built count (event-driven, O(1) 增量)
        self._unit_built_counts: dict[str, int] = {}
        # directive_id → issued_at (for time_elapsed_since ref=directive_issued)
        self._issued_at: dict[str, float] = {}
        # directive_id → done_when dict
        self._done_when: dict[str, dict[str, Any]] = {}
        # directive_id → timeout_s (None = no timeout)
        self._timeout_s: dict[str, int | None] = {}

    def attach_directive(
        self,
        directive_id: str,
        done_when: dict[str, Any] | None,
        issued_at: float,
        timeout_s: int | None,
    ) -> None:
        """directive 进 board 时调, 设 listener + 状态初始化。

        done_when 是 dict 形态 (P3.1 retrofit pydantic 后再换强类型)。
        """
        self._issued_at[directive_id] = issued_at
        self._timeout_s[directive_id] = timeout_s
        self._sub_ids.setdefault(directive_id, [])

        if done_when is None:
            self._done_when[directive_id] = {}
            return

        self._done_when[directive_id] = done_when
        kind = done_when.get("kind")

        # unit_count_built_since: 订阅 UNIT_CREATED, 按 unit_type filter 后累加 counter
        if kind == "unit_count_built_since":
            ut = done_when.get("unit_type")
            self._unit_built_counts[directive_id] = 0

            def _handler(event: Event, _did: str = directive_id) -> None:
                self._unit_built_counts[_did] = self._unit_built_counts.get(_did, 0) + 1

            filter_fn: Callable[[Event], bool] | None
            if ut is not None:

                def _filter_with_type(
                    e: Event, _ut: str = ut, _iat: float = issued_at
                ) -> bool:
                    return e.unit_type == _ut and e.owner == "own" and e.ts >= _iat

                filter_fn = _filter_with_type
            else:

                def _filter_any_own(e: Event, _iat: float = issued_at) -> bool:
                    return e.owner == "own" and e.ts >= _iat

                filter_fn = _filter_any_own

            sub_id = self.event_bus.subscribe(EventKind.UNIT_CREATED, _handler, filter=filter_fn)
            self._sub_ids[directive_id].append(sub_id)

    def detach(self, directive_id: str) -> None:
        """directive complete/expire 时 unsubscribe 全部 listener + 清状态。"""
        for sub_id in self._sub_ids.pop(directive_id, []):
            self.event_bus.unsubscribe(sub_id)
        self._unit_built_counts.pop(directive_id, None)
        self._issued_at.pop(directive_id, None)
        self._done_when.pop(directive_id, None)
        self._timeout_s.pop(directive_id, None)

    def tick(self, now: float, game_state: Any) -> list[str]:
        """每 sharpy step 调。返回本 tick 该 mark completed 的 directive_id list。

        timeout 也在这里 check (独立于 done_when, 兜底防永不完成)。
        """
        completed: list[str] = []

        # 遍历当前 attach 的所有 directive
        for directive_id in list(self._issued_at.keys()):
            issued_at = self._issued_at[directive_id]
            timeout_s = self._timeout_s.get(directive_id)
            done_when = self._done_when.get(directive_id, {})

            # timeout 兜底 (优先于 done_when 检查)
            if timeout_s is not None and (now - issued_at) >= timeout_s:
                logger.debug(
                    "task_monitor timeout directive_id=%s elapsed=%.1f timeout=%d",
                    directive_id,
                    now - issued_at,
                    timeout_s,
                )
                completed.append(directive_id)
                continue

            # done_when 检查 (仅 done_when 非空时)
            if done_when:
                kind = done_when.get("kind")
                if kind is not None and kind in DONE_CHECKERS:
                    try:
                        if DONE_CHECKERS[kind](done_when, directive_id, game_state, self):
                            logger.debug(
                                "task_monitor done directive_id=%s kind=%s",
                                directive_id,
                                kind,
                            )
                            completed.append(directive_id)
                    except Exception as exc:
                        logger.warning(
                            "done_checker_error directive_id=%s kind=%s: %s",
                            directive_id,
                            kind,
                            exc,
                        )

        return completed


# ---------------------------------------------------------------------------
# Reference checkers
# ---------------------------------------------------------------------------


@register("time_elapsed_since")
def _check_time_elapsed_since(
    done_when: dict[str, Any],
    directive_id: str,
    game_state: Any,
    monitor: TaskMonitor,
) -> bool:
    """done_when: {kind, seconds, ref}

    ref: "directive_issued" | "game_start"
    seconds: float - 需要经过的秒数

    game_state 需要有 game_time: float 属性 (ref=game_start 时用)。
    ref=directive_issued 时比较 game_state.game_time - issued_at。
    """
    seconds = float(done_when.get("seconds", 0))
    ref = done_when.get("ref", "directive_issued")

    if ref == "directive_issued":
        issued_at = monitor._issued_at.get(directive_id)
        if issued_at is None:
            return False
        # game_time 从 game_state 取
        try:
            game_time = float(game_state.game_time)
        except Exception:
            return False
        return (game_time - issued_at) >= seconds

    if ref == "game_start":
        try:
            game_time = float(game_state.game_time)
        except Exception:
            return False
        return game_time >= seconds

    return False


@register("unit_count_built_since")
def _check_unit_count_built_since(
    done_when: dict[str, Any],
    directive_id: str,
    game_state: Any,
    monitor: TaskMonitor,
) -> bool:
    """done_when: {kind, unit_type, op, value}

    op: ">=" | ">" | "==" | "<=" | "<"
    monitor._unit_built_counts[directive_id] 由 EventBus UNIT_CREATED handler 维护。
    这里只比较 counter vs value。
    """
    count = monitor._unit_built_counts.get(directive_id, 0)
    value = int(done_when.get("value", 0))
    op = done_when.get("op", ">=")

    if op == ">=":
        return count >= value
    if op == ">":
        return count > value
    if op == "==":
        return count == value
    if op == "<=":
        return count <= value
    if op == "<":
        return count < value

    logger.warning("unknown op in unit_count_built_since: %s", op)
    return False
