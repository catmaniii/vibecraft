"""PC 终端二维码显示（M1.1d）。

bot service 启动后在终端打印：
  1. 二维码（实心块字符 ██ = 黑模块，空格 = 白模块，比 ## 无缝隙、好扫）
  2. 明文 URL（扫码失败可手输）
  3. 局域网 IP 自动检测

注意：实心块 █ 是 Unicode 字符，终端需 UTF-8 输出编码才能正确显示
（scripts/start.ps1 已设 [Console]::OutputEncoding = UTF8）。

二维码内容：http://<局域网IP>:<port>/?room=<token>

IP 检测策略（标准做法）：
  - 开一个 UDP socket，connect 8.8.8.8:80（不真正发包），
    读 getsockname() 取本机出向 IP。
  - 若失败（纯离线 / 无网络接口）fallback 到 127.0.0.1 + 打警告。
"""

from __future__ import annotations

import socket
import textwrap

import qrcode
import structlog

logger = structlog.get_logger(__name__)

# 二维码渲染：每个模块 2 字符宽，黑模块用实心块 ██，白模块用空格。
# 实心块无缝隙、扫码识别率高；终端需 UTF-8 输出编码（见 start.ps1）。
_CELL_BLACK = "██"
_CELL_WHITE = "  "


def get_lan_ip() -> str:
    """获取本机局域网 IP（非 127.0.0.1）。

    原理：UDP connect 不发包，OS 选择路由后可通过 getsockname() 读到出向接口 IP。
    失败则 fallback 到 127.0.0.1（纯离线环境）。
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip: str = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        logger.warning("get_lan_ip_failed", fallback="127.0.0.1")
        return "127.0.0.1"


def build_connect_url(ip: str, port: int, token: str) -> str:
    """拼装手机扫码连接 URL。"""
    return f"http://{ip}:{port}/?room={token}"


def render_qr_ascii(url: str) -> str:
    """把 URL 渲染成二维码字符串（含边距）。

    黑模块用实心块 ██、白模块用空格，每格 2 字符宽（长宽比接近正方形）。
    返回多行字符串，调用方直接 print()。
    """
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()  # list[list[bool]]，True=黑

    lines: list[str] = []
    for row in matrix:
        line = "".join(_CELL_BLACK if cell else _CELL_WHITE for cell in row)
        lines.append(line)
    return "\n".join(lines)


def render_qr_png(data: str, box_size: int = 20, border: int = 4) -> bytes:
    """把任意字符串渲染成**高清** QR 码 PNG 字节（供 PWA `<img>` 展示 + 下载后被识别工具读）。

    用 PNG（栅格）而非 SVG：识别工具下载图片后常按 SVG 的固有 mm 尺寸栅格化成小图（~140px）
    被判"太小"。PNG 直接给足像素（box_size=20 → 每模块 20px，典型 URL ~800-1000px），扫码/识别稳。
    data 只进入 QR 模块矩阵，无注入风险；调用方应对 data 长度做上限限制。
    """
    import io

    qr = qrcode.QRCode(box_size=box_size, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def print_connect_info(
    port: int, token: str, ip: str | None = None, name: str | None = None
) -> str:
    """打印二维码 + 明文 URL 到终端，返回 URL 字符串（便于测试断言）。

    Args:
        port:  监听端口
        token: room_token
        ip:    局域网 IP；None 时自动检测
        name:  服务器友好名称（来自 --config name 字段）；None 时不显示

    Returns:
        connect URL（供测试断言）
    """
    resolved_ip = ip or get_lan_ip()
    url = build_connect_url(resolved_ip, port, token)

    qr_art = render_qr_ascii(url)

    separator = "=" * 50
    name_line = f"\n 服务器: {name}" if name else ""
    info = textwrap.dedent(f"""\
        {separator}
         VibeCraft 已启动{name_line}
        {separator}
        {qr_art}

         用手机扫描上方二维码，或浏览器输入：
           {url}

         IP:port  {resolved_ip}:{port}
         token    {token}
        {separator}
    """)
    print(info)
    logger.info("bot_service_started", url=url, ip=resolved_ip, port=port, name=name)
    return url
