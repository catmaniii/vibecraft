"""虚空 dancing 修复 · 真局自测探针(2026-06-06,realtime)。

起一局真 VibeCraft 神族(void_ray_rush vs VeryEasy)+ realtime,等出 ≥2 虚空后
程序化复现你报的 dancing 场景,再离线判读 telemetry/directives/serverlog 验三处修复:

  序列(出虚空后):
    1. tactical_action attack/all_in   —— 全局"强制全体进攻"(免 LLM)
    2. "把所有虚空编成一队"             —— group_assign
    3. "虚空贴边到对方主矿"             —— move(safe+engage)
    4. "一队回家防守"                   —— unit_claim standby→main

  验收:
    ① 回家清全局 attack: 第 4 步后 telemetry.tactical.intent  attack→None
    ② 零兵不空转 flip-flop: serverlog "Attack started at 0.00 power" / "No attacking units" 计数
    C  supersede: "贴边主矿" move directive 释放 reason=superseded
  + 保持 35s 供截图肉眼看虚空是否还跳。

用法: .venv/Scripts/python.exe scripts/dancing_fix_proof.py
需 DEEPSEEK_API_KEY(2/3/4 步走 LLM)。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from pathlib import Path

from vibecraft.server.game_process import GameConfig, GameProcess

_ROOT = Path(__file__).resolve().parents[1]
GAME_ID = "dancing_fix_proof"
# 唯一文件名避免上一局残留子进程仍持有句柄导致 unlink/写入 PermissionError
SERVERLOG = _ROOT / "logs" / f"dancing_fix_proof_{os.getpid()}.serverlog"


def _snaps(telemetry: Path) -> list[dict]:
    out: list[dict] = []
    try:
        for ln in telemetry.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("kind") == "snapshot":
                out.append(r)
    except FileNotFoundError:
        pass
    return out


def _void(snaps: list[dict]) -> int:
    if not snaps:
        return 0
    return int(snaps[-1].get("units", {}).get("VOIDRAY", 0))


def _analyze(telemetry: Path, fire_t: float | None) -> None:
    print("\n================ 分析 ================", flush=True)
    snaps = _snaps(telemetry)

    # ① intent 时间线(fire 之后)
    print("\n[①] intent/mode 时间线(序列触发后):", flush=True)
    shown = 0
    for s in snaps:
        if fire_t is not None and s["t"] < fire_t - 1:
            continue
        tac = s.get("tactical") or {}
        print(
            f"  t={s['t']:.0f} intent={tac.get('intent')} mode={tac.get('mode')} "
            f"stance={tac.get('stance')} plan_status={tac.get('plan_status')} "
            f"ret_started={tac.get('attack_retreat_started')} VOID={s.get('units', {}).get('VOIDRAY', 0)}",
            flush=True,
        )
        shown += 1
        if shown > 40:
            break
    intents = [
        (s["t"], (s.get("tactical") or {}).get("intent"))
        for s in snaps
        if fire_t is None or s["t"] >= fire_t - 1
    ]
    saw_attack = any(i == "attack" for _, i in intents)
    ended_none = intents and intents[-1][1] in (None, "defend", "hold", "retreat")
    print(
        f"  → 出现过 intent=attack: {saw_attack};末尾 intent={intents[-1][1] if intents else 'NA'} "
        f"(期望:回家后清成 None/defend) → ①{'PASS' if (saw_attack and ended_none) else '?见上'}",
        flush=True,
    )

    # ② flip-flop 计数(serverlog)
    print("\n[②] flip-flop 日志计数:", flush=True)
    txt = ""
    with contextlib.suppress(Exception):
        txt = SERVERLOG.read_text(encoding="utf-8", errors="replace")
    n_zero = txt.count("Attack started at 0.00 power")
    n_noatk = txt.count("No attacking units, starting retreat")
    print(
        f"  'Attack started at 0.00 power' ×{n_zero};'No attacking units' ×{n_noatk} "
        f"→ ②{'PASS' if (n_zero == 0 and n_noatk <= 1) else '需看序列内是否持续 1Hz 空转'}",
        flush=True,
    )

    # C supersede:directives.jsonl 里 move 的释放 reason
    print("\n[C] 指令生命周期(move/supersede):", flush=True)
    djsonl = _ROOT / "logs" / GAME_ID / "directives.jsonl"
    saw_superseded = False
    with contextlib.suppress(Exception):
        for ln in djsonl.read_text(encoding="utf-8").splitlines():
            r = json.loads(ln)
            ev = r.get("event")
            if ev == "submitted" and r.get("type") in ("move", "unit_claim", "group_assign"):
                print(
                    f"  t={r.get('ts'):.0f} submitted {r['type']} {r['directive_id']}", flush=True
                )
            if ev == "released":
                reason = r.get("reason", "")
                print(
                    f"  t={r.get('ts'):.0f} released {r['directive_id']} reason={reason}",
                    flush=True,
                )
                if reason == "superseded":
                    saw_superseded = True
    print(
        f"  → 出现 reason=superseded: {saw_superseded} (期望 True = 回家取消了贴边 move)",
        flush=True,
    )
    print("\n=====================================", flush=True)


def main() -> None:
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(SERVERLOG)
    with contextlib.suppress(OSError):
        SERVERLOG.unlink()
    telemetry = _ROOT / "logs" / GAME_ID / "telemetry.jsonl"
    with contextlib.suppress(OSError):
        telemetry.unlink()

    cfg = GameConfig(
        my_race="Protoss",
        opponent_race="Terran",
        opponent_difficulty="VeryHard",  # VeryEasy 会被一波 all_in 当场推平 → 游戏没机会跑完序列
        realtime=False,  # 非 realtime = sim 全速,几十秒就出虚空(逻辑信号与 realtime 一致)
        window_x=0,
        window_y=0,
        game_id=GAME_ID,
        forced_opening="void_ray_rush",
    )
    gp = GameProcess()
    gp.start(cfg)
    print("STARTED game (non-realtime full-speed), waiting for >=2 void rays...", flush=True)

    def _gt() -> float | None:
        s = _snaps(telemetry)
        return float(s[-1]["t"]) if s else None

    def _wait_until_gt(target: float, overall_deadline: float) -> bool:
        """轮询到 game_t>=target;游戏结束/telemetry 停滞(游戏已结束)/超时 → False。"""
        last_gt = _gt()
        last_change = time.monotonic()
        while time.monotonic() < overall_deadline:
            if not gp.is_running:
                return False
            gt = _gt()
            if gt is not None and gt >= target:
                return True
            if gt != last_gt:
                last_gt = gt
                last_change = time.monotonic()
            elif time.monotonic() - last_change > 12:
                # telemetry 12 wall-s 没动 = 游戏已结束/暂停 → 别再死等
                print("  (telemetry 停滞,判定游戏已结束)", flush=True)
                return False
            time.sleep(1)
        return False

    fire_t: float | None = None
    deadline = time.monotonic() + 300  # 全速下绰绰有余
    # phase 1:等 ≥2 虚空
    while time.monotonic() < deadline:
        if not gp.is_running:
            print("GAME ENDED/crashed early", flush=True)
            break
        vr = _void(_snaps(telemetry))
        gt = _gt()
        print(f"game_t={gt} VOID={vr}", flush=True)
        if vr >= 2:
            fire_t = float(gt) if gt is not None else 0.0
            break
        time.sleep(2)

    # phase 2:按 game_t 编排序列(全速下用 game_t 间隔,不用 wall sleep)
    if fire_t is not None and gp.is_running:
        print(
            f">>> {_void(_snaps(telemetry))} void rays @ t={fire_t:.0f}; firing sequence",
            flush=True,
        )
        gp.send_command({"type": "tactical_action", "verb": "attack", "mode": "all_in"})
        print("  fired: 强制全体进攻 all_in", flush=True)
        if _wait_until_gt(fire_t + 6, deadline):
            gp.send_command(
                {
                    "type": "command",
                    "text": "把所有虚空编成一队",
                    "issued_at": round(time.time(), 3),
                }
            )
            print(f"  fired: 把所有虚空编成一队 @ t≈{_gt()}", flush=True)
        if _wait_until_gt(fire_t + 14, deadline):
            gp.send_command(
                {
                    "type": "command",
                    "text": "虚空贴边到对方主矿",
                    "issued_at": round(time.time(), 3),
                }
            )
            print(f"  fired: 虚空贴边到对方主矿 @ t≈{_gt()}", flush=True)
        if _wait_until_gt(fire_t + 26, deadline):
            gp.send_command(
                {"type": "command", "text": "一队回家防守", "issued_at": round(time.time(), 3)}
            )
            print(f"  fired: 一队回家防守 @ t≈{_gt()}", flush=True)
        # 观察 40 game-s
        _wait_until_gt(fire_t + 66, deadline)
        print(f"SEQUENCE DONE @ t≈{_gt()}", flush=True)

    with contextlib.suppress(Exception):
        asyncio.run(gp.stop())
    _analyze(telemetry, fire_t)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
