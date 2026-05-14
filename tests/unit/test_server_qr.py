"""二维码 + IP 检测单测（M1.1d）。

覆盖：
- build_connect_url：URL 格式正确
- render_qr_ascii：用实心块 ██ 渲染、行宽对齐、包含多行
- get_lan_ip：正常路径 + OSError fallback
- print_connect_info：返回正确 URL、包含 port/token
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from voicecraft.server.qr import build_connect_url, get_lan_ip, print_connect_info, render_qr_ascii


class TestBuildConnectUrl:
    def test_format(self) -> None:
        url = build_connect_url("192.168.1.100", 8080, "abc123")
        assert url == "http://192.168.1.100:8080/?room=abc123"

    def test_different_port(self) -> None:
        url = build_connect_url("10.0.0.1", 9090, "tok")
        assert url.startswith("http://10.0.0.1:9090/")
        assert "room=tok" in url


class TestRenderQrAscii:
    def test_returns_string(self) -> None:
        result = render_qr_ascii("http://example.com")
        assert isinstance(result, str)

    def test_multiline(self) -> None:
        result = render_qr_ascii("http://example.com")
        lines = result.splitlines()
        assert len(lines) > 5

    def test_uses_solid_block(self) -> None:
        """黑模块用实心块 ██（比 ## 无缝隙、扫码识别率高）。"""
        result = render_qr_ascii("http://example.com")
        assert "██" in result
        assert "#" not in result

    def test_lines_equal_width(self) -> None:
        """每行宽度相同（矩阵对齐）。"""
        result = render_qr_ascii("http://example.com")
        lines = result.splitlines()
        widths = {len(line) for line in lines if line}
        assert len(widths) == 1, f"行宽不一致：{widths}"


class TestGetLanIp:
    def test_returns_string(self) -> None:
        ip = get_lan_ip()
        assert isinstance(ip, str)
        # 基本 IP 格式：四段数字
        parts = ip.split(".")
        assert len(parts) == 4
        assert all(p.isdigit() for p in parts)

    def test_fallback_on_error(self) -> None:
        """socket.connect 抛 OSError 时 fallback 127.0.0.1。"""
        with patch("voicecraft.server.qr.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.connect.side_effect = OSError("no network")
            mock_socket_cls.return_value = mock_sock

            ip = get_lan_ip()
            assert ip == "127.0.0.1"

    def test_normal_path_returns_socket_ip(self) -> None:
        """正常情况下返回 getsockname()[0] 的结果。"""
        with patch("voicecraft.server.qr.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("192.168.0.42", 0)
            mock_socket_cls.return_value = mock_sock

            ip = get_lan_ip()
            assert ip == "192.168.0.42"


class TestPrintConnectInfo:
    def test_returns_correct_url(self, capsys: object) -> None:
        url = print_connect_info(port=8080, token="mytoken", ip="192.168.1.1")
        assert url == "http://192.168.1.1:8080/?room=mytoken"

    def test_output_contains_url(self, capsys: object) -> None:
        """stdout 必须包含 URL（用户能看到明文地址）。"""
        import io

        # capsys 用 pytest fixture 类型，这里用 monkeypatch 替代
        captured = io.StringIO()
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.write(str(a[0]) + "\n")):
            print_connect_info(port=8080, token="tok123", ip="10.0.0.1")

        output = captured.getvalue()
        assert "10.0.0.1:8080" in output
        assert "tok123" in output

    def test_auto_ip_detection(self) -> None:
        """ip=None 时应当自动检测（不抛异常）。"""
        with patch("voicecraft.server.qr.get_lan_ip", return_value="192.168.99.1"):
            url = print_connect_info(port=8080, token="t")
        assert "192.168.99.1" in url
