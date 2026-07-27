"""FunASR 引擎/会话单测。

全部使用注入假 model factory，不依赖真实 funasr 安装。
pytest asyncio_mode = "auto"（pyproject.toml 全局配置）。
"""

from __future__ import annotations

from unittest.mock import patch

# asr 模块必须能 import，即使没装 funasr
from vibecraft.server.asr import _CHUNK_STRIDE_SAMPLES, AsrEngine

# 一个完整 600ms stride 的静音 PCM16（=_CHUNK_STRIDE_SAMPLES 采样 × 2 字节）。
# feed 必须攒够一个 stride 才会真正喂模型，所以单测要按 stride 粒度喂。
_CHUNK_PCM = b"\x00\x00" * _CHUNK_STRIDE_SAMPLES

# ---------------------------------------------------------------------------
# 测试桩
# ---------------------------------------------------------------------------


class _FakeModel:
    """假 funasr AutoModel。

    generate(is_final=False) 依次返回 partials 里的文字（循环）。
    generate(is_final=True)  返回 final。
    """

    def __init__(self, partials: list[str], final: str) -> None:
        self.partials = partials
        self.final = final
        self._call_idx = 0

    def generate(
        self,
        input: object,
        cache: dict,
        is_final: bool = False,
        **kwargs: object,
    ) -> list[dict[str, object]]:
        if is_final:
            return [{"text": self.final}]
        text = self.partials[self._call_idx % len(self.partials)] if self.partials else ""
        self._call_idx += 1
        return [{"text": text}]


def _make_factory(
    partials: list[str] | None = None,
    final: str = "最终文字",
) -> tuple[object, _FakeModel]:
    """返回 (factory_callable, model_instance)。

    factory 被调用时返回同一 _FakeModel 实例；外部通过 factory.call_count 检查是否被调用。
    """
    model = _FakeModel(partials=partials or ["草稿"], final=final)
    calls: list[int] = [0]

    def factory(**kwargs: object) -> _FakeModel:  # type: ignore[misc]
        calls[0] += 1
        return model

    factory.calls = calls  # type: ignore[attr-defined]
    return factory, model


# ---------------------------------------------------------------------------
# AsrSession：feed / finalize / cancel
# ---------------------------------------------------------------------------


class TestAsrSessionFeed:
    async def test_feed_accumulates_full_sentence(self) -> None:
        """feed 每次返回**累积到目前为止的全句**（流式分片拼接），不是单块。

        paraformer 每块只吐增量，session 必须拼起来 —— 否则长句只剩最后一块
        （2026-06-09 真机：'派一个农民出去探路' 只剩 '探路'）。
        每次喂一个完整 600ms stride → 触发一次 generate。
        """
        factory, _model = _make_factory(partials=["派一个", "农民", "出去探路"], final="")
        engine = AsrEngine(model_factory=factory)
        session = await engine.create_session()
        assert session is not None

        assert await session.feed(_CHUNK_PCM) == "派一个"
        assert await session.feed(_CHUNK_PCM) == "派一个农民"
        assert await session.feed(_CHUNK_PCM) == "派一个农民出去探路"

    async def test_feed_buffers_until_full_chunk(self) -> None:
        """不足一个 600ms stride 的小帧先攒着，不调模型，返回 None。

        手机 worklet 每帧 100ms，必须攒够 6 帧(600ms)才喂 —— 碎块喂模型会
        让识别支离破碎(2026-06-10 真机：整句只识别出几个字)。
        """
        factory, model = _make_factory(partials=["不该出现"])
        engine = AsrEngine(model_factory=factory)
        session = await engine.create_session()
        assert session is not None

        # 喂 100ms (1600 采样) < 600ms stride → 攒着，不调模型
        assert await session.feed(b"\x00\x00" * 1600) is None
        assert model._call_idx == 0  # generate 没被调

    async def test_feed_assembles_small_frames_into_chunk(self) -> None:
        """连续小帧攒够一个 stride 后触发一次 generate，吐出累积文字。"""
        factory, model = _make_factory(partials=["攒够了"])
        engine = AsrEngine(model_factory=factory)
        session = await engine.create_session()
        assert session is not None

        # 1600 采样 × 6 = 9600 = 一个 stride；前 5 帧返回 None、不调模型
        for _ in range(5):
            assert await session.feed(b"\x00\x00" * 1600) is None
        assert model._call_idx == 0
        # 第 6 帧攒满 → 喂一次模型
        assert await session.feed(b"\x00\x00" * 1600) == "攒够了"
        assert model._call_idx == 1

    async def test_feed_empty_pcm_returns_none(self) -> None:
        """攒满 stride 但模型返回空串时，feed 返回 None（不重复推）。"""
        factory, _model = _make_factory(partials=[""])
        engine = AsrEngine(model_factory=factory)
        session = await engine.create_session()
        assert session is not None

        result = await session.feed(_CHUNK_PCM)
        assert result is None


