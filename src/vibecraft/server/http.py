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

import asyncio
import contextlib
import csv
import http
import json
import mimetypes
import pathlib
import re
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Any

import structlog
from websockets.asyncio.server import ServerConnection
from websockets.datastructures import Headers as WsHeaders
from websockets.http11 import Request, Response

if TYPE_CHECKING:
    from vibecraft.llm.config import LLMConfig
    from vibecraft.server.admin_scram import AdminScram
    from vibecraft.server.room_service import RoomService
    from vibecraft.server.turn_config import TurnConfig
    from vibecraft.strategy.library import StrategyLibrary

logger = structlog.get_logger(__name__)

# 默认 static 目录：同包 server/static/
_DEFAULT_STATIC_DIR = pathlib.Path(__file__).parent / "static"

# ---------------------------------------------------------------------------
# Guide Chat（手册 AI 助手）配置
# ---------------------------------------------------------------------------

# 项目根目录（从 src/vibecraft/server/http.py 上溯 3 层）
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

# 每 IP 每分钟最多 N 次 guide-chat 请求（未来加鉴权前先用限流防烧钱）
_GUIDE_CHAT_RATE_LIMIT = 20

# 限流滑动窗口：{ip: deque([monotonic_timestamp, ...])}
_guide_chat_buckets: dict[str, deque[float]] = defaultdict(deque)

# 手册全文缓存（按语言，懒加载；启动后第一次调用时读文件，之后复用）
_guide_text_cache: dict[str, str] = {}

# ---- Guide Chat 系统提示词 -----------------------------------------------

_GUIDE_SYSTEM_ZH = (
    "你是 VibeCraft 的游戏内助手。VibeCraft 让操作跟不上的老 SC2 玩家**用手机**指挥 AI 替自己"
    "打星际争霸2（StarCraft II）：**实时游戏画面推流到玩家手机上，玩家全程看手机看战况 + 用文字/"
    "语音下指令**，AI 在 PC 上执行所有键鼠操作。PC 只是跑 SC2 和 AI 的主机，玩家不用看 PC、也不碰"
    "键鼠。介绍这个游戏时要按这套**手机看画面 + 手机指挥**的玩法讲，别说成「在电脑上看」。\n\n"
    "你面向的是**已经连上手机、正在游戏里的玩家**。只回答**游戏内怎么玩**：怎么下指令、"
    "有哪些 build 可选、各种战术/骚扰/编队/攻防/侦察怎么用。**不要讲怎么部署、怎么启动"
    "服务器、命令行参数、怎么搭多人房间——这些是房主/开发者看文档的事，纯玩家不需要。**"
    "玩家问跟这个游戏完全无关的问题，礼貌说明你只能回答 VibeCraft 玩法相关问题。\n\n"
    "用玩家提问的语言回答，简洁（不超过 200 字），直接给答案，不废话。"
)

_GUIDE_SYSTEM_EN = (
    "You are the in-game assistant for VibeCraft. VibeCraft lets veteran StarCraft II players"
    " whose hands can't keep up command an AI **from their phone**: the **live game is streamed"
    " to the player's phone — they watch the battle and issue text/voice commands entirely on"
    " the phone**, while the AI executes all mouse/keyboard actions on the PC. The PC is just the"
    " host running SC2 and the AI; the player never watches the PC or touches its keyboard/mouse."
    " When you describe the game, describe this **watch-and-command-on-phone** experience — do"
    " NOT say the player watches on the PC.\n\n"
    "You are talking to a **player who is already connected and in a game**. Only answer"
    " **how to play in-game**: how to issue commands, what builds are available, how to use"
    " tactics/harass/groups/attack-defend/scouting. **Do NOT explain deployment, how to"
    " start the server, command-line flags, or how to set up multiplayer rooms — those are"
    " host/developer topics covered in the docs; a plain player does not need them.** If"
    " someone asks something unrelated to VibeCraft, politely say you only help with"
    " VibeCraft gameplay.\n\n"
    "Reply in the language the player used. Be concise (under 200 words), answer directly."
)

_GUIDE_FAQ_ZH = """
## 常见问题快速答案

- 怎么连接？手机扫码或输入 server 地址+房间码，填用户名，点连接。
- 怎么下指令？手机文本框打字或长按调系统语音转字，点发送按钮。
- 大和舰（战巡）骚扰：说"所有大舰去骚扰"或"派3个大舰骚扰对方三矿"。
- 骚扰农民（虫族飞龙）：说"飞龙骚扰对方主矿，打死5个农民就回"。
- 全军进攻/撤退/守家：说"进攻对方二矿"/"全军撤退"/"守家"。
- 编队：说"把虚空编成1队"，之后"1队进攻对方三矿"。
- 视频/音频没声音：点一下屏幕或发一条指令解除浏览器静音。
- 支持哪些种族：神族、虫族、人族三族都支持。
- 有哪些build：界面「宏观策略」面板里有40+剧本可选。
- 语音转字不准：在文本框里改完再发，VibeCraft 自己不做语音识别。
"""

