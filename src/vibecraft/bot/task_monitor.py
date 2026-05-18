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
import re
from collections.abc import Callable
from typing import Any

from vibecraft.bot.event_bus import Event, EventBus, EventKind

logger = logging.getLogger(__name__)


def _normalize_upgrade_id(s: str) -> str:
    """归一化 upgrade id 字符串便于跨格式比较。

    输入可能形式：
    - "UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1" (str(enum))
    - "PROTOSSGROUNDWEAPONSLEVEL1" (enum.name)
    - "ProtossGroundWeapons" (LLM 给的 canonical 无 LEVEL 后缀)
    - "BlinkTech" / "BLINKTECH" / "blink" (各种 case + Tech 后缀)

    归一化：去 "UpgradeId." 前缀 → upper → 去 LEVEL[0-9]+ 后缀 → 去 TECH 后缀
    例：
      "UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1" → "PROTOSSGROUNDWEAPONS"
      "ProtossGroundWeapons"               → "PROTOSSGROUNDWEAPONS"
      "BlinkTech" / "BLINKTECH"            → "BLINK"
      "PsiStormTech"                       → "PSISTORM"
    """
    if not s:
        return ""
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    s = s.upper()
    s = re.sub(r"LEVEL\d+$", "", s)
    if s.endswith("TECH"):
        s = s[:-4]
    return s


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
# named_spot whitelist (P3 fallback; P5 改走 NamedSpotRegistry)
# ---------------------------------------------------------------------------

_NAMED_SPOT_WHITELIST = {"natural", "third", "main"}


def _resolve_named_spot(name: str, game_state: Any) -> Any | None:
    """把 named_spot 解析成位置对象。

    P5.C: 优先用 game_state.named_spots.resolve(name, game_state)（bot 含 NamedSpotRegistry）。
    只在 named_spots 是 NamedSpotRegistry 实例时走新路径，避免 MagicMock 自动创建属性
    干扰单测 fallback 逻辑。
    若 game_state 无真实 named_spots 则 fallback 到 P3 白名单占位逻辑，
    保证现有 P3.3 测试不受影响。
    """
    if game_state is not None:
        from vibecraft.bot.named_spot import NamedSpotRegistry

        registry = getattr(game_state, "named_spots", None)
        if isinstance(registry, NamedSpotRegistry):
            return registry.resolve(name, game_state)

    # P3 fallback: 只支持 natural/third/main，其它返回 None
    if name not in _NAMED_SPOT_WHITELIST:
        logger.warning(
            "named_spot '%s' 不在 P3 白名单 [natural, third, main]，checker 返回 False", name
        )
        return None
    # P3 placeholder: 返回 sentinel 让 caller 知道 spot 已知但坐标未实现
    return _NAMED_SPOT_PLACEHOLDER(name)


