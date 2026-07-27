"""admin_games 单测：真人对局扫描白名单 / 元数据提取。

关键断言（评审 M1）：
- game_* 目录（build_acceptance 沙盒）必须被排除
- match_* 目录是真人局，应被收录
- 元数据字段（种族 / 配方 / 昵称 / 时长）正确提取
- 昵称字段防御性读取（不存在 → "—"）
"""

from __future__ import annotations

import os
import pathlib
import time

import pytest

from vibecraft.server.admin_games import (
    _is_match_dir,
    _read_last_line,
    scan_match_games,
)

# ---------------------------------------------------------------------------
# _is_match_dir 规则
# ---------------------------------------------------------------------------


class TestIsMatchDir:
    def test_match_accepted(self) -> None:
        assert _is_match_dir("match_20240101_120000_p0") is True

    def test_match_solo_accepted(self) -> None:
        assert _is_match_dir("match_20260615_093000_p0") is True

    def test_game_excluded(self) -> None:
        """game_* = build_acceptance 沙盒，必须排除（评审 M1 核心）。"""
        assert _is_match_dir("game_20240101_120000") is False

    def test_eff_excluded(self) -> None:
        assert _is_match_dir("eff_something") is False

    def test_e2e_excluded(self) -> None:
        assert _is_match_dir("e2e_test") is False

    def test_selftest_in_name_excluded(self) -> None:
        assert _is_match_dir("match_selftest_001") is False

    def test_proof_in_name_excluded(self) -> None:
        assert _is_match_dir("match_proof_001") is False

    def test_build_prefix_excluded(self) -> None:
        assert _is_match_dir("build_acceptance_4bg") is False

    def test_bare_name_excluded(self) -> None:
        assert _is_match_dir("logs") is False

    def test_empty_name_excluded(self) -> None:
        assert _is_match_dir("") is False


# ---------------------------------------------------------------------------
# _read_last_line
# ---------------------------------------------------------------------------


