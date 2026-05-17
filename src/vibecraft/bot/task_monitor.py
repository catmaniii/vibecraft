"""TaskMonitor —— 每 sharpy step 检查 in-flight directive 是否完成。

设计文档: docs/plans/2026-05-17-task-completion-and-eventbus-design.md §3.4
ADR 0010 §8 done_when 决策摘要

职责:
- attach_directive: directive 进 board 时注册 EventBus listener + 初始化状态
- detach: directive complete/expire 时 unsubscribe 全部 listener + 清状态
- tick: 每 sharpy step 调, 检查 done_when 条件 + timeout, 返回 completed directive_id list

done_when 支持 pydantic (有 model_dump) 或 dict（向后兼容）:
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
# named_spot whitelist (P5 扩展, P3 只支持 natural/third/main)
# ---------------------------------------------------------------------------

_NAMED_SPOT_WHITELIST = {"natural", "third", "main"}


def _resolve_named_spot(name: str, game_state: Any) -> Any | None:
    """把 named_spot 解析成位置对象。P3 只支持 natural/third/main, 其它 fallback None。

    game_state 需要有 enemy_start_locations / expansions_sorted (sharpy) 等。
    实际坐标解析留到 P5 再精化；P3 placeholder 只检查白名单。
    """
    if name not in _NAMED_SPOT_WHITELIST:
        logger.warning("named_spot '%s' 不在 P3 白名单 [natural, third, main]，checker 返回 False", name)
        return None
    # P3 placeholder: 返回 sentinel 让 caller 知道 spot 已知但坐标未实现
    return _NAMED_SPOT_PLACEHOLDER(name)


class _NAMED_SPOT_PLACEHOLDER:
    """P3 阶段 named_spot 的占位对象。P5 替换为真实坐标。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"NamedSpot({self.name!r})"


# ---------------------------------------------------------------------------
# army supply helper
# ---------------------------------------------------------------------------


def _supply_now(game_state: Any) -> float:
    """计算自方军队 supply (排除 worker/building)。

    sharpy game_state 上应有 supply_army 或 units.filter。
    P3 简化: 优先用 game_state.supply_army (sc2 标准属性)。
    若不可用, 静默返回 0.0。
    """
    try:
        return float(game_state.supply_army)
    except Exception:
        pass
    # fallback: 0
    return 0.0


# ---------------------------------------------------------------------------
# op compare helper
# ---------------------------------------------------------------------------


