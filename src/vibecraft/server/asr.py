"""FunASR 流式 ASR 引擎 + 会话管理。

组件：
  AsrEngine  —— 进程内 funasr 模型，惰性加载，graceful（无 funasr 时禁用语音功能）。
  AsrSession —— 一段录音（按住~松手）的流式状态；feed/finalize/cancel。

设计要点：
  - funasr import 失败（未装）→ available=False，不抛异常，server 正常启动。
  - 模型只在第一次 create_session() 时初始化（懒加载），不拖慢 server 启动。
  - 推理是 CPU-bound 阻塞调用 → run_in_executor(None, ...)，不卡 event loop。
  - 构造函数接受可选的 model_factory callable，便于单测注入假模型。

热词文件：config/asr_hotwords.txt，格式 "词 权重" 或 "词"（每行一个），
失败时静默忽略（graceful）。

参考：funasr paraformer-zh-streaming 2pass 流式用法。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

# funasr 是可选依赖（torch ~GB），顶层 import 失败时 graceful。
# 注意：try/except 必须在模块顶层，不能在函数内 — 这样无论如何模块本身都能 import。
try:
    from funasr import AutoModel as _FunasrAutoModel  # type: ignore[import-untyped]

    _FUNASR_AVAILABLE = True
except ImportError:
    _FunasrAutoModel = None  # type: ignore[assignment,misc]
    _FUNASR_AVAILABLE = False

# --------------------------------------------------------------------------
# paraformer-zh-streaming 2pass 参数
# --------------------------------------------------------------------------
_MODEL_ID = "paraformer-zh-streaming"
# chunk_size=[0, 10, 5]：非流式 look-ahead=0，块=10×60ms=600ms，右文=5×60ms
_CHUNK_SIZE: list[int] = [0, 10, 5]
_ENCODER_LOOKBACK = 4  # encoder self-attention 往前看的块数
_DECODER_LOOKBACK = 1  # decoder cross-attention 往前看的 encoder 块数
# 每次喂模型的步进采样数 = chunk_size[1] × 960 = 10 × 960 = 9600（600ms @ 16kHz）。
# funasr 流式 generate 必须按这个粒度喂 —— 手机 worklet 每帧只发 100ms(1600 采样)，
# 直接拿 100ms 调 generate 会让 chunk 边界全乱、整句只识别出几个字(2026-06-10 真机
# 实测 partial 长度 1/4、final 5~7)。所以 session 内攒够一个 stride 再喂。
_CHUNK_STRIDE_SAMPLES = _CHUNK_SIZE[1] * 960
# 热词文件路径（相对于工作目录，即项目根）
_HOTWORD_PATH = "config/asr_hotwords.txt"

# --------------------------------------------------------------------------
# 英文模型（SenseVoiceSmall，多语、**非流式/离线**；spike 实测 SC2 英文指令近乎完美）
# 非流式：松手后整段一次性解码（无逐字 partial）。输出带 <|en|><|EMO|>… 标签 + 尾标点，需后处理。
# --------------------------------------------------------------------------
_MODEL_ID_EN = "iic/SenseVoiceSmall"
# 离线 buffer 封顶（秒）：SenseVoice ~30s 设计上限 + 超长 clip CPU 解码慢。SC2 指令都 <10s，
# 25s 安全；超限丢最旧采样（保留最近 25s）。
_OFFLINE_MAX_SECONDS = 25.0
_OFFLINE_MAX_SAMPLES = int(_OFFLINE_MAX_SECONDS * 16000)
# 支持的 locale → 模型 id。zh=流式 paraformer，en=离线 SenseVoice。
_LOCALES = ("zh", "en")


def _strip_sensevoice(text: str) -> str:
    """SenseVoice 输出后处理：剥 `<|en|><|EMO|>…` 标签 + 尾标点/首尾空白 → 纯指令文本。

    优先用 funasr 官方 `rich_transcription_postprocess`（覆盖所有 tag/emoji/event 变体）；
    不可用时回退正则。两者都失败 → 原样返回（不崩）。
    """
    if not text:
        return ""
    try:
        from funasr.utils.postprocess_utils import (  # type: ignore[import-untyped]
            rich_transcription_postprocess,
        )

        cleaned = str(rich_transcription_postprocess(text))
    except Exception:
        import re

        cleaned = re.sub(r"<\|[^|]*\|>", "", text)
    # 去尾句末标点（官方 postprocess 会保留尾句号；指令文本与中文 ASR 保持一致，不带尾标点）。
    return cleaned.strip().rstrip(".!?。！？")


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------


def _load_hotwords(path: str) -> str:
    """读热词文件，返回空格拼接的词串（funasr hotword 参数格式）。

    文件格式：每行 "词 权重" 或 "词"，# 开头为注释。
    文件不存在时返回空字符串（graceful）。
    """
    p = Path(path)
    if not p.exists():
        logger.debug("asr_hotwords_file_not_found", path=path)
        return ""
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        words = [parts[0] for line in lines if (parts := line.split()) and not line.startswith("#")]
        return " ".join(words)
    except Exception:
        logger.warning("asr_hotwords_load_failed", path=path, exc_info=True)
        return ""


def _extract_text(result: Any) -> str:
    """从 funasr generate 结果提取文字，容错。

    funasr 返回 list[dict]，e.g. [{"key": "...", "text": "识别结果", ...}]。
    空列表 / 无 text 字段 / 非预期类型 → 返回 ""。
    """
    if not result or not isinstance(result, list):
        return ""
    first = result[0]
    if isinstance(first, dict):
        return str(first.get("text", "")).strip()
    return ""


# --------------------------------------------------------------------------
# AsrEngine
# --------------------------------------------------------------------------


class AsrEngine:
    """进程内 funasr 引擎，**按 locale 双模型**，各自惰性加载。

    - `zh` → `paraformer-zh-streaming`（流式，逐块吐 partial + 热词纠偏）。
    - `en` → `SenseVoiceSmall`（离线，松手后整段一次解码；多语，无热词）。

    每个 locale 独立的 model / load lock / loaded flag / available，互不阻塞。
    `model_factory`：可注入替换，默认用真 funasr.AutoModel（单测注入假 factory，
    按 `model=` 参数分支返回不同假模型）。
    """

    def __init__(self, model_factory: Callable[..., Any] | None = None) -> None:
        # 可注入 factory（None = 用真 funasr）
        self._factory = model_factory
        # per-locale 模型实例（None = 尚未加载 / 加载失败）
        self._models: dict[str, Any] = dict.fromkeys(_LOCALES)
        # per-locale load lock：并发 create_session 只初始化一次（各 locale 独立）。
        # 必须每个 locale 一把**独立** Lock —— 不能用 dict.fromkeys（会共享同一个对象）。
        self._load_locks: dict[str, asyncio.Lock] = {lc: asyncio.Lock() for lc in _LOCALES}
        # per-locale 是否已尝试过加载
        self._loaded: dict[str, bool] = dict.fromkeys(_LOCALES, False)
        # per-locale 三态可用性：None=未检查，True=可用，False=加载失败
        self._available_map: dict[str, bool | None] = dict.fromkeys(_LOCALES)
        self._log = logger.bind(component="asr_engine")

    # ------------------------------------------------------------------
    # available
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """ASR 功能总开关（= 主路径 zh 模型可用性，向后兼容）。

        ws.py 应按玩家语言用 `available_for(locale)` 做更精确的门控；这个无参属性保留
        给"引擎整体能不能干活"的粗判（默认 create_session 也走 zh）。
        """
        return self.available_for("zh")

    def available_for(self, locale: str) -> bool:
        """指定 locale 的模型是否可用（已成功加载，或乐观未加载）。"""
        lc = locale if locale in _LOCALES else "zh"
        v = self._available_map[lc]
        if v is not None:
            return v
        if self._factory is not None:
            return True
        return _FUNASR_AVAILABLE

    # ------------------------------------------------------------------
    # 惰性加载（per-locale）
    # ------------------------------------------------------------------

    def _build_model(self, factory: Callable[..., Any], locale: str) -> Any:
        """在 executor 线程里真正构造模型（CPU-bound，可能下载 + 编译）。"""
        if locale == "en":
            return factory(model=_MODEL_ID_EN, disable_update=True)
        hotwords = _load_hotwords(_HOTWORD_PATH)
        return factory(model=_MODEL_ID, hotword=hotwords)

    async def _ensure_loaded(self, locale: str) -> bool:
        """惰性加载指定 locale 的模型；首次调用真正初始化，之后返回缓存结果。"""
        lc = locale if locale in _LOCALES else "zh"
        if self._loaded[lc]:
            return self._available_map[lc] is True

        async with self._load_locks[lc]:
            if self._loaded[lc]:  # 双重检查
                return self._available_map[lc] is True

            factory = self._factory
            if factory is None:
                if not _FUNASR_AVAILABLE:
                    self._log.warning("asr_funasr_not_installed", locale=lc)
                    self._available_map[lc] = False
                    self._loaded[lc] = True
                    return False
                factory = _FunasrAutoModel

            model_id = _MODEL_ID_EN if lc == "en" else _MODEL_ID
            try:
                loop = asyncio.get_running_loop()
                model = await loop.run_in_executor(None, lambda: self._build_model(factory, lc))
                self._models[lc] = model
                self._available_map[lc] = True
                self._loaded[lc] = True
                self._log.info("asr_model_loaded", model=model_id, locale=lc)
                return True
            except Exception:
                self._log.warning("asr_model_load_failed", model=model_id, locale=lc, exc_info=True)
                self._available_map[lc] = False
                self._loaded[lc] = True
                return False

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    async def warmup(self) -> bool:
        """预热**中文**模型（server 启动时后台调用）。

        2026-06-13 用户实测：惰性加载导致**第一句语音必失败**。启动即预热后首句即可用。
        英文模型不在启动盲目预热（省 ~1GB 内存，多数局是 zh）——见 `warmup_en`。
        """
        return await self._ensure_loaded("zh")

    async def warmup_en(self) -> bool:
        """预热**英文**模型（ws 握手见 locale=en 时后台调用）。

        SenseVoiceSmall **首次**从 modelscope 下载 ~1GB（~数分钟），之后缓存秒载。首次部署
        建议先跑 `scripts/prefetch_asr_en.py` 预拉，避免第一个英文玩家 finalize 卡在下载上。
        """
        return await self._ensure_loaded("en")

    async def create_session(self, locale: str = "zh") -> AsrSession | None:
        """按 locale 创建 ASR 会话：zh→流式 / en→离线。不可用时返回 None（graceful）。"""
        lc = locale if locale in _LOCALES else "zh"
        ok = await self._ensure_loaded(lc)
        model = self._models[lc]
        if not ok or model is None:
            return None
        if lc == "en":
            return OfflineAsrSession(model=model, engine=self, language="en")
        return StreamingAsrSession(model=model, engine=self)

    async def run_in_executor(self, fn: Callable[[], Any]) -> Any:
        """把 CPU-bound 推理放 ThreadPoolExecutor，不阻塞 event loop。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn)


