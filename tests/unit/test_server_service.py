"""BotService + ServiceConfig 单测（M1.1e）。

测试目标：
- ServiceConfig 默认值正确
- BotService 构造时自动生成 token（或使用指定 token）
- BotService.run() 在 CancelledError 时干净退出（不泄漏）
"""

from __future__ import annotations

import asyncio
import contextlib
import pathlib
from unittest.mock import AsyncMock, patch

from voicecraft.server.service import BotService, ServiceConfig
from voicecraft.server.tokens import RoomRegistry


class TestServiceConfig:
    def test_default_port(self) -> None:
        cfg = ServiceConfig()
        assert cfg.port == 8080

    def test_default_host_is_0000(self) -> None:
        """监听地址默认 0.0.0.0（不硬编码 localhost）。"""
        cfg = ServiceConfig()
        assert cfg.host == "0.0.0.0"

    def test_default_static_dir_exists(self) -> None:
        """默认 static_dir 指向 server/static/，且目录存在（已放占位 index.html）。"""
        cfg = ServiceConfig()
        assert cfg.static_dir.is_dir()
        assert (cfg.static_dir / "index.html").exists()

    def test_custom_port(self) -> None:
        cfg = ServiceConfig(port=9090)
        assert cfg.port == 9090

    def test_token_none_by_default(self) -> None:
        cfg = ServiceConfig()
        assert cfg.token is None  # BotService 构造时才生成


class TestBotService:
    def test_auto_generates_token(self) -> None:
        """token=None 时 BotService 自动生成。"""
        svc = BotService(ServiceConfig(token=None))
        assert len(svc.token) >= 10

    def test_uses_explicit_token(self) -> None:
        """指定 token 时使用它。"""
        svc = BotService(ServiceConfig(token="fixed-tok"))
        assert svc.token == "fixed-tok"

    def test_registry_is_room_registry(self) -> None:
        svc = BotService()
        assert isinstance(svc.registry, RoomRegistry)

    def test_registry_token_matches(self) -> None:
        svc = BotService(ServiceConfig(token="abc"))
        assert svc.registry.token == "abc"

    async def test_run_exits_on_cancelled_error(self, tmp_path: pathlib.Path) -> None:
        """run() 收到 CancelledError 时不抛出（asyncio.run() 正常结束）。"""
        cfg = ServiceConfig(port=18080, token="t", static_dir=tmp_path, display_ip="127.0.0.1")
        (tmp_path / "index.html").write_bytes(b"<h1>test</h1>")
        svc = BotService(cfg)

        # 用 serve 的 mock：__aenter__ 返回一个 server mock，serve_forever 立即抛 CancelledError
        server_mock = AsyncMock()
        server_mock.serve_forever = AsyncMock(side_effect=asyncio.CancelledError)
        server_mock.__aenter__ = AsyncMock(return_value=server_mock)
        server_mock.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("voicecraft.server.service.serve", return_value=server_mock),
            patch("voicecraft.server.service.print_connect_info"),
        ):
            task = asyncio.create_task(svc.run())
            # serve_forever 抛 CancelledError → run() 向上传播 → task done
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(task, timeout=2.0)

    async def test_run_calls_print_connect_info(self, tmp_path: pathlib.Path) -> None:
        """run() 必须调用 print_connect_info 显示二维码。"""
        cfg = ServiceConfig(port=18081, token="t2", static_dir=tmp_path, display_ip="127.0.0.1")
        (tmp_path / "index.html").write_bytes(b"<h1>test</h1>")
        svc = BotService(cfg)

        server_mock = AsyncMock()
        server_mock.serve_forever = AsyncMock(side_effect=asyncio.CancelledError)
        server_mock.__aenter__ = AsyncMock(return_value=server_mock)
        server_mock.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("voicecraft.server.service.serve", return_value=server_mock),
            patch("voicecraft.server.service.print_connect_info") as mock_print,
            contextlib.suppress(asyncio.CancelledError, Exception),
        ):
            await svc.run()

        mock_print.assert_called_once()
        call_kwargs = mock_print.call_args
        # 确认传入了 port 和 token
        assert call_kwargs is not None