def _compare(lhs: float, op: str, rhs: float) -> bool:
    if op == ">=":
        return lhs >= rhs
    if op == ">":
        return lhs > rhs
    if op == "==":
        return lhs == rhs
    if op == "<=":
        return lhs <= rhs
    if op == "<":
        return lhs < rhs
    logger.warning("unknown op: %s", op)
    return False


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
        # directive_id → bool: UPGRADE_COMPLETE event 是否命中 (tech_done checker)
        self._tech_done_flags: dict[str, bool] = {}
        # directive_id → 初始 army supply snapshot (own_army_size_ratio checker)
        self._initial_army_supply: dict[str, float] = {}
        # directive_id → vision spell 开始时的 ts (None = 上 tick 不可见)
        self._vision_first_visible_ts: dict[str, float | None] = {}
        # directive_id → 累计 enemy killed 数量 (enemy_killed_in_area checker)
        self._enemy_killed_counts: dict[str, int] = {}

    def attach_directive(
        self,
        directive_id: str,
        done_when: Any,  # dict | pydantic BaseModel | None
        issued_at: float,
        timeout_s: int | None,
    ) -> None:
        """directive 进 board 时调, 设 listener + 状态初始化。

        done_when 接受 pydantic model (有 model_dump) 或 dict，向后兼容。
        """
        # pydantic → dict
        if done_when is not None and hasattr(done_when, "model_dump"):
            done_when = done_when.model_dump()

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

        # tech_done: 订阅 UPGRADE_COMPLETE, filter upgrade_id, 标 flag
        elif kind == "tech_done":
            upgrade_id = done_when.get("upgrade_id")
            self._tech_done_flags[directive_id] = False

            def _tech_handler(event: Event, _did: str = directive_id) -> None:
                self._tech_done_flags[_did] = True

            tech_filter: Callable[[Event], bool] | None = None
            if upgrade_id is not None:

                def _tech_filter(e: Event, _uid: str = upgrade_id) -> bool:
                    return e.payload.get("upgrade_id") == _uid

                tech_filter = _tech_filter

            sub_id = self.event_bus.subscribe(
                EventKind.UPGRADE_COMPLETE, _tech_handler, filter=tech_filter
            )
            self._sub_ids[directive_id].append(sub_id)

        # enemy_killed_in_area: 订阅 UNIT_DESTROYED, filter enemy + unit_type + area
        elif kind == "enemy_killed_in_area":
            self._enemy_killed_counts[directive_id] = 0
            area_name = done_when.get("area")
            unit_type = done_when.get("unit_type")

            def _kill_handler(event: Event, _did: str = directive_id) -> None:
                self._enemy_killed_counts[_did] = self._enemy_killed_counts.get(_did, 0) + 1

            def _kill_filter(
                e: Event, _ut: str | None = unit_type, _area: str | None = area_name
            ) -> bool:
                if e.owner != "enemy":
                    return False
                if _ut is not None and e.unit_type != _ut:
                    return False
                # area 检查: P3 用 payload.area 字段（publisher 填）
                if _area is not None:
                    event_area = e.payload.get("area")
                    if event_area != _area:
                        return False
                return True

            sub_id = self.event_bus.subscribe(
                EventKind.UNIT_DESTROYED, _kill_handler, filter=_kill_filter
            )
            self._sub_ids[directive_id].append(sub_id)

        # vision_acquired: 初始化 first_visible_ts (None = 当前不可见)
        elif kind == "vision_acquired":
            self._vision_first_visible_ts[directive_id] = None

    def detach(self, directive_id: str) -> None:
        """directive complete/expire 时 unsubscribe 全部 listener + 清状态。"""
        for sub_id in self._sub_ids.pop(directive_id, []):
            self.event_bus.unsubscribe(sub_id)
        self._unit_built_counts.pop(directive_id, None)
        self._issued_at.pop(directive_id, None)
        self._done_when.pop(directive_id, None)
        self._timeout_s.pop(directive_id, None)
        self._tech_done_flags.pop(directive_id, None)
        self._initial_army_supply.pop(directive_id, None)
        self._vision_first_visible_ts.pop(directive_id, None)
        self._enemy_killed_counts.pop(directive_id, None)

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

            # own_army_size_ratio: 首次 tick 时 snapshot 初始 supply
            if (
                done_when
                and done_when.get("kind") == "own_army_size_ratio"
                and directive_id not in self._initial_army_supply
                and game_state is not None
            ):
                self._initial_army_supply[directive_id] = _supply_now(game_state)

            # done_when 检查 (仅 done_when 非空时)
            if done_when:
                kind = done_when.get("kind")
                if kind is not None and kind in DONE_CHECKERS:
                    try:
                        if DONE_CHECKERS[kind](done_when, directive_id, game_state, self, now):
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
    now: float = 0.0,
) -> bool:
    """done_when: {kind, seconds, ref}

    ref: "directive_issued" | "game_start"
    seconds: float - 需要经过的秒数

    game_state 需要有 game_time: float 属性 (ref=game_start 时用)。
    ref=directive_issued 时比较 game_state.game_time - issued_at。
    """
    if game_state is None:
        return False
    seconds = float(done_when.get("seconds", 0))
    ref = done_when.get("ref", "directive_issued")

    if ref == "directive_issued":
        issued_at = monitor._issued_at.get(directive_id)
        if issued_at is None:
            return False
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
    now: float = 0.0,
) -> bool:
    """done_when: {kind, unit_type, op, value}

    op: ">=" | ">" | "==" | "<=" | "<"
    monitor._unit_built_counts[directive_id] 由 EventBus UNIT_CREATED handler 维护。
    这里只比较 counter vs value。
    """
    count = monitor._unit_built_counts.get(directive_id, 0)
    value = int(done_when.get("value", 0))
    op = done_when.get("op", ">=")
    return _compare(count, op, value)


