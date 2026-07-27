"""系统声音采集子进程 —— WASAPI loopback → stdout 原始 PCM。

为什么独立子进程（方案 A+2，2026-06-03 用户决策）：
  原 soundcard 的 WASAPI loopback 在 native 层触发堆损坏(0xc0000374)会**崩整个
  server**（连视频 + 操控一起死，见 webrtc.py 历史注释 + logs/server_crash.log）。
  把音频采集隔离进子进程后，即便 native 再崩，也只死这个子进程，父进程
  (SystemAudioGrabber) 自动重启它，server 主体不受影响。

两种采集模式（2026-06-13 任务 #516）：
  - **--pid <pid>**：per-PID process loopback（process_loopback.py，Win10 20H1+）。
    只采该 PID（含子进程树）的渲染音频 —— 多人局每个 SC2 实例一路，互不混音。
    目标进程不出声时 WASAPI 不产包 → stdout 无输出，父进程 read() 补静音。
    初始化失败（老系统 / PID 已死）→ stderr 记一笔，回退 device loopback。
  - **无 --pid**：默认输出设备的 device loopback（PyAudioWPatch，整机混音），
    单人路径兼容 + pid 模式的兜底。

输出契约（父进程 SystemAudioGrabber 依赖）：
  - **stdout**：纯二进制 PCM，固定格式 **48000 Hz / 2ch / s16le 交织**
    （[L,R,L,R,...] int16 小端）。无 header、无分帧标记 —— 父进程按字节读。
  - **stderr**：人类可读诊断（设备名 / 错误 / 重采样信息）。父进程转发到日志。
  - **exit code**：正常不退出（持续采集）；致命错误 exit(1) → 父进程重启。

采集设备（device 模式）：默认输出设备(default speaker)对应的 loopback。SC2 声音
必须走这个设备才抓得到（坑：默认输出可能是 HDMI/虚拟声卡，SC2 不一定走它 → 静音。
设备名打到 stderr，静音排查时一眼看出）。

采样格式：device 模式按设备原生 rate/channels 采（WASAPI 共享模式 loopback 必须
匹配 mix format，不能强行要 48k）；再用 av.AudioResampler 统一重采样到
48k/stereo/s16。pid 模式下 process loopback 引擎按请求格式转换 —— 直接要
48k/2/s16，零重采样。
"""

from __future__ import annotations

import argparse
import faulthandler
import sys
from fractions import Fraction
from typing import Any, BinaryIO

# 目标输出格式（与 webrtc.py Opus 编码对齐）
TARGET_RATE = 48000
TARGET_CHANNELS = 2
# 采集块大小：480 帧 = 10ms @ 48k，低延迟
_READ_FRAMES = 480


def _pick_loopback(p: Any) -> Any:
    """选默认输出设备对应的 loopback device info；找不到则取第一个 loopback。"""
    import pyaudiowpatch as pyaudio  # type: ignore[import-untyped]

    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    default_out = p.get_device_info_by_index(wasapi["defaultOutputDevice"])

    # 默认输出本身若已是 loopback 设备直接用；否则按名字匹配它的 loopback 镜像
    if default_out.get("isLoopbackDevice"):
        return default_out
    chosen: dict[str, Any] | None = None
    for d in p.get_loopback_device_info_generator():
        if default_out["name"] in d["name"]:
            chosen = d
            break
        if chosen is None:
            chosen = d  # 兜底：第一个 loopback
    if chosen is None:
        raise RuntimeError("no WASAPI loopback device found")
    return chosen


def _layout_for(channels: int) -> str:
    """av AudioFrame layout 名（按声道数）。>2 声道当 stereo 处理（只取前 2 路）。"""
    if channels <= 1:
        return "mono"
    return "stereo"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="audio_grab")
    parser.add_argument(
        "--pid",
        type=int,
        default=None,
        help="目标进程 PID：用 process loopback 只采该进程(树)的声音；缺省=整机 device loopback",
    )
    return parser.parse_args(argv)


