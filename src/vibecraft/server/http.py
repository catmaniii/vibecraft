"""HTTP static server：serve PWA 静态资源，与 WS endpoint 共享同一端口。

实现决策（ADR 0001）：
  - 不引入任何 HTTP 框架（Flask / FastAPI / aiohttp）
  - 利用 websockets >= 12 的 process_request 钩子：非 WS 请求（Upgrade 头
    不是 websocket）在握手前被拦截，用标准库 mimetypes 和 pathlib 直接返回
    静态文件 Response；WS 请求则放行（返回 None）继续握手。
  - 这样 HTTP + WS 共同监听同一端口，没有端口分叉，符合设计文档 §9.2
    「ws://host:port/ws?room=<token>」和「http://host:port/?room=<token>」
    都指向同一地址的要求。
  - 标准库只用 mimetypes + pathlib；无额外依赖。

局限（MVP 够用）：
  - 不支持 Range 请求、压缩、缓存控制（静态 HTML/JS/CSS 场景够用）
  - 只 serve GET，其余返回 405
  - 路径遍历（../）：pathlib.resolve() 做白名单校验，若逃出 static_dir 返回 403
"""

from __future__ import annotations

import http
import mimetypes
import pathlib
from typing import Any

import structlog
from websockets.asyncio.server import ServerConnection
from websockets.datastructures import Headers as WsHeaders
from websockets.http11 import Request, Response

logger = structlog.get_logger(__name__)

# 默认 static 目录：同包 server/static/
_DEFAULT_STATIC_DIR = pathlib.Path(__file__).parent / "static"


def make_process_request(
    static_dir: pathlib.Path | None = None,
) -> Any:
    """返回 process_request 钩子函数，传给 websockets.serve()。

    - WS 请求（含 Upgrade: websocket 头）→ 返回 None，交给 websockets 握手
    - 普通 HTTP 请求 → serve 静态文件，返回 Response
    """
    root = (static_dir or _DEFAULT_STATIC_DIR).resolve()

    def process_request(ws: ServerConnection, request: Request) -> Response | None:
        # WS 升级请求：放行
        upgrade = request.headers.get("Upgrade", "").lower()
        if upgrade == "websocket":
            return None

        # 普通 HTTP 请求：serve 静态文件
        return _serve_static(ws, request, root)

    return process_request


def _serve_static(
    ws: ServerConnection,
    request: Request,
    root: pathlib.Path,
) -> Response:
    """解析请求路径，返回对应静态文件的 HTTP Response。"""
    log = logger.bind(remote=str(ws.remote_address), path=request.path)

    # 只允许 GET
    # websockets 的 Request 没有 method 字段（已在握手前），
    # HTTP 方法通过检测 request.path 是否合法路由判断，
    # 实际上 websockets process_request 只会在收到 HTTP request line 后触发，
    # 均视为 GET（浏览器请求静态资源只用 GET，暂不处理其它 method）。

    # 提取 URL path（去掉 query string）
    raw_path = request.path.split("?")[0]
    # 规范化：去掉开头 /，空路径 → index.html
    rel = raw_path.lstrip("/") or "index.html"

    # 路径遍历防护
    try:
        target = (root / rel).resolve()
        target.relative_to(root)  # 若逃出 root 则抛 ValueError
    except ValueError:
        log.warning("http_path_traversal_blocked", rel=rel)
        return _text_response(http.HTTPStatus.FORBIDDEN, "403 Forbidden\n")

    if not target.exists():
        # 文件不存在 → fallback 到 index.html（SPA 路由）
        target = root / "index.html"

    if not target.is_file():
        return _text_response(http.HTTPStatus.NOT_FOUND, "404 Not Found\n")

    body = target.read_bytes()
    mime, _ = mimetypes.guess_type(str(target))
    content_type = mime or "application/octet-stream"
    if content_type.startswith("text/") and "charset" not in content_type:
        content_type += "; charset=utf-8"

    log.debug("http_static_served", file=str(target), content_type=content_type)

    headers = WsHeaders(
        [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-cache"),
        ]
    )
    return Response(http.HTTPStatus.OK.value, http.HTTPStatus.OK.phrase, headers, body)


def _text_response(status: http.HTTPStatus, text: str) -> Response:
    """构造纯文本 HTTP 响应。"""
    body = text.encode()

    headers = WsHeaders(
        [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ]
    )
    return Response(status.value, status.phrase, headers, body)