# ---------------------------------------------------------------------------
# 6 个新 checker
# ---------------------------------------------------------------------------


@register("expansion_count")
def _check_expansion_count(
    done_when: dict[str, Any],
    directive_id: str,
    game_state: Any,
    monitor: TaskMonitor,
    now: float = 0.0,
) -> bool:
    """done_when: {kind, op, value} — 己方分基 (townhalls) 数量满足条件。

    game_state None → False。
    game_state.townhalls 需可 len()（sharpy/sc2 标准）。
    """
    if game_state is None:
        return False
    try:
        count = len(game_state.townhalls)
    except Exception:
        return False
    value = int(done_when.get("value", 0))
    op = done_when.get("op", ">=")
    return _compare(count, op, value)


@register("tech_done")
def _check_tech_done(
    done_when: dict[str, Any],
    directive_id: str,
    game_state: Any,
    monitor: TaskMonitor,
    now: float = 0.0,
) -> bool:
    """done_when: {kind, upgrade_id} — 升级完成。

    flag 由 attach_directive 订阅的 UPGRADE_COMPLETE handler 设置。
    game_state None 时 flag 可能已 True（event 先于 tick），仍返回 flag 值。
    """
    return bool(monitor._tech_done_flags.get(directive_id, False))


@register("target_destroyed")
def _check_target_destroyed(
    done_when: dict[str, Any],
    directive_id: str,
    game_state: Any,
    monitor: TaskMonitor,
    now: float = 0.0,
) -> bool:
    """done_when: {kind, target_kind, target_param, area}

    target_kind: "natural" | "third" | "main" | "building_at" | "unit_type"

    P3 实现:
    - natural/third/main → named_spot 白名单检查，坐标 P5 精化
      (P3 阶段：spot 解析成 _NAMED_SPOT_PLACEHOLDER，
       poll game_state.enemy_structures.closer_than 暂不可行，返回 False + warn)
    - unit_type → len(game_state.enemy_units.of_type(target_param)) == 0
    - building_at → P5 实现，P3 返回 False + warn
    """
    if game_state is None:
        return False

    target_kind = done_when.get("target_kind", "")
    target_param = done_when.get("target_param")

    if target_kind == "unit_type":
        if target_param is None:
            return False
        try:
            remaining = len(game_state.enemy_units.of_type(target_param))
            return remaining == 0
        except Exception:
            return False

    if target_kind in ("natural", "third", "main"):
        # P3: named_spot 白名单已支持，但坐标 poll 留 P5
        # 返回 False，不 warn（行为已经在 _resolve_named_spot 里记录）
        logger.debug(
            "target_destroyed target_kind=%s: 坐标 poll 留 P5，P3 返回 False", target_kind
        )
        return False

    # building_at 及其它
    logger.warning(
        "target_destroyed target_kind=%s 暂不支持（P5 实现），返回 False", target_kind
    )
    return False


@register("own_army_size_ratio")
def _check_own_army_size_ratio(
    done_when: dict[str, Any],
    directive_id: str,
    game_state: Any,
    monitor: TaskMonitor,
    now: float = 0.0,
) -> bool:
    """done_when: {kind, op, value} — 己方军队 supply 比初始快照满足条件。

    initial_supply 在首次 tick 时 snapshot (见 tick())。
    """
    if game_state is None:
        return False
    initial = monitor._initial_army_supply.get(directive_id)
    if initial is None:
        # 快照尚未建立（attach 后首个 tick 会建立）
        return False
    if initial == 0.0:
        # 初始即 0 army: ratio 定义为 1.0（全损）
        ratio = 1.0
    else:
        current = _supply_now(game_state)
        ratio = current / initial

    value = float(done_when.get("value", 0.0))
    op = done_when.get("op", "<=")
    return _compare(ratio, op, value)


