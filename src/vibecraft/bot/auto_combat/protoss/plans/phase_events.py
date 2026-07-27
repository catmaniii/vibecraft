"""EmitPhaseEventAct: phase 事件 latch act —— 通用 phase 触发信号。

放在独立模块(不寄居 gate4_pressure.py)是为了 minimal import 链:测试时只需
fake sharpy.plans.acts.ActBase,不需要 fake gate4_pressure 整条 vibecraft plan
依赖树(forward_proxy / forward_rally / forward_warp / vibecraft_zone_attack ...)。

用途
====
配合 `Phase.start_at_event` —— bot 在某事件首次满足时调
`director.notify_phase_event(<name>)`,Director._compute_current_phase_id 据此
推 Phase 已开始。典型场景:
  - "野水晶真建好"(supply 估计不准)
  - "DT 真杀到农民"(supply/time 阈值估不出来)

用法
====
    EmitPhaseEventAct(
        "dt_rush_forward_pylon_ready",
        lambda ai: <check forward PYLON ready>,
    )

事件名约定加 strategy_id 前缀,避免跨 strategy 冲突。
Director 不存在(测试)时 silent skip。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sharpy.plans.acts import ActBase

logger = logging.getLogger(__name__)


class EmitPhaseEventAct(ActBase):  # type: ignore[misc]
    """phase 事件首次满足时,通知 Director 注册事件 — 一次性 latch。"""

    def __init__(self, event_name: str, check_fn: Callable[[Any], bool]) -> None:
        super().__init__()
        self._event_name = event_name
        self._check_fn = check_fn
        self._signaled = False

    async def execute(self) -> bool:
        if self._signaled:
            return True
        try:
            ok = bool(self._check_fn(self.ai))
        except Exception:
            return False
        if not ok:
            return False
        director = getattr(self.ai, "director", None)
        if director is not None:
            try:
                director.notify_phase_event(self._event_name)
                logger.info(
                    "phase event signaled: %s (game_t=%.1f)",
                    self._event_name,
                    float(getattr(self.ai, "time", 0.0)),
                )
            except Exception as exc:
                logger.warning("notify_phase_event(%s) fail: %s", self._event_name, exc)
        self._signaled = True
        return True