class TestAsrSessionFinalize:
    async def test_finalize_returns_full_accumulated_sentence(self) -> None:
        """finalize 返回**累积全句** + flush 尾巴块，不只是 final 那一块。"""
        factory, _model = _make_factory(partials=["前面", "中间"], final="结尾")
        engine = AsrEngine(model_factory=factory)
        session = await engine.create_session()
        assert session is not None

        await session.feed(_CHUNK_PCM)  # "前面"
        await session.feed(_CHUNK_PCM)  # "前面中间"
        result = await session.finalize()  # flush 尾巴 "结尾" → 全句
        assert result == "前面中间结尾"

    async def test_finalize_without_feed_returns_final(self) -> None:
        """没 feed 直接 finalize 也应正常返回（空输入 is_final 的 flush 结果）。"""
        factory, _model = _make_factory(final="仅定稿")
        engine = AsrEngine(model_factory=factory)
        session = await engine.create_session()
        assert session is not None

        result = await session.finalize()
        assert result == "仅定稿"


class TestAsrSessionCancel:
    async def test_cancel_then_finalize_empty(self) -> None:
        """cancel 后 finalize 返回空字符串，不应出现 final 文字。"""
        factory, _model = _make_factory(final="不该出现的文字")
        engine = AsrEngine(model_factory=factory)
        session = await engine.create_session()
        assert session is not None

        await session.feed(_CHUNK_PCM)
        session.cancel()
        result = await session.finalize()
        assert result == ""

    async def test_cancel_then_feed_returns_none(self) -> None:
        """cancel 后继续 feed 应返回 None（不调模型）。"""
        factory, _model = _make_factory(partials=["不该出现"])
        engine = AsrEngine(model_factory=factory)
        session = await engine.create_session()
        assert session is not None

        session.cancel()
        result = await session.feed(_CHUNK_PCM)
        assert result is None

    async def test_cancel_stops_model_calls(self) -> None:
        """cancel 后的 feed 不应再调模型的 generate。"""
        factory, model = _make_factory(partials=["草稿"])
        engine = AsrEngine(model_factory=factory)
        session = await engine.create_session()
        assert session is not None

        session.cancel()
        calls_before = model._call_idx
        await session.feed(_CHUNK_PCM)  # 满一个 stride，若没 cancel 会调模型
        assert model._call_idx == calls_before  # 没增加


# ---------------------------------------------------------------------------
# AsrEngine：惰性加载
# ---------------------------------------------------------------------------


class TestAsrEngineLazyLoad:
    async def test_factory_not_called_on_init(self) -> None:
        """构造 AsrEngine 后、未调用任何方法前，factory 不被调用。"""
        factory, _ = _make_factory()
        _engine = AsrEngine(model_factory=factory)
        assert factory.calls[0] == 0  # type: ignore[attr-defined]

    async def test_factory_called_on_first_create_session(self) -> None:
        """第一次 create_session 时 factory 被调用一次。"""
        factory, _ = _make_factory()
        engine = AsrEngine(model_factory=factory)

        assert factory.calls[0] == 0  # type: ignore[attr-defined]
        session = await engine.create_session()
        assert session is not None
        assert factory.calls[0] == 1  # type: ignore[attr-defined]

    async def test_factory_called_only_once_multiple_sessions(self) -> None:
        """多次 create_session，factory 只调用一次（模型复用）。"""
        factory, _ = _make_factory()
        engine = AsrEngine(model_factory=factory)

        await engine.create_session()
        await engine.create_session()
        await engine.create_session()
        assert factory.calls[0] == 1  # type: ignore[attr-defined]

    async def test_all_sessions_share_same_model(self) -> None:
        """多个 session 应共享同一个模型实例（相同 _model 引用）。"""
        factory, model = _make_factory()
        engine = AsrEngine(model_factory=factory)

        s1 = await engine.create_session()
        s2 = await engine.create_session()
        assert s1 is not None
        assert s2 is not None
        # 通过实际 feed 验证两个 session 用的是同一个 _FakeModel（call_idx 累积）
        # 各喂一个完整 stride → 各触发一次 generate
        await s1.feed(_CHUNK_PCM)
        await s2.feed(_CHUNK_PCM)
        assert model._call_idx == 2  # 两次 feed 都算进同一个 model


