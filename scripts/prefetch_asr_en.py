"""部署期一次性预拉英文 ASR 模型（SenseVoiceSmall，~1GB）。

首次跑会从 modelscope 下载到本地缓存（~数分钟）；之后 server 起来后英文玩家的第一句
语音 finalize 就不会卡在下载上（见设计 docs/plans/2026-06-27-i18n-localization-design.md §10.5）。

用法（venv）：
    .venv/Scripts/python.exe scripts/prefetch_asr_en.py
"""

from __future__ import annotations

import asyncio
import sys


async def _main() -> int:
    from vibecraft.server.asr import AsrEngine

    engine = AsrEngine()
    if not engine.available:
        print("funasr 不可用（未安装？）— 跳过英文 ASR 预拉。", file=sys.stderr)
        return 1
    print("预拉英文 ASR 模型 SenseVoiceSmall（首次 ~1GB，请耐心）...")
    ok = await engine.warmup_en()
    if ok:
        print("英文 ASR 模型就绪（已缓存，后续秒载）。")
        return 0
    print("英文 ASR 模型加载失败（看上面的日志）。", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
