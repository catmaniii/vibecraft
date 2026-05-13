"""脚手架冒烟：能 import 包、跑通 CLI。"""

from __future__ import annotations

from voicecraft import __version__
from voicecraft.cli import main


def test_version_is_set() -> None:
    assert __version__
    assert isinstance(__version__, str)
    # PEP 440：可以是 "X.Y.Z" 也可以带 pre-release 后缀如 "0.1.0a1"
    assert __version__[0].isdigit()


def test_cli_version_flag(capsys) -> None:  # type: ignore[no-untyped-def]
    exit_code = main(["--version"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert __version__ in captured.out


def test_cli_default(capsys) -> None:  # type: ignore[no-untyped-def]
    exit_code = main([])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "voicecraft" in captured.out
