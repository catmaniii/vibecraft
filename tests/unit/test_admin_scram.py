"""Admin SCRAM-SHA-256（RFC 5802）核心单测：握手 round-trip + 拒绝路径。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from vibecraft.server.admin_scram import AdminScram, _parse_attr

_PW = "super-secret-admin-pw-123456"


def _client_login(scram: AdminScram, password: str, tamper: bool = False):
    """模拟 SCRAM 客户端走完整握手，返回 scram.final 的结果。"""
    cnonce = secrets.token_urlsafe(16)
    cfb = f"n=admin,r={cnonce}"
    sid, sf = scram.first(cfb)
    combined = _parse_attr(sf, "r")
    salt = base64.b64decode(_parse_attr(sf, "s"))
    iters = int(_parse_attr(sf, "i"))
    assert combined.startswith(cnonce)  # 组合 nonce 含 client nonce（抗篡改）
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iters)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    cfnp = f"c=biws,r={combined}"
    auth_msg = f"{cfb},{sf},{cfnp}"
    client_sig = hmac.new(stored_key, auth_msg.encode(), hashlib.sha256).digest()
    proof = bytes(a ^ b for a, b in zip(client_key, client_sig, strict=False))
    if tamper:
        proof = bytes(p ^ 1 for p in proof)
    return scram.final(sid, cfnp, proof), sid, cfnp, proof


def test_correct_password_login_and_session() -> None:
    s = AdminScram(_PW)
    res, _sid, _cfnp, _proof = _client_login(s, _PW)
    assert res is not None
    server_final, token = res
    assert server_final.startswith("v=")  # ServerSignature（双向认证）
    assert s.check_session(token) is True


def test_wrong_password_rejected() -> None:
    s = AdminScram(_PW)
    res, _, _, _ = _client_login(s, "totally-wrong-password-xx")
    assert res is None


def test_tampered_proof_rejected() -> None:
    s = AdminScram(_PW)
    res, _, _, _ = _client_login(s, _PW, tamper=True)
    assert res is None


def test_replay_same_sid_rejected() -> None:
    s = AdminScram(_PW)
    res, sid, cfnp, proof = _client_login(s, _PW)
    assert res is not None
    # 同 sid 再 final → None（sid 一次性作废，抗重放）
    assert s.final(sid, cfnp, proof) is None


def test_invalid_session_rejected() -> None:
    s = AdminScram(_PW)
    assert s.check_session("bogus-token") is False
    assert s.check_session("") is False


def test_server_does_not_store_plaintext() -> None:
    s = AdminScram(_PW)
    # 不持有明文口令 / 不在派生材料里残留
    assert not hasattr(s, "password")
    for attr in ("salt", "stored_key", "server_key"):
        assert _PW.encode() not in getattr(s, attr)
