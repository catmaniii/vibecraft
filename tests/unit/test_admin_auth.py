"""Admin SCRAM 登录 + 会话鉴权 + 限速锁定 + API 端点单测。

覆盖：
1. admin_scram 未配 → /admin 与 /api/admin/* 全 404（secure by default）。
2. /admin 页面直接 serve（登录 UI，无机密）。
3. SCRAM 登录成功 → 会话令牌 → /api/admin/* 可访问；错口令 → 404 + 锁定；无效会话 → 404。
4. 端点内容：games 负样本（game_* 排除，锁 M1）、feedback 解析 + IP 脱敏、chat-send 复用同一
   ChatHub + 广播、status 白名单字段。
"""

from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import hmac
import json
import pathlib
import secrets
from typing import Any
from unittest.mock import MagicMock, patch

_run = asyncio.run

from vibecraft.server import RoomRegistry
from vibecraft.server.admin_scram import AdminScram, _parse_attr, _ub64e
from vibecraft.server.chat import ChatHub
from vibecraft.server.http import (
    _ADMIN_MAX_FAILS,
    _admin_fail_count,
    _admin_lockout_until,
    _mask_ip,
    make_process_request,
)

_PW = "super-secret-admin-pw-2026"  # >=16


def _make_ws(ip: str = "127.0.0.1") -> Any:
    ws = MagicMock()
    ws.remote_address = (ip, 9999)
    return ws


def _make_request(path: str, headers: dict[str, str] | None = None) -> Any:
    req = MagicMock()
    req.path = path
    h = headers or {}
    req.headers = MagicMock()
    req.headers.get = lambda k, d="": h.get(k, d)
    return req


def _make_room_service(chat: ChatHub | None = None, registry: RoomRegistry | None = None) -> Any:
    rs = MagicMock()
    rs.room.state = "lobby"
    rs.room.match_id = ""
    rs.room.realtime = True
    rs.room.slots = []
    rs.chat = chat or ChatHub()
    rs._registry = registry or RoomRegistry(token="test-room-token")
    return rs


def _clear_rate_limiter(ip: str = "127.0.0.1") -> None:
    _admin_fail_count.pop(ip, None)
    _admin_lockout_until.pop(ip, None)


def _scram_login(hook: Any, password: str, ip: str = "127.0.0.1") -> str | None:
    """走完整 SCRAM 握手（经 process_request hook），成功返回 session token，失败返回 None。"""
    ws = _make_ws(ip)
    cnonce = secrets.token_urlsafe(16)
    cfb = f"n=admin,r={cnonce}"
    r1 = _run(hook(ws, _make_request(f"/api/admin/scram-first?msg={_ub64e(cfb.encode())}")))
    if r1.status_code != 200:
        return None
    d1 = json.loads(r1.body)
    sid, sf = d1["sid"], d1["server_first"]
    combined = _parse_attr(sf, "r")
    salt = base64.b64decode(_parse_attr(sf, "s"))
    iters = int(_parse_attr(sf, "i"))
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iters)
    ck = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    sk = hashlib.sha256(ck).digest()
    cfnp = f"c=biws,r={combined}"
    cs = hmac.new(sk, f"{cfb},{sf},{cfnp}".encode(), hashlib.sha256).digest()
    proof = bytes(a ^ b for a, b in zip(ck, cs, strict=False))
    url = f"/api/admin/scram-final?sid={sid}&msg={_ub64e(cfnp.encode())}&proof={_ub64e(proof)}"
    r2 = _run(hook(ws, _make_request(url)))
    if r2.status_code != 200:
        return None
    return str(json.loads(r2.body)["session"])


class TestAdminNotConfigured:
    def test_admin_page_404(self, tmp_path: pathlib.Path) -> None:
        hook = make_process_request(static_dir=tmp_path, admin_scram=None)
        assert _run(hook(_make_ws(), _make_request("/admin"))).status_code == 404

    def test_api_404(self, tmp_path: pathlib.Path) -> None:
        hook = make_process_request(static_dir=tmp_path, admin_scram=None)
        assert _run(hook(_make_ws(), _make_request("/api/admin/status"))).status_code == 404


class TestAdminPage:
    def test_admin_page_served(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "admin.html").write_bytes(b"<html>login</html>")
        hook = make_process_request(static_dir=tmp_path, admin_scram=AdminScram(_PW))
        resp = _run(hook(_make_ws(), _make_request("/admin")))
        assert resp.status_code == 200
        assert b"login" in resp.body


