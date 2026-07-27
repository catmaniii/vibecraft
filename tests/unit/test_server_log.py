"""server_log 模块单测：捕获 stdout/stderr/logging 到固定文件。"""

from __future__ import annotations

import contextlib
import importlib
import logging
import os
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_server_log_module():
    """每条用例重新 import server_log，重置模块级 _attached 状态 + 恢复 std stream。"""
    real_stdout, real_stderr = sys.stdout, sys.stderr
    old_env = os.environ.pop("VIBECRAFT_SERVER_LOG_PATH", None)

    import vibecraft.logging_.server_log as sl

    # 用 importlib.reload 强制重置模块状态(_attached=False)
    importlib.reload(sl)

    # 把所有添加的 FileHandler 都拆掉(避免污染下一个用例)
    root = logging.getLogger()
    existing = list(root.handlers)

    yield sl

    # cleanup：恢复 stream + handler + env + close 持有的文件
    sys.stdout, sys.stderr = real_stdout, real_stderr
    for h in list(root.handlers):
        if h not in existing:
            root.removeHandler(h)
            with contextlib.suppress(Exception):
                h.close()
    if sl._log_file is not None:
        with contextlib.suppress(Exception):
            sl._log_file.close()
        sl._log_file = None
    if old_env is None:
        os.environ.pop("VIBECRAFT_SERVER_LOG_PATH", None)
    else:
        os.environ["VIBECRAFT_SERVER_LOG_PATH"] = old_env


class TestDefaultPath:
    def test_default_path_under_logs(self, _reset_server_log_module):
        path = _reset_server_log_module.default_server_log_path()
        assert path.parent == Path("logs")
        assert path.name.startswith("server_")
        assert path.suffix == ".log"

    def test_default_path_respects_base_dir(self, _reset_server_log_module, tmp_path: Path):
        path = _reset_server_log_module.default_server_log_path(tmp_path)
        assert path.parent == tmp_path


class TestInitServerLogFile:
    def test_creates_file_and_writes_print(self, _reset_server_log_module, tmp_path: Path) -> None:
        log_path = tmp_path / "server.log"
        _reset_server_log_module.init_server_log_file(log_path)

        print("hello-to-server-log")
        # 强制 flush
        sys.stdout.flush()

        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "hello-to-server-log" in content

    def test_captures_logging(self, _reset_server_log_module, tmp_path: Path) -> None:
        log_path = tmp_path / "server.log"
        _reset_server_log_module.init_server_log_file(log_path)

        logger = logging.getLogger("test_server_log_capture")
        logger.warning("logging-test-message-zxqw")

        content = log_path.read_text(encoding="utf-8")
        assert "logging-test-message-zxqw" in content

    def test_sets_env_for_subprocess(self, _reset_server_log_module, tmp_path: Path) -> None:
        log_path = tmp_path / "server.log"
        _reset_server_log_module.init_server_log_file(log_path)

        assert os.environ.get("VIBECRAFT_SERVER_LOG_PATH") == str(log_path)

    def test_idempotent(self, _reset_server_log_module, tmp_path: Path) -> None:
        """重复 init 不重复 attach handler。"""
        log_path = tmp_path / "server.log"
        root = logging.getLogger()
        before = len(root.handlers)

        _reset_server_log_module.init_server_log_file(log_path)
        after_first = len(root.handlers)

        _reset_server_log_module.init_server_log_file(log_path)
        after_second = len(root.handlers)

        assert after_first == before + 1  # 只加一个 FileHandler
        assert after_second == after_first  # 第二次不增加

    def test_keeps_terminal_output(
        self, _reset_server_log_module, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Tee：文件写入的同时，原 stdout 也保留（terminal 看得到）。"""
        log_path = tmp_path / "server.log"
        _reset_server_log_module.init_server_log_file(log_path)

        print("dual-output-marker")
        sys.stdout.flush()

        # pytest capsys 仍能捕获到 → 原 stdout 流没被吞掉
        captured = capsys.readouterr()
        assert "dual-output-marker" in captured.out


class TestInitFromEnv:
    def test_returns_none_when_env_unset(self, _reset_server_log_module) -> None:
        os.environ.pop("VIBECRAFT_SERVER_LOG_PATH", None)
        assert _reset_server_log_module.init_from_env() is None

    def test_uses_env_path_when_set(self, _reset_server_log_module, tmp_path: Path) -> None:
        target = tmp_path / "child.log"
        os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(target)

        result = _reset_server_log_module.init_from_env()
        assert result == target
        assert target.exists()
