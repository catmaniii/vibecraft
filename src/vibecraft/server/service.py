"""BotService：组装 HTTP+WS server + 生命周期管理（M1.1e）。

设计文档 §9.2：
  - bot service 启动 → 生成 room_token → 打印二维码 → run forever
  - HTTP + WS 共端口（process_request 钩子）
  - listen 0.0.0.0（不硬编码 localhost）

BotService 是可测的核心类；CLI 的 serve 子命令只做参数解析 + BotService.run()。

WebRTC 信令 (ADR 0013):
  - WebRtcSignalServer 运行在 webrtc_port（默认 port + 1）
  - 前端用 window.location.port + 1 计算信令端口
  - BotService.run() 同时启动两个 server（websockets + WebRtcSignalServer）
"""

from __future__ import annotations

import asyncio
import pathlib
import time
from dataclasses import dataclass, field

import structlog
from websockets.asyncio.server import serve

from vibecraft.server.asr import AsrEngine
from vibecraft.server.game_process import GameProcess
from vibecraft.server.http import _ADMIN_TOKEN_MIN_LEN, make_process_request
from vibecraft.server.qr import print_connect_info
from vibecraft.server.room_service import RoomService
from vibecraft.server.tokens import RoomRegistry, generate_room_token
from vibecraft.server.turn_config import load_turn_config
from vibecraft.server.webrtc import WebRtcManager, WebRtcSignalServer, make_webrtc_manager
from vibecraft.server.ws import make_ws_handler
from vibecraft.strategy.library import StrategyLibrary

logger = structlog.get_logger(__name__)


@dataclass
class ServiceConfig:
    """BotService 启动配置。"""

    port: int = 8080
    """监听端口，默认 8080。"""

    host: str = "0.0.0.0"
    """监听地址，不硬编码 localhost（设计文档「不硬编码 localhost」要求）。"""

    static_dir: pathlib.Path = field(
        default_factory=lambda: pathlib.Path(__file__).parent / "static"
    )
    """PWA 静态资源目录。"""

    token: str | None = None
    """room_token；None 时自动生成。"""

    display_ip: str | None = None
    """二维码显示用 IP；None 时自动检测局域网 IP。"""

    default_realtime: bool = True
    """start_game 帧未显式传 realtime 时的 SC2 默认运行模式。

    True = SC2 按 wall-clock 实时跑（玩家观战速度，默认）。
    False = SC2 按 step 推进（速度由 SC2 引擎决定，远快于 1x，调试用）。
    PWA 在 start_game.config.realtime 显式传值时优先使用 PWA 值。
    """

    default_my_race: str = "Protoss"
    """我方种族（Protoss / Zerg / Terran），默认 Protoss。由 CLI --my-race 控制。"""

    webrtc_port: int | None = None
    """WebRTC 信令端口；None 时自动用 port + 1。"""

    enable_webrtc: bool = True
    """是否启动 WebRTC 直播功能（默认开启）。"""

    max_players: int = 2
    """房间 slot 数（2026-06-12 用户 #8：引擎多 agent 仅 1v1，>2 个位是 UI 噪音；
    单人+多电脑 FFA 未实测，验证后再放开）。"""

    admin_token: str | None = None
    """Admin dashboard 独立鉴权 token（None = admin 整体关闭，secure by default）。
    CLI --admin-token 或 env VIBECRAFT_ADMIN_TOKEN 注入；最小 8 字符。
    """

    name: str | None = None
    """可选的服务器友好名称（来自 --config YAML 的 name 字段）。
    通过 GET /api/server-info 暴露给 PWA，显示在连接界面而非 URL。
    """


