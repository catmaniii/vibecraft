"""ASR 烟雾测试：加载 funasr 模型 + 跑一遍 feed/finalize，验证语音管线在 server 端跑通。

用法：
    .venv/Scripts/python.exe scripts/asr_smoke.py [wav_path]

- 不带 wav：喂 1s 静音 → 只验"模型能加载 + 管线 feed/finalize 不崩"（首次运行会下模型，~GB，慢）。
- 带 wav（必须 16kHz mono PCM16）：真识别，打印识别文字 → 验真实识别能力。

退出码 0=PASS / 1=FAIL。
"""

from __future__ import annotations

import asyncio
import sys
import wave
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.server.asr import AsrEngine  # noqa: E402


async def main() -> int:
    eng = AsrEngine()
    print(f"AsrEngine.available = {eng.available}")
    sess = await eng.create_session()
    if sess is None:
        print("FAIL: create_session 返回 None（模型未加载 / funasr 不可用）")
        return 1

    if len(sys.argv) > 1:
        wav_path = sys.argv[1]
        with wave.open(wav_path, "rb") as w:
            if w.getframerate() != 16000 or w.getnchannels() != 1:
                print(f"FAIL: wav 必须 16kHz mono，实际 {w.getframerate()}Hz {w.getnchannels()}ch")
                return 1
            pcm = w.readframes(w.getnframes())
        print(f"读入 {wav_path}: {len(pcm)} bytes PCM")
    else:
        pcm = b"\x00\x00" * 16000  # 1s 静音
        print("无 wav 参数 → 喂 1s 静音（只验加载 + 管线）")

    # 分块 feed（~100ms = 1600 samples = 3200 bytes）
    chunk = 3200
    partials: list[str] = []
    for i in range(0, len(pcm), chunk):
        p = await sess.feed(pcm[i : i + chunk])
        if p:
            partials.append(p)
    final = await sess.finalize()

    print(f"partials: {partials}")
    print(f"FINAL: {final!r}")
    print("PASS: 模型加载成功 + feed/finalize 管线跑通")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
