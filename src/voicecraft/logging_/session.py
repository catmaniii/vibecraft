"""GameSession：一场对局的日志生命周期。

职责：
- 决定 `logs/<game_id>/` 目录
- 为每个 LogStream 持有一个 Sink
- 提供 `log()` / `log_event()` / `log_llm_call()` 等便捷方法
- close 时 flush + close 所有 sink

设计取舍：M0 阶段同步写入即可（写盘 IO 远小于 SC2 tick 间隔 ~22ms）。
若发现 jitter，再引入 asyncio.Queue + 后台 flush 协程。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from voicecraft.logging_.sinks import JsonlSink, NullSink, Sink
from voicecraft.logging_.types import Event, LogStream


class GameSessionConfig(BaseModel):
    """GameSession 的构造参数。"""

    model_config = ConfigDict(extra="forbid")

    base_dir: Path = Field(default=Path("logs"))
    game_id: str | None = Field(
        default=None,
        description="对局唯一 id；None 时由当前时间 + 随机后缀生成",
    )
    use_null_sinks: bool = Field(
        default=False,
        description="单测模式：所有写入留在内存，便于断言",
    )


class GameSession:
    """一场对局对应的日志会话。"""

    def __init__(self, config: GameSessionConfig | None = None) -> None:
        self.config = config or GameSessionConfig()
        self.game_id = self.config.game_id or self._generate_game_id()
        self.dir: Path = self.config.base_dir / self.game_id
        self._sinks: dict[LogStream, Sink] = {}
        self._llm_call_counter = 0
        self._closed = False

        if not self.config.use_null_sinks:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "llm_calls").mkdir(exist_ok=True)

        for stream in LogStream:
            self._sinks[stream] = self._build_sink(stream)

    @staticmethod
    def _generate_game_id() -> str:
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        return f"game_{ts}_{uuid.uuid4().hex[:6]}"

    def _build_sink(self, stream: LogStream) -> Sink:
        if self.config.use_null_sinks:
            return NullSink()
        return JsonlSink(self.dir / f"{stream.value}.jsonl")

    # ---- 写入 API -----------------------------------------------------

    def log(self, stream: LogStream, record: dict[str, Any]) -> None:
        """直接往某条 stream 写一行。

        record 必须是可 json.dumps 的 dict。会在 record 中注入 `_seq` 字段。
        """
        self._ensure_open()
        sink = self._sinks[stream]
        sink.write(record)

    def log_event(self, event: Event) -> None:
        """向 events 流写入一条业务事件。"""
        self.log(LogStream.EVENTS, event.model_dump(mode="json"))

    def log_llm_call(self, call: dict[str, Any]) -> int:
        """保存一次 LLM 调用全量（含 prompt / response / tokens / latency）。

        返回分配的 call 序号。落盘路径 `llm_calls/call_NNN.json`。
        """
        self._ensure_open()
        self._llm_call_counter += 1
        seq = self._llm_call_counter
        if self.config.use_null_sinks:
            return seq

        path = self.dir / "llm_calls" / f"call_{seq:03d}.json"
        path.write_text(
            json.dumps(call, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return seq

    # ---- 生命周期 -----------------------------------------------------

    def flush(self) -> None:
        for sink in self._sinks.values():
            sink.flush()

    def close(self) -> None:
        if self._closed:
            return
        for sink in self._sinks.values():
            sink.close()
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"GameSession {self.game_id} already closed")

    # ---- 单测辅助 -----------------------------------------------------

    def get_null_records(self, stream: LogStream) -> list[dict[str, Any]]:
        """仅用于 use_null_sinks=True 的单测。"""
        sink = self._sinks[stream]
        if not isinstance(sink, NullSink):
            raise RuntimeError("GameSession not in null mode")
        return list(sink.records)

    # ---- context manager ---------------------------------------------

    def __enter__(self) -> GameSession:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()
