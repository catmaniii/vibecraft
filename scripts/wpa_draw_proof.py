"""WP-A 真局画框证明探针(2026-06-05)。

起一局真 VibeCraft 神族(vs VeryEasy)+ realtime,程序化注入两条接管指令:
  - game_t~10s:「派4个农民去基地左边待命」→ unit_claim → 方框(verb 配色 + 英文任务名)
  - game_t~22s:「把3个农民编成1队」     → 编队 group_id=1 → 圆环(队色 + "1")
然后保持运行,Opus 截 SC2 窗口判读框/圈到底有没有渲染。

用法: .venv/Scripts/python.exe scripts/wpa_draw_proof.py
需 DEEPSEEK_API_KEY(text command 走 LLM)。
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path

from vibecraft.server.game_process import GameConfig, GameProcess

_ROOT = Path(__file__).resolve().parents[1]
GAME_ID = "wpa_draw_proof"


def _last_game_t(telemetry: Path) -> float | None:
    try:
        lines = telemetry.read_text(encoding="utf-8").splitlines()
    except Exception:
        return None
    for ln in reversed(lines):
        try:
            rec = json.loads(ln)
            if "t" in rec:
                return float(rec["t"])
        except Exception:
            continue
    return None


def main() -> None:
    cfg = GameConfig(
        my_race="Protoss",
        opponent_race="Terran",
        opponent_difficulty="VeryEasy",
        realtime=True,
        window_x=0,
        window_y=0,
        game_id=GAME_ID,
    )
    telemetry = _ROOT / "logs" / GAME_ID / "telemetry.jsonl"
    # 清掉上一把的 telemetry,否则 _last_game_t 读到旧 game_t → 误判已到点立刻注入
    with contextlib.suppress(FileNotFoundError):
        telemetry.unlink()
    gp = GameProcess()
    gp.start(cfg)
    print("STARTED game, waiting for in_game...", flush=True)

    injected_box = False
    injected_ring = False
    t0 = time.monotonic()
    while time.monotonic() - t0 < 240:
        if not gp.is_running:
            print("GAME ENDED/crashed", flush=True)
            break
        gt = _last_game_t(telemetry)
        if gt is None:
            time.sleep(2)
            continue
        if gt >= 10 and not injected_box:
            # 框单位:派去主基地(留在基地镜头里),方框 3 层好对比
            gp.send_command(
                {
                    "type": "command",
                    "text": "派3个农民去主基地待命",
                    "issued_at": round(time.time(), 3),
                }
            )
            injected_box = True
            print(f"INJECTED box-cmd @ game_t={gt:.1f}", flush=True)
        if gt >= 22 and not injected_ring:
            # 环单位:编 1 队,留在矿线(基地镜头里),圆环 3 层
            gp.send_command(
                {"type": "command", "text": "把3个农民编成1队", "issued_at": round(time.time(), 3)}
            )
            injected_ring = True
            print(f"INJECTED ring-cmd @ game_t={gt:.1f}", flush=True)
        print(f"game_t={gt:.1f}", flush=True)
        if gt >= 32 and injected_box and injected_ring:
            print("READY_FOR_SCREENSHOT holding 60s...", flush=True)
            time.sleep(60)
            break
        time.sleep(3)

    import asyncio

    with contextlib.suppress(Exception):
        asyncio.run(gp.stop())
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
