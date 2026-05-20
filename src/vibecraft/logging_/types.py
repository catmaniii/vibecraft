"""Logger 的数据类型与流定义。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LogStream(str, Enum):
    """每条 stream 对应一个 JSONL 文件。

    枚举 value 即文件名（不含 .jsonl 后缀）。
    """

    EVENTS = "events"
    COMMANDS = "commands"
    DIRECTIVES = "directives"
    DECISIONS = "decisions"
    SC2_ACTIONS = "sc2_actions"
    METRICS = "metrics"
    ERRORS = "errors"
    WS_TRAFFIC = "ws_traffic"
    TELEMETRY = "telemetry"


class EventKind(str, Enum):
    """事件 kind（对应设计文档 §9.4 taxonomy）。

    新增 kind 时同时更新驾驶舱端 schema。
    """

    # 战略层
    STRATEGY_SET = "strategy.set"
    STRATEGY_PHASE_CHANGE = "strategy.phase_change"
    STRATEGY_ABORTED = "strategy.aborted"
    STRATEGY_TRANSITIONED = "strategy.transitioned"
    # 两层架构（2026-05-19）：cancel 触发的自动 persistent 切换 / 开局完成自动切
    STRATEGY_AUTO_SWITCH = "strategy.auto_switch"

    # 建造 / 科技
    BUILD_STARTED = "build.started"
    BUILD_COMPLETED = "build.completed"
    BUILD_CANCELLED = "build.cancelled"
    RESEARCH_COMPLETED = "research.completed"
    EXPANSION_COMPLETED = "expansion.completed"

    # 战斗 / 警报
    COMBAT_ENGAGED = "combat.engaged"
    COMBAT_RESOLVED = "combat.resolved"
    ALERT_ATTACKED = "alert.attacked"
    ALERT_BASE_HARASSED = "alert.base_harassed"
    ALERT_UNIT_LOST = "alert.unit_lost"

    # 敌情
    ENEMY_SPOTTED = "enemy.spotted"
    ENEMY_TECH_REVEALED = "enemy.tech_revealed"

    # Directive 生命周期
    DIRECTIVE_COMMITTED = "directive.committed"
    DIRECTIVE_RELEASED = "directive.released"
    DIRECTIVE_FAILED = "directive.failed"

    # Bot rationale
    DECISION_DELAYING_ATTACK = "decision.delaying_attack"
    DECISION_CHANGED_TARGET = "decision.changed_target"

    # Camera
    CAMERA_MOVED = "camera.moved"

    # 单位 role
    UNIT_ROLE_CHANGED = "unit.role_changed"


EventPriority = Literal["high", "medium", "low"]


class Event(BaseModel):
    """事件流通用 envelope。

    每条事件最终写一行 JSON 到 events.jsonl。其他 stream 用专门的 model。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ts: float = Field(description="游戏内秒")
    kind: EventKind
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: EventPriority = "medium"
    caused_by: str | None = Field(
        default=None,
        description="溯源链：例如 'voice:c_91f' / 'auto_transition' / 'bot_internal'",
    )
