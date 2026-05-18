"""Server-level log file（service + 子进程统一捕获到一个文件）。

存在意义
========
现有 ``logs/<game_id>/*.jsonl`` 只记录 vibecraft 自己 emit 的结构化事件
（directive / event / llm_call ...）。但当 bot 子进程在 ``on_start`` /
``create_plan`` 早期就抛 exception 时，traceback 只打到 service terminal
stdout/stderr — 不进任何 jsonl，用户关了窗口就找不回。

这一层把 service + 子进程的所有 stdout/stderr/logging 输出都捕获到一个
固定文件 ``logs/server_<startup_ts>.log``，定位早期崩溃的 traceback 不再
依赖 user 是否记得贴 terminal。

设计
====
- **一次 service 启动 = 一个文件**：BotService.run() 启动时生成路径
- **环境变量传给子进程**：``VIBECRAFT_SERVER_LOG_PATH``，spawn 子进程读它后
  也 attach 到同一文件（spawn 子进程不继承 Python in-process state，但继承
  父进程 env）
- **Tee stdout/stderr**：保留原 stream 仍 print 到 terminal（user 体验不变），
  同时镜像到文件 → SC2 / loguru / print() 全捕获
- **stdlib logging**：再加一个 FileHandler（DEBUG level，所有等级都进文件）
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO

# 环境变量名：父进程设置后，spawn 子进程通过 env 继承
ENV_KEY = "VIBECRAFT_SERVER_LOG_PATH"

# 模块级状态：避免重复 attach handler（_init 多次调用）
_attached: bool = False
# 持有已打开的 file handle 引用，避免 GC 在 sys.stdout Tee 还在用时关掉它
_log_file: TextIO | None = None


class _TeeStream:
    """把 write 镜像到多个 stream（不丢原 terminal 输出）。

    被 sys.stdout/stderr 覆盖。任意 stream raise 时 swallow，避免一处写失败
    把整个 print 链路打断。
    """

    def __init__(self, primary: TextIO, mirror: TextIO) -> None:
        self._primary = primary
        self._mirror = mirror

    def write(self, data: str) -> int:
        try:
            n = self._primary.write(data)
        except Exception:
            n = 0
        try:
            self._mirror.write(data)
            self._mirror.flush()
        except Exception:
            pass
        return n

    def flush(self) -> None:
        for s in (self._primary, self._mirror):
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        try:
            return bool(self._primary.isatty())
        except Exception:
            return False

    # 转发 logging FileHandler 可能用到的属性
    def __getattr__(self, name: str) -> object:
        return getattr(self._primary, name)


def default_server_log_path(base_dir: Path | None = None) -> Path:
    """生成默认 server log 路径：``logs/server_YYYYMMDD_HHMMSS.log``。"""
    base = base_dir or Path("logs")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return base / f"server_{ts}.log"


def init_server_log_file(path: Path) -> Path:
    """启用 server log file 捕获（幂等 — 重复调安全）。

    做三件事：
      1. 在 ``path`` 处 append-open 一个文件流
      2. 把 sys.stdout / sys.stderr 替换为 Tee（原 stream + file）
      3. 给 root logger 加一个 FileHandler（DEBUG level）

    返回最终生效的 path。重复调用直接 return 现有 ENV_KEY 路径，不再 attach。
    """
    global _attached, _log_file
    if _attached:
        # 已经 attach 过 — 直接 return 当前路径
        return Path(os.environ.get(ENV_KEY, str(path)))

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # append 模式：父进程和子进程可能轮流写同一个文件，append 是安全的
    # (Windows 上 append 写小块也是原子的，写日志这种场景足够)
    # 存到模块级 _log_file 避免被 GC：sys.stdout 的 Tee 还引用着它,GC 关掉
    # 会触发 unraisable warning
    _log_file = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered

    # Tee stdout / stderr → 原 terminal 不影响，文件镜像写入
    sys.stdout = _TeeStream(sys.stdout, _log_file)  # type: ignore[assignment]
    sys.stderr = _TeeStream(sys.stderr, _log_file)  # type: ignore[assignment]

    # stdlib logging：给 root logger 加 FileHandler
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.addHandler(file_handler)
    # 不改 root.level — 让 basicConfig / 各模块自己控制；FileHandler 自己 DEBUG

    # 标记 + 设 env（spawn 子进程通过 env 继承）
    _attached = True
    os.environ[ENV_KEY] = str(path)

    return path


def init_from_env() -> Path | None:
    """子进程入口辅助：若父进程设了 ENV_KEY，attach 到同一文件。

    返回 attach 的 path，或 None（env 未设）。
    """
    env_path = os.environ.get(ENV_KEY)
    if not env_path:
        return None
    return init_server_log_file(Path(env_path))
