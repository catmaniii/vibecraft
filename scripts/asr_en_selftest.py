"""英文 ASR 真模型自验（hermetic）：用**已提交的 wav fixture** 跑真 SenseVoiceSmall。

验证"真模型 + OfflineAsrSession + 后处理"这条链对 SC2 英文指令的识别质量（外部终态：
模型真的吐出正确文本）。**不**每次 live 调 edge-tts（fixture 一次合成、提交进 repo，
重新生成才用 edge-tts）。真机英文语音（手机麦克风）仍需用户端到端，这里覆盖到模型那一层。

用法（venv，首次会下载 ~1GB 模型，建议先跑 scripts/prefetch_asr_en.py）：
    .venv/Scripts/python.exe scripts/asr_en_selftest.py
退出码 0 = 通过（≥5/6 句剥标点/ITN 后一致），非 0 = 失败。
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

_FIX = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "asr_en"


def _norm(s: str) -> str:
    """剥非字母数字 + 小写 + 折叠空白（吸收尾标点、ITN one→1、复数差异由调用方容忍）。"""
    return " ".join(re.sub(r"[^a-z0-9 ]", "", s.lower()).split())


def _pcm16_bytes(wav_path: Path) -> bytes:
    """读 wav → 16k mono PCM16 小端字节（模拟手机发来的音频帧）。"""
    import numpy as np
    import soundfile as sf

    y, sr = sf.read(str(wav_path), dtype="float32")
    if y.ndim > 1:
        y = y.mean(axis=1)
    if sr != 16000:
        import librosa

        y = librosa.resample(y, orig_sr=sr, target_sr=16000)
    return (np.clip(y, -1, 1) * 32767).astype("<i2").tobytes()


async def _main() -> int:
    from vibecraft.server.asr import AsrEngine

    manifest = json.loads((_FIX / "manifest.json").read_text(encoding="utf-8"))
    engine = AsrEngine()
    if not engine.available_for("en"):
        print("英文 ASR 不可用（funasr 未装？）", file=sys.stderr)
        return 1
    print("加载英文模型（首次会下载 ~1GB）...")
    n_ok = 0
    for item in manifest:
        session = await engine.create_session("en")
        if session is None:
            print("create_session('en') 返回 None（加载失败）", file=sys.stderr)
            return 2
        pcm = _pcm16_bytes(_FIX / item["file"])
        # 模拟分帧喂（每 100ms 一帧），再 finalize
        frame = 1600 * 2  # 100ms @16k PCM16
        for off in range(0, len(pcm), frame):
            await session.feed(pcm[off : off + frame])
        hyp = await session.finalize()
        ref_n, hyp_n = _norm(item["text"]), _norm(hyp)
        # 容忍 ITN(one→1) / 复数(rays→ray)：词级召回 ≥ 0.8 视为对
        ref_w, hyp_w = ref_n.split(), hyp_n.split()
        recall = sum(1 for w in ref_w if w in hyp_w) / max(1, len(ref_w))
        ok = ref_n == hyp_n or recall >= 0.8
        n_ok += ok
        print(f"[{'OK ' if ok else 'BAD'}] ref={item['text']!r} hyp={hyp!r}")
    total = len(manifest)
    print(f"\n=== {n_ok}/{total} 通过 ===")
    return 0 if n_ok >= total - 1 else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