_GUIDE_FAQ_EN = """
## Quick FAQ

- How to connect? Scan the QR code or enter server address + room token, fill in username, tap Connect.
- How to issue commands? Type in the text box or long-press to use voice-to-text, then tap Send.
- Battlecruiser harass: say "send all BCs to harass" or "send 3 BCs to harass their third base".
- Mutalisk harass (Zerg): "mutalisk harass enemy main, retreat after killing 5 workers".
- All-army attack/retreat/defend: say "attack their second" / "all units retreat" / "defend at home".
- Groups: say "put void rays in group 1", then "group 1 attack their third".
- No audio in video: tap the screen or send a command to unblock browser autoplay mute.
- Which races: Protoss, Zerg, Terran — all three supported.
- What builds: 40+ builds in the Strategy panel in the UI.
- Voice recognition inaccurate: edit the text box after transcription, then send.
"""

# ---------------------------------------------------------------------------
# Admin 鉴权：进程内失败计数 + 锁定（按来源 IP）
# ---------------------------------------------------------------------------

# 最小 admin token 长度（太短=弱口令，启动时 warn）。2026-06-20 用户：测试阶段降到 8。
_ADMIN_TOKEN_MIN_LEN = 8

# 连续失败超过此次数 → 锁定
_ADMIN_MAX_FAILS = 5

# 锁定持续时间（秒）
_ADMIN_LOCKOUT_S = 60.0

# 进程内状态（模块级，整个 server 生命周期共享）
_admin_fail_count: dict[str, int] = defaultdict(int)
_admin_lockout_until: dict[str, float] = {}


def _get_source_ip(ws: ServerConnection, request: Request) -> str:
    """从 X-Forwarded-For 或 remote_address 提取来源 IP（用于限速）。"""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    ip = ""
    with contextlib.suppress(Exception):
        ip = str(ws.remote_address[0])
    return ip


def _admin_is_locked(source_ip: str) -> bool:
    """该来源 IP 是否处于登录锁定期（暴破防护）。"""
    if time.monotonic() < _admin_lockout_until.get(source_ip, 0.0):
        logger.warning("admin_auth_locked", source_ip=source_ip)
        return True
    return False


def _admin_record_fail(source_ip: str) -> None:
    """记一次登录失败（SCRAM-final 验证不通过），达阈值触发锁定。"""
    _admin_fail_count[source_ip] += 1
    fails = _admin_fail_count[source_ip]
    if fails >= _ADMIN_MAX_FAILS:
        _admin_lockout_until[source_ip] = time.monotonic() + _ADMIN_LOCKOUT_S
        _admin_fail_count[source_ip] = 0
        logger.warning(
            "admin_auth_lockout_triggered", source_ip=source_ip, lockout_s=_ADMIN_LOCKOUT_S
        )
    else:
        logger.debug("admin_auth_fail", source_ip=source_ip, fails=fails)


def _admin_reset_fail(source_ip: str) -> None:
    """登录成功 → 清失败计数。"""
    _admin_fail_count.pop(source_ip, None)
    _admin_lockout_until.pop(source_ip, None)


