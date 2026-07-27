"""SystemAudioGrabber 单测 —— 纯逻辑，不 spawn 真子进程 / 不碰真音频。

覆盖：read() 取帧 + 补静音、ring buffer 上限丢最旧、子进程死亡自动重启 +
退避、stop 幂等。真实采集链路（子进程 → PCM）靠手动 smoke 验证，不进 CI。
"""

from __future__ import annotations

import numpy as np

from vibecraft.server.audio_capture import SystemAudioGrabber


def _frames(rows: list[list[int]]) -> bytes:
    """把 [[L,R],...] int16 行打包成交织 s16le bytes。"""
    return np.array(rows, dtype=np.int16).tobytes()


def test_read_silence_when_empty() -> None:
    g = SystemAudioGrabber()
    out = g.read(960)
    assert out.shape == (960, 2)
    assert out.dtype == np.int16
    assert not out.any()  # 全 0 静音


def test_read_returns_buffered_pcm() -> None:
    g = SystemAudioGrabber(prime_ms=0)
    g._append(_frames([[1, 2], [3, 4], [5, 6]]))
    out = g.read(3)
    assert out.shape == (3, 2)
    assert np.array_equal(out, np.array([[1, 2], [3, 4], [5, 6]], dtype=np.int16))
    # 取完后 buffer 应空 → 再读是静音
    assert not g.read(1).any()


def test_read_pads_partial_with_silence() -> None:
    g = SystemAudioGrabber(prime_ms=0)
    g._append(_frames([[7, 8], [9, 10]]))  # 只有 2 帧
    out = g.read(5)  # 要 5 帧
    assert out.shape == (5, 2)
    assert np.array_equal(out[:2], np.array([[7, 8], [9, 10]], dtype=np.int16))
    assert not out[2:].any()  # 后 3 帧补静音


def test_read_consumes_only_requested() -> None:
    g = SystemAudioGrabber(prime_ms=0)
    g._append(_frames([[1, 1], [2, 2], [3, 3], [4, 4]]))
    first = g.read(2)
    assert np.array_equal(first, np.array([[1, 1], [2, 2]], dtype=np.int16))
    # 剩余 2 帧仍在 buffer
    second = g.read(2)
    assert np.array_equal(second, np.array([[3, 3], [4, 4]], dtype=np.int16))


def test_append_caps_buffer_drops_oldest() -> None:
    g = SystemAudioGrabber(prime_ms=0)
    cap_frames = g._MAX_BUFFER_BYTES // g._FRAME_BYTES
    # 灌 cap+10 帧，每帧值 = 序号，便于验证"留最新"
    rows = [[i % 32000, i % 32000] for i in range(cap_frames + 10)]
    g._append(_frames(rows))
    assert len(g._buf) == g._MAX_BUFFER_BYTES
    # 读 1 帧应是丢弃最旧 10 帧后的第 11 帧（序号 10）
    out = g.read(1)
    assert out[0, 0] == 10


def test_restart_loop_increments_and_stops(monkeypatch) -> None:
    g = SystemAudioGrabber()
    g._RESTART_BACKOFF = (0.0,)  # type: ignore[misc] —— 测试不等退避
    calls = {"n": 0}

    def fake_pump() -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            g._stop.set()  # 第 2 次 pump 时模拟收到 stop

    monkeypatch.setattr(g, "_spawn_and_pump", fake_pump)
    g._run()  # 同步跑 manager 循环（不开线程）
    assert calls["n"] == 2  # pump 被调 2 次（死一次重启一次）
    assert g.restart_count == 1  # 重启计数 1


def test_restart_loop_survives_pump_exception(monkeypatch) -> None:
    g = SystemAudioGrabber()
    g._RESTART_BACKOFF = (0.0,)  # type: ignore[misc]
    calls = {"n": 0}

    def boom() -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            g._stop.set()
            return
        raise RuntimeError("native crash 模拟")

    monkeypatch.setattr(g, "_spawn_and_pump", boom)
    g._run()  # 异常不应让 _run 崩，应继续重启
    assert calls["n"] == 2
    assert g.restart_count == 1


def test_stop_before_start_is_noop() -> None:
    g = SystemAudioGrabber()
    g.stop()  # 没 start 过，不应抛
    assert not g._started


# ---------------------------------------------------------------------------
# per-PID 模式（2026-06-13 任务 #516）：pid 透传到子进程命令行
# ---------------------------------------------------------------------------


def test_spawn_cmd_without_pid_has_no_pid_flag() -> None:
    g = SystemAudioGrabber()
    cmd = g._spawn_cmd()
    assert "--pid" not in cmd
    assert cmd == list(g._SPAWN_CMD)


def test_spawn_cmd_with_pid_appends_flag() -> None:
    g = SystemAudioGrabber(pid=4242)
    cmd = g._spawn_cmd()
    assert cmd[: len(g._SPAWN_CMD)] == list(g._SPAWN_CMD)
    assert cmd[-2:] == ["--pid", "4242"]


# ---------------------------------------------------------------------------
# 起播预缓冲（2026-06-13 修张三音乐破音）：攒够 prime_ms 才消费,欠载重新蓄水
# ---------------------------------------------------------------------------


def test_priming_holds_silence_until_threshold() -> None:
    g = SystemAudioGrabber()  # 默认 60ms 预缓冲
    g._append(_frames([[5, 5]] * 100))  # 100 帧 << 60ms(2880 帧)
    out = g.read(50)
    assert not out.any()  # 蓄水中:回静音
    assert len(g._buf) == 100 * g._FRAME_BYTES  # 且不消费


def test_priming_opens_after_threshold_and_underrun_reprimes() -> None:
    g = SystemAudioGrabber()
    prime_frames = g._prime_bytes // g._FRAME_BYTES
    g._append(_frames([[7, 7]] * (prime_frames + 10)))
    out = g.read(10)
    assert out[0, 0] == 7  # 攒够 → 开闸出数据
    # 欠载:把剩余全取走还不够 → 计数 + 回到蓄水态
    g.read(prime_frames + 100)
    assert g.underrun_count == 1
    assert not g._primed
    # 蓄水态下新进少量数据仍回静音
    g._append(_frames([[9, 9]] * 10))
    assert not g.read(5).any()


def test_prime_ms_zero_disables_priming() -> None:
    g = SystemAudioGrabber(prime_ms=0)
    g._append(_frames([[3, 3]]))
    out = g.read(1)
    assert out[0, 0] == 3  # 无预缓冲:立即消费


def test_trim_count_increments_on_cap() -> None:
    g = SystemAudioGrabber(prime_ms=0)
    cap_frames = g._MAX_BUFFER_BYTES // g._FRAME_BYTES
    g._append(_frames([[1, 1]] * (cap_frames + 10)))
    assert g.trim_count == 1
