"""结构化 JSONL 日志层。

设计约束（见 docs/plans/2026-05-14-vibecraft-design.md §11.4）：

- 一场游戏一个目录 `logs/<game_id>/`
- 每类事件一个 JSONL 文件：events / commands / directives / decisions /
  sc2_actions / metrics / errors / ws_traffic
- 单次 LLM 调用全量保留到 `logs/<game_id>/llm_calls/call_NNN.json`
- 异步写入：调用方塞 queue，后台 flush 协程批量落盘
- 每条事件必带 `ts`（游戏内秒）和 `issued_by` 溯源链
"""

from __future__ import annotations

from vibecraft.logging_.session import GameSession, GameSessionConfig
from vibecraft.logging_.sinks import JsonlSink, NullSink, Sink
from vibecraft.logging_.types import Event, EventKind, LogStream

__all__ = [
    "Event",
    "EventKind",
    "GameSession",
    "GameSessionConfig",
    "JsonlSink",
    "LogStream",
    "NullSink",
    "Sink",
]
