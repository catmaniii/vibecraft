"""Admin 登录：SCRAM-SHA-256（RFC 5802）→ 短期会话令牌。

口令本身**永不过线**（客户端只发 ClientProof）、server **不存明文**（启动时从口令派生
salt/StoredKey/ServerKey 后丢弃口令）、抗重放（每次握手新 nonce）、双向认证（ServerSignature）。

握手映射到 HTTP GET（项目无 POST 通道）：
- client-first-bare（`n=admin,r=<cnonce>`）→ server-first（`r=<combined>,s=<b64 salt>,i=<iter>`）+ 一个 sid
- client-final-without-proof（`c=biws,r=<combined>`）+ ClientProof → server-final（`v=<b64 ServerSignature>`）+ 会话令牌
传输层：握手消息整体用 **URL-safe base64** 包一层走 query（与 SCRAM 内部的标准 base64 解耦）。
之后 admin 用会话令牌（header `X-Admin-Session`）访问 /api/admin/*。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

_ITERATIONS = 8192  # PBKDF2 迭代次数
_HANDSHAKE_TTL_S = 60.0  # 一次握手 first→final 的超时
_SESSION_TTL_S = 30 * 60.0  # 会话令牌有效期（30 min）


def _ub64e(b: bytes) -> str:
    """URL-safe base64 编码（传输层，无 +/=，适合 query）。"""
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _ub64d(s: str) -> bytes:
    """URL-safe base64 解码（补齐 padding）。"""
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _parse_attr(msg: str, key: str) -> str:
    """从 `a=x,b=y,...` 形式的 SCRAM 消息取某属性值（取第一个匹配）。"""
    for part in msg.split(","):
        if part.startswith(key + "="):
            return part[len(key) + 1 :]
    return ""


class AdminScram:
    """从 admin 口令派生 SCRAM material，处理握手 + 会话令牌。线程模型：单 event-loop 同步调用。"""

    def __init__(self, password: str) -> None:
        self.salt = secrets.token_bytes(16)
        self.iterations = _ITERATIONS
        salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), self.salt, self.iterations)
        client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
        self.stored_key = hashlib.sha256(client_key).digest()  # H(ClientKey)
        self.server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
        # 不保留 password / salted / client_key（GC 即可，不再引用）
        self._pending: dict[
            str, dict
        ] = {}  # sid -> {combined, client_first_bare, server_first, exp}
        self._sessions: dict[str, float] = {}  # session_token -> expiry(monotonic)

    # -- 握手 --------------------------------------------------------------
    def first(self, client_first_bare: str) -> tuple[str, str]:
        """处理 client-first-bare，返回 (sid, server_first)。"""
        self._gc()
        cnonce = _parse_attr(client_first_bare, "r")
        snonce = secrets.token_urlsafe(18)
        combined = cnonce + snonce
        server_first = f"r={combined},s={base64.b64encode(self.salt).decode()},i={self.iterations}"
        sid = secrets.token_urlsafe(12)
        self._pending[sid] = {
            "combined": combined,
            "client_first_bare": client_first_bare,
            "server_first": server_first,
            "exp": time.monotonic() + _HANDSHAKE_TTL_S,
        }
        return sid, server_first

    def final(
        self, sid: str, client_final_no_proof: str, proof_bytes: bytes
    ) -> tuple[str, str] | None:
        """校验 ClientProof，成功返回 (server_final, session_token)，失败返回 None。"""
        self._gc()
        p = self._pending.pop(sid, None)  # 一次性：无论成败该 sid 作废
        if p is None or time.monotonic() > p["exp"]:
            return None
        # nonce 必须与 server-first 一致（抗重放/抗篡改）
        if _parse_attr(client_final_no_proof, "r") != p["combined"]:
            return None
        auth_message = f"{p['client_first_bare']},{p['server_first']},{client_final_no_proof}"
        client_signature = hmac.new(
            self.stored_key, auth_message.encode("utf-8"), hashlib.sha256
        ).digest()
        if len(proof_bytes) != len(client_signature):
            return None
        # ClientKey = ClientProof XOR ClientSignature；验 H(ClientKey) == StoredKey
        client_key = bytes(a ^ b for a, b in zip(proof_bytes, client_signature, strict=False))
        if not hmac.compare_digest(hashlib.sha256(client_key).digest(), self.stored_key):
            return None
        server_signature = hmac.new(
            self.server_key, auth_message.encode("utf-8"), hashlib.sha256
        ).digest()
        token = secrets.token_urlsafe(24)
        self._sessions[token] = time.monotonic() + _SESSION_TTL_S
        return f"v={base64.b64encode(server_signature).decode()}", token

    # -- 会话 --------------------------------------------------------------
    def check_session(self, token: str) -> bool:
        """校验会话令牌有效且未过期（常数时间比对每个候选，防时序枚举）。"""
        if not token:
            return False
        now = time.monotonic()
        for t, exp in list(self._sessions.items()):
            if now > exp:
                self._sessions.pop(t, None)
                continue
            if hmac.compare_digest(t, token):
                return True
        return False

    def _gc(self) -> None:
        now = time.monotonic()
        for sid, p in list(self._pending.items()):
            if now > p["exp"]:
                self._pending.pop(sid, None)
        for t, exp in list(self._sessions.items()):
            if now > exp:
                self._sessions.pop(t, None)