# ---------------------------------------------------------------------------
# AsrEngine：available + graceful 降级
# ---------------------------------------------------------------------------


class TestAsrEngineAvailable:
    def test_available_true_with_injected_factory(self) -> None:
        """注入 factory 时，available 在加载前就是 True（乐观判断）。"""
        factory, _ = _make_factory()
        engine = AsrEngine(model_factory=factory)
        assert engine.available is True

    def test_available_false_when_funasr_not_installed(self) -> None:
        """模拟 funasr 未安装（_FUNASR_AVAILABLE=False）+ 无 factory → available=False。"""
        import vibecraft.server.asr as asr_mod

        with patch.object(asr_mod, "_FUNASR_AVAILABLE", False):
            engine = AsrEngine()  # 无 factory
            assert engine.available is False

    async def test_factory_import_error_graceful(self) -> None:
        """factory 抛 ImportError → create_session 返回 None + available 变 False，不崩。"""

        def bad_factory(**kwargs: object) -> object:
            raise ImportError("funasr not installed")

        engine = AsrEngine(model_factory=bad_factory)
        assert engine.available is True  # 加载前乐观

        session = await engine.create_session()  # 不应抛异常
        assert session is None
        assert engine.available is False

    async def test_factory_generic_error_graceful(self) -> None:
        """factory 抛其他异常（如模型下载失败）→ 同样 graceful。"""

        def broken_factory(**kwargs: object) -> object:
            raise RuntimeError("模型下载失败")

        engine = AsrEngine(model_factory=broken_factory)
        session = await engine.create_session()
        assert session is None
        assert engine.available is False

    async def test_create_session_none_when_funasr_unavailable(self) -> None:
        """funasr 未安装 + 无 factory → create_session 返回 None。"""
        import vibecraft.server.asr as asr_mod

        with patch.object(asr_mod, "_FUNASR_AVAILABLE", False):
            engine = AsrEngine()
            session = await engine.create_session()
            assert session is None

    async def test_available_stays_true_after_successful_load(self) -> None:
        """成功加载后 available 仍为 True。"""
        factory, _ = _make_factory()
        engine = AsrEngine(model_factory=factory)
        await engine.create_session()
        assert engine.available is True


# ---------------------------------------------------------------------------
# 模块级 import 可行性（funasr 未装时不崩）
# ---------------------------------------------------------------------------


class TestModuleImport:
    def test_import_asr_module_does_not_raise(self) -> None:
        """asr 模块的 import 本身不应抛任何异常（即使 funasr 未安装）。"""
        import importlib

        # 重新 import 确认无副作用
        mod = importlib.import_module("vibecraft.server.asr")
        assert hasattr(mod, "AsrEngine")
        assert hasattr(mod, "AsrSession")

    def test_asr_engine_instantiation_does_not_raise(self) -> None:
        """AsrEngine() 构造不应抛异常。"""
        engine = AsrEngine()
        assert engine is not None


# ---------------------------------------------------------------------------
# 英文离线会话（OfflineAsrSession / SenseVoice）+ 按 locale 路由
# ---------------------------------------------------------------------------

from vibecraft.server.asr import (  # noqa: E402
    _OFFLINE_MAX_SAMPLES,
    OfflineAsrSession,
    StreamingAsrSession,
    _strip_sensevoice,
)

# 1s 静音 PCM16（16kHz × 2 字节）。
_ONE_SEC_PCM = b"\x00\x00" * 16000


class _FakeSenseVoice:
    """假 SenseVoiceSmall：generate(input, language, use_itn) 返回带标签的英文文本。"""

    def __init__(self, text: str = "<|en|><|NEUTRAL|><|Speech|>build two gateways.") -> None:
        self._text = text
        self.calls = 0
        self.last_samples = 0

    def generate(
        self, input: object, language: str = "en", use_itn: bool = True, **kw: object
    ) -> list[dict[str, object]]:
        self.calls += 1
        try:
            self.last_samples = len(input)  # type: ignore[arg-type]
        except TypeError:
            self.last_samples = 0
        return [{"text": self._text}]


def _dual_factory(zh_model: _FakeModel, en_model: _FakeSenseVoice) -> object:
    """按 model= 参数分支：en 模型 id → SenseVoice 假；否则 → 流式假。"""

    def factory(**kwargs: object) -> object:
        model_id = str(kwargs.get("model", ""))
        if "SenseVoice" in model_id:
            return en_model
        return zh_model

    return factory


