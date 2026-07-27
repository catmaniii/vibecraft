"""GET /api/server-info 处理函数单测。

测目标：
  - 正确返回 {"name": <value>}
  - name=None 时返回 {"name": null}
  - 绝不泄漏 token / admin_token 等敏感字段
  - make_process_request 正确路由 /api/server-info
"""

from __future__ import annotations

import asyncio
import http
import json

from vibecraft.server.http import _serve_server_info, make_process_request

_run = asyncio.run


# ---------------------------------------------------------------------------
# _serve_server_info 直接测试
# ---------------------------------------------------------------------------


def test_server_info_with_name():
    """/api/server-info 有 name → {"name": "..."}。"""
    response = _serve_server_info("close_test")
    assert response.status_code == http.HTTPStatus.OK.value
    payload = json.loads(response.body)
    assert payload == {"name": "close_test"}


def test_server_info_without_name():
    """/api/server-info name=None → {"name": null}。"""
    response = _serve_server_info(None)
    assert response.status_code == http.HTTPStatus.OK.value
    payload = json.loads(response.body)
    assert payload == {"name": None}


def test_server_info_content_type_is_json():
    """响应头 Content-Type 必须是 application/json。"""
    response = _serve_server_info("test")
    ct = response.headers.get("Content-Type", "")
    assert "application/json" in ct


def test_server_info_cors_header_present():
    """响应头包含 CORS Access-Control-Allow-Origin: *（和其他 API 一致）。"""
    response = _serve_server_info("test")
    acao = response.headers.get("Access-Control-Allow-Origin", "")
    assert acao == "*"


def test_server_info_never_leaks_token():
    """响应 payload 绝不含 token。"""
    response = _serve_server_info("some_name")
    payload = json.loads(response.body)
    assert "token" not in payload


def test_server_info_never_leaks_admin_token():
    """响应 payload 绝不含 admin_token。"""
    response = _serve_server_info("x")
    payload = json.loads(response.body)
    assert "admin_token" not in payload


def test_server_info_payload_has_exactly_one_key():
    """payload 只有 'name' 一个 key（严格不扩展）。"""
    response = _serve_server_info("srv")
    payload = json.loads(response.body)
    assert list(payload.keys()) == ["name"]


# ---------------------------------------------------------------------------
# make_process_request 路由验证
# ---------------------------------------------------------------------------


class _FakeRequest:
    """最小假 Request，够 process_request 内部路由用。"""

    def __init__(self, path: str) -> None:
        self.path = path
        self.headers: dict[str, str] = {}  # no Upgrade header → HTTP路由


class _FakeWs:
    """最小假 ServerConnection，避免 make_process_request 闭包里访问真 ws 属性。"""

    remote_address = ("127.0.0.1", 12345)


def test_make_process_request_routes_server_info():
    """make_process_request 闭包对 /api/server-info 返回正确 JSON。"""
    handler = make_process_request(server_name="routed_server")
    response = _run(handler(_FakeWs(), _FakeRequest("/api/server-info")))
    assert response is not None
    assert response.status_code == http.HTTPStatus.OK.value
    payload = json.loads(response.body)
    assert payload == {"name": "routed_server"}


def test_make_process_request_server_info_default_none():
    """server_name 未传（默认 None）→ /api/server-info 返回 {"name": null}。"""
    handler = make_process_request()
    response = _run(handler(_FakeWs(), _FakeRequest("/api/server-info")))
    assert response is not None
    payload = json.loads(response.body)
    assert payload["name"] is None


def test_make_process_request_server_info_query_string_ignored():
    """路径带 query string 时也能正确路由（?room=xxx 等）。"""
    handler = make_process_request(server_name="qs_test")
    response = _run(handler(_FakeWs(), _FakeRequest("/api/server-info?room=tok")))
    assert response is not None
    payload = json.loads(response.body)
    assert payload == {"name": "qs_test"}