def make_process_request(
    static_dir: pathlib.Path | None = None,
    strategy_library: StrategyLibrary | None = None,
    turn_config: TurnConfig | None = None,
    room_token: str | None = None,
    admin_scram: AdminScram | None = None,
    room_service: RoomService | None = None,
    server_name: str | None = None,
    llm_config: LLMConfig | None = None,
) -> Any:
    """返回 process_request 钩子协程函数，传给 websockets.serve()。

    websockets 15+ 支持 async process_request（见官方文档），本函数返回
    async def 以支持 /api/guide-chat 的异步 LLM 调用，不阻塞事件循环。

    - WS 请求（含 Upgrade: websocket 头）→ 返回 None，交给 websockets 握手
    - GET /api/strategies → 返回 JSON 策略列表（若 strategy_library 已注入）
    - GET /api/turn-credential → 返回 WebRTC iceServers + 现签短期 TURN 凭证（阶段1 中继）
    - GET /api/server-info → 返回 {"name": <server_name or null>}（公开，非敏感）
    - GET /api/guide-chat → 手册 AI 助手（DeepSeek 纯文本对话；限流 20 req/min/IP）
    - GET /admin → serve admin.html（登录 UI；口令在页内走 SCRAM-SHA-256）
    - GET /api/admin/scram-first|scram-final → SCRAM 握手 → 会话令牌
    - GET /api/admin/* → admin API（需 X-Admin-Session header = 有效会话令牌）
    - 普通 HTTP 请求 → serve 静态文件，返回 Response

    turn_config=None → /api/turn-credential 返回空 iceServers（手机回退 STUN，纯 P2P）。
    room_token 不为 None 时校验 ?room=<token>，挡随机扫描拿中继凭证。
    admin_scram=None → /admin 与 /api/admin/* 全部返回 404（secure by default）。
    server_name=None → /api/server-info 返回 {"name": null}。
    llm_config=None → /api/guide-chat 用默认 DeepSeek 配置（DEEPSEEK_API_KEY env）。
    """
    root = (static_dir or _DEFAULT_STATIC_DIR).resolve()

    async def process_request(ws: ServerConnection, request: Request) -> Response | None:
        # WS 升级请求：放行
        upgrade = request.headers.get("Upgrade", "").lower()
        if upgrade == "websocket":
            return None

        # API 路由
        raw_path = request.path.split("?")[0]
        if raw_path == "/api/strategies":
            from urllib.parse import parse_qs, urlparse

            _loc = (parse_qs(urlparse(request.path).query).get("locale") or ["zh"])[0]
            _lang = _loc if _loc in ("zh", "en") else "zh"
            return _serve_strategies_api(strategy_library, _lang)
        if raw_path == "/api/turn-credential":
            return _serve_turn_credential(request, turn_config, room_token)
        if raw_path == "/api/feedback":
            return _serve_feedback(ws, request)
        if raw_path == "/api/server-info":
            return _serve_server_info(server_name)
        if raw_path == "/api/qr":
            return _serve_qr(request)
        if raw_path == "/rg" or raw_path == "/reasoning-graph":
            return _serve_rg()
        if raw_path == "/api/guide-chat":
            return await _serve_guide_chat(ws, request, llm_config)

        # Admin 路由（未配 admin → 全部 404）
        if raw_path == "/admin" or raw_path.startswith("/api/admin/"):
            return _dispatch_admin(ws, request, raw_path, admin_scram, room_service, root)

        # 普通 HTTP 请求：serve 静态文件
        return _serve_static(ws, request, root)

    return process_request


# ---------------------------------------------------------------------------
# Admin 路由分发 + 鉴权
# ---------------------------------------------------------------------------


def _dispatch_admin(
    ws: ServerConnection,
    request: Request,
    raw_path: str,
    admin_scram: AdminScram | None,
    room_service: RoomService | None,
    static_root: pathlib.Path,
) -> Response:
    """分发 /admin 与 /api/admin/* 请求（SCRAM-SHA-256 登录 → 会话令牌，RFC 5802）。

    - admin_scram 未配 → 统一 404（secure by default，非披露）。
    - `/admin` 页面：直接 serve admin.html（登录 UI，无机密；口令在页内走 SCRAM）。
    - `/api/admin/scram-first` / `scram-final`：SCRAM 握手（final 失败 → 404 + 锁定，暴破点）。
    - 其余 `/api/admin/*`：要 `X-Admin-Session` header 是有效会话令牌，否则 404。
    """
    if admin_scram is None:
        return _admin_404()

    if raw_path == "/admin":
        admin_html = static_root / "admin.html"
        if not admin_html.is_file():
            return _text_response(http.HTTPStatus.NOT_FOUND, "admin.html not found\n")
        body = admin_html.read_bytes()
        headers = WsHeaders(
            [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-cache"),
            ]
        )
        return Response(http.HTTPStatus.OK.value, http.HTTPStatus.OK.phrase, headers, body)

    # SCRAM 握手
    if raw_path == "/api/admin/scram-first":
        return _serve_scram_first(request, admin_scram)
    if raw_path == "/api/admin/scram-final":
        return _serve_scram_final(ws, request, admin_scram)

    # 其余 API：要有效会话令牌
    session = request.headers.get("X-Admin-Session", "")
    if not admin_scram.check_session(session):
        return _admin_404()

    if raw_path == "/api/admin/status":
        return _serve_admin_status(room_service)
    if raw_path == "/api/admin/chat":
        return _serve_admin_chat(room_service)
    if raw_path == "/api/admin/chat-send":
        return _serve_admin_chat_send(request, room_service)
    if raw_path == "/api/admin/games":
        return _serve_admin_games()
    if raw_path == "/api/admin/feedback":
        return _serve_admin_feedback()

    return _admin_404()


def _admin_query(request: Request, key: str) -> str:
    """从 request.path 的 query 取一个参数（SCRAM 握手消息走 query，URL-safe base64）。"""
    from urllib.parse import parse_qs, urlparse

    vals = parse_qs(urlparse(request.path).query).get(key) or []
    return vals[0] if vals else ""


def _serve_scram_first(request: Request, admin_scram: AdminScram) -> Response:
    """SCRAM 第一步：client-first-bare（URL-safe b64）→ {sid, server_first}。"""
    from vibecraft.server.admin_scram import _ub64d

    try:
        client_first_bare = _ub64d(_admin_query(request, "msg")).decode("utf-8")
        sid, server_first = admin_scram.first(client_first_bare)
    except Exception:
        return _admin_404()
    return _admin_json_response(http.HTTPStatus.OK, {"sid": sid, "server_first": server_first})


