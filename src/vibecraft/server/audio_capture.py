"""系统声音采集的父进程管理器 —— spawn audio_grab 子进程 + ring buffer + 崩溃自愈。

方案 A+2（2026-06-03 用户）：采集本身在隔离子进程里跑（见 audio_grab.py），
本类负责：
  - spawn 子进程，从其 stdout 持续读 48k/2/s16 PCM 进环形缓冲；
  - 子进程死了（native 崩溃 / EOF）→ 按退避自动重启，server 主体不受影响；
  - 把子进程 stderr 转发到结构化日志（设备名 / 崩溃栈 / 静音诊断）；
  - read(n) 给 aiortc 音频轨取帧，缓冲不足补静音（解耦子进程阻塞与 recv 节流）。

线程模型：一个 manager 线程跑 spawn→pump→restart 循环；一个 stderr drain 线程。
read() 在 aiortc 的音频 executor 线程里被调用，与 pump 线程靠 _lock 同步访问 buffer。
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import threading
import time

import numpy as np
import numpy.typing as npt
import structlog

logger = structlog.get_logger(__name__)


class SystemAudioGrabber:
    """管理 audio_grab 子进程，提供线程安全的 read(n_frames)。

    输出格式固定 48000 Hz / 2ch / s16（与子进程契约一致）。子进程异常退出时
    按退避重启；重启期间 read() 返回静音，不抛异常、不阻塞 recv。

    pid（2026-06-13 任务 #516）：非 None 时子进程用 per-PID process loopback
    只采该进程(树)的声音 —— 多人局每个 SC2 实例一路独立音频；None = 整机
    device loopback（单人路径原行为）。
    """

    RATE = 48000
    CHANNELS = 2
    _SAMPLE_BYTES = 2  # s16
    _FRAME_BYTES = CHANNELS * _SAMPLE_BYTES  # 每个采样点(跨声道) 4 字节
    # 环形缓冲上限 ~200ms：生产快于消费时丢最旧，控制端到端延迟
    _MAX_BUFFER_BYTES = RATE * _FRAME_BYTES // 5
    # 起播预缓冲（2026-06-13 修张三音乐破音）：process loopback 在游戏静音期**不产包**
    # （device loopback 是持续产流），缓冲被排空到 0；声音恢复后生产/消费同速率，缓冲
    # 一直贴 0 走 —— Windows 调度抖动(时间片 ~15.6ms)随便一抖就供不上 20ms 帧 →
    # 补零 → 持续音乐里听得见咔哒(人声短促听不出)。修法：缓冲攒够 prime_ms 才开始
    # 消费，欠载后重新蓄水，用 ~60ms 余量垫掉抖动（代价=音频多 60ms 延迟，可忽略）。
    _DEFAULT_PRIME_MS = 60
    # 缓冲统计日志限频（秒）
    _STAT_LOG_INTERVAL_S = 10.0
    # 子进程 stdout 单次读块（字节）；按字节拼接，不要求与帧对齐
    _READ_CHUNK = 4096
    # 重启退避序列（秒），到顶后保持最大值
    _RESTART_BACKOFF = (0.5, 1.0, 2.0, 5.0)

    # 子进程启动命令（可在测试中 monkeypatch 成 echo 假进程）
    _SPAWN_CMD = (sys.executable, "-u", "-m", "vibecraft.server.audio_grab")

    def __init__(self, pid: int | None = None, prime_ms: int | None = None) -> None:
        self.pid = pid
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._proc: subprocess.Popen[bytes] | None = None
        self._mgr_thread: threading.Thread | None = None
        self._started = False
        self.restart_count = 0
        # 起播预缓冲：prime_ms=0 关闭（部分单测用）；None = 默认 60ms
        ms = self._DEFAULT_PRIME_MS if prime_ms is None else prime_ms
        self._prime_bytes = self.RATE * self._FRAME_BYTES * ms // 1000
        self._primed = self._prime_bytes == 0
        # 诊断计数：underrun=消费欠载(重新蓄水)次数；trim=缓冲超上限丢最旧次数
        self.underrun_count = 0
        self.trim_count = 0
        self._last_stat_log = 0.0
        self._log = logger.bind(component="audio_grabber", pid=pid)

    def start(self) -> None:
        """启动 manager 线程（非阻塞）。spawn 子进程在后台进行，不卡调用方。"""
        if self._started:
            return
        self._started = True
        self._stop.clear()
        self._mgr_thread = threading.Thread(target=self._run, name="audio-grab-mgr", daemon=True)
        self._mgr_thread.start()

    def _run(self) -> None:
        """manager 循环：spawn → pump 到死 → 退避后重启，直到 stop()。"""
        attempt = 0
        while not self._stop.is_set():
            try:
                self._spawn_and_pump()
            except Exception:
                self._log.warning("audio_grab_pump_error", exc_info=True)
            if self._stop.is_set():
                break
            backoff = self._RESTART_BACKOFF[min(attempt, len(self._RESTART_BACKOFF) - 1)]
            self.restart_count += 1
            self._log.warning("audio_grab_restart", restart=self.restart_count, backoff_s=backoff)
            attempt += 1
            self._stop.wait(backoff)

    def _spawn_cmd(self) -> list[str]:
        """子进程命令行：有 pid 时追加 --pid（process loopback 模式）。"""
        cmd = list(self._SPAWN_CMD)
        if self.pid is not None:
            cmd += ["--pid", str(self.pid)]
        return cmd

    def _spawn_and_pump(self) -> None:
        """spawn 子进程并把它的 stdout PCM 持续抽进 buffer，直到它退出或 stop()。"""
        proc = subprocess.Popen(
            self._spawn_cmd(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._proc = proc
        self._log.info("audio_grab_spawned", pid=proc.pid)
        if proc.stderr is not None:
            threading.Thread(
                target=self._drain_stderr, args=(proc,), name="audio-grab-err", daemon=True
            ).start()
        try:
            stdout = proc.stdout
            assert stdout is not None
            while not self._stop.is_set():
                chunk = stdout.read(self._READ_CHUNK)
                if not chunk:
                    break  # EOF —— 子进程退出（崩溃或正常）
                self._append(chunk)
        finally:
            with contextlib.suppress(Exception):
                proc.terminate()
            with contextlib.suppress(Exception):
                proc.wait(timeout=2)
            self._log.info("audio_grab_exited", pid=proc.pid, code=proc.returncode)
            self._proc = None

    def _append(self, chunk: bytes) -> None:
        """把一块 PCM 追加进 ring buffer；超 _MAX_BUFFER_BYTES 丢最旧（低延迟优先）。"""
        with self._lock:
            self._buf.extend(chunk)
            if len(self._buf) > self._MAX_BUFFER_BYTES:
                del self._buf[: len(self._buf) - self._MAX_BUFFER_BYTES]
                self.trim_count += 1

    def _drain_stderr(self, proc: subprocess.Popen[bytes]) -> None:
        """把子进程 stderr 逐行转发到日志（设备名 / native 崩溃栈 / 诊断）。"""
        stderr = proc.stderr
        if stderr is None:
            return
        for raw in iter(stderr.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self._log.info("audio_grab_stderr", line=line)

    def read(self, n_frames: int) -> npt.NDArray[np.int16]:
        """取 n_frames 个采样点，返回 (n_frames, 2) int16。缓冲不足补静音(0)。

        起播预缓冲：未 primed 时不消费（回静音），等缓冲攒够 _prime_bytes 再开闸；
        消费中欠载 → 取空 + 回到未 primed 重新蓄水。游戏静音期(process loopback
        不产包)自然停在未 primed 态，声音恢复后先攒 60ms 再播，垫掉调度抖动。
        """
        need = n_frames * self._FRAME_BYTES
        with self._lock:
            if not self._primed:
                if len(self._buf) >= self._prime_bytes:
                    self._primed = True
                else:
                    # 蓄水中：不消费，回静音
                    self._maybe_log_stats_locked()
                    return np.zeros((n_frames, self.CHANNELS), dtype=np.int16)
            if len(self._buf) >= need:
                data = bytes(self._buf[:need])
                del self._buf[:need]
            else:
                data = bytes(self._buf)
                self._buf.clear()
                self._primed = self._prime_bytes == 0
                self.underrun_count += 1
            self._maybe_log_stats_locked()
        out = np.zeros((n_frames, self.CHANNELS), dtype=np.int16)
        if data:
            arr = np.frombuffer(data, dtype=np.int16)
            # 防御：截到偶数长度再 reshape（理论上 buffer 始终 4 对齐）
            usable = (arr.size // self.CHANNELS) * self.CHANNELS
            if usable:
                arr = arr[:usable].reshape(-1, self.CHANNELS)
                out[: arr.shape[0]] = arr[:n_frames]
        return out

    def _maybe_log_stats_locked(self) -> None:
        """限频落一行缓冲健康统计（调用方须已持 _lock；计数全 0 不刷屏）。

        诊断用（2026-06-13 张三音乐破音）：underruns 涨 = 消费欠载（抖动/CPU 饥饿），
        trims 涨 = 生产快于消费（时钟漂移/消费端卡）。两者都是可听破音的来源。
        """
        now = time.monotonic()
        if now - self._last_stat_log < self._STAT_LOG_INTERVAL_S:
            return
        self._last_stat_log = now
        if self.underrun_count or self.trim_count:
            self._log.info(
                "audio_buffer_stats",
                underruns=self.underrun_count,
                trims=self.trim_count,
                fill_ms=len(self._buf) * 1000 // (self.RATE * self._FRAME_BYTES),
                primed=self._primed,
            )

    def stop(self) -> None:
        """停止 manager 循环 + 杀子进程（aiortc 关音频轨时调用）。"""
        if not self._started:
            return
        self._stop.set()
        proc = self._proc
        if proc is not None:
            with contextlib.suppress(Exception):
                proc.terminate()
        self._started = False
