"""TURN relay 真实打通自测 —— 从本机(PC)向云 coturn 申请 relay 候选。

验证：网络可达 + TLS + REST 短期凭证鉴权 + relay 分配，全链路从真实客户端侧打通。
用 aiortc(vibecraft 同款库)，不依赖 SC2/浏览器。

测两条路：
  - turn:<dom>:3478?transport=udp   (PC 主路径)
  - turns:<dom>:443?transport=tcp   (穿中国防火墙的 TLS 路径)
各看 ICE gather 出的候选里有没有 `typ relay`（有=云上成功分配了中继地址=打通）。

secret/域名从 .secrets/vibecraft-turn.env 读（不硬编码、不进 git）。

用法：.venv/Scripts/python.exe deploy/turn/turn_selftest.py
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import re
import sys
import time
from pathlib import Path

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection

_ENV_PATH = Path(__file__).resolve().parents[2] / ".secrets" / "vibecraft-turn.env"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        env[k.strip()] = v.strip().strip('"')
    return env


def make_cred(secret: str, name: str = "vibecraft", ttl: int = 3600) -> tuple[str, str]:
    """coturn TURN REST 短期凭证：username=<expiry>:<name>，password=base64(HMAC-SHA1(secret,username))。"""
    expiry = int(time.time()) + ttl
    username = f"{expiry}:{name}"
    digest = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
    return username, base64.b64encode(digest).decode()


async def gather_candidates(urls: list[str], username: str, credential: str, timeout: float = 25.0):
    cfg = RTCConfiguration(
        iceServers=[RTCIceServer(urls=urls, username=username, credential=credential)]
    )
    pc = RTCPeerConnection(cfg)
    pc.createDataChannel("probe")
    await pc.setLocalDescription(await pc.createOffer())
    t0 = time.time()
    while pc.iceGatheringState != "complete" and time.time() - t0 < timeout:
        await asyncio.sleep(0.2)
    sdp = pc.localDescription.sdp if pc.localDescription else ""
    types = re.findall(r"a=candidate:\S+ \d+ \S+ \d+ \S+ \d+ typ (\w+)", sdp)
    relay_line = next((ln.strip() for ln in sdp.splitlines() if "typ relay" in ln), None)
    await pc.close()
    return types, relay_line


async def main() -> int:
    env = load_env(_ENV_PATH)
    secret = env["TURN_STATIC_SECRET"]
    dom = env["TURN_DOMAIN"]
    username, credential = make_cred(secret)
    print(f"TURN domain={dom}  cred-username={username}")

    tests = [
        ("turn  UDP :3478", [f"turn:{dom}:3478?transport=udp"]),
        ("turns TLS :443 ", [f"turns:{dom}:443?transport=tcp"]),
    ]
    results = []
    for name, urls in tests:
        try:
            types, relay = await gather_candidates(urls, username, credential)
        except Exception as exc:
            print(f"[FAIL] {name}: 异常 {type(exc).__name__}: {exc}")
            results.append(False)
            continue
        has_relay = "relay" in types
        print(f"[{'PASS' if has_relay else 'FAIL'}] {name}: 候选类型={sorted(set(types))}")
        if relay:
            print(f"        {relay}")
        results.append(has_relay)

    ok = all(results)
    print(
        "\n=== OVERALL:",
        "PASS — PC 能在云上申请到 relay 候选(打通) ===" if ok else "PARTIAL/FAIL（见上）===",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