def _serve_scram_final(ws: ServerConnection, request: Request, admin_scram: AdminScram) -> Response:
    """SCRAM 第二步：验 ClientProof → {server_final, session}；失败 404 + 锁定。"""
    from vibecraft.server.admin_scram import _ub64d

    source_ip = _get_source_ip(ws, request)
    if _admin_is_locked(source_ip):
        return _admin_404()
    try:
        sid = _admin_query(request, "sid")
        client_final_no_proof = _ub64d(_admin_query(request, "msg")).decode("utf-8")
        proof = _ub64d(_admin_query(request, "proof"))
        result = admin_scram.final(sid, client_final_no_proof, proof)
    except Exception:
        result = None
    if result is None:
        _admin_record_fail(source_ip)
        return _admin_404()
    _admin_reset_fail(source_ip)
    server_final, session = result
    return _admin_json_response(
        http.HTTPStatus.OK, {"server_final": server_final, "session": session}
    )


def _admin_404() -> Response:
    """Admin 统一 404（不分"未配"与"key 错"，非披露）。"""
    return _text_response(http.HTTPStatus.NOT_FOUND, "404 Not Found\n")


def _admin_json_response(status: http.HTTPStatus, payload: Any) -> Response:
    """Admin API JSON 响应（不加 CORS 通配符，收紧安全）。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = WsHeaders(
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-cache"),
        ]
    )
    return Response(status.value, status.phrase, headers, body)


def _mask_ip(ip: str) -> str:
    """IP 轻度脱敏：IPv4 保留前两段，后两段用 *.*。IPv6 保留前半截。"""
    if not ip:
        return ""
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.*.*"
    # IPv6 简单截断
    colon_pos = ip.rfind(":")
    if colon_pos > 0:
        return ip[: colon_pos + 1] + "***"
    return ip[: max(1, len(ip) // 2)] + "***"


def _serve_admin_status(room_service: RoomService | None) -> Response:
    """GET /api/admin/status → server + room 只读状态（白名单字段）。"""
    if room_service is None:
        return _admin_json_response(
            http.HTTPStatus.OK,
            {"online": True, "room_state": "unknown", "error": "room_service_not_injected"},
        )

    room = room_service.room
    registry = room_service._registry  # type: ignore[attr-defined]

    # 白名单字段输出（绝不 dump 整个 config/registry/room，防漏 room token/TURN/.secrets）
    slots_out = [
        {
            "index": s.index,
            "kind": s.kind,
            "name": s.name,
            "race": s.race,
            "ready": s.ready,
            "team": s.team,
        }
        for s in room.slots
    ]

    payload: dict[str, Any] = {
        "online": True,
        "room_state": room.state,
        "match_id": room.match_id,
        "realtime": room.realtime,
        "slots": slots_out,
        "players_online": registry.player_ids,
        "in_game": room.state == "in_game",
    }
    return _admin_json_response(http.HTTPStatus.OK, payload)


def _serve_admin_chat(room_service: RoomService | None) -> Response:
    """GET /api/admin/chat → ChatHub 当前内存历史。"""
    if room_service is None:
        return _admin_json_response(http.HTTPStatus.OK, {"messages": []})
    history = room_service.chat.history()
    return _admin_json_response(http.HTTPStatus.OK, {"messages": history})


def _serve_admin_chat_send(
    request: Request,
    room_service: RoomService | None,
) -> Response:
    """GET /api/admin/chat-send?text=... → 发聊天消息（复用同一 ChatHub，fire-and-forget 广播）。

    走 GET+query（websockets process_request 无 method/body，见 ADR 0001 与评审）。
    token 走 X-Admin-Token header（已在调用方验过）。
    """
    from urllib.parse import parse_qs, urlparse

    if room_service is None:
        return _admin_json_response(
            http.HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "room_service_not_injected"}
        )

    qs = parse_qs(urlparse(request.path).query)
    text_vals = qs.get("text") or []
    text = (text_vals[0] if text_vals else "").strip()[:500]
    if not text:
        return _admin_json_response(
            http.HTTPStatus.BAD_REQUEST, {"ok": False, "error": "text_empty"}
        )

    # 复用同一 ChatHub 实例（id 连续，不割裂历史）
    msg = room_service.chat.add(name="管理员", pid="__admin__", text=text)

    # fire-and-forget 广播（process_request 是同步函数，在 event loop 内被调用）
    registry = room_service._registry  # type: ignore[attr-defined]
    frame = json.dumps(msg, ensure_ascii=False)
    try:
        loop = asyncio.get_running_loop()
        # RUF006: 保留任务引用防 GC（生命周期 = broadcast 完成，无需手动 cancel）
        _broadcast_task = loop.create_task(registry.broadcast(frame))
        # 添加 done callback 防止被 GC 提前回收（任务集合式保活）
        _broadcast_task.add_done_callback(lambda _t: None)
    except RuntimeError:
        # 单测场景没有 running loop，静默忽略广播
        pass

    return _admin_json_response(http.HTTPStatus.OK, {"ok": True, "msg": msg})


def _serve_admin_games() -> Response:
    """GET /api/admin/games → 最近 50 局真人对局元数据。

    注意：games 扫描是同步文件 IO，不进 3s 轮询（设计决策：按需点开/低频）。
    """
    from vibecraft.server.admin_games import scan_match_games

    games = scan_match_games()
    return _admin_json_response(http.HTTPStatus.OK, {"games": games})


def _serve_admin_feedback() -> Response:
    """GET /api/admin/feedback → 解析 logs/feedback.csv，IP 轻度脱敏。"""
    feedback_path = pathlib.Path("logs") / "feedback.csv"
    if not feedback_path.exists():
        return _admin_json_response(http.HTTPStatus.OK, {"rows": []})

    rows: list[dict[str, Any]] = []
    try:
        # 首行可能有 BOM（utf-8-sig 写入），用 utf-8-sig 兼容读
        with feedback_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            headers_row = next(reader, None)
            if headers_row is None:
                return _admin_json_response(http.HTTPStatus.OK, {"rows": []})
            col_names = [h.strip() for h in headers_row]
            for data_row in reader:
                # 行长度防御：zip 对齐
                row_dict = dict(zip(col_names, data_row, strict=False))
                # IP 轻度脱敏
                if "IP" in row_dict:
                    row_dict["IP"] = _mask_ip(row_dict["IP"])
                rows.append(row_dict)
    except Exception:
        logger.warning("admin_feedback_read_failed", exc_info=True)
        return _admin_json_response(
            http.HTTPStatus.INTERNAL_SERVER_ERROR, {"rows": [], "error": "read_failed"}
        )

    return _admin_json_response(http.HTTPStatus.OK, {"rows": rows})


def _serve_feedback(ws: ServerConnection, request: Request) -> Response:
    """GET /api/feedback?name=&category=&content= → 追加一条玩家反馈到本地 CSV。

    走 GET query（项目 process_request 只处理 GET，无 HTTP 框架，见 ADR 0001）。
    记录：提交时间 / 昵称 / 分类 / 反馈内容 / IP（经 nginx X-Forwarded-For 取真实客户端
    IP，否则 remote_address）/ UA。存 logs/feedback.csv（UTF-8 BOM，Excel 直接可开；
    logs/ 已 gitignore，不进 git）。
    """
    import csv
    import time
    from urllib.parse import parse_qs, urlparse

    qs = parse_qs(urlparse(request.path).query)

    def _q(key: str, limit: int) -> str:
        vals = qs.get(key) or []
        return (vals[0] if vals else "")[:limit].strip()

    content = _q("content", 2000)
    if not content:
        return _json_response(http.HTTPStatus.BAD_REQUEST, {"ok": False, "error": "empty"})
    name = _q("name", 60) or "匿名"
    category = _q("category", 20) or "其他"

    # 真实客户端 IP：nginx 前门已设 X-Forwarded-For；直连时取 remote_address
    xff = request.headers.get("X-Forwarded-For", "")
    ip = xff.split(",")[0].strip() if xff else ""
    if not ip:
        with contextlib.suppress(Exception):
            ip = str(ws.remote_address[0])
    ua = (request.headers.get("User-Agent", "") or "")[:200]
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    try:
        path = pathlib.Path("logs") / "feedback.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not path.exists()
        # 新文件用 utf-8-sig 写 BOM（Excel 中文不乱码）；追加用 utf-8
        with path.open("a", encoding="utf-8-sig" if is_new else "utf-8", newline="") as f:
            w = csv.writer(f)
            if is_new:
                w.writerow(["提交时间", "昵称", "分类", "反馈内容", "IP", "UA"])
            w.writerow([ts, name, category, content, ip, ua])
        logger.info("feedback_saved", name=name, category=category, ip=ip)
    except Exception:
        logger.warning("feedback_save_failed", exc_info=True)
        return _json_response(
            http.HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "save_failed"}
        )

    return _json_response(http.HTTPStatus.OK, {"ok": True})


def _serve_qr(request: Request) -> Response:
    """GET /api/qr?data=<url> → 返回该字符串的**高清** QR 码 PNG（PWA 弹窗展示 + 可下载识别）。

    公开端点，无敏感数据：QR 内容由前端传入（通常是 window.location 的首页 URL），
    server 只把它渲染成图片。data 进入 QR 矩阵、不作文本，无注入风险；
    仍对长度设上限（QR 容量有限，超长无意义且防滥用）。
    用 PNG 高分辨率（非 SVG）：避免下载后被识别工具按 SVG mm 尺寸栅格化成小图判"太小"。
    """
    from urllib.parse import parse_qs, urlparse

    from vibecraft.server.qr import render_qr_png

    vals = parse_qs(urlparse(request.path).query).get("data") or []
    data = vals[0] if vals else ""
    if not data or len(data) > 1024:
        return _text_response(http.HTTPStatus.BAD_REQUEST, "missing or oversized 'data'\n")

    try:
        body = render_qr_png(data)
    except Exception:  # 渲染失败兜底，不让单请求崩服务
        return _text_response(http.HTTPStatus.INTERNAL_SERVER_ERROR, "qr render failed\n")

    headers = WsHeaders(
        [
            ("Content-Type", "image/png"),
            ("Content-Length", str(len(body))),
            ("Access-Control-Allow-Origin", "*"),
            ("Cache-Control", "public, max-age=300"),
        ]
    )
    return Response(http.HTTPStatus.OK.value, http.HTTPStatus.OK.phrase, headers, body)


def _serve_rg() -> Response:
    """GET /rg（或 /reasoning-graph）→ 实时渲染推理图谱交互可视化。

    「落地 + 自动适配」(2026-07-12 用户)：直接读**当前** `docs/reasoning-graph.yaml`(唯一真理源)
    注入全局 skill 的 `rg-viewer.html`(`~/.claude/skills/reasoning-graph/assets/`)返回，改 yaml →
    刷新页面即自动生效，无需重发 artifact。渲染逻辑与 `rg_render.py` 同源(都读同一 yaml)；viewer
    同一份模板支持服务端注入(本路径)+ drag-drop 两种喂法。

    SECURITY: **本路由刻意无门控、公网可见**（用户 2026-07-14 明确决定：推理图谱是内部研发
    认知、不含密钥/凭证，接受公网 IP 前门任意访问，换取裸 URL 的便利）。这是知情决策、不是漏配。
    推理图谱内容判定为非敏感；若将来往 yaml 里放了敏感信息，需回来重新评估是否加 room-token 门控
    （参照 `_serve_turn_credential` 的 ?room= 范式）。响应体不含任何 token/凭证。
    """
    try:
        import json as _json

        import yaml as _yaml

        repo_root = pathlib.Path(__file__).resolve().parents[3]
        yaml_path = repo_root / "docs" / "reasoning-graph.yaml"
        tmpl_path = (
            pathlib.Path.home()
            / ".claude"
            / "skills"
            / "reasoning-graph"
            / "assets"
            / "rg-viewer.html"
        )
        data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        # 注入 {nodes, title}:title 为 yaml 顶层可选字段(左上角项目标题;缺省 None→模板通用标题)。
        payload = _json.dumps(
            {"nodes": data["nodes"], "title": data.get("title")},
            ensure_ascii=False,
            separators=(",", ":"),
        ).replace("</", "<\\/")
        tmpl = tmpl_path.read_text(encoding="utf-8")
        s = tmpl.index("/*__RG_JSON__*/")
        e = tmpl.index("/*__RG_JSON_END__*/") + len("/*__RG_JSON_END__*/")
        body = (tmpl[:s] + payload + tmpl[e:]).encode("utf-8")
    except Exception as exc:  # 缺文件/坏 yaml → 500
        # 完整 exc（常含本机绝对路径 home/用户名）只进 log；客户端只回泛化文案，
        # 避免叠加公网暴露泄露主机路径。
        logger.warning("rg_render_failed", error=str(exc))
        return _text_response(http.HTTPStatus.INTERNAL_SERVER_ERROR, "推理图谱渲染失败")
    headers = WsHeaders(
        [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),  # 每次读最新 yaml
        ]
    )
    return Response(http.HTTPStatus.OK.value, http.HTTPStatus.OK.phrase, headers, body)


def _serve_server_info(name: str | None) -> Response:
    """GET /api/server-info → {"name": <server_name or null>}.

    Public endpoint — no room-token gating needed (payload is non-sensitive).

    SECURITY: Only expose ``name``. NEVER add token, admin_token, host, port,
    or any other field to this payload without explicit security review.
    Do not widen this response.
    """
    return _json_response(http.HTTPStatus.OK, {"name": name})


def _serve_turn_credential(
    request: Request,
    turn_config: TurnConfig | None,
    room_token: str | None,
) -> Response:
    """GET /api/turn-credential → {"iceServers": [...]}（含现签短期 TURN 凭证）。

    无 TURN 配置 / room token 不匹配 → 返回空 iceServers（手机回退 google STUN，graceful）。
    **每请求现签**（不缓存），凭证 expiry 才新鲜。
    """
    from urllib.parse import parse_qs, urlparse

    if turn_config is None:
        return _json_response(http.HTTPStatus.OK, {"iceServers": []})

    # room-token 门控：与现有 PWA/WS 的 ?room=<token> 模型一致
    if room_token is not None:
        qs = parse_qs(urlparse(request.path).query)
        room_vals = qs.get("room") or []
        provided = room_vals[0] if room_vals else None
        if provided != room_token:
            logger.debug("turn_credential_room_token_mismatch")
            return _json_response(http.HTTPStatus.OK, {"iceServers": []})

    from vibecraft.server.turn_config import build_ice_servers

    return _json_response(http.HTTPStatus.OK, {"iceServers": build_ice_servers(turn_config)})


def _serve_strategies_api(
    library: StrategyLibrary | None,
    lang: str = "zh",
) -> Response:
    """返回 GET /api/strategies 的 JSON 响应。

    返回结构：
    {
      "strategies": [
        {
          "id": "4bg",
          "display": "4bg一波",
          "race": "protoss",
          "stage": "opening",
          "summary_zh": "...",
          "aliases": [...]
        },
        ...
      ]
    }

    stage 映射：opening_build / midgame_stance → "opening"；
               persistent_doctrine / lategame_doctrine → "persistent"。
    """
    if library is None:
        # library 未注入（旧 unit test 场景）：返回空列表
        payload: dict[str, Any] = {"strategies": []}
        return _json_response(http.HTTPStatus.OK, payload)

    from vibecraft.strategy.models import localized_name, localized_summary

    items: list[dict[str, Any]] = []

    def _chip(strat: Any, stage: str) -> dict[str, Any]:
        # display/summary_zh 字段名保留(前端读它),但按玩家 locale 装本地化文本(en 缺→回退 zh)。
        return {
            "id": strat.id,
            "display": localized_name(strat, lang),
            "race": library.race_of(strat.id),
            "stage": stage,
            "summary_zh": localized_summary(strat, lang),
            "aliases": list(strat.aliases),
        }

    # opening_build + midgame_stance → stage = "opening"
    for opening in library.openings:
        items.append(_chip(opening, "opening"))
    for midgame in library.midgames:
        items.append(_chip(midgame, "opening"))

    # persistent_doctrine + lategame_doctrine → stage = "persistent"
    for persistent in library.persistents:
        items.append(_chip(persistent, "persistent"))
    for lategame in library.lategames:
        items.append(_chip(lategame, "persistent"))

    payload = {"strategies": items}
    return _json_response(http.HTTPStatus.OK, payload)


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


def _json_response(status: http.HTTPStatus, payload: Any) -> Response:
    """构造 JSON HTTP 响应（CORS 允许所有来源，方便本地开发调试）。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = WsHeaders(
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Access-Control-Allow-Origin", "*"),
            ("Cache-Control", "no-cache"),
        ]
    )
    return Response(status.value, status.phrase, headers, body)


