"""HTTP static server 单测（M1.1c）。

测试 process_request 钩子：
- WS 请求（含 Upgrade: websocket）→ 返回 None，交由 websockets 握手
- 普通 GET → serve 静态文件 / 404 fallback / 路径遍历防护
"""

from __future__ import annotations

import pathlib
from typing import Any
from unittest.mock import MagicMock

from voicecraft.server.http import make_process_request

# ---------------------------------------------------------------------------
# 测试桩
# ---------------------------------------------------------------------------


def _make_request(path: str, upgrade: str = "") -> Any:
    """构造 fake websockets Request。"""
    req = MagicMock()
    req.path = path
    # 用 MagicMock 模拟 headers，使 .get() 可被覆写
    headers_mock = MagicMock()
    headers_mock.get = lambda k, d="": upgrade if k == "Upgrade" else d
    req.headers = headers_mock
    return req


def _make_ws(remote: tuple[str, int] = ("127.0.0.1", 9999)) -> Any:
    ws = MagicMock()
    ws.remote_address = remote
    return ws


# ---------------------------------------------------------------------------
# WS 请求放行
# ---------------------------------------------------------------------------


class TestWsPassthrough:
    def test_ws_upgrade_returns_none(self, tmp_path: pathlib.Path) -> None:
        """Upgrade: websocket 请求必须返回 None（放行给握手流程）。"""
        hook = make_process_request(static_dir=tmp_path)
        req = _make_request("/ws?room=abc", upgrade="websocket")
        result = hook(_make_ws(), req)
        assert result is None

    def test_ws_upgrade_case_insensitive(self, tmp_path: pathlib.Path) -> None:
        """Upgrade 头大小写不敏感：WebSocket / WEBSOCKET 都放行。"""
        hook = make_process_request(static_dir=tmp_path)
        for val in ("WebSocket", "WEBSOCKET", "websocket"):
            req = _make_request("/ws", upgrade=val)
            assert hook(_make_ws(), req) is None


# ---------------------------------------------------------------------------
# 静态文件 serve
# ---------------------------------------------------------------------------


class TestStaticServe:
    def test_serve_index_html(self, tmp_path: pathlib.Path) -> None:
        """GET / → 返回 index.html 内容，状态码 200。"""
        (tmp_path / "index.html").write_bytes(b"<h1>hello</h1>")
        hook = make_process_request(static_dir=tmp_path)
        resp = hook(_make_ws(), _make_request("/"))
        assert resp is not None
        assert resp.status_code == 200
        assert b"hello" in resp.body

    def test_serve_explicit_file(self, tmp_path: pathlib.Path) -> None:
        """GET /app.js → 返回对应文件。"""
        (tmp_path / "app.js").write_bytes(b"console.log('hi')")
        hook = make_process_request(static_dir=tmp_path)
        resp = hook(_make_ws(), _make_request("/app.js"))
        assert resp is not None
        assert resp.status_code == 200
        assert b"console" in resp.body

    def test_content_type_html(self, tmp_path: pathlib.Path) -> None:
        """HTML 文件 Content-Type 包含 text/html。"""
        (tmp_path / "index.html").write_bytes(b"<h1>x</h1>")
        hook = make_process_request(static_dir=tmp_path)
        resp = hook(_make_ws(), _make_request("/"))
        assert resp is not None
        ct = resp.headers.get("Content-Type", "")
        assert "text/html" in ct

    def test_content_type_js(self, tmp_path: pathlib.Path) -> None:
        """JS 文件 Content-Type 包含 javascript。"""
        (tmp_path / "main.js").write_bytes(b"x=1")
        hook = make_process_request(static_dir=tmp_path)
        resp = hook(_make_ws(), _make_request("/main.js"))
        assert resp is not None
        ct = resp.headers.get("Content-Type", "")
        assert "javascript" in ct

    def test_missing_file_falls_back_to_index(self, tmp_path: pathlib.Path) -> None:
        """SPA 路由：/unknown/path → fallback 到 index.html。"""
        (tmp_path / "index.html").write_bytes(b"<h1>spa</h1>")
        hook = make_process_request(static_dir=tmp_path)
        resp = hook(_make_ws(), _make_request("/some/spa/route"))
        assert resp is not None
        assert resp.status_code == 200
        assert b"spa" in resp.body

    def test_no_index_html_returns_404(self, tmp_path: pathlib.Path) -> None:
        """static 目录里没有 index.html → 404。"""
        hook = make_process_request(static_dir=tmp_path)
        resp = hook(_make_ws(), _make_request("/"))
        assert resp is not None
        assert resp.status_code == 404

    def test_query_string_ignored(self, tmp_path: pathlib.Path) -> None:
        """URL 中的 query string 不影响文件路由。"""
        (tmp_path / "index.html").write_bytes(b"<h1>q</h1>")
        hook = make_process_request(static_dir=tmp_path)
        resp = hook(_make_ws(), _make_request("/?room=abc"))
        assert resp is not None
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 路径遍历防护
# ---------------------------------------------------------------------------


class TestPathTraversal:
    def test_traversal_blocked(self, tmp_path: pathlib.Path) -> None:
        """../../../etc/passwd 路径逃出 static_dir → 403。"""
        # 在 tmp_path 的父目录放一个文件
        parent = tmp_path.parent
        secret = parent / "secret.txt"
        secret.write_text("secret", encoding="utf-8")

        hook = make_process_request(static_dir=tmp_path)
        resp = hook(_make_ws(), _make_request("/../secret.txt"))
        assert resp is not None
        assert resp.status_code == 403

        secret.unlink()

    def test_normal_subdir_allowed(self, tmp_path: pathlib.Path) -> None:
        """正常子目录路径不被误拦截。"""
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "page.html").write_bytes(b"<p>sub</p>")
        hook = make_process_request(static_dir=tmp_path)
        resp = hook(_make_ws(), _make_request("/sub/page.html"))
        assert resp is not None
        assert resp.status_code == 200
