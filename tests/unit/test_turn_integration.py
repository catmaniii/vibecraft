"""TURN 接入集成单测：/api/turn-credential 端点 + WebRtcManager iceServers 构造。

不变量：无 turn_config → 端点空 iceServers、manager 空 RTCIceServer（行为不变）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from vibecraft.server.http import _serve_turn_credential
from vibecraft.server.turn_config import TurnConfig
from vibecraft.server.webrtc import WebRtcManager

_CFG = TurnConfig(domain="d.sslip.io", secret="sek", port=3478, tls_port=443)


def _body(resp) -> dict:
    return json.loads(resp.body)


def _req(path: str):
    # _serve_turn_credential 只读 request.path
    return SimpleNamespace(path=path)


def test_turn_credential_no_config_returns_empty():
    resp = _serve_turn_credential(_req("/api/turn-credential"), None, None)
    assert _body(resp) == {"iceServers": []}


def test_turn_credential_with_config_no_gate():
    resp = _serve_turn_credential(_req("/api/turn-credential"), _CFG, None)
    servers = _body(resp)["iceServers"]
    assert any("turns:d.sslip.io:443?transport=tcp" in s.get("urls", []) for s in servers)
    assert any("username" in s and "credential" in s for s in servers)


def test_turn_credential_room_token_match():
    resp = _serve_turn_credential(_req("/api/turn-credential?room=secret-tok"), _CFG, "secret-tok")
    assert _body(resp)["iceServers"], "room token 匹配 → 应下发 iceServers"


def test_turn_credential_room_token_mismatch_returns_empty():
    resp = _serve_turn_credential(_req("/api/turn-credential?room=wrong"), _CFG, "secret-tok")
    assert _body(resp) == {"iceServers": []}


def test_turn_credential_room_token_missing_returns_empty():
    # 配了 room_token 门控但请求没带 ?room → 空（挡随机扫描）
    resp = _serve_turn_credential(_req("/api/turn-credential"), _CFG, "secret-tok")
    assert _body(resp) == {"iceServers": []}


def test_webrtc_manager_no_turn_empty_ice_servers():
    mgr = WebRtcManager()
    assert mgr._ice_servers() == []


def test_webrtc_manager_with_turn_builds_ice_servers():
    mgr = WebRtcManager(turn_config=_CFG)
    servers = mgr._ice_servers()
    assert servers, "有 turn_config → 应构造 RTCIceServer"
    # aiortc RTCIceServer：至少一条含 turns:443 + 凭证
    urls_all = []
    has_cred = False
    for s in servers:
        u = s.urls if isinstance(s.urls, list) else [s.urls]
        urls_all += u
        if getattr(s, "username", None) and getattr(s, "credential", None):
            has_cred = True
    assert any("turns:d.sslip.io:443" in u for u in urls_all)
    assert has_cred