class BotService:
    """HTTP+WS bot service 实例。

    用法::

        config = ServiceConfig(port=8080)
        svc = BotService(config)
        asyncio.run(svc.run())
    """

    def __init__(self, config: ServiceConfig | None = None) -> None:
        self._config = config or ServiceConfig()
        self._registry = RoomRegistry(token=self._config.token or generate_room_token())
        # RoomService：Room + MatchOrchestrator 的聚合根（M3：无 legacy_gp 双轨）
        self._room_service = RoomService(
            self._registry,
            map_name=(self._config.default_realtime and "DaybreakLE") or "DaybreakLE",
            max_slots=self._config.max_players,
            default_realtime=self._config.default_realtime,
        )
        # 向后兼容属性：_game_process 指向 orchestrator 当前 solo 进程（或 None）
        # 旧单测 svc.game_process 语义保留，但不再直接 start()
        # （注：solo 开局走 shim，进程由 orchestrator 管；此处只做指针暴露）
        self._log = logger.bind(port=self._config.port, host=self._config.host)
        # 阶段1：TURN 中继配置（None=无 TURN，纯 P2P/Tailnet，行为不变）。
        # 同一份配置喂给 webrtc_manager(PC aiortc 侧)和 process_request(手机下发凭证)。
        self._turn_config = load_turn_config()
        # WebRTC manager — 持有活跃 PeerConnection 集合
        self._webrtc_manager: WebRtcManager | None = (
            make_webrtc_manager(turn_config=self._turn_config)
            if self._config.enable_webrtc
            else None
        )
        # StrategyLibrary：父进程启动时就加载，供 /api/strategies 使用。
        # 不依赖游戏状态，server 启动即可用。
        self._strategy_library: StrategyLibrary = _load_strategy_library()

    @property
    def registry(self) -> RoomRegistry:
        """暴露 registry，方便测试 / M1.2 扩展注入 SC2 生命周期。"""
        return self._registry

    @property
    def room_service(self) -> RoomService:
        """暴露 RoomService，方便测试。"""
        return self._room_service

    @property
    def game_process(self) -> GameProcess | None:  # type: ignore[override]
        """向后兼容：返回 orchestrator 当前 solo 进程（solo 开局时才有值）。

        M3：进程由 RoomService.orchestrator 管，此属性仅供旧单测用。
        solo 局开局后 orchestrator._procs 里只有一个 pid 的进程。
        对局未开始时返回 None。
        """
        procs = self._room_service.orchestrator.processes
        if not procs:
            return None
        return next(iter(procs.values()), None)

    @property
    def webrtc_manager(self) -> WebRtcManager | None:
        """暴露 WebRtcManager，方便测试检查状态。"""
        return self._webrtc_manager

    @property
    def token(self) -> str:
        return self._registry.token

    @property
    def webrtc_port(self) -> int | None:
        """实际使用的 WebRTC 信令端口（enable_webrtc=False 时为 None）。"""
        if not self._config.enable_webrtc:
            return None
        return self._config.webrtc_port or (self._config.port + 1)

    async def _loop_heartbeat(self) -> None:
        """事件循环健康心跳 —— 诊断「网站无响应 / 突然卡死」用。

        每 2s 测一次 event loop 延迟：健康时 lag≈0；loop 被同步阻塞时，
        阻塞结束后那一拍会量到大 lag（event_loop_lag warning）。loop 彻底
        卡死 → 心跳日志直接断 —— 日志里最后一条 event_loop_* 就是卡死时刻。
        """
        tick = 0
        while True:
            t0 = time.monotonic()
            await asyncio.sleep(2.0)
            lag = time.monotonic() - t0 - 2.0
            tick += 1
            if lag > 0.5:
                self._log.warning("event_loop_lag", lag_s=round(lag, 2))
            elif tick % 5 == 0:
                self._log.info("event_loop_alive", lag_s=round(lag, 3))

    async def _loop_sc2_crash_cleanup(self) -> None:
        """周期清理 SC2 崩溃弹窗(BlizzardError / WerFault)。固定脚本，不走 LLM。

        2026-06-17 用户：跑真局自测会反复起 SC2，崩溃时留一个 "StarCraft II" 崩溃上报
        弹窗堆在桌面。server 一运行就自带这个后台清理，每 30 分钟扫一次。
        只在 Windows 生效；复用 scripts/cleanup_sc2_crash.ps1 的安全清理逻辑——只杀崩溃
        弹窗类进程，绝不碰正在跑的真局（SC2_x64 且 Responding=true）。
        """
        import sys

        if sys.platform != "win32":
            return
        # service.py 在 src/vibecraft/server/ → parents[3] = 项目根（一次性启动解析，非热路径）
        script = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "cleanup_sc2_crash.ps1"  # noqa: ASYNC240
        if not script.exists():
            self._log.info("sc2_crash_cleanup_disabled", reason="script_missing")
            return
        self._log.info("sc2_crash_cleanup_started", interval_min=30)
        while True:
            await asyncio.sleep(1800.0)  # 30 分钟
            try:
                proc = await asyncio.create_subprocess_exec(
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-NoProfile",
                    "-File",
                    str(script),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                out, _ = await proc.communicate()
                text = (out or b"").decode("utf-8", "replace").strip()
                if "cleaned:" in text:
                    self._log.info("sc2_crash_cleaned", detail=text)
            except Exception as exc:
                self._log.warning("sc2_crash_cleanup_fail", error=str(exc))

    async def run(self) -> None:
        """启动 server，打印二维码，run forever（收到 SIGINT / CancelledError 退出）。"""
        cfg = self._config

        # 启用 server log file 捕获(父进程 stdout/stderr/logging 全镜像到文件 +
        # 通过 env 让 spawn 子进程接力到同一文件)。失败不阻塞 service。
        try:
            from vibecraft.logging_.server_log import (
                default_server_log_path,
                init_server_log_file,
            )

            log_path = init_server_log_file(default_server_log_path())
            self._log.info("server_log_file", path=str(log_path))
        except Exception as exc:
            self._log.warning("server_log_init_failed", error=str(exc))

        # faulthandler：捕获 native crash（access violation / segfault）。
        # WebRTC 视频编码走 aiortc + libav/libvpx 的 C 代码 —— 崩了不会有
        # Python traceback、server 进程直接消失。faulthandler 在崩的瞬间把
        # 所有线程的 Python 栈写进 server_crash.log，精确定位 native 崩溃点。
        try:
            import faulthandler

            crash_path = pathlib.Path("logs") / "server_crash.log"
            crash_path.parent.mkdir(parents=True, exist_ok=True)
            self._crash_file = crash_path.open("a", encoding="utf-8")
            faulthandler.enable(file=self._crash_file, all_threads=True)
            self._log.info("faulthandler_enabled", path=str(crash_path))
        except Exception as exc:
            self._log.warning("faulthandler_init_failed", error=str(exc))

        # 防 PC 闲置黑屏（用户反馈：闲置后 PWA 直播全黑，CRD 远程登录唤醒才回画面）。
        # 根因：Windows 闲置关显示器 → SC2 停渲染 → 抓屏抓到黑帧。这里告诉 Windows
        # "推流中，别关显示器/别睡"，与系统电源设置（monitor-timeout=0）双保险。
        try:
            import sys

            if sys.platform == "win32":
                import ctypes

                es_continuous = 0x80000000
                es_system_required = 0x00000001
                es_display_required = 0x00000002
                ctypes.windll.kernel32.SetThreadExecutionState(
                    es_continuous | es_system_required | es_display_required
                )
                self._log.info("keep_awake_enabled")
        except Exception as exc:
            self._log.warning("keep_awake_failed", error=str(exc))

        # admin 口令 → SCRAM material（口令本身不存，见 admin_scram.AdminScram）。
        admin_scram = None
        _admin_pw = self._config.admin_token
        if _admin_pw:
            if len(_admin_pw) < _ADMIN_TOKEN_MIN_LEN:
                self._log.warning("admin_token_too_short_ignored", min_len=_ADMIN_TOKEN_MIN_LEN)
            else:
                from vibecraft.server.admin_scram import AdminScram

                admin_scram = AdminScram(_admin_pw)
                self._log.info("admin_dashboard_enabled", auth="SCRAM-SHA-256")
        process_request = make_process_request(
            static_dir=cfg.static_dir,
            strategy_library=self._strategy_library,
            turn_config=self._turn_config,
            room_token=self._registry.token,
            admin_scram=admin_scram,
            room_service=self._room_service,
            server_name=cfg.name,
        )
        self._log.info(
            "turn_relay",
            enabled=bool(self._turn_config),
            domain=self._turn_config.domain if self._turn_config else None,
        )
        # ASR 引擎(语音输入)：service 级单例,惰性加载模型(funasr 没装 → available=False,
        # 收到音频帧静默忽略,不影响其它功能)。per-连接的 AsrSession 由 ws handler 管。
        asr_engine = AsrEngine()
        self._log.info("asr_engine_init", available=asr_engine.available)

        # 2026-06-13 用户:首句语音必失败(模型惰性加载几秒) → 启动即后台预热。
        # executor 里加载,不阻塞服务;失败只 warning(语音功能 graceful 降级)。
        async def _asr_warmup() -> None:
            try:
                ok = await asr_engine.warmup()
                self._log.info("asr_warmup_done", ok=ok)
            except Exception as exc:
                self._log.warning("asr_warmup_failed", error=str(exc))

        # 引用挂 self 防 GC(RUF006);server 生命周期内最多一个预热任务
        self._asr_warmup_task: asyncio.Task[None] | None = None
        if asr_engine.available:
            self._asr_warmup_task = asyncio.create_task(_asr_warmup(), name="asr-warmup")
        ws_handler = make_ws_handler(
            self._registry,
            room_service=self._room_service,
            default_realtime=self._config.default_realtime,
            default_my_race=self._config.default_my_race,
            # 2026-05-24 webrtc signaling 走 WS frame(单端口反代场景也支持)
            webrtc_manager=self._webrtc_manager,
            asr_engine=asr_engine,
        )

        # #522：多人音频前提检查 —— SC2 后台播放(soundglobal=true)未开 → 失焦窗口被
        # 引擎静音，两玩家不能同时听各自声音。仅警告，不阻塞启动（单人局 / 不关心
        # 音频时无害）；开源新用户靠这条日志知道要去开后台播放。
        try:
            from vibecraft.server.sound_check import (
                HINT_NOT_ENABLED,
                HINT_NOT_FOUND,
                check_sound_global,
            )

            sg = check_sound_global()
            if sg.enabled:
                self._log.info("sound_global_ok", path=str(sg.path))
            elif sg.found:
                self._log.warning(
                    "sound_global_not_enabled", path=str(sg.path), hint=HINT_NOT_ENABLED
                )
            else:
                self._log.warning("sound_global_variables_not_found", hint=HINT_NOT_FOUND)
        except Exception as exc:
            self._log.warning("sound_global_check_failed", error=str(exc))

        self._log.info("bot_service_starting")

        # 启动 WebRTC 信令服务（若启用）
        webrtc_signal: WebRtcSignalServer | None = None
        if self._webrtc_manager is not None:
            wrtc_port = self._config.webrtc_port or (cfg.port + 1)
            webrtc_signal = WebRtcSignalServer(
                manager=self._webrtc_manager,
                port=wrtc_port,
                host=cfg.host,
            )
            try:
                await webrtc_signal.start()
                self._log.info(
                    "webrtc_signal_listening",
                    host=cfg.host,
                    port=wrtc_port,
                )
            except Exception as exc:
                self._log.warning("webrtc_signal_start_failed", error=str(exc))
                webrtc_signal = None

        try:
            async with serve(
                ws_handler,
                cfg.host,
                cfg.port,
                process_request=process_request,
                # 关闭 websockets 内置 ping（业务层 ping 帧由 WsConnection 负责）
                ping_interval=None,
            ) as server:
                self._log.info(
                    "bot_service_listening",
                    host=cfg.host,
                    port=cfg.port,
                    token=self._registry.token,
                )
                # 打印二维码（display_ip=None → 自动检测）
                print_connect_info(
                    port=cfg.port,
                    token=self._registry.token,
                    ip=cfg.display_ip,
                    name=cfg.name,
                )
                # 事件循环健康心跳（诊断「网站无响应」）
                heartbeat = asyncio.create_task(self._loop_heartbeat())
                # SC2 崩溃弹窗周期清理（每 30 分钟；Windows only，固定脚本）
                crash_cleanup = asyncio.create_task(self._loop_sc2_crash_cleanup())
                try:
                    await server.serve_forever()
                finally:
                    heartbeat.cancel()
                    crash_cleanup.cancel()
        finally:
            # 清理 WebRTC
            if webrtc_signal is not None:
                await webrtc_signal.stop()
            if self._webrtc_manager is not None:
                await self._webrtc_manager.close_all()


# ---------------------------------------------------------------------------
# 辅助：父进程加载 StrategyLibrary（不依赖游戏状态）
# ---------------------------------------------------------------------------


def _load_strategy_library() -> StrategyLibrary:
    """在父进程（server 进程）加载 StrategyLibrary，供 /api/strategies 使用。

    策略目录路径：本文件在 src/vibecraft/server/service.py，项目根上溯 4 层。
    别名文件：任意种族均可（三族策略混排，API 直接用 race_of 过滤，alias 全量返回）。
    若目录不存在（开发环境无 strategies/），返回空 library——接口返回空列表而非 500。
    """
    _pkg_dir = pathlib.Path(__file__).parent  # server/
    _src_vc_dir = _pkg_dir.parent  # vibecraft/
    _src_dir = _src_vc_dir.parent  # src/
    _project_root = _src_dir.parent  # 项目根

    strategies_dir = _project_root / "strategies"
    # 用 protoss 别名文件做代表；三族别名文件对 StrategyLibrary 的策略列表没影响
    # （别名表由 AliasTable 管，不是 StrategyChip 列表的来源）。
    aliases_path = _project_root / "docs" / "aliases" / "protoss.yaml"

    _log = logger.bind(strategies_dir=str(strategies_dir))
    if strategies_dir.exists() and aliases_path.exists():
        try:
            lib = StrategyLibrary.from_directories(strategies_dir, aliases_path)
            _log.info(
                "strategy_library_loaded",
                count=len(lib.all_ids()),
            )
            return lib
        except Exception as exc:
            _log.warning("strategy_library_load_failed", error=str(exc))
    else:
        _log.warning(
            "strategy_library_dir_missing",
            strategies_exists=strategies_dir.exists(),
            aliases_exists=aliases_path.exists(),
        )
    return StrategyLibrary()
