"""Spike：验证 per-PID WASAPI process loopback（任务 #516 可行性铁证）。

跑法（venv）::

    .venv/Scripts/python.exe scripts/spike_process_loopback.py

做三件事：
1. 起一个**循环放声音**的 python 子进程（winsound 循环播系统 wav）+ 一个**安静**子进程；
2. 用 ProcessLoopbackCapture 分别按两个 PID 各采 ~3s；
3. 断言：有声 PID 采到非静音（RMS 显著>0），安静 PID 采到全静音 → PASS/FAIL 退出码。

顺带把有声 PID 的采样写成 logs/spike_loopback.wav，可人耳复核。
"""

from __future__ import annotations

import math
import subprocess
import sys
import time
import wave
from pathlib import Path

sys.coinit_flags = 0  # MTA —— 必须在 import comtypes/process_loopback 之前

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vibecraft.server.process_loopback import ProcessLoopbackCapture  # noqa: E402

_WAV_CANDIDATES = [
    r"C:\Windows\Media\Alarm01.wav",
    r"C:\Windows\Media\Ring01.wav",
    r"C:\Windows\Media\tada.wav",
]


def _pick_wav() -> str:
    for p in _WAV_CANDIDATES:
        if Path(p).exists():
            return p
    raise SystemExit("no system wav found for test sound")


def _capture(pid: int, seconds: float) -> bytes:
    cap = ProcessLoopbackCapture(pid, rate=48000, channels=2)
    cap.start()
    try:
        buf = bytearray()
        deadline = time.time() + seconds
        while time.time() < deadline:
            buf.extend(cap.read(100))
        return bytes(buf)
    finally:
        cap.close()


def _rms(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    import array

    arr = array.array("h")
    arr.frombytes(pcm[: len(pcm) // 2 * 2])
    if not arr:
        return 0.0
    return math.sqrt(sum(x * x for x in arr) / len(arr))


def main() -> int:
    wav_path = _pick_wav()
    print(f"test sound: {wav_path}")

    noisy_code = (
        "import winsound, time; "
        f"winsound.PlaySound(r'{wav_path}', "
        "winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP); "
        "time.sleep(60)"
    )
    quiet_code = "import time; time.sleep(60)"
    noisy = subprocess.Popen([sys.executable, "-c", noisy_code])
    quiet = subprocess.Popen([sys.executable, "-c", quiet_code])
    try:
        time.sleep(1.5)  # 等播放起来

        pcm_noisy = _capture(noisy.pid, 3.0)
        pcm_quiet = _capture(quiet.pid, 3.0)
    finally:
        noisy.kill()
        quiet.kill()

    rms_noisy = _rms(pcm_noisy)
    rms_quiet = _rms(pcm_quiet)
    print(f"noisy pid={noisy.pid}: {len(pcm_noisy)} bytes, RMS={rms_noisy:.1f}")
    print(f"quiet pid={quiet.pid}: {len(pcm_quiet)} bytes, RMS={rms_quiet:.1f}")

    out = Path("logs/spike_loopback.wav")
    out.parent.mkdir(exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(pcm_noisy)
    print(f"wrote {out} ({len(pcm_noisy)} bytes)")

    ok = rms_noisy > 100.0 and rms_quiet < 10.0
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