class TestScramLoginFlow:
    def setup_method(self) -> None:
        _clear_rate_limiter()

    def test_correct_password_grants_session(self, tmp_path: pathlib.Path) -> None:
        rs = _make_room_service()
        hook = make_process_request(
            static_dir=tmp_path, admin_scram=AdminScram(_PW), room_service=rs
        )
        session = _scram_login(hook, _PW)
        assert session
        resp = _run(
            hook(_make_ws(), _make_request("/api/admin/status", {"X-Admin-Session": session}))
        )
        assert resp.status_code == 200

    def test_invalid_session_404(self, tmp_path: pathlib.Path) -> None:
        hook = make_process_request(static_dir=tmp_path, admin_scram=AdminScram(_PW))
        assert (
            _run(
                hook(_make_ws(), _make_request("/api/admin/status", {"X-Admin-Session": "bogus"}))
            ).status_code
            == 404
        )
        assert _run(hook(_make_ws(), _make_request("/api/admin/status"))).status_code == 404

    def test_wrong_password_404(self, tmp_path: pathlib.Path) -> None:
        hook = make_process_request(static_dir=tmp_path, admin_scram=AdminScram(_PW))
        assert _scram_login(hook, "definitely-wrong-pw-xx") is None

    def test_wrong_password_triggers_lockout(self, tmp_path: pathlib.Path) -> None:
        hook = make_process_request(static_dir=tmp_path, admin_scram=AdminScram(_PW))
        for _ in range(_ADMIN_MAX_FAILS):
            _scram_login(hook, "wrong-pw-aaaaaaaa")
        assert _admin_lockout_until.get("127.0.0.1", 0) > 0  # 已锁定
        # 锁定期内即便正确口令也被拒
        assert _scram_login(hook, _PW) is None


class TestAdminEndpoints:
    """端点内容（经有效会话）。"""

    def setup_method(self) -> None:
        _clear_rate_limiter()

    def _logged_in(self, tmp_path: pathlib.Path, rs: Any) -> tuple[Any, str]:
        hook = make_process_request(
            static_dir=tmp_path, admin_scram=AdminScram(_PW), room_service=rs
        )
        session = _scram_login(hook, _PW)
        assert session
        return hook, session

    def test_status_whitelist_fields(self, tmp_path: pathlib.Path) -> None:
        rs = _make_room_service()
        hook, session = self._logged_in(tmp_path, rs)
        resp = _run(
            hook(_make_ws(), _make_request("/api/admin/status", {"X-Admin-Session": session}))
        )
        data = json.loads(resp.body)
        # 不应泄露 token / 内部对象
        flat = json.dumps(data)
        assert "test-room-token" not in flat

    def test_games_excludes_sandbox_dirs(self, tmp_path: pathlib.Path) -> None:
        # M1 回归：game_*（build_acceptance 沙盒形态）必须被排除，只留 match_*（真人局）。
        logs = tmp_path / "logs"
        for name in ["match_20260616_120000_p0", "game_20260616_120000_abc123", "eff_4bg_s1_x"]:
            d = logs / name
            d.mkdir(parents=True)
            (d / "telemetry.jsonl").write_text(
                json.dumps({"kind": "game_start", "my_race": "Protoss", "active_recipe": "4bg"})
                + "\n",
                encoding="utf-8",
            )
        rs = _make_room_service()
        hook, session = self._logged_in(tmp_path, rs)
        with patch("vibecraft.server.admin_games._DEFAULT_LOGS_DIR", logs):
            resp = _run(
                hook(_make_ws(), _make_request("/api/admin/games", {"X-Admin-Session": session}))
            )
        ids = [g.get("match_id", "") for g in json.loads(resp.body).get("games", [])]
        assert any("match_" in i for i in ids)
        assert not any(i.startswith("game_") or i.startswith("eff_") for i in ids)

    def test_feedback_parsed_and_ip_masked(self, tmp_path: pathlib.Path, monkeypatch: Any) -> None:
        # feedback 读硬编码相对 logs/feedback.csv → chdir 到 tmp_path
        monkeypatch.chdir(tmp_path)
        logs = tmp_path / "logs"
        logs.mkdir(parents=True)
        with (logs / "feedback.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["提交时间", "昵称", "分类", "反馈内容", "IP", "UA"])
            w.writerow(["2026-06-16 12:00", "玩家A", "bug", "卡顿", "1.2.3.4", "UA"])
        rs = _make_room_service()
        hook, session = self._logged_in(tmp_path, rs)
        resp = _run(
            hook(_make_ws(), _make_request("/api/admin/feedback", {"X-Admin-Session": session}))
        )
        rows = json.loads(resp.body).get("rows", [])
        assert rows and "1.2.3.4" not in json.dumps(rows)  # IP 已脱敏

    def test_chat_send_reuses_hub(self, tmp_path: pathlib.Path) -> None:
        chat = ChatHub()
        rs = _make_room_service(chat=chat)
        hook, session = self._logged_in(tmp_path, rs)
        _run(
            hook(
                _make_ws(),
                _make_request("/api/admin/chat-send?text=hi", {"X-Admin-Session": session}),
            )
        )
        msgs = chat.history()
        assert any(m["pid"] == "__admin__" and m["text"] == "hi" for m in msgs)


def test_mask_ip() -> None:
    assert _mask_ip("1.2.3.4") == "1.2.*.*"
    assert _mask_ip("") == ""
