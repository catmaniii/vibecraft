"""guide-chat API 单测（/api/guide-chat?q=...&lang=...）。

覆盖：
1. 空问题 → 400
2. 限流：超过 _GUIDE_CHAT_RATE_LIMIT 次 → 429
3. mock LLM 正常返回 → 200 + {"answer": ...}
4. 限流错误消息按 lang 返回（zh=中文 / en=英文）
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from vibecraft.server.http import (
    _GUIDE_CHAT_RATE_LIMIT,
    _guide_chat_buckets,
    make_process_request,
)

_run = asyncio.run


# ---------------------------------------------------------------------------
# 测试桩
# ---------------------------------------------------------------------------


def _make_ws(ip: str = "127.0.0.1") -> Any:
    ws = MagicMock()
    ws.remote_address = (ip, 12345)
    return ws


def _make_request(path: str) -> Any:
    req = MagicMock()
    req.path = path
    req.headers = {}  # 无 Upgrade 头 → 走 HTTP 路由
    return req


def _clear_bucket(ip: str = "127.0.0.1") -> None:
    _guide_chat_buckets.pop(ip, None)


# ---------------------------------------------------------------------------
# 空问题
# ---------------------------------------------------------------------------


class TestGuideChatEmptyQuestion:
    def setup_method(self) -> None:
        _clear_bucket()

    def test_no_q_param_returns_400(self, tmp_path: pathlib.Path) -> None:
        """q 参数缺失 → 400。"""
        hook = make_process_request(static_dir=tmp_path)
        resp = _run(hook(_make_ws(), _make_request("/api/guide-chat?lang=zh")))
        assert resp is not None
        assert resp.status_code == 400
        payload = json.loads(resp.body)
        assert "error" in payload

    def test_empty_q_param_returns_400(self, tmp_path: pathlib.Path) -> None:
        """q= 空字符串 → 400。"""
        hook = make_process_request(static_dir=tmp_path)
        resp = _run(hook(_make_ws(), _make_request("/api/guide-chat?q=&lang=zh")))
        assert resp is not None
        assert resp.status_code == 400

    def test_empty_q_whitespace_returns_400(self, tmp_path: pathlib.Path) -> None:
        """q=+++ (只有空白) → 400（strip 后为空）。"""
        hook = make_process_request(static_dir=tmp_path)
        resp = _run(hook(_make_ws(), _make_request("/api/guide-chat?q=+++&lang=en")))
        assert resp is not None
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# mock LLM 正常返回
# ---------------------------------------------------------------------------


class TestGuideChatMockLLM:
    def setup_method(self) -> None:
        _clear_bucket()

    def test_zh_question_returns_200_with_answer(self, tmp_path: pathlib.Path) -> None:
        """中文问题 → 200 + {"answer": ...}。"""
        hook = make_process_request(static_dir=tmp_path)
        mock_answer = "神族最基础的 build 是 4BG，快速出四个 BG 出兵。"
        with patch(
            "vibecraft.server.http._call_guide_chat_llm",
            new=AsyncMock(return_value=mock_answer),
        ):
            resp = _run(hook(_make_ws(), _make_request("/api/guide-chat?q=4BG怎么玩&lang=zh")))
        assert resp is not None
        assert resp.status_code == 200
        payload = json.loads(resp.body)
        assert "answer" in payload
        assert payload["answer"] == mock_answer

    def test_en_question_returns_200_with_answer(self, tmp_path: pathlib.Path) -> None:
        """英文问题 → 200 + {"answer": ...}。"""
        hook = make_process_request(static_dir=tmp_path)
        mock_answer = "To connect: scan the QR code, fill in your username, tap Connect."
        with patch(
            "vibecraft.server.http._call_guide_chat_llm",
            new=AsyncMock(return_value=mock_answer),
        ):
            resp = _run(hook(_make_ws(), _make_request("/api/guide-chat?q=how+to+connect&lang=en")))
        assert resp is not None
        assert resp.status_code == 200
        payload = json.loads(resp.body)
        assert payload["answer"] == mock_answer

    def test_content_type_is_json(self, tmp_path: pathlib.Path) -> None:
        """guide-chat 响应 Content-Type = application/json。"""
        hook = make_process_request(static_dir=tmp_path)
        with patch(
            "vibecraft.server.http._call_guide_chat_llm",
            new=AsyncMock(return_value="ok"),
        ):
            resp = _run(hook(_make_ws(), _make_request("/api/guide-chat?q=test&lang=zh")))
        assert resp is not None
        ct = resp.headers.get("Content-Type", "")
        assert "application/json" in ct


# ---------------------------------------------------------------------------
# 限流（率先打满桶，都在同一个 asyncio.run() 内，避免 Windows 频繁创建/销毁事件循环）
# ---------------------------------------------------------------------------


class TestGuideChatRateLimit:
    def setup_method(self) -> None:
        _clear_bucket()
        _clear_bucket("10.0.0.1")
        _clear_bucket("10.0.0.2")

    def test_exceeding_limit_returns_429(self, tmp_path: pathlib.Path) -> None:
        """连续 N+1 次请求 → 第 N+1 次 429（N = _GUIDE_CHAT_RATE_LIMIT）。"""
        hook = make_process_request(static_dir=tmp_path)

        async def _run_test() -> tuple[list[int], int]:
            codes: list[int] = []
            with patch(
                "vibecraft.server.http._call_guide_chat_llm",
                new=AsyncMock(return_value="ok"),
            ):
                for i in range(_GUIDE_CHAT_RATE_LIMIT):
                    r = await hook(_make_ws(), _make_request(f"/api/guide-chat?q=fill+{i}"))
                    codes.append(r.status_code)
                overflow = await hook(_make_ws(), _make_request("/api/guide-chat?q=overflow"))
                return codes, overflow.status_code

        codes, overflow_code = _run(_run_test())
        assert all(c == 200 for c in codes), f"限额内应全部 200，实际：{codes}"
        assert overflow_code == 429

    def test_rate_limit_error_has_chinese_message_for_zh(self, tmp_path: pathlib.Path) -> None:
        """lang=zh 时 429 错误消息含中文。"""
        hook = make_process_request(static_dir=tmp_path)

        async def _run_test() -> bytes:
            with patch(
                "vibecraft.server.http._call_guide_chat_llm",
                new=AsyncMock(return_value="ok"),
            ):
                for i in range(_GUIDE_CHAT_RATE_LIMIT):
                    await hook(_make_ws(), _make_request(f"/api/guide-chat?q=fill+{i}&lang=zh"))
                resp = await hook(_make_ws(), _make_request("/api/guide-chat?q=overflow&lang=zh"))
                return resp.body

        body = _run(_run_test())
        payload = json.loads(body)
        assert "error" in payload
        assert any(ord(c) > 127 for c in payload["error"]), "zh 限流消息应含中文"

    def test_rate_limit_error_has_english_message_for_en(self, tmp_path: pathlib.Path) -> None:
        """lang=en 时 429 错误消息为英文（纯 ASCII）。"""
        hook = make_process_request(static_dir=tmp_path)

        async def _run_test() -> bytes:
            with patch(
                "vibecraft.server.http._call_guide_chat_llm",
                new=AsyncMock(return_value="ok"),
            ):
                for i in range(_GUIDE_CHAT_RATE_LIMIT):
                    await hook(_make_ws(), _make_request(f"/api/guide-chat?q=fill+{i}&lang=en"))
                resp = await hook(_make_ws(), _make_request("/api/guide-chat?q=overflow&lang=en"))
                return resp.body

        body = _run(_run_test())
        payload = json.loads(body)
        assert "error" in payload
        assert all(ord(c) < 128 for c in payload["error"]), "en 限流消息应为纯英文"

    def test_different_ips_have_independent_buckets(self, tmp_path: pathlib.Path) -> None:
        """不同 IP 的限流桶独立，IP-A 打满不影响 IP-B。"""
        hook = make_process_request(static_dir=tmp_path)

        async def _run_test() -> tuple[int, int]:
            with patch(
                "vibecraft.server.http._call_guide_chat_llm",
                new=AsyncMock(return_value="ok"),
            ):
                for i in range(_GUIDE_CHAT_RATE_LIMIT):
                    await hook(_make_ws("10.0.0.1"), _make_request(f"/api/guide-chat?q=a+{i}"))
                resp_a = await hook(
                    _make_ws("10.0.0.1"), _make_request("/api/guide-chat?q=overflow")
                )
                resp_b = await hook(_make_ws("10.0.0.2"), _make_request("/api/guide-chat?q=hello"))
                return resp_a.status_code, resp_b.status_code

        code_a, code_b = _run(_run_test())
        assert code_a == 429
        assert code_b == 200