class _NAMED_SPOT_PLACEHOLDER:
    """P3 阶段 named_spot 的占位对象（fallback 路径用，P5 registry 路径不经此）。"""

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
        import time as _time

        self.board = board
        self.event_bus = event_bus
        # M3 fix:wall-time monotonic fn(注入式,单测可替换)。timeout 用
        # wall time 不用 game time —— fast mode game time 跑得快会误触发。
        self._monotonic: Any = _time.monotonic
        # directive_id → list of sub_id, detach 时统一 unsubscribe
        self._sub_ids: dict[str, list[int]] = {}
        # directive_id → unit_type → accumulated unit_built count（per-type 计数，
        # 让 all_of([unit_count_built_since(Zealot,2), unit_count_built_since(Stalker,3)])
        # 两条子条件各自独立计数。unit_type=None 时用 "*" 当 key 表示"任意单位"。
        self._unit_built_counts: dict[str, dict[str, int]] = {}
        # directive_id → issued_at (for time_elapsed_since ref=directive_issued)
        self._issued_at: dict[str, float] = {}
        # M3 fix:directive attach 时的 wall-clock(monotonic 秒)。timeout 兜底
        # 用 wall time 不用 game time —— fast mode 下 game time 跑得快,
        # game-time-based timeout 在 wall ~2-3s 误触发,把 directive 干掉
        # 让 bot.train 没机会真造完单位(L4 真出兵 verify 暴露)。
        self._issued_wall: dict[str, float] = {}
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
        self._issued_wall[directive_id] = self._monotonic()
        self._timeout_s[directive_id] = timeout_s
        self._sub_ids.setdefault(directive_id, [])
        self._unit_built_counts.setdefault(directive_id, {})

        if done_when is None:
            self._done_when[directive_id] = {}
            return

        self._done_when[directive_id] = done_when
        self._attach_subscriptions(directive_id, done_when, issued_at)

    def _attach_subscriptions(
        self, directive_id: str, done_when: dict[str, Any], issued_at: float
    ) -> None:
        """递归遍历 done_when，给每个 leaf checker 装 event 订阅。

        all_of / any_of 的多个 unit_count_built_since 子条件各自独立计数
        （per-unit_type）；其余 event-driven checker（tech_done / enemy_killed_in_area）
        在嵌套 all_of 多实例时仍共享单 flag/counter（罕见，先不优化）。
        """
        kind = done_when.get("kind")
        if kind in ("all_of", "any_of"):
            for sub in done_when.get("conditions", []):
                if isinstance(sub, dict):
                    sub_dict = sub
                elif hasattr(sub, "model_dump"):
                    sub_dict = sub.model_dump()
                else:
                    continue
                self._attach_subscriptions(directive_id, sub_dict, issued_at)
            return

        # unit_count_built_since: 订阅 UNIT_CREATED, 按 unit_type filter 后累加 per-type counter
        if kind == "unit_count_built_since":
            ut = done_when.get("unit_type")
            counter_key = ut or "*"
            self._unit_built_counts[directive_id].setdefault(counter_key, 0)

            def _handler(event: Event, _did: str = directive_id, _key: str = counter_key) -> None:
                self._unit_built_counts[_did][_key] = self._unit_built_counts[_did].get(_key, 0) + 1

            filter_fn: Callable[[Event], bool] | None
            if ut is not None:

                def _filter_with_type(e: Event, _ut: str = ut, _iat: float = issued_at) -> bool:
                    if e.owner != "own" or e.ts < _iat:
                        return False
                    # 兼容多种 unit_type 格式:
                    # - publisher 写 str(unit.type_id) = "UnitTypeId.ZEALOT"
                    # - 或 unit.type_id.name = "ZEALOT"
                    # - 或 LLM payload 给 "Zealot"
                    # 全部 normalize 成 UPPER + 取最后段比较
                    e_ut = (e.unit_type or "").rsplit(".", 1)[-1].upper()
                    target = _ut.upper()
                    return e_ut == target

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
                # 两边归一化（处理 "ProtossGroundWeapons" vs
                # "PROTOSSGROUNDWEAPONSLEVEL1" 这种 LLM canonical vs python-sc2 enum
                # 命名差异）。详见 _normalize_upgrade_id。
                expected = _normalize_upgrade_id(upgrade_id)

                def _tech_filter(e: Event, _expected: str = expected) -> bool:
                    actual = e.payload.get("upgrade_id", "")
                    return _normalize_upgrade_id(actual) == _expected

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
        self._issued_wall.pop(directive_id, None)
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
            timeout_s = self._timeout_s.get(directive_id)
            done_when = self._done_when.get(directive_id, {})

            # timeout 兜底 (优先于 done_when 检查)。用 wall time 不用 game time:
            # fast mode game time 跑得快,game-time-based timeout 在 wall ~2-3s
            # 误触发(L4 真出兵 verify 暴露)。done_when 字段(如 time_elapsed_since
            # 仍用 game time,因为玩家说"30 秒后撤"通常指游戏内时间)。
            issued_wall = self._issued_wall.get(directive_id)
            if timeout_s is not None and issued_wall is not None:
                elapsed_wall = self._monotonic() - issued_wall
                if elapsed_wall >= timeout_s:
                    logger.debug(
                        "task_monitor timeout directive_id=%s elapsed_wall=%.1f timeout=%d",
                        directive_id,
                        elapsed_wall,
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
            # sharpy bot 真实 attr 是 .time (python-sc2 BotAI 标准),
            # mock 测试常用 .game_time -- 兜底两个
            game_time = float(
                getattr(game_state, "game_time", None)
                if getattr(game_state, "game_time", None) is not None
                else game_state.time
            )
        except Exception:
            return False
        return (game_time - issued_at) >= seconds

    if ref == "game_start":
        try:
            # sharpy bot 真实 attr 是 .time (python-sc2 BotAI 标准),
            # mock 测试常用 .game_time -- 兜底两个
            game_time = float(
                getattr(game_state, "game_time", None)
                if getattr(game_state, "game_time", None) is not None
                else game_state.time
            )
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
    monitor._unit_built_counts[did][unit_type] 由 EventBus UNIT_CREATED handler 维护
    (per-unit_type 计数，让 all_of 多个 unit_count_built_since 各自独立计数)。
    unit_type=None 时用 "*" key 表示"任意单位"。
    """
    counts_by_type = monitor._unit_built_counts.get(directive_id, {})
    counter_key = done_when.get("unit_type") or "*"
    count = counts_by_type.get(counter_key, 0)
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

    P5 实现:
    - natural/third/main → 转换为 enemy_natural/enemy_third/enemy_main，
      用 game_state.named_spots.resolve(spot_name, game_state) 解析坐标，
      poll game_state.enemy_structures.closer_than(8, pos) 检查是否清空
    - unit_type → len(game_state.enemy_units.of_type(target_param)) == 0
    - building_at → P5 范围外，返回 False + warn
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
        # P5: 转换为敌方 spot，用 NamedSpotRegistry 解析坐标
        spot_name = f"enemy_{target_kind}"
        pos = _resolve_named_spot(spot_name, game_state)
        if pos is None:
            logger.debug(
                "target_destroyed target_kind=%s: spot=%s 解析返回 None，返回 False",
                target_kind,
                spot_name,
            )
            return False
        try:
            remaining = len(game_state.enemy_structures.closer_than(8, pos))
            return remaining == 0
        except Exception:
            return False

    # building_at 及其它
    logger.warning("target_destroyed target_kind=%s 暂不支持（P5 范围外），返回 False", target_kind)
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


# ---------------------------------------------------------------------------
# P0d Task 6: L4 done_when 扩词表 checker
# ---------------------------------------------------------------------------


def _resolve_typeid(name: str) -> Any:
    """str → UnitTypeId enum；未知或 sc2 不可用返回 None。"""
    try:
        from sc2.ids.unit_typeid import UnitTypeId

        return UnitTypeId[name.upper()]
    except (KeyError, ImportError):
        return None


@register("structure_count")
def _check_structure_count(
    done_when: dict[str, Any],
    directive_id: str,
    game_state: Any,
    monitor: TaskMonitor,
    now: float = 0.0,
) -> bool:
    """done_when: {kind, structure_type, op, value} — 己方建筑数（ready + pending）。

    structure_type: UnitTypeId 名称字符串（如 "Gateway"）。
    未知 type 或 game_state None → False。
    """
    if game_state is None:
        return False
    type_id = _resolve_typeid(done_when["structure_type"])
    if type_id is None:
        return False
    try:
        current = game_state.structures(type_id).amount + int(game_state.already_pending(type_id))
    except Exception:
        return False
    return _compare(current, done_when["op"], int(done_when["value"]))


@register("own_unit_count")
def _check_own_unit_count(
    done_when: dict[str, Any],
    directive_id: str,
    game_state: Any,
    monitor: TaskMonitor,
    now: float = 0.0,
) -> bool:
    """done_when: {kind, unit_type, op, value} — 己方单位数（ready + pending）。

    unit_type: UnitTypeId 名称字符串（如 "Immortal"）。
    未知 type 或 game_state None → False。
    """
    if game_state is None:
        return False
    type_id = _resolve_typeid(done_when["unit_type"])
    if type_id is None:
        return False
    try:
        current = game_state.units(type_id).amount + int(game_state.already_pending(type_id))
    except Exception:
        return False
    return _compare(current, done_when["op"], int(done_when["value"]))


@register("supply_used")
def _check_supply_used(
    done_when: dict[str, Any],
    directive_id: str,
    game_state: Any,
    monitor: TaskMonitor,
    now: float = 0.0,
) -> bool:
    """done_when: {kind, op, value} — 当前已用人口满足条件。"""
    if game_state is None:
        return False
    return _compare(game_state.supply_used, done_when["op"], done_when["value"])


@register("supply_cap")
def _check_supply_cap(
    done_when: dict[str, Any],
    directive_id: str,
    game_state: Any,
    monitor: TaskMonitor,
    now: float = 0.0,
) -> bool:
    """done_when: {kind, op, value} — 当前人口上限满足条件。"""
    if game_state is None:
        return False
    return _compare(game_state.supply_cap, done_when["op"], done_when["value"])


@register("minerals")
def _check_minerals(
    done_when: dict[str, Any],
    directive_id: str,
    game_state: Any,
    monitor: TaskMonitor,
    now: float = 0.0,
) -> bool:
    """done_when: {kind, op, value} — 当前矿物量满足条件。"""
    if game_state is None:
        return False
    return _compare(game_state.minerals, done_when["op"], done_when["value"])


@register("gas")
def _check_gas(
    done_when: dict[str, Any],
    directive_id: str,
    game_state: Any,
    monitor: TaskMonitor,
    now: float = 0.0,
) -> bool:
    """done_when: {kind, op, value} — 当前气矿量满足条件。"""
    if game_state is None:
        return False
    return _compare(game_state.gas, done_when["op"], done_when["value"])


@register("worker_count")
def _check_worker_count(
    done_when: dict[str, Any],
    directive_id: str,
    game_state: Any,
    monitor: TaskMonitor,
    now: float = 0.0,
) -> bool:
    """done_when: {kind, op, value} — 当前探机数量满足条件。"""
    if game_state is None:
        return False
    return _compare(game_state.workers.amount, done_when["op"], done_when["value"])
