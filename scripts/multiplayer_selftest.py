"""multiplayer_selftest.py — 多人阶段 0 端到端自验（MP-T11）。

起**真 BotService**（in-process）+ 两个 websockets 客户端模拟两部手机，走完整链路：
入房 → lobby（选种族/ready）→ lobby_start → 双 SC2 实例 host/join 成局 →
指令路由隔离（A 的指令绝不进 B）→ end_game 收场回 lobby → 无 SC2 孤儿。

mock LLM（VIBECRAFT_MOCK_LLM_JSON）→ non-realtime，不花 API 钱、不用等真 LLM。

用法（.venv 里跑，需 SC2 客户端）：
  .venv/Scripts/python.exe scripts/multiplayer_selftest.py
  .venv/Scripts/python.exe scripts/multiplayer_selftest.py --play-seconds 60

判读：末行 SELFTEST PASS / FAIL（带逐项 checklist）。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

_PORT = 18090
_TOKEN = "mp-selftest"

MOCK_LLM = [
    {
        "match": "防守",
        "response": {
            "interpretation_zh": "全军防守（持续姿态）",
            "confidence": 0.95,
            "directives": [
                {"type": "tactical_objective", "payload": {"verb": "defend", "persistent": True}}
            ],
        },
    },
]


class WsClient:
    """一个模拟手机：收帧入列表 + 谓词等待。"""

    def __init__(self, name: str, pid: str) -> None:
        self.name = name
        self.pid = pid
        self.frames: list[dict[str, Any]] = []
        self._ws: Any = None
        self._reader: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        import websockets

        url = f"ws://127.0.0.1:{_PORT}/ws?room={_TOKEN}&player={self.name}&pid={self.pid}"
        self._ws = await websockets.connect(url, ping_interval=None)
        self._reader = asyncio.create_task(self._read_loop(), name=f"reader-{self.name}")

    async def _read_loop(self) -> None:
        with contextlib.suppress(Exception):
            async for raw in self._ws:
                with contextlib.suppress(Exception):
                    self.frames.append(json.loads(raw))

    async def send(self, frame: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(frame))

    async def wait_for(self, pred: Any, timeout_s: float, desc: str) -> dict[str, Any] | None:
        """等到某帧满足谓词；超时返回 None。"""
        deadline = time.monotonic() + timeout_s
        seen = 0
        while time.monotonic() < deadline:
            for f in self.frames[seen:]:
                if pred(f):
                    return f
            seen = len(self.frames)
            await asyncio.sleep(0.3)
        print(f"  [WAIT-TIMEOUT] {self.name}: {desc}（{timeout_s:.0f}s）", flush=True)
        return None

    def count(self, pred: Any) -> int:
        return sum(1 for f in self.frames if pred(f))

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._ws:
            with contextlib.suppress(Exception):
                await self._ws.close()


def _room_state(state: str) -> Any:
    return lambda f: f.get("type") == "room_state" and f.get("state") == state


def _playing(f: dict[str, Any]) -> bool:
    return f.get("type") == "game_status" and f.get("sc2") == "playing"


async def main() -> int:
    ap = argparse.ArgumentParser(description="多人阶段 0 端到端自验")
    ap.add_argument("--play-seconds", type=float, default=45.0, help="进局后观战 wall 秒")
    ap.add_argument("--launch-timeout", type=float, default=300.0, help="等 playing 超时")
    args = ap.parse_args()

    ts = int(time.time())
    mock_path = _ROOT / "logs" / f"mpself_mock_{ts}.json"
    mock_path.parent.mkdir(parents=True, exist_ok=True)
    mock_path.write_text(json.dumps(MOCK_LLM, ensure_ascii=False), encoding="utf-8")
    server_log = _ROOT / "logs" / f"mpself_server_{ts}.log"
    os.environ["VIBECRAFT_MOCK_LLM_JSON"] = str(mock_path)
    os.environ["VIBECRAFT_SERVER_LOG_PATH"] = str(server_log)

    from vibecraft.server.service import BotService, ServiceConfig

    svc = BotService(
        ServiceConfig(
            port=_PORT,
            token=_TOKEN,
            default_realtime=False,  # mock LLM → non-realtime（CLAUDE.md 纪律）
            enable_webrtc=False,  # 自验不验视频，省 aiortc 开销
        )
    )
    svc_task = asyncio.create_task(svc.run(), name="bot-service")
    await asyncio.sleep(2.0)  # 等端口起来

    checks: list[tuple[str, bool]] = []

    def check(desc: str, ok: bool) -> None:
        checks.append((desc, ok))
        print(f"  [{'PASS' if ok else 'FAIL'}] {desc}", flush=True)

    alice = WsClient("alice", "pa")
    bob = WsClient("bob", "pb")
    try:
        # ---- 1. 入房：connect 后各发 lobby_join 才真正入房（连接与入房解耦）----
        await alice.connect()
        await alice.send({"type": "lobby_join"})
        f = await alice.wait_for(
            lambda f: f.get("type") == "room_state" and f.get("host_player_id") == "pa",
            10,
            "alice lobby_join room_state",
        )
        check("alice lobby_join 后收到 room_state 且为房主", f is not None)

        await bob.connect()
        await bob.send({"type": "lobby_join"})
        f = await bob.wait_for(
            lambda f: (
                f.get("type") == "room_state"
                and sum(1 for s in f.get("slots", []) if s.get("kind") == "bot") == 2
            ),
            10,
            "bob lobby_join 后双人 room_state",
        )
        check("bob lobby_join，双人 slot 就位", f is not None)

        # ---- 2. lobby 操作：bob 选 Zerg；双方 ready；非房主 start 被拒 ----
        await bob.send({"type": "lobby_set_race", "race": "Zerg"})
        f = await alice.wait_for(
            lambda f: (
                f.get("type") == "room_state"
                and any(
                    s.get("player_id") == "pb" and s.get("race") == "Zerg"
                    for s in f.get("slots", [])
                )
            ),
            10,
            "bob 选 Zerg 广播到 alice",
        )
        check("bob 选 Zerg，alice 收到广播", f is not None)

        await bob.send({"type": "lobby_start"})
        f = await bob.wait_for(lambda f: f.get("type") == "room_error", 10, "非房主 start 被拒")
        check("非房主 lobby_start 收到 room_error", f is not None)

        await alice.send({"type": "lobby_ready", "ready": True})
        await bob.send({"type": "lobby_ready", "ready": True})

        def _both_ready(f: dict[str, Any]) -> bool:
            # 注意必须要求"恰好 2 个 bot 位"：解耦后握手先发空房间预览帧，
            # 0 个 bot 位时 all([]) 空集恒真 → 谓词假通过 → start 与 ready 赛跑被拒
            # （2026-06-12 排查实录：rejected reason="还有玩家未准备：bob"）。
            if f.get("type") != "room_state":
                return False
            bots = [s for s in f.get("slots", []) if s.get("kind") == "bot"]
            return len(bots) == 2 and all(s.get("ready") for s in bots)

        f = await alice.wait_for(_both_ready, 10, "双方 ready 广播")
        check("双方 ready", f is not None)

        # ---- 3. 房主开局 → starting → 双实例进局 playing ----
        await alice.send({"type": "lobby_start"})
        f = await bob.wait_for(_room_state("starting"), 15, "starting 广播")
        check("lobby_start → 双方收到 starting", f is not None)

        fa = await alice.wait_for(_playing, args.launch_timeout, "alice 侧 playing")
        fb = await bob.wait_for(_playing, args.launch_timeout, "bob 侧 playing")
        check("双方都收到自己实例的 sc2=playing", fa is not None and fb is not None)

        f = await alice.wait_for(_room_state("in_game"), 30, "in_game 广播")
        check("room_state 进入 in_game", f is not None)

        # ---- 4. 指令路由隔离：alice 下令，echo 只回 alice ----
        n_bob_before = bob.count(lambda f: f.get("type") in ("command_received", "command_echo"))
        await alice.send({"type": "command", "text": "全军防守"})
        f = await alice.wait_for(
            lambda f: f.get("type") == "command_echo", 30, "alice 收 command_echo"
        )
        check("alice 收到自己指令的 echo", f is not None)
        await asyncio.sleep(5)
        n_bob_after = bob.count(lambda f: f.get("type") in ("command_received", "command_echo"))
        check("bob 没收到 alice 的指令回执（路由隔离）", n_bob_after == n_bob_before)

        # ---- 5. 观战一段 → snapshot 双方各自有 ----
        await asyncio.sleep(args.play_seconds)
        check(
            "alice 收到 snapshot 流",
            alice.count(lambda f: f.get("type") == "snapshot") > 0,
        )
        check(
            "bob 收到 snapshot 流",
            bob.count(lambda f: f.get("type") == "snapshot") > 0,
        )

        # ---- 6. 收场：end_game → 双方回 lobby ----
        await alice.send({"type": "end_game"})
        fa = await alice.wait_for(_room_state("lobby"), 60, "alice 回 lobby")
        fb = await bob.wait_for(_room_state("lobby"), 60, "bob 回 lobby")
        check("end_game 后双方 room_state 回 lobby", fa is not None and fb is not None)

    finally:
        await alice.close()
        await bob.close()
        # 关 service + 清残留
        with contextlib.suppress(Exception):
            await svc.room_service.stop_match()
        svc_task.cancel()
        with contextlib.suppress(BaseException):
            await svc_task
        await asyncio.sleep(2.0)

    # ---- 7. SC2 孤儿检查 ----
    try:
        import psutil

        orphans = [
            p.pid
            for p in psutil.process_iter(["name"])
            if "sc2_x64" in (p.info["name"] or "").lower()
        ]
        check("无 SC2_x64 孤儿进程", not orphans)
        for pid in orphans:  # 兜底清掉，别给用户留黑屏窗
            with contextlib.suppress(Exception):
                psutil.Process(pid).kill()
    except ImportError:
        pass

    ok = all(c[1] for c in checks)
    print("\n" + "=" * 60)
    for desc, passed in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {desc}")
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({sum(1 for c in checks if c[1])}/{len(checks)})")
    print(f"server log: {server_log}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