# --------------------------------------------------------------------------
# AsrSession
# --------------------------------------------------------------------------


class AsrSession:
    """一段录音（按住~松手）的 ASR 会话基类。

    统一接口：`feed(pcm)->部分草稿|None`、`finalize()->整句`、`cancel()`。
    每个手机连接同一时刻只有一个活跃 session。具体两种实现：
      - `StreamingAsrSession`（zh）：每攒够一个 stride 就喂模型，逐块吐 partial。
      - `OfflineAsrSession`（en）：feed 只 append buffer，finalize 整段一次解码。
    """

    async def feed(self, pcm: bytes) -> str | None:  # pragma: no cover - 抽象
        raise NotImplementedError

    async def finalize(self) -> str:  # pragma: no cover - 抽象
        raise NotImplementedError

    def cancel(self) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError


class StreamingAsrSession(AsrSession):
    """流式会话（zh / paraformer-zh-streaming）：逐块喂模型，partial 即时回。"""

    def __init__(self, model: Any, engine: AsrEngine) -> None:
        self._model = model
        self._engine = engine
        # funasr streaming cache（encoder/decoder 状态，跨帧累积）
        self._cache: dict[str, Any] = {}
        # 上滑取消后拒绝一切后续操作
        self._cancelled = False
        # 累积全句草稿：paraformer-zh-streaming 每次 generate 只吐**当前块**的
        # 增量文字（"派一个"→"农民"→"探路" 逐块），不是累计句。必须在 session
        # 内把每块拼起来，否则 partial/final 都只剩最后一块（2026-06-09 真机踩坑：
        # "派一个农民出去探路" 最后只剩"探路"）。
        self._text = ""
        # PCM 余量缓冲（float32）：手机每帧 100ms，攒够一个 600ms stride 再喂模型。
        # 见 _CHUNK_STRIDE_SAMPLES 注释 —— 不攒满直接喂会让识别支离破碎。
        self._buf: np.ndarray = np.zeros(0, dtype=np.float32)
        self._log = logger.bind(component="asr_session")

    def _run_generate(self, audio: np.ndarray, is_final: bool) -> str:
        """同步调一次 model.generate（在 executor 线程里跑），返回提取的增量文字。

        失败返回 ""（graceful，不抛）。audio 为 float32 [-1,1]。
        """
        model = self._model
        cache = self._cache
        chunk_size = _CHUNK_SIZE
        enc_lb = _ENCODER_LOOKBACK
        dec_lb = _DECODER_LOOKBACK
        try:
            result = model.generate(
                input=audio,
                cache=cache,
                is_final=is_final,
                chunk_size=chunk_size,
                encoder_chunk_look_back=enc_lb,
                decoder_chunk_look_back=dec_lb,
            )
        except Exception:
            self._log.warning("asr_generate_error", is_final=is_final, exc_info=True)
            return ""
        return _extract_text(result)

    async def feed(self, pcm: bytes) -> str | None:
        """喂一帧 16kHz mono PCM16 字节，返回**累积到目前为止的全句**草稿；

        本帧没攒够一个 600ms stride（或攒够后模型没吐新字）时返回 None。

        pcm：原始 PCM16 小端字节，长度 = 采样数 × 2。
        手机 worklet 每帧约 100ms（1600 samples × 2 = 3200 bytes @ 16kHz），
        攒满 6 帧（600ms）才真正喂一次模型 —— 见 _CHUNK_STRIDE_SAMPLES。
        """
        if self._cancelled:
            return None

        # PCM16 → float32 归一化（funasr 要求 float32 输入，范围 [-1, 1]）
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        self._buf = np.concatenate([self._buf, audio])

        # 不够一个 stride → 先攒着，不调模型（避免碎块喂模型识别支离破碎）
        if len(self._buf) < _CHUNK_STRIDE_SAMPLES:
            return None

        # 攒够了：取出整数个 stride 逐块喂，余量留到下次
        new_text = ""
        while len(self._buf) >= _CHUNK_STRIDE_SAMPLES:
            chunk = self._buf[:_CHUNK_STRIDE_SAMPLES]
            self._buf = self._buf[_CHUNK_STRIDE_SAMPLES:]
            new_text += await self._engine.run_in_executor(
                lambda c=chunk: self._run_generate(c, is_final=False)
            )

        if not new_text:
            return None  # 本批没新内容 → 不推（避免重复刷同一串）
        self._text += new_text  # 拼进全句
        return self._text

    async def finalize(self) -> str:
        """句末定稿，返回**累积的全句**文字。cancel 后返回空字符串。

        把缓冲里剩下的余量（不足一个 stride 的尾巴）+ is_final=True 一起 flush，
        触发 paraformer 出最后一块字，拼进全句后返回整句。
        """
        if self._cancelled:
            return ""

        tail_audio = self._buf  # 余量（可能为空）
        self._buf = np.zeros(0, dtype=np.float32)
        tail = await self._engine.run_in_executor(
            lambda: self._run_generate(tail_audio, is_final=True)
        )
        if tail:
            self._text += tail  # flush 出的残留增量拼进全句
        return self._text

    def cancel(self) -> None:
        """丢弃本段录音（上滑取消）；之后 feed 返回 None，finalize 返回空字符串。"""
        self._cancelled = True
        self._log.info("asr_session_cancelled")


