"""turn_config 单测：配置加载（env/文件/缺失）+ 凭证现签 + iceServers 组装。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

from vibecraft.server.turn_config import (
    TurnConfig,
    build_ice_servers,
    load_turn_config,
    mint_credential,
)


def test_mint_credential_format_matches_coturn_rest():
    """username=expiry:name；password=base64(HMAC-SHA1(secret,username))，与 coturn 一致。"""
    secret = "s3cr3t"
    user, pw = mint_credential(secret, ttl_s=3600, name="vibecraft")
    assert user.endswith(":vibecraft")
    expiry = int(user.split(":")[0])
    assert expiry > time.time()  # 未来时间戳
    # 独立复算 HMAC 验证
    want = base64.b64encode(
        hmac.new(secret.encode(), user.encode(), hashlib.sha1).digest()
    ).decode()
    assert pw == want


def test_mint_credential_default_ttl_24h():
    """默认 TTL 24h（评审采纳：长局不中途断）。"""
    user, _ = mint_credential("x")
    expiry = int(user.split(":")[0])
    # ~24h 后（留宽容窗口）
    assert 86000 < expiry - int(time.time()) <= 86400


def test_build_ice_servers_structure():
    cfg = TurnConfig(domain="d.sslip.io", secret="x", port=3478, tls_port=443)
    servers = build_ice_servers(cfg)
    # 第一条 = coturn STUN（无凭证）
    assert servers[0] == {"urls": ["stun:d.sslip.io:3478"]}
    # 第二条 = turn/turns，含凭证 + turns:443
    turn = servers[1]
    assert "username" in turn and "credential" in turn
    assert "turns:d.sslip.io:443?transport=tcp" in turn["urls"]
    assert "turn:d.sslip.io:3478?transport=udp" in turn["urls"]
    # 不含 google STUN（评审采纳：有 TURN 就用 coturn STUN）
    flat = " ".join(u for s in servers for u in s["urls"])
    assert "google" not in flat


def test_build_ice_servers_fresh_cred_each_call():
    """每次调用现签新凭证（评审纪律：不缓存）。"""
    cfg = TurnConfig(domain="d", secret="x")
    a = build_ice_servers(cfg, ttl_s=3600)
    time.sleep(1.05)
    b = build_ice_servers(cfg, ttl_s=3600)
    # expiry 随时间推进 → username 不同
    assert a[1]["username"] != b[1]["username"]


def test_load_turn_config_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBECRAFT_TURN_DOMAIN", "envdom")
    monkeypatch.setenv("VIBECRAFT_TURN_SECRET", "envsecret")
    monkeypatch.setenv("VIBECRAFT_TURN_PORT", "3478")
    monkeypatch.setenv("VIBECRAFT_TURN_TLS_PORT", "443")
    cfg = load_turn_config(secrets_env=tmp_path / "nope.env")
    assert cfg is not None
    assert cfg.domain == "envdom" and cfg.secret == "envsecret"
    assert cfg.port == 3478 and cfg.tls_port == 443


def test_load_turn_config_from_secrets_file(monkeypatch, tmp_path):
    for k in (
        "VIBECRAFT_TURN_DOMAIN",
        "VIBECRAFT_TURN_SECRET",
        "VIBECRAFT_TURN_PORT",
        "VIBECRAFT_TURN_TLS_PORT",
    ):
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / "vibecraft-turn.env"
    f.write_text(
        'TURN_DOMAIN="filedom"\nTURN_STATIC_SECRET="filesecret"\nTURN_TLS_PORT="443"\n# 注释\n',
        encoding="utf-8",
    )
    cfg = load_turn_config(secrets_env=f)
    assert cfg is not None
    assert cfg.domain == "filedom" and cfg.secret == "filesecret"
    assert cfg.port == 3478  # 缺省


def test_load_turn_config_missing_returns_none(monkeypatch, tmp_path):
    """env 和文件都没有 → None（graceful，无 TURN）。"""
    for k in ("VIBECRAFT_TURN_DOMAIN", "VIBECRAFT_TURN_SECRET"):
        monkeypatch.delenv(k, raising=False)
    cfg = load_turn_config(secrets_env=tmp_path / "absent.env")
    assert cfg is None