# ---------------------------------------------------------------------------
# Guide Chat（手册 AI 助手）
# ---------------------------------------------------------------------------


def _strip_chat_skip(text: str) -> str:
    """剥掉手册里 <!-- chat-skip-start --> ... <!-- chat-skip-end --> 之间的内容。

    聊天助手只面向**纯玩家**（来问的一定是玩家，不是开发者/房主）。手册里"起服务器 /
    扫码部署 / 搭 1v1 / 版本"这类**部署主机**内容不该进系统提示词——否则玩家问"怎么玩"
    会被回一堆部署步骤（2026-07-05 用户反馈）。这些段在 USER_GUIDE(_EN).md 里用 HTML
    注释标记包起来（渲染不可见，只影响喂给 LLM 的提示词）。
    """
    return re.sub(
        r"<!--\s*chat-skip-start\s*-->.*?<!--\s*chat-skip-end\s*-->\s*",
        "",
        text,
        flags=re.DOTALL,
    )


def _get_guide_text(lang: str) -> str:
    """加载并缓存玩家手册**纯玩家部分**（按语言，已剥离部署主机段）。

    zh → USER_GUIDE.md；en → USER_GUIDE_EN.md。文件不存在时回退到中文版。
    剥离规则见 `_strip_chat_skip`。
    """
    if lang not in _guide_text_cache:
        filename = "USER_GUIDE.md" if lang == "zh" else "USER_GUIDE_EN.md"
        path = _REPO_ROOT / filename
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            # 英文版不存在时回退到中文版（优雅降级，不崩服务）
            fallback = _REPO_ROOT / "USER_GUIDE.md"
            raw = fallback.read_text(encoding="utf-8") if fallback.exists() else ""
            logger.warning("guide_text_file_not_found", filename=filename, fallback="USER_GUIDE.md")
        _guide_text_cache[lang] = _strip_chat_skip(raw)
    return _guide_text_cache[lang]