@register("vision_acquired")
def _check_vision_acquired(
    done_when: dict[str, Any],
    directive_id: str,
    game_state: Any,
    monitor: TaskMonitor,
    now: float = 0.0,
) -> bool:
    """done_when: {kind, area, hold_seconds} — 在指定区域保持连续视野 >= hold_seconds 秒。

    实现: 用 wall-clock ts diff 而非 step count 计时。
    - 可见 → 如果 first_ts 为 None: 设 first_ts = now; 比较 now - first_ts >= hold_seconds
    - 不可见 → reset first_ts = None
    """
    if game_state is None:
        return False
    area_name = done_when.get("area", "")
    hold_seconds = float(done_when.get("hold_seconds", 0.0))

    point = _resolve_named_spot(area_name, game_state)
    if point is None:
        return False

    try:
        is_visible = bool(game_state.is_visible(point))
    except Exception:
        is_visible = False

    if is_visible:
        if monitor._vision_first_visible_ts.get(directive_id) is None:
            monitor._vision_first_visible_ts[directive_id] = now
        elapsed = now - monitor._vision_first_visible_ts[directive_id]  # type: ignore[operator]
        return elapsed >= hold_seconds
    monitor._vision_first_visible_ts[directive_id] = None
    return False


@register("enemy_killed_in_area")
def _check_enemy_killed_in_area(
    done_when: dict[str, Any],
    directive_id: str,
    game_state: Any,
    monitor: TaskMonitor,
    now: float = 0.0,
) -> bool:
    """done_when: {kind, area, unit_type, op, value} — 区域内击杀敌方单位数满足条件。

    counter 由 attach_directive 订阅的 UNIT_DESTROYED handler 维护。
    """
    count = monitor._enemy_killed_counts.get(directive_id, 0)
    value = int(done_when.get("value", 0))
    op = done_when.get("op", ">=")
    return _compare(count, op, value)


# ---------------------------------------------------------------------------
# 复合 checker: any_of / all_of (recursive)
# ---------------------------------------------------------------------------


@register("any_of")
def _check_any_of(
    done_when: dict[str, Any],
    directive_id: str,
    game_state: Any,
    monitor: TaskMonitor,
    now: float = 0.0,
) -> bool:
    """done_when: {kind, conditions: [...]} — 任意一个子条件满足即 done。"""
    conditions = done_when.get("conditions", [])
    for cond in conditions:
        if isinstance(cond, dict):
            cond_dict = cond
        elif hasattr(cond, "model_dump"):
            cond_dict = cond.model_dump()
        else:
            continue
        kind = cond_dict.get("kind")
        if kind and kind in DONE_CHECKERS:
            try:
                if DONE_CHECKERS[kind](cond_dict, directive_id, game_state, monitor, now):
                    return True
            except Exception as exc:
                logger.warning("any_of sub-checker error kind=%s: %s", kind, exc)
    return False


@register("all_of")
def _check_all_of(
    done_when: dict[str, Any],
    directive_id: str,
    game_state: Any,
    monitor: TaskMonitor,
    now: float = 0.0,
) -> bool:
    """done_when: {kind, conditions: [...]} — 所有子条件都满足才 done。"""
    conditions = done_when.get("conditions", [])
    if not conditions:
        return False
    for cond in conditions:
        if isinstance(cond, dict):
            cond_dict = cond
        elif hasattr(cond, "model_dump"):
            cond_dict = cond.model_dump()
        else:
            return False
        kind = cond_dict.get("kind")
        if kind and kind in DONE_CHECKERS:
            try:
                if not DONE_CHECKERS[kind](cond_dict, directive_id, game_state, monitor, now):
                    return False
            except Exception as exc:
                logger.warning("all_of sub-checker error kind=%s: %s", kind, exc)
                return False
        else:
            return False
    return True
