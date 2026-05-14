"""SC2 子进程生命周期管理（M1.2）。

设计预研 `docs/plans/2026-05-14-m1.2-sc2-lifecycle.md`（4 个架构难点 + spike 结论）。

架构难点解决方案（spike 结论）：
- 难点 1：`run_game()` 阻塞 → 方案 B：独立 `multiprocessing` spawn 子进程。
  Windows 用 spawn，父进程只传 picklable 的 GameConfig，子进程自己构造 bot。
  spike 确认：GameConfig 全是基本类型，可 pickle；子进程 import ares + 构造 bot
  在自己的进程空间完成，不跨进程传 bot 对象。
- 难点 2：阶段检测 → bot 回调（on_start / on_step / on_end）往上行队列 put 状态事件。
- 难点 3：崩溃捕获 → try/except 包 run_game()；父进程轮询 exitcode 兜底。
- 难点 4：双向通信 → 两个 multiprocessing.Queue（上行 / 下行）；asyncio 侧用
  loop.run_in_executor 桥接阻塞 Queue.get()。

ADR：见 `docs/adr/0002-game-process-multiprocessing-spawn.md`。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import multiprocessing
import multiprocessing.queues
import queue
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, cast

import structlog

logger = structlog.get_logger(__name__)

# -----------------------------------------------------------------------
# 进程间消息类型
# -----------------------------------------------------------------------

# 子进程 → 父进程（上行）的消息 dict 格式：
#   {"sc2": <Sc2State>, "bot": <BotState>, "detail": <str>}
# 其中 sc2 / bot 取下列字面量字符串（对应 §9.3 game_status 下行帧字段）。

Sc2State = str  # "idle" | "launching" | "in_game" | "playing" | "ended" | "crashed"
BotState = str  # "idle" | "running" | "error"

# -----------------------------------------------------------------------
# 数据类
# -----------------------------------------------------------------------


@dataclass
class GameConfig:
    """拉起一局游戏所需的配置。

    必须 picklable —— 跨 spawn 子进程边界传递。
    全部使用标准 Python 基本类型。
    """

    map_name: str = "Goldenaura LE"
    """地图文件名（去掉 .SC2Map），传给 sc2.maps.get()。"""

    opponent_race: str = "Random"
    """内置 AI 种族：Protoss / Terran / Zerg / Random。"""

    opponent_difficulty: str = "Easy"
    """内置 AI 难度：VeryEasy / Easy / Medium / Hard / Harder / VeryHard / CheatVision。"""

    realtime: bool = True
    """是否以实时（1x）速度跑。玩家要看画面，默认 True。"""

    llm_controlled_probes: int = 0
    """预留：开局置入 LLM 控制 role 的探机数（M1.5 用，M1.2 暂 0）。"""


@dataclass
class GameStatus:
    """一个状态快照，从上行队列取出后封包成 game_status 帧。"""

    sc2: Sc2State
    bot: BotState
    ts: float = field(default_factory=time.time)
    detail: str = ""


# -----------------------------------------------------------------------
# 子进程入口（顶层函数，multiprocessing spawn 要求 picklable）
# -----------------------------------------------------------------------

# 启动超时（秒）：子进程 spawn 到 on_start 被调用前，最多等这么久。
# M0c 观察：SC2 冷启动到进对局约 5-6s；留出充足余量。
_LAUNCH_TIMEOUT: float = 120.0

# watchdog 间隔（秒）：父进程轮询子进程存活的频率。
_WATCHDOG_INTERVAL: float = 1.0


def _child_entry(
    config: GameConfig,
    up_q: multiprocessing.Queue,  # type: ignore[type-arg]
    _down_q: multiprocessing.Queue,  # type: ignore[type-arg]  # M1.4+ 用
    log_level: int,
) -> None:
    """子进程入口：在子进程内构造 bot、调 run_game()，往 up_q 推状态事件。

    父进程只传 picklable 的 GameConfig（基本类型），此函数负责 import ares 并
    构造 VoiceCraftBot / SmokeBot。M1.2 用最小 stub bot 验证进程通信。
    """
    # 子进程需要重新配置日志（spawn 后父进程 logging state 不继承）
    logging.basicConfig(level=log_level)
    child_log = logging.getLogger(__name__)

    def _put(sc2: Sc2State, bot: BotState, detail: str = "") -> None:
        """向上行队列推一条状态消息。queue 是进程安全的。"""
        try:
            up_q.put_nowait({"sc2": sc2, "bot": bot, "detail": detail})
        except Exception as exc:
            child_log.warning("up_queue_put_failed: %s", exc)

    _put("launching", "idle")

    try:
        from sc2 import maps
        from sc2.data import Difficulty, Race
        from sc2.main import run_game
        from sc2.player import Bot, Computer
    except ImportError as exc:
        _put("crashed", "error", detail=f"ImportError: {exc}")
        return

    try:
        bot_class = _build_bot_class(_put)
    except Exception as exc:
        _put("crashed", "error", detail=f"bot_class构造失败: {type(exc).__name__}: {exc}")
        return

    bot_instance = bot_class()

    try:
        sc2_map = maps.get(config.map_name)
    except Exception as exc:
        _put("crashed", "error", detail=f"地图未找到 '{config.map_name}': {exc}")
        return

    try:
        _put("launching", "running")
        run_game(
            sc2_map,
            [
                Bot(Race.Protoss, bot_instance, name="VoiceCraft"),
                Computer(
                    Race[config.opponent_race],
                    Difficulty[config.opponent_difficulty],
                ),
            ],
            realtime=config.realtime,
        )
        _put("ended", "idle")
    except Exception as exc:
        _put("crashed", "error", detail=f"run_game失败: {type(exc).__name__}: {exc}")


def _build_bot_class(
    put_status: Any,
) -> type:
    """在子进程内构造 bot 类。M1.2 用最小 stub，M1.5 替换成 VoiceCraftBot。

    put_status 是子进程内的 _put 闭包（不跨进程边界传递，只在 _child_entry 内部用）。
    ares / sc2 只在子进程内 import（mypy 设 ignore_missing_imports）。
    """
    try:
        from ares import AresBot
    except ImportError:
        # ares 未装：退到最小 python-sc2 Bot（M1.2 smoke 环境里 ares 是可选的）
        from sc2.bot_ai import BotAI as AresBot

    class _M12Bot(AresBot):  # type: ignore[misc]
        """M1.2 stub bot：仅负责在 on_start / on_step / on_end 推状态。

        M1.5 会把这里替换成真正的 VoiceCraftBot。
        """

        async def on_start(self) -> None:
            """进对局时推 in_game → playing。"""
            if hasattr(super(), "on_start"):
                await super().on_start()
            put_status("in_game", "running")
            put_status("playing", "running")

        async def on_step(self, iteration: int) -> None:
            """每 tick 推进，M1.2 不做任何操作。"""
            if hasattr(super(), "on_step"):
                await super().on_step(iteration)

        async def on_end(self, game_result: Any) -> None:
            """游戏结束时推 ended。"""
            put_status("ended", "idle")

    return _M12Bot


# -----------------------------------------------------------------------
# 辅助函数（公开，供 ws.py 和测试复用）
# -----------------------------------------------------------------------


def _apply_raw_dict(
    raw: dict[str, str],
    current_sc2: Sc2State,
    current_bot: BotState,
) -> tuple[Sc2State, BotState, str]:
    """从上行队列的 raw dict 提取 (sc2, bot, detail)，缺字段 fallback 到当前值。"""
    sc2 = raw.get("sc2", current_sc2)
    bot = raw.get("bot", current_bot)
    detail = raw.get("detail", "")
    return sc2, bot, detail


def _build_game_status_frame_dict(status: GameStatus) -> dict[str, object]:
    """把 GameStatus 转成 game_status 帧的 dict（不含 JSON 序列化）。"""
    return {
        "type": "game_status",
        "ts": round(status.ts, 3),
        "link": "connected",
        "sc2": status.sc2,
        "bot": status.bot,
        "detail": status.detail,
    }


# -----------------------------------------------------------------------
# 主类
# -----------------------------------------------------------------------


class GameProcess:
    """管一局游戏子进程的生命周期。bot service 持有一个实例。

    用法::

        gp = GameProcess()
        config = GameConfig(map_name="Goldenaura LE", realtime=True)
        gp.start(config)

        async for status in gp.status_events():
            ws.send(game_status_frame(status))

        await gp.stop()

    线程安全性：start / stop 必须从同一个 asyncio event loop 调。
    """

    def __init__(self) -> None:
        self._proc: multiprocessing.Process | None = None
        self._up_q: multiprocessing.Queue[dict[str, str]] | None = None
        self._down_q: multiprocessing.Queue[dict[str, Any]] | None = None
        self._sc2_state: Sc2State = "idle"
        self._bot_state: BotState = "idle"
        self._log = logger.bind(component="game_process")

    @property
    def status(self) -> GameStatus:
        """当前状态快照（同步读取，不阻塞）。"""
        return GameStatus(sc2=self._sc2_state, bot=self._bot_state)

    @property
    def is_running(self) -> bool:
        """子进程是否还活着。"""
        return self._proc is not None and self._proc.is_alive()

    def start(self, config: GameConfig) -> None:
        """Spawn 子进程，开始拉 SC2。

        如果已有进程在跑，先 stop 再 start（防止孤儿进程）。
        """
        if self._proc is not None and self._proc.is_alive():
            self._log.warning("game_process_already_running_force_stop")
            self._terminate_and_join()

        ctx = multiprocessing.get_context("spawn")
        self._up_q = ctx.Queue()
        self._down_q = ctx.Queue()

        self._sc2_state = "launching"
        self._bot_state = "idle"

        # SpawnContext.Process 返回 SpawnProcess，是 multiprocessing.Process 子类；
        # multiprocessing stubs 的类型窄化不够精确，用 cast 告诉 mypy 这里是 Process。
        proc: multiprocessing.Process = cast(
            multiprocessing.Process,
            ctx.Process(
                target=_child_entry,
                args=(config, self._up_q, self._down_q, logging.WARNING),
                daemon=True,  # daemon：父进程退时自动 kill，防孤儿
                name="voicecraft-sc2",
            ),
        )
        proc.start()
        self._proc = proc
        self._log.info(
            "game_process_started",
            pid=proc.pid,
            map_name=config.map_name,
            realtime=config.realtime,
        )

    async def status_events(self) -> AsyncIterator[GameStatus]:
        """上行流：持续 yield GameStatus，直到子进程结束或出错。

        asyncio 侧用 run_in_executor 桥接阻塞 Queue.get()，不阻塞 event loop。
        """
        proc = self._proc
        q = self._up_q
        if proc is None or q is None:
            return

        loop = asyncio.get_running_loop()

        def _blocking_get() -> dict[str, str] | None:
            """在 executor 线程里阻塞等队列消息（最多 1s timeout 轮一次）。"""
            try:
                return q.get(timeout=_WATCHDOG_INTERVAL)
            except queue.Empty:
                return None

        while True:
            # 非阻塞：先把队列里积压的消息全部处理
            try:
                raw = q.get_nowait()
                status = self._apply_raw(raw)
                yield status
                continue
            except queue.Empty:
                pass

            # 队列空了，检查进程状态
            if not proc.is_alive():
                # 进程已退出：兜底判定
                exit_code = proc.exitcode
                if exit_code != 0 and self._sc2_state not in ("ended", "crashed"):
                    self._sc2_state = "crashed"
                    self._bot_state = "error"
                    yield GameStatus(
                        sc2="crashed",
                        bot="error",
                        detail=f"子进程非正常退出，exitcode={exit_code}",
                    )
                break

            # 阻塞等（在 executor 线程，不卡 event loop）
            result: dict[str, str] | None = await loop.run_in_executor(None, _blocking_get)
            if result is not None:
                status = self._apply_raw(result)
                yield status

    def send_command(self, cmd: dict[str, Any]) -> None:
        """下行通道：发指令到子进程（M1.4+ 用）。"""
        if self._down_q is None:
            self._log.warning("send_command_no_queue")
            return
        try:
            self._down_q.put_nowait(cmd)
        except Exception as exc:
            self._log.warning("send_command_failed", error=str(exc))

    async def stop(self) -> None:
        """善后：先请求 leave，等几秒，再强杀，最后 join。"""
        if self._proc is None:
            return

        self._log.info("game_process_stopping", pid=self._proc.pid)

        # 先发 leave 信号（M1.4+ 实现；M1.2 阶段直接进入强杀）
        self.send_command({"type": "leave"})

        # 给子进程最多 5s 自然退出
        proc = self._proc
        loop = asyncio.get_event_loop()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                loop.run_in_executor(None, lambda: proc.join(timeout=5)),
                timeout=6.0,
            )

        self._terminate_and_join()

    def _terminate_and_join(self) -> None:
        """强杀子进程（terminate + join）。"""
        if self._proc is None:
            return
        try:
            if self._proc.is_alive():
                self._proc.terminate()
                self._proc.join(timeout=3)
                if self._proc.is_alive():
                    self._proc.kill()
                    self._proc.join(timeout=2)
        except Exception as exc:
            self._log.warning("game_process_terminate_error", error=str(exc))
        finally:
            self._proc = None
            self._up_q = None
            self._down_q = None
            self._log.info("game_process_terminated")

    def _apply_raw(self, raw: dict[str, str]) -> GameStatus:
        """解析上行队列消息，更新内部状态，返回 GameStatus。"""
        sc2, bot, detail = _apply_raw_dict(raw, self._sc2_state, self._bot_state)
        self._sc2_state = sc2
        self._bot_state = bot
        status = GameStatus(sc2=sc2, bot=bot, detail=detail)
        self._log.debug(
            "game_status_update",
            sc2=sc2,
            bot=bot,
            detail=detail,
        )
        return status
