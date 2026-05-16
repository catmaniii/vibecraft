"""logging_ 模块的单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibecraft.logging_ import (
    Event,
    EventKind,
    GameSession,
    GameSessionConfig,
    JsonlSink,
    LogStream,
    NullSink,
)

# =========================================================================
# JsonlSink
# =========================================================================


class TestJsonlSink:
    def test_write_appends_one_line_per_record(self, tmp_path: Path) -> None:
        sink = JsonlSink(tmp_path / "out.jsonl")
        sink.write({"a": 1})
        sink.write({"b": 2})
        sink.close()

        content = (tmp_path / "out.jsonl").read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"a": 1}
        assert json.loads(lines[1]) == {"b": 2}

    def test_chinese_text_not_escaped(self, tmp_path: Path) -> None:
        """ensure_ascii=False 必须保留中文原文，便于人眼 debug。"""
        sink = JsonlSink(tmp_path / "out.jsonl")
        sink.write({"text": "切到双矿凤凰"})
        sink.close()
        line = (tmp_path / "out.jsonl").read_text(encoding="utf-8").strip()
        assert "切到双矿凤凰" in line

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "deep" / "out.jsonl"
        sink = JsonlSink(path)
        sink.write({"x": 1})
        sink.close()
        assert path.exists()

    def test_write_after_close_raises(self, tmp_path: Path) -> None:
        sink = JsonlSink(tmp_path / "out.jsonl")
        sink.close()
        with pytest.raises(RuntimeError, match="already closed"):
            sink.write({"x": 1})

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        sink = JsonlSink(tmp_path / "out.jsonl")
        sink.close()
        sink.close()


# =========================================================================
# NullSink
# =========================================================================


class TestNullSink:
    def test_captures_in_memory(self) -> None:
        sink = NullSink()
        sink.write({"a": 1})
        sink.write({"b": 2})
        assert sink.records == [{"a": 1}, {"b": 2}]

    def test_flush_close_are_noops(self) -> None:
        sink = NullSink()
        sink.flush()
        sink.close()
        sink.write({"a": 1})  # 仍可写
        assert sink.records == [{"a": 1}]


# =========================================================================
# Event schema
# =========================================================================


class TestEvent:
    def test_minimal_event(self) -> None:
        ev = Event(ts=12.5, kind=EventKind.BUILD_STARTED)
        d = ev.model_dump(mode="json")
        assert d["ts"] == 12.5
        assert d["kind"] == "build.started"
        assert d["payload"] == {}
        assert d["priority"] == "medium"
        assert d["caused_by"] is None

    def test_event_is_frozen(self) -> None:
        ev = Event(ts=1, kind=EventKind.STRATEGY_SET)
        with pytest.raises(Exception, match=r"(frozen|immutable)"):
            ev.ts = 2  # type: ignore[misc]

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(Exception, match="extra"):
            Event(ts=1, kind=EventKind.STRATEGY_SET, unknown_field=1)  # type: ignore[call-arg]


# =========================================================================
# GameSession
# =========================================================================


class TestGameSession:
    def test_null_session_collects_records(self) -> None:
        cfg = GameSessionConfig(use_null_sinks=True)
        with GameSession(cfg) as session:
            session.log_event(
                Event(ts=1.0, kind=EventKind.STRATEGY_SET, payload={"id": "1g_robo_immortal"})
            )
            session.log_event(Event(ts=2.0, kind=EventKind.BUILD_STARTED))

        records = session.get_null_records(LogStream.EVENTS)
        assert len(records) == 2
        assert records[0]["kind"] == "strategy.set"
        assert records[0]["payload"] == {"id": "1g_robo_immortal"}

    def test_real_session_writes_jsonl_files(self, tmp_path: Path) -> None:
        cfg = GameSessionConfig(base_dir=tmp_path, game_id="test_game_1")
        with GameSession(cfg) as session:
            session.log_event(Event(ts=1.0, kind=EventKind.BUILD_STARTED))
            session.log_event(Event(ts=2.0, kind=EventKind.BUILD_COMPLETED))
            session.log(LogStream.COMMANDS, {"ts": 3.0, "text": "切凤凰"})

        events_path = tmp_path / "test_game_1" / "events.jsonl"
        commands_path = tmp_path / "test_game_1" / "commands.jsonl"
        assert events_path.exists()
        assert commands_path.exists()

        events_lines = events_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(events_lines) == 2

        commands_lines = commands_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(commands_lines) == 1
        assert json.loads(commands_lines[0])["text"] == "切凤凰"

    def test_auto_generated_game_id_has_expected_shape(self) -> None:
        cfg = GameSessionConfig(use_null_sinks=True)
        session = GameSession(cfg)
        try:
            assert session.game_id.startswith("game_")
            # game_YYYYMMDD_HHMMSS_xxxxxx
            parts = session.game_id.split("_")
            assert len(parts) == 4
            assert len(parts[1]) == 8  # 日期
            assert len(parts[2]) == 6  # 时间
            assert len(parts[3]) == 6  # 随机后缀
        finally:
            session.close()

    def test_log_llm_call_writes_indexed_json(self, tmp_path: Path) -> None:
        cfg = GameSessionConfig(base_dir=tmp_path, game_id="g1")
        with GameSession(cfg) as session:
            seq1 = session.log_llm_call({"provider": "claude", "prompt": "..."})
            seq2 = session.log_llm_call({"provider": "claude", "prompt": "..."})
        assert seq1 == 1
        assert seq2 == 2
        assert (tmp_path / "g1" / "llm_calls" / "call_001.json").exists()
        assert (tmp_path / "g1" / "llm_calls" / "call_002.json").exists()

    def test_log_llm_call_counter_in_null_mode(self) -> None:
        cfg = GameSessionConfig(use_null_sinks=True)
        with GameSession(cfg) as session:
            assert session.log_llm_call({}) == 1
            assert session.log_llm_call({}) == 2

    def test_log_after_close_raises(self) -> None:
        cfg = GameSessionConfig(use_null_sinks=True)
        session = GameSession(cfg)
        session.close()
        with pytest.raises(RuntimeError, match="already closed"):
            session.log_event(Event(ts=1, kind=EventKind.BUILD_STARTED))

    def test_close_is_idempotent(self) -> None:
        cfg = GameSessionConfig(use_null_sinks=True)
        session = GameSession(cfg)
        session.close()
        session.close()  # 不抛
