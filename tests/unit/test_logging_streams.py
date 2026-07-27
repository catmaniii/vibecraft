"""LogStream 枚举包含 telemetry 流。"""

from __future__ import annotations

from vibecraft.logging_.types import LogStream


def test_telemetry_stream_exists():
    assert LogStream.TELEMETRY.value == "telemetry"


def test_game_session_creates_telemetry_sink(tmp_path):
    """GameSession 自动为 TELEMETRY 建一个 sink。"""
    from vibecraft.logging_.session import GameSession, GameSessionConfig

    session = GameSession(GameSessionConfig(base_dir=tmp_path, game_id="t1"))
    session.log(LogStream.TELEMETRY, {"kind": "ping"})
    session.close()
    tel = tmp_path / "t1" / "telemetry.jsonl"
    assert tel.exists()
    assert "ping" in tel.read_text(encoding="utf-8")
