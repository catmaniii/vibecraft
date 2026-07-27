"""WASAPI loopback 音频采集探针 —— 复刻 SC2AudioCapture._ensure_recorder 的路径。

跑法（worktree 里）：
    uv run python scripts/probe_audio.py

测试时最好先在 PC 上放点声音（音乐 / 视频），否则 loopback 本来就返回静音。
判断：
  - 抛异常        → soundcard API / 设备层有问题，根因在采集
  - 全 0 不抛异常 → 采集通，只是当时没声音播放
  - 有非零幅度    → 采集层 OK，问题在下游（编码 / 协商 / 前端）
"""

from __future__ import annotations

import contextlib
import time
import traceback

import numpy as np


def main() -> None:
    print("=== WASAPI loopback 探针 ===")
    try:
        import soundcard  # type: ignore[import-untyped]

        print(f"soundcard 版本: {getattr(soundcard, '__version__', '?')}")
    except Exception:
        print("!! import soundcard 失败")
        traceback.print_exc()
        return

    # 列出所有扬声器 / 麦克风（含 loopback）
    try:
        speakers = soundcard.all_speakers()
        print(f"\n所有扬声器 ({len(speakers)}):")
        for s in speakers:
            print(f"  - name={s.name!r} id={getattr(s, 'id', '?')!r}")
        default_speaker = soundcard.default_speaker()
        print(f"\n默认扬声器: name={default_speaker.name!r}")
    except Exception:
        print("!! 枚举扬声器失败")
        traceback.print_exc()
        return

    # 复刻 _ensure_recorder：get_microphone(id=speaker.name, include_loopback=True)
    try:
        mic = soundcard.get_microphone(id=str(default_speaker.name), include_loopback=True)
        print(f"\nloopback mic: name={mic.name!r} isloopback={getattr(mic, 'isloopback', '?')}")
    except Exception:
        print("!! get_microphone(include_loopback=True) 失败")
        traceback.print_exc()
        return

    # 开 recorder：48kHz 立体声，复刻 SC2AudioCapture
    sample_rate = 48000
    channels = 2
    frame_samples = 960
    try:
        recorder = mic.recorder(samplerate=sample_rate, channels=channels)
        recorder.__enter__()
    except Exception:
        print("!! recorder 开启失败 (mic.recorder + __enter__)")
        traceback.print_exc()
        return

    print(f"\nrecorder 开启成功，开始录约 1 秒 ({sample_rate}Hz x {channels}ch)...")
    print("（如果想看到非零幅度，现在让 PC 放点声音）")
    try:
        chunks = []
        # 录 ~1 秒 = 约 50 个 20ms 帧
        for _ in range(50):
            data = recorder.record(numframes=frame_samples)
            chunks.append(np.ascontiguousarray(data, dtype=np.float32))
        arr = np.concatenate(chunks, axis=0)
        print(f"\n录到 shape={arr.shape} dtype={arr.dtype}")
        print(f"  RMS    = {float(np.sqrt(np.mean(arr**2))):.6f}")
        print(f"  峰值   = {float(np.max(np.abs(arr))):.6f}")
        print(f"  全 0   = {bool(np.all(arr == 0))}")
        # 单帧耗时（确认 record 是否阻塞到够帧 = 天然实时）
        t0 = time.time()
        recorder.record(numframes=frame_samples)
        dt = (time.time() - t0) * 1000
        print(f"  单帧 record(960) 耗时 = {dt:.1f}ms (理论 ~20ms)")
    except Exception:
        print("!! recorder.record() 失败")
        traceback.print_exc()
    finally:
        with contextlib.suppress(Exception):
            recorder.__exit__(None, None, None)

    # 顺带验证 av.AudioFrame 帧格式（复刻 _audio_frame_from_float）
    print("\n=== av.AudioFrame 帧格式验证 ===")
    try:
        import av

        print(f"av 版本: {av.__version__}")
        stereo = np.zeros((frame_samples, channels), dtype=np.float32)
        interleaved = stereo.reshape(-1)
        pcm = np.clip(interleaved * 32767.0, -32768.0, 32767.0).astype(np.int16)
        frame = av.AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16", layout="stereo")
        frame.sample_rate = sample_rate
        print(
            f"AudioFrame 构造成功: samples={frame.samples} "
            f"format={frame.format.name} layout={frame.layout.name} "
            f"rate={frame.sample_rate}"
        )
    except Exception:
        print("!! av.AudioFrame 构造失败")
        traceback.print_exc()


if __name__ == "__main__":
    main()