class TestStripSensevoice:
    def test_strips_tags_and_trailing_punct(self) -> None:
        out = _strip_sensevoice("<|en|><|NEUTRAL|><|Speech|>build two gateways.")
        assert "build two gateways" in out
        assert "<|" not in out

    def test_empty_returns_empty(self) -> None:
        assert _strip_sensevoice("") == ""


class TestLocaleRouting:
    async def test_zh_routes_to_streaming(self) -> None:
        zh, en = _FakeModel(partials=["草"], final="稿"), _FakeSenseVoice()
        engine = AsrEngine(model_factory=_dual_factory(zh, en))
        s = await engine.create_session("zh")
        assert isinstance(s, StreamingAsrSession)

    async def test_en_routes_to_offline(self) -> None:
        zh, en = _FakeModel(partials=["草"], final="稿"), _FakeSenseVoice()
        engine = AsrEngine(model_factory=_dual_factory(zh, en))
        s = await engine.create_session("en")
        assert isinstance(s, OfflineAsrSession)

    async def test_unknown_locale_falls_back_zh(self) -> None:
        zh, en = _FakeModel(partials=["草"], final="稿"), _FakeSenseVoice()
        engine = AsrEngine(model_factory=_dual_factory(zh, en))
        s = await engine.create_session("fr")
        assert isinstance(s, StreamingAsrSession)

    async def test_en_load_failure_isolated_from_zh(self) -> None:
        """en 模型加载失败不影响 zh：available_for 分别反映。"""

        def factory(**kwargs: object) -> object:
            if "SenseVoice" in str(kwargs.get("model", "")):
                raise RuntimeError("en download failed")
            return _FakeModel(partials=["草"], final="稿")

        engine = AsrEngine(model_factory=factory)
        assert await engine.create_session("en") is None
        assert engine.available_for("en") is False
        # zh 仍可用
        assert await engine.create_session("zh") is not None
        assert engine.available_for("zh") is True


class TestOfflineAsrSession:
    async def test_feed_returns_none_no_partial(self) -> None:
        """离线模式 feed 恒返回 None（无逐字 partial）。"""
        zh, en = _FakeModel(partials=["x"], final="y"), _FakeSenseVoice()
        engine = AsrEngine(model_factory=_dual_factory(zh, en))
        s = await engine.create_session("en")
        assert s is not None
        assert await s.feed(_ONE_SEC_PCM) is None
        assert en.calls == 0  # feed 不触发推理

    async def test_finalize_decodes_whole_buffer_and_strips(self) -> None:
        zh, en = _FakeModel(partials=["x"], final="y"), _FakeSenseVoice()
        engine = AsrEngine(model_factory=_dual_factory(zh, en))
        s = await engine.create_session("en")
        assert s is not None
        await s.feed(_ONE_SEC_PCM)
        await s.feed(_ONE_SEC_PCM)
        out = await s.finalize()
        assert en.calls == 1  # 整段一次解码
        assert out == "build two gateways"  # 标签/标点已剥

    async def test_finalize_empty_buffer_returns_empty(self) -> None:
        zh, en = _FakeModel(partials=["x"], final="y"), _FakeSenseVoice()
        engine = AsrEngine(model_factory=_dual_factory(zh, en))
        s = await engine.create_session("en")
        assert s is not None
        assert await s.finalize() == ""

    async def test_cancel_discards(self) -> None:
        zh, en = _FakeModel(partials=["x"], final="y"), _FakeSenseVoice()
        engine = AsrEngine(model_factory=_dual_factory(zh, en))
        s = await engine.create_session("en")
        assert s is not None
        await s.feed(_ONE_SEC_PCM)
        s.cancel()
        assert await s.feed(_ONE_SEC_PCM) is None
        assert await s.finalize() == ""
        assert en.calls == 0

    async def test_buffer_caps_at_max(self) -> None:
        """超过 ~25s 上限时保留最近段（buffer 不无限增长）。"""
        zh, en = _FakeModel(partials=["x"], final="y"), _FakeSenseVoice()
        engine = AsrEngine(model_factory=_dual_factory(zh, en))
        s = await engine.create_session("en")
        assert s is not None
        # 喂 40s（> 25s cap）
        for _ in range(40):
            await s.feed(_ONE_SEC_PCM)
        await s.finalize()
        assert en.last_samples <= _OFFLINE_MAX_SAMPLES