def _run_process_loopback(pid: int, out: BinaryIO) -> int:
    """per-PID process loopback 采集循环（2026-06-13 #516）。

    直接以目标格式 48k/2/s16 采，无重采样。目标进程静音期 read() 返回 b""
    → 不写 stdout，父进程补静音。正常不返回（直到被父进程 kill）。
    """
    # ActivateAudioInterfaceAsync 要求 MTA；必须在 import comtypes 之前设
    sys.coinit_flags = 0  # type: ignore[attr-defined]
    from vibecraft.server.process_loopback import ProcessLoopbackCapture

    cap = ProcessLoopbackCapture(pid, rate=TARGET_RATE, channels=TARGET_CHANNELS)
    cap.start()
    print(
        f"audio_grab: process loopback pid={pid} -> {TARGET_RATE}/{TARGET_CHANNELS}/s16",
        file=sys.stderr,
        flush=True,
    )
    try:
        while True:
            data = cap.read(100)
            if data:
                out.write(data)
                out.flush()
    finally:
        cap.close()


def main() -> int:
    # 子进程自带 faulthandler：native 崩溃栈落到 stderr，父进程转发到日志
    faulthandler.enable()

    args = _parse_args(sys.argv[1:])
    if args.pid is not None:
        try:
            return _run_process_loopback(args.pid, sys.stdout.buffer)
        except Exception as exc:
            # 老系统不支持 / PID 已死 / 激活失败 → 回退整机 device loopback
            # （至少有声，只是不分局）。父进程日志里能看到这行定位原因。
            print(
                f"audio_grab: process loopback failed ({type(exc).__name__}: {exc}); "
                "falling back to device loopback",
                file=sys.stderr,
                flush=True,
            )
    return _run_device_loopback()


def _run_device_loopback() -> int:
    """整机 device loopback 采集循环（PyAudioWPatch，原路径）。"""
    import av
    import numpy as np
    import pyaudiowpatch as pyaudio

    p = pyaudio.PyAudio()
    stream = None
    try:
        dev = _pick_loopback(p)
        native_rate = int(dev["defaultSampleRate"])
        native_ch = int(dev["maxInputChannels"]) or 2
        print(
            f"audio_grab: device={dev['name']!r} rate={native_rate} ch={native_ch} "
            f"-> target {TARGET_RATE}/{TARGET_CHANNELS}",
            file=sys.stderr,
            flush=True,
        )

        stream = p.open(
            format=pyaudio.paInt16,
            channels=native_ch,
            rate=native_rate,
            frames_per_buffer=_READ_FRAMES,
            input=True,
            input_device_index=dev["index"],
        )

        resampler = av.AudioResampler(
            format="s16", layout=_layout_for(TARGET_CHANNELS), rate=TARGET_RATE
        )
        in_layout = _layout_for(native_ch)
        pts = 0
        out = sys.stdout.buffer

        while True:
            data = stream.read(_READ_FRAMES, exception_on_overflow=False)
            arr = np.frombuffer(data, dtype=np.int16)
            if native_ch > 0:
                arr = arr.reshape(-1, native_ch)
            else:
                arr = arr.reshape(-1, 1)
            # >2 声道：只取前 2 路当 stereo（av 没有所有多声道 layout 的简单名）
            if native_ch > 2:
                arr = np.ascontiguousarray(arr[:, :2])
                in_layout = "stereo"
            # 打包成 av 期望的 (1, samples*channels) s16
            frame = av.AudioFrame.from_ndarray(arr.reshape(1, -1), format="s16", layout=in_layout)
            frame.sample_rate = native_rate
            frame.pts = pts
            frame.time_base = Fraction(1, native_rate)
            pts += arr.shape[0]

            for out_frame in resampler.resample(frame):
                out.write(out_frame.to_ndarray().tobytes())
            out.flush()
    except Exception as exc:
        print(f"audio_grab: fatal {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        try:
            if stream is not None:
                stream.stop_stream()
                stream.close()
        except Exception:
            pass
        p.terminate()
    # while True 只经异常路径退出（return 1）；正常路径不可达，无 fall-through。


if __name__ == "__main__":
    raise SystemExit(main())