# --------------------------------------------------------------------------
# OfflineAsrSession（en / SenseVoiceSmall）
# --------------------------------------------------------------------------


class OfflineAsrSession(AsrSession):
    """离线会话（en / SenseVoiceSmall，非流式）。

    feed 只把采样**追加进 buffer**（不每块推理，无逐字 partial）；finalize 把整段 buffer
    一次 `model.generate(language=...)` → 剥 SenseVoice 标签/标点 → 返回纯指令文本。
    buffer 封顶 `_OFFLINE_MAX_SAMPLES`（~25s）：超限丢最旧采样，避免超长 clip 解码慢/质量退化。
    """

    def __init__(self, model: Any, engine: AsrEngine, language: str = "en") -> None:
        self._model = model
        self._engine = engine
        self._language = language
        self._cancelled = False
        self._buf: np.ndarray = np.zeros(0, dtype=np.float32)
        self._log = logger.bind(component="asr_session_offline")

    async def feed(self, pcm: bytes) -> str | None:
        """累积一帧 16kHz mono PCM16 字节；离线模式**不出 partial**，恒返回 None。

        超过 buffer 上限时丢最旧采样（保留最近 ~25s），防超长录音拖慢 finalize 解码。
        """
        if self._cancelled:
            return None
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        self._buf = np.concatenate([self._buf, audio])
        if len(self._buf) > _OFFLINE_MAX_SAMPLES:
            # 丢最旧，保留最近一段（SenseVoice 短语音设计；指令都很短）
            self._buf = self._buf[-_OFFLINE_MAX_SAMPLES:]
        return None  # 离线模式无逐字 partial（前端显示"识别中…"占位）

    def _run_generate(self, audio: np.ndarray) -> str:
        """整段一次 generate（executor 线程里跑）；剥 SenseVoice 标签后返回纯文本。失败返回 ""。"""
        try:
            result = self._model.generate(input=audio, language=self._language, use_itn=True)
        except Exception:
            self._log.warning("asr_offline_generate_error", exc_info=True)
            return ""
        return _strip_sensevoice(_extract_text(result))

    async def finalize(self) -> str:
        """句末整段解码，返回纯指令文本。cancel 后 / 空录音 返回空字符串。"""
        if self._cancelled or len(self._buf) == 0:
            return ""
        audio = self._buf
        self._buf = np.zeros(0, dtype=np.float32)
        text: str = await self._engine.run_in_executor(lambda: self._run_generate(audio))
        return text

    def cancel(self) -> None:
        """丢弃本段录音（上滑取消）。"""
        self._cancelled = True
        self._buf = np.zeros(0, dtype=np.float32)
        self._log.info("asr_offline_session_cancelled")