class TestReadLastLine:
    def test_single_line(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "a.jsonl"
        f.write_text('{"kind":"game_start"}\n', encoding="utf-8")
        assert _read_last_line(f) == '{"kind":"game_start"}'

    def test_multi_line(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "b.jsonl"
        f.write_text('{"kind":"game_start"}\n{"kind":"snapshot","t":55.0}\n', encoding="utf-8")
        result = _read_last_line(f)
        assert result is not None
        assert '"snapshot"' in result
        assert '"t":55.0' in result

    def test_no_trailing_newline(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "c.jsonl"
        f.write_bytes(b'{"kind":"game_start"}\n{"kind":"snapshot","t":12.3}')
        result = _read_last_line(f)
        assert result is not None
        assert '"snapshot"' in result

    def test_empty_file(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "empty.jsonl"
        f.write_bytes(b"")
        assert _read_last_line(f) is None

    def test_missing_file(self, tmp_path: pathlib.Path) -> None:
        assert _read_last_line(tmp_path / "nonexistent.jsonl") is None


# ---------------------------------------------------------------------------
# scan_match_games
# ---------------------------------------------------------------------------


class TestScanMatchGames:
    def _make_match(
        self,
        base: pathlib.Path,
        name: str,
        first_line: str | None = None,
        last_line: str | None = None,
    ) -> pathlib.Path:
        d = base / name
        d.mkdir()
        lines: list[str] = []
        if first_line:
            lines.append(first_line)
        if last_line and last_line != first_line:
            lines.append(last_line)
        (d / "telemetry.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return d

    # ─── 白名单过滤 ──────────────────────────────────────────────

    def test_game_dir_excluded(self, tmp_path: pathlib.Path) -> None:
        """game_* 目录（build_acceptance 沙盒）必须被排除（评审 M1 负样本锁定）。"""
        game_dir = tmp_path / "game_20240101_p0"
        game_dir.mkdir()
        (game_dir / "telemetry.jsonl").write_text(
            '{"kind":"game_start","my_race":"Protoss"}\n', encoding="utf-8"
        )
        results = scan_match_games(tmp_path)
        ids = [r["match_id"] for r in results]
        assert "game_20240101_p0" not in ids

    def test_match_dir_included(self, tmp_path: pathlib.Path) -> None:
        """match_* 目录正样本。"""
        self._make_match(
            tmp_path,
            "match_20240101_120000_p0",
            first_line='{"kind":"game_start","my_race":"Protoss","active_recipe":"4bg"}',
            last_line='{"kind":"snapshot","t":120.5}',
        )
        results = scan_match_games(tmp_path)
        assert len(results) == 1
        r = results[0]
        assert r["match_id"] == "match_20240101_120000"  # 去 _pN（一局一条）
        assert r["my_race"] == "Protoss"
        assert r["active_recipe"] == "4bg"
        assert r["duration_s"] == pytest.approx(120.5)

    def test_two_player_dirs_deduped_with_roster(self, tmp_path: pathlib.Path) -> None:
        """两人局 p0/p1 两个目录 → 一条记录，roster 含全部参战方。"""
        import json

        roster = json.dumps(
            [
                {"name": "Alice", "race": "Protoss", "kind": "human"},
                {"name": "Bob", "race": "Zerg", "kind": "human"},
            ]
        )
        for p in ("match_20240103_p0", "match_20240103_p1"):
            self._make_match(
                tmp_path,
                p,
                first_line=json.dumps(
                    {"kind": "game_start", "my_race": "Protoss", "roster": json.loads(roster)}
                ),
            )
        results = scan_match_games(tmp_path)
        assert len(results) == 1  # p0/p1 归一局
        assert results[0]["match_id"] == "match_20240103"
        assert len(results[0]["roster"]) == 2

    def test_game_dir_excluded_with_match_included(self, tmp_path: pathlib.Path) -> None:
        """同时存在 game_* 和 match_* 目录：game_* 排除、match_* 收录。"""
        # 沙盒目录（应被排除）
        game_dir = tmp_path / "game_20240101_p0"
        game_dir.mkdir()
        (game_dir / "telemetry.jsonl").write_text(
            '{"kind":"game_start","my_race":"Zerg"}\n', encoding="utf-8"
        )
        # 真人对局（应被收录）
        self._make_match(
            tmp_path,
            "match_20240102_080000_p0",
            first_line='{"kind":"game_start","my_race":"Terran","active_recipe":"bio_stim"}',
        )

        results = scan_match_games(tmp_path)
        ids = [r["match_id"] for r in results]
        assert "game_20240101" not in ids and "game_20240101_p0" not in ids
        assert "match_20240102_080000" in ids  # 去 _pN

    # ─── 元数据提取 ──────────────────────────────────────────────

    def test_nickname_field_defensive(self, tmp_path: pathlib.Path) -> None:
        """昵称字段防御性读取：字段不存在 → '—'。"""
        self._make_match(
            tmp_path,
            "match_20240101_120000_p0",
            first_line='{"kind":"game_start","my_race":"Zerg"}',
        )
        results = scan_match_games(tmp_path)
        assert results[0]["nickname"] == "—"

    def test_nickname_field_read(self, tmp_path: pathlib.Path) -> None:
        """昵称字段存在时正确读取。"""
        self._make_match(
            tmp_path,
            "match_20240101_120000_p0",
            first_line='{"kind":"game_start","my_race":"Zerg","nickname":"玩家甲"}',
        )
        results = scan_match_games(tmp_path)
        assert results[0]["nickname"] == "玩家甲"

    def test_result_victory(self, tmp_path: pathlib.Path) -> None:
        """末行 victory 记录。"""
        self._make_match(
            tmp_path,
            "match_20240101_120000_p0",
            first_line='{"kind":"game_start","my_race":"Protoss"}',
            last_line='{"kind":"victory","t":300.0}',
        )
        results = scan_match_games(tmp_path)
        assert results[0]["result"] == "victory"
        assert results[0]["duration_s"] == pytest.approx(300.0)

    def test_missing_telemetry(self, tmp_path: pathlib.Path) -> None:
        """telemetry.jsonl 不存在时返回默认元数据（不崩溃）。"""
        d = tmp_path / "match_20240101_120000_p0"
        d.mkdir()
        results = scan_match_games(tmp_path)
        assert len(results) == 1
        assert results[0]["my_race"] == "—"
        assert results[0]["duration_s"] is None

    # ─── 性能 / 排序 ─────────────────────────────────────────────

    def test_sort_by_mtime_descending(self, tmp_path: pathlib.Path) -> None:
        """多局按 mtime 倒序：最新的在最前。"""
        for i, name in enumerate(["match_old", "match_mid", "match_new"]):
            d = tmp_path / name
            d.mkdir()
            (d / "telemetry.jsonl").write_text(
                '{"kind":"game_start","my_race":"Protoss"}\n', encoding="utf-8"
            )
            # 手动设置 mtime（old < mid < new）
            os_time = time.time() - (3 - i) * 1000
            os.utime(d, (os_time, os_time))

        results = scan_match_games(tmp_path)
        ids = [r["match_id"] for r in results]
        assert ids == ["match_new", "match_mid", "match_old"]

    def test_limit_respected(self, tmp_path: pathlib.Path) -> None:
        """limit=2 时只返回最近 2 局。"""
        for i in range(5):
            d = tmp_path / f"match_{i:03d}"
            d.mkdir()
            (d / "telemetry.jsonl").write_text(
                '{"kind":"game_start","my_race":"Zerg"}\n', encoding="utf-8"
            )
        results = scan_match_games(tmp_path, limit=2)
        assert len(results) == 2

    def test_empty_logs_dir(self, tmp_path: pathlib.Path) -> None:
        """空目录返回空列表。"""
        assert scan_match_games(tmp_path) == []


# ---------------------------------------------------------------------------
# builds = 玩家用过的 build 序列（顺序 + 切换时间），滤掉持续 <10s 的段
# ---------------------------------------------------------------------------


class TestBuildSequence:
    def _make(self, base: pathlib.Path, name: str, lines: list[str]) -> pathlib.Path:
        d = base / name
        d.mkdir()
        (d / "telemetry.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return d

    def _snap(self, t: float, recipe: str) -> str:
        return f'{{"kind":"snapshot","t":{t},"active_recipe":"{recipe}"}}'

    def test_early_default_filtered_shows_real_build(self, tmp_path: pathlib.Path) -> None:
        """开局默认 reaper_expand 只活 6s（<10s 被滤），6s 切 bc_rush 打满 → builds=[bc_rush@6]。"""
        lines = ['{"kind":"game_start","my_race":"Terran","active_recipe":"reaper_expand"}']
        lines += [self._snap(t, "reaper_expand") for t in (0.0, 2.0, 4.0)]
        lines += [self._snap(float(t), "bc_rush") for t in range(6, 300, 2)]
        self._make(tmp_path, "match_20240101_120000_p0", lines)
        r = scan_match_games(tmp_path)[0]
        assert [b["build"] for b in r["builds"]] == ["bc_rush"]  # reaper(6s)被滤
        assert r["builds"][0]["at_s"] == pytest.approx(6.0)
        assert r["active_recipe"] == "bc_rush"  # 兼容字段 = 第一段

    def test_full_switch_sequence_in_order(self, tmp_path: pathlib.Path) -> None:
        """多次切 build：builds 按时间顺序，带各自切换时间 at_s。"""
        lines = ['{"kind":"game_start","my_race":"Terran","active_recipe":"reaper_expand"}']
        lines += [self._snap(float(t), "reaper_expand") for t in range(0, 200, 2)]
        lines += [self._snap(float(t), "bc_rush") for t in range(200, 400, 2)]
        lines += [self._snap(float(t), "mech") for t in range(400, 500, 2)]
        self._make(tmp_path, "match_20240102_120000_p0", lines)
        r = scan_match_games(tmp_path)[0]
        assert [b["build"] for b in r["builds"]] == ["reaper_expand", "bc_rush", "mech"]
        assert [b["at_s"] for b in r["builds"]] == pytest.approx([0.0, 200.0, 400.0])

    def test_short_middle_segment_filtered(self, tmp_path: pathlib.Path) -> None:
        """中间一次 8s 的误切被滤掉，序列里不出现。"""
        lines = ['{"kind":"game_start","my_race":"Terran","active_recipe":"bio_stim"}']
        lines += [self._snap(float(t), "bio_stim") for t in range(0, 100, 2)]
        lines += [self._snap(float(t), "mech") for t in range(100, 108, 2)]  # 8s 段
        lines += [self._snap(float(t), "bio_stim") for t in range(108, 300, 2)]
        self._make(tmp_path, "match_20240103_120000_p0", lines)
        r = scan_match_games(tmp_path)[0]
        # mech(8s) 被滤；bio_stim 因中断成两段，第二段 108~298 也 ≥10s
        builds = [b["build"] for b in r["builds"]]
        assert "mech" not in builds
        assert builds[0] == "bio_stim"

    def test_no_snapshots_falls_back_to_game_start(self, tmp_path: pathlib.Path) -> None:
        """无 snapshot → builds 空，active_recipe 回退 game_start 默认。"""
        self._make(
            tmp_path,
            "match_20240104_120000_p0",
            ['{"kind":"game_start","my_race":"Protoss","active_recipe":"4bg"}'],
        )
        r = scan_match_games(tmp_path)[0]
        assert r["builds"] == []
        assert r["active_recipe"] == "4bg"

    def test_short_game_late_switch_filtered(self, tmp_path: pathlib.Path) -> None:
        """短局末尾 8s 误切 mech 被滤，只剩开局 build；结局/时长照常。"""
        lines = ['{"kind":"game_start","my_race":"Terran","active_recipe":"bio_stim"}']
        lines += [self._snap(float(t), "bio_stim") for t in range(0, 110, 2)]
        lines += [self._snap(float(t), "mech") for t in range(110, 118, 2)]  # 8s
        lines.append('{"kind":"victory","t":120.0}')
        self._make(tmp_path, "match_20240105_120000_p0", lines)
        r = scan_match_games(tmp_path)[0]
        assert [b["build"] for b in r["builds"]] == ["bio_stim"]
        assert r["result"] == "victory"
        assert r["duration_s"] == pytest.approx(120.0)

    def test_missing_logs_dir(self, tmp_path: pathlib.Path) -> None:
        """logs 目录不存在返回空列表。"""
        assert scan_match_games(tmp_path / "nonexistent") == []