async def _serve_guide_chat(
    ws: ServerConnection,
    request: Request,
    llm_config: LLMConfig | None = None,
) -> Response:
    """GET /api/guide-chat?q=<question>&lang=zh|en&h=<json-history>

    用 DeepSeek 作手册内嵌 AI 助手，系统提示词 = 手册全文 + FAQ + 约束。
    限流：每 IP 每分钟最多 20 次（进程内内存计数，无 Redis 依赖）。
    未来加鉴权时在此处添加（当前整体无鉴权，敞开）。
    """
    from urllib.parse import parse_qs, urlparse

    source_ip = _get_source_ip(ws, request)

    # ---- 解析 query 参数 ----
    qs = parse_qs(urlparse(request.path).query)
    lang = (qs.get("lang") or ["zh"])[0]
    lang = "en" if lang == "en" else "zh"
    question = (qs.get("q") or [""])[0][:500].strip()

    if not question:
        return _json_response(http.HTTPStatus.BAD_REQUEST, {"error": "empty question"})

    # ---- 限流检查（滑动窗口 60s）----
    now = time.monotonic()
    bucket = _guide_chat_buckets[source_ip]
    while bucket and bucket[0] < now - 60:
        bucket.popleft()
    if len(bucket) >= _GUIDE_CHAT_RATE_LIMIT:
        err = (
            "提问太频繁，请稍等一下" if lang == "zh" else "Too many requests, please wait a moment"
        )
        return _json_response(http.HTTPStatus(429), {"error": err})
    bucket.append(now)

    # ---- 解析历史（最近 3 轮 = 6 条）----
    history_raw = (qs.get("h") or ["[]"])[0]
    try:
        raw_history = json.loads(history_raw)
        if not isinstance(raw_history, list):
            raw_history = []
    except Exception:
        raw_history = []
    history: list[dict[str, Any]] = []
    for turn in raw_history[-6:]:
        role = str(turn.get("role", ""))
        content = str(turn.get("content", ""))[:1000]
        if role in ("user", "assistant") and content:
            history.append({"role": role, "content": content})

    # ---- 构造系统提示词（手册 + FAQ + 约束）----
    guide_text = _get_guide_text(lang)
    faq = _GUIDE_FAQ_ZH if lang == "zh" else _GUIDE_FAQ_EN
    system_header = _GUIDE_SYSTEM_ZH if lang == "zh" else _GUIDE_SYSTEM_EN
    system_prompt = f"{system_header}\n\n---\n\n{guide_text}\n\n---\n\n{faq}"

    messages = [*history, {"role": "user", "content": question}]

    # ---- 调用 LLM ----
    try:
        answer = await asyncio.wait_for(
            _call_guide_chat_llm(system_prompt, messages, llm_config),
            timeout=20.0,
        )
    except TimeoutError:
        err = "回复超时，请稍后再试" if lang == "zh" else "Response timed out, please try again"
        logger.warning("guide_chat_timeout", source_ip=source_ip)
        return _json_response(http.HTTPStatus.SERVICE_UNAVAILABLE, {"error": err})
    except Exception:
        logger.warning("guide_chat_llm_error", exc_info=True, source_ip=source_ip)
        err = "服务暂时不可用" if lang == "zh" else "Service temporarily unavailable"
        return _json_response(http.HTTPStatus.SERVICE_UNAVAILABLE, {"error": err})

    logger.info("guide_chat_answered", lang=lang, source_ip=source_ip)
    return _json_response(http.HTTPStatus.OK, {"answer": answer})


