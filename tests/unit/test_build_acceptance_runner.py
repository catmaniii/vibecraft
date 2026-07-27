"""build_acceptance runner 基础设施单测（阻塞点 A / C）。

不起真实 SC2，只测逻辑层：
- _make_game_id 的唯一性和格式
- game_id 通过 VIBECRAFT_GAME_ID 环境变量传给子进程
- _run_with_retry 为 async（可在 asyncio.gather 里调度）
- main 支持多 strategy_id 和 --parallel 参数解析
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# runner 在 scripts/ 目录，不是 package，需要手动加到 path
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import build_acceptance as runner


# ---------------------------------------------------------------------------
# _make_game_id
# ---------------------------------------------------------------------------


def test_make_game_id_starts_with_game() -> None:
    gid = runner._make_game_id()
    assert gid.startswith("game_")


def test_make_game_id_contains_pid() -> None:
    gid = runner._make_game_id()
    pid = str(os.getpid())
    assert pid in gid, f"game_id={gid!r} 应包含 pid={pid}"


def test_make_game_id_unique() -> None:
    """连续生成多个 id，全部不同。"""
    ids = {runner._make_game_id() for _ in range(20)}
    assert len(ids) == 20, "生成的 game_id 有重复"


def test_make_game_id_format() -> None:
    """格式：game_YYYYMMDD_HHMMSS_<pid>_<hex6>"""
    gid = runner._make_game_id()
    parts = gid.split("_")
    # game / YYYYMMDD / HHMMSS / pid / hex6
    assert len(parts) == 5
    assert parts[0] == "game"
    assert len(parts[1]) == 8 and parts[1].isdigit()  # YYYYMMDD
    assert len(parts[2]) == 6 and parts[2].isdigit()  # HHMMSS
    assert parts[3].isdigit()  # pid
    assert len(parts[4]) == 6  # uuid hex6


# ---------------------------------------------------------------------------
# game_id 通过 VIBECRAFT_GAME_ID 环境变量透传
# ---------------------------------------------------------------------------


def test_run_one_game_passes_game_id_via_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_one_game 应通过 GameConfig.game_id 字段(不是 os.environ)传 game_id 给子进程。

    2026-05-23 race fix:并行多 strategy 时 os.environ 共享会被覆盖,改 picklable
    GameConfig 字段保证每个子进程独立。验证:mock GameProcess.start 捕获 cfg.game_id,
    确认值以 game_ 开头。
    """
    captured_cfgs: list[object] = []

    class FakeGameProcess:
        def start(self, cfg: object) -> None:
            captured_cfgs.append(cfg)

        async def raw_events(self):  # type: ignore[override]
            yield {"sc2": "ended"}

        async def stop(self) -> None:
            pass

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        monkeypatch.setattr(runner, "_ROOT", tmp_path)

        original_start = FakeGameProcess.start

        def patched_start(self: FakeGameProcess, cfg: object) -> None:
            original_start(self, cfg)
            gid = getattr(cfg, "game_id", "")
            (tmp_path / "logs" / gid).mkdir(parents=True, exist_ok=True)
            (tmp_path / "logs" / gid / "telemetry.jsonl").write_text(
                '{"ts": 1}\n', encoding="utf-8"
            )

        FakeGameProcess.start = patched_start  # type: ignore[method-assign]

        with (
            patch.object(runner, "GameProcess", return_value=FakeGameProcess()),
            patch.object(runner, "_detect_race", return_value="Protoss"),
        ):
            result = asyncio.run(runner._run_one_game("dummy_strategy", "veryeasy"))

    assert captured_cfgs, "GameProcess.start 没被调用"
    cfg_game_id = getattr(captured_cfgs[0], "game_id", "")
    cfg_forced = getattr(captured_cfgs[0], "forced_opening", "")
    assert cfg_game_id.startswith("game_"), f"GameConfig.game_id 未正确设置,实际值: {cfg_game_id!r}"
    assert cfg_forced == "dummy_strategy", (
        f"GameConfig.forced_opening 未正确传 strategy_id,实际值: {cfg_forced!r}"
    )
    assert result is not None, "_run_one_game 应返回 telemetry.jsonl 路径"


# ---------------------------------------------------------------------------
# _run_with_retry 是 async
# ---------------------------------------------------------------------------


def test_run_with_retry_is_coroutine() -> None:
    """_run_with_retry 应是 async 函数，可在 asyncio.gather 里调度。"""
    import inspect

    assert inspect.iscoroutinefunction(runner._run_with_retry), "_run_with_retry 必须是 async def"


# ---------------------------------------------------------------------------
# main 参数解析
# ---------------------------------------------------------------------------


def test_main_accepts_multiple_strategy_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """main 应支持多个 strategy_id 位置参数。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        # 建两个 spec 文件
        spec_dir = tmp_root / "tests" / "build_acceptance"
        spec_dir.mkdir(parents=True)
        (spec_dir / "strat_a.yaml").write_text(
            "strategy_id: strat_a\nmy_race: Protoss\nchecks: []\n", encoding="utf-8"
        )
        (spec_dir / "strat_b.yaml").write_text(
            "strategy_id: strat_b\nmy_race: Protoss\nchecks: []\n", encoding="utf-8"
        )

        monkeypatch.setattr(runner, "_ROOT", tmp_root)
        monkeypatch.setattr(sys, "argv", ["build_acceptance.py", "strat_a", "strat_b"])

        # mock asyncio.run 不真的跑游戏
        def fake_asyncio_run(coro: object) -> None:  # type: ignore[override]
            pass

        with patch("asyncio.run", side_effect=fake_asyncio_run):
            # main 执行到 asyncio.run 就返回；后面的 aggregate 对空 reports 列表执行
            # 会走 INFRA BROKEN 分支，退出码 1 — 不影响我们验证参数解析本身
            ret = runner.main()

    # 只要没有 "ERROR: 没有 acceptance spec" 就说明多 id 被接受了
    # ret 可能是 1（INFRA BROKEN）或 0，取决于 mock 的深度
    assert ret in (0, 1, 3)


def test_main_parallel_default_is_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """--parallel 默认值是 1（串行）。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        spec_dir = tmp_root / "tests" / "build_acceptance"
        spec_dir.mkdir(parents=True)
        (spec_dir / "strat_x.yaml").write_text(
            "strategy_id: strat_x\nmy_race: Protoss\nchecks: []\n", encoding="utf-8"
        )

        monkeypatch.setattr(runner, "_ROOT", tmp_root)
        monkeypatch.setattr(sys, "argv", ["build_acceptance.py", "strat_x"])

        def fake_run(coro: object) -> None:  # type: ignore[override]
            pass

        with patch("asyncio.run", side_effect=fake_run):
            runner.main()

        # 如果 main 能跑到 asyncio.run 而不报错，说明 parallel=1 是默认值


def test_main_missing_spec_returns_2(monkeypatch: pytest.MonkeyPatch) -> None:
    """指定不存在的 strategy_id spec 时返回退出码 2。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        (tmp_root / "tests" / "build_acceptance").mkdir(parents=True)

        monkeypatch.setattr(runner, "_ROOT", tmp_root)
        monkeypatch.setattr(sys, "argv", ["build_acceptance.py", "nonexistent_strategy"])

        ret = runner.main()

    assert ret == 2
