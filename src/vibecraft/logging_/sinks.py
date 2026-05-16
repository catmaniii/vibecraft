"""Sink 抽象：一行 JSON 写到哪里。

- JsonlSink：append 一行到磁盘文件
- NullSink：丢弃（用于单测和 dry-run）
- 后续可加：StdoutSink / RemoteSink

所有 Sink 实现 thread/coroutine safe write，但**不**保证 fsync 实时；
flush 由 GameSession 控制。
"""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class Sink(ABC):
    """单个 stream 的写入终点。"""

    @abstractmethod
    def write(self, record: dict[str, Any]) -> None: ...

    @abstractmethod
    def flush(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class NullSink(Sink):
    """丢弃所有写入。用于单测或临时禁用。"""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        with self._lock:
            self.records.append(record)

    def flush(self) -> None:
        return

    def close(self) -> None:
        return


class JsonlSink(Sink):
    """append 一行 JSON 到指定 path。

    采用 line-buffered text mode。每条 record 末尾追加 `\\n`。
    record 中不允许包含 `\\n`（json.dumps 会自动 escape）。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self._closed = False

    def write(self, record: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError(f"JsonlSink already closed: {self.path}")
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._fh.write(line)
            self._fh.write("\n")

    def flush(self) -> None:
        with self._lock:
            if not self._closed:
                self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._fh.flush()
                self._fh.close()
                self._closed = True