async def _call_guide_chat_llm(
    system_prompt: str,
    messages: list[dict[str, Any]],
    llm_config: LLMConfig | None = None,
) -> str:
    """调用 DeepSeek（Anthropic 兼容端点）做手册纯文本对话（无 tool_use）。

    llm_config=None 时从 DEEPSEEK_API_KEY 环境变量 + 默认端点读取。
    guide chat 用 disable_thinking=True（与 IntentParser 一致，避免 reasoner 模式）。
    """
    import os

    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:
        raise RuntimeError("anthropic SDK 未安装: uv add anthropic") from exc

    # 确定 API key、端点、模型
    if llm_config is not None and llm_config.provider == "deepseek":
        key_env = llm_config.api_key_env or "DEEPSEEK_API_KEY"
        api_key = llm_config.api_key or os.environ.get(key_env)
        base_url: str | None = llm_config.base_url or "https://api.deepseek.com/anthropic"
        model = llm_config.model
    else:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        base_url = "https://api.deepseek.com/anthropic"
        model = "deepseek-v4-flash"

    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY 未配置，无法调用 guide chat LLM")

    client = AsyncAnthropic(api_key=api_key, base_url=base_url)
    create_kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": 600,
        "system": system_prompt,
        "messages": messages,
        "thinking": {"type": "disabled"},  # deepseek compat：禁用 reasoner 模式
    }
    msg = await client.messages.create(**create_kwargs)

    for block in msg.content:
        if getattr(block, "type", None) == "text":
            return str(getattr(block, "text", ""))
    return ""
