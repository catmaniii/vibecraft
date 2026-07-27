"""Per-PID WASAPI process loopback 采集（Windows 10 20H1+ / Win11）。

任务 #516：多人局两台手机各听各的 SC2 实例声音。原 device loopback 抓的是
**整机混音**（两局声音混在一起），且共享 grabber 多消费者分帧造成破音。
本模块用 `ActivateAudioInterfaceAsync` + `AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK`
只采**指定 PID（含子进程树）**的渲染音频 —— 每个 SC2 实例一路独立采集，
互不混音、互不分帧。

使用约定（重要）：
- **只在 audio_grab 子进程里用**（native 崩溃隔离，见 audio_grab.py 头注释）。
- 必须在 **MTA COM 线程**调用：import 本模块（或 comtypes）前设
  ``sys.coinit_flags = 0``。模块顶部有防御性兜底，但消费方仍应显式设置。
- 采集格式由调用方指定（process loopback 模式下 GetMixFormat 不可用，
  引擎按请求格式转换）—— 我们直接要 48k/2ch/s16，零重采样。
- 目标进程不出声时 WASAPI **不产包**（event 不触发 / GetNextPacketSize=0），
  read() 返回 b""，由上层补静音。
"""

from __future__ import annotations

import contextlib
import ctypes
import sys
from ctypes import POINTER, wintypes
from typing import ClassVar

# comtypes 按 sys.coinit_flags 决定 CoInitializeEx 模式；ActivateAudioInterfaceAsync
# 要求 MTA(0)。若消费方忘了设，这里兜底（仅当 comtypes 尚未被 import 时有效）。
if "comtypes" not in sys.modules and not hasattr(sys, "coinit_flags"):
    sys.coinit_flags = 0  # type: ignore[attr-defined]  # COINIT_MULTITHREADED

import comtypes  # type: ignore[import-untyped]
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

# ---------------------------------------------------------------- 常量

VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
WAVE_FORMAT_PCM = 1
VT_BLOB = 0x41
_WAIT_OBJECT_0 = 0
# 共享模式缓冲 200ms（100ns 单位）；event 驱动下实际延迟远小于此
_BUFFER_DURATION_HNS = 2_000_000

_IID_IAudioClient = GUID("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
_IID_IAudioCaptureClient = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")

# ---------------------------------------------------------------- 结构体


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class _AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(ctypes.Structure):
    _fields_ = [
        ("TargetProcessId", wintypes.DWORD),
        ("ProcessLoopbackMode", ctypes.c_int),
    ]


class _AUDIOCLIENT_ACTIVATION_PARAMS(ctypes.Structure):
    _fields_ = [
        ("ActivationType", ctypes.c_int),
        ("ProcessLoopbackParams", _AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS),
    ]


class _BLOB(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.ULONG),
        ("pBlobData", ctypes.c_void_p),
    ]


class _PROPVARIANT(ctypes.Structure):
    """最小 PROPVARIANT：只需要 VT_BLOB 分支（union 里其余成员不用）。"""

    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("blob", _BLOB),
    ]


# ---------------------------------------------------------------- COM 接口


class IActivateAudioInterfaceAsyncOperation(IUnknown):  # type: ignore[misc]
    _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
    _methods_: ClassVar = [
        COMMETHOD(
            [],
            HRESULT,
            "GetActivateResult",
            (["out"], POINTER(HRESULT), "activateResult"),
            (["out"], POINTER(POINTER(IUnknown)), "activatedInterface"),
        ),
    ]


class IActivateAudioInterfaceCompletionHandler(IUnknown):  # type: ignore[misc]
    _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
    _methods_: ClassVar = [
        COMMETHOD(
            [],
            HRESULT,
            "ActivateCompleted",
            (["in"], POINTER(IActivateAudioInterfaceAsyncOperation), "activateOperation"),
        ),
    ]


class IAgileObject(IUnknown):  # type: ignore[misc]
    """marker 接口（无方法）。完成回调必须 agile，否则
    ActivateAudioInterfaceAsync 返回 E_ILLEGAL_METHOD_CALL(0x8000000E)。"""

    _iid_ = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
    _methods_: ClassVar[list[object]] = []


class IAudioClient(IUnknown):  # type: ignore[misc]
    _iid_ = _IID_IAudioClient
    _methods_: ClassVar = [
        COMMETHOD(
            [],
            HRESULT,
            "Initialize",
            (["in"], ctypes.c_int, "ShareMode"),
            (["in"], wintypes.DWORD, "StreamFlags"),
            (["in"], ctypes.c_longlong, "hnsBufferDuration"),
            (["in"], ctypes.c_longlong, "hnsPeriodicity"),
            (["in"], POINTER(WAVEFORMATEX), "pFormat"),
            (["in"], POINTER(GUID), "AudioSessionGuid"),
        ),
        COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(ctypes.c_uint32), "p")),
        COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(ctypes.c_longlong), "p")),
        COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(ctypes.c_uint32), "p")),
        COMMETHOD(
            [],
            HRESULT,
            "IsFormatSupported",
            (["in"], ctypes.c_int, "ShareMode"),
            (["in"], POINTER(WAVEFORMATEX), "pFormat"),
            (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppClosestMatch"),
        ),
        COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(POINTER(WAVEFORMATEX)), "pp")),
        COMMETHOD(
            [],
            HRESULT,
            "GetDevicePeriod",
            (["out"], POINTER(ctypes.c_longlong), "pDefault"),
            (["out"], POINTER(ctypes.c_longlong), "pMinimum"),
        ),
        COMMETHOD([], HRESULT, "Start"),
        COMMETHOD([], HRESULT, "Stop"),
        COMMETHOD([], HRESULT, "Reset"),
        COMMETHOD([], HRESULT, "SetEventHandle", (["in"], wintypes.HANDLE, "eventHandle")),
        COMMETHOD(
            [],
            HRESULT,
            "GetService",
            (["in"], POINTER(GUID), "riid"),
            (["out"], POINTER(POINTER(IUnknown)), "ppv"),
        ),
    ]


class IAudioCaptureClient(IUnknown):  # type: ignore[misc]
    _iid_ = _IID_IAudioCaptureClient
    _methods_: ClassVar = [
        COMMETHOD(
            [],
            HRESULT,
            "GetBuffer",
            (["out"], POINTER(POINTER(ctypes.c_byte)), "ppData"),
            (["out"], POINTER(ctypes.c_uint32), "pNumFramesToRead"),
            (["out"], POINTER(wintypes.DWORD), "pdwFlags"),
            (["in"], POINTER(ctypes.c_uint64), "pu64DevicePosition"),
            (["in"], POINTER(ctypes.c_uint64), "pu64QPCPosition"),
        ),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], ctypes.c_uint32, "NumFramesRead")),
        COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(ctypes.c_uint32), "p")),
    ]


class _ActivateHandler(comtypes.COMObject):  # type: ignore[misc]
    """ActivateAudioInterfaceAsync 完成回调：set 一个 threading.Event。"""

    _com_interfaces_: ClassVar = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

    def __init__(self) -> None:
        super().__init__()
        import threading

        self.done = threading.Event()

    def ActivateCompleted(self, activateOperation: object) -> int:
        self.done.set()
        return 0


# ---------------------------------------------------------------- 主类

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_mmdevapi = ctypes.WinDLL("mmdevapi")
_ActivateAudioInterfaceAsync = _mmdevapi.ActivateAudioInterfaceAsync
_ActivateAudioInterfaceAsync.restype = ctypes.HRESULT
_ActivateAudioInterfaceAsync.argtypes = [
    wintypes.LPCWSTR,
    POINTER(GUID),
    ctypes.c_void_p,  # PROPVARIANT*
    ctypes.c_void_p,  # IActivateAudioInterfaceCompletionHandler*
    ctypes.c_void_p,  # IActivateAudioInterfaceAsyncOperation**
]


class ProcessLoopbackCapture:
    """采集指定 PID（含子进程树）的渲染音频，固定输出请求的 PCM 格式。

    用法::

        cap = ProcessLoopbackCapture(pid, rate=48000, channels=2)
        cap.start()                  # 激活 + Initialize + Start（同线程 MTA）
        while ...:
            data = cap.read(100)     # 等 event ≤100ms，drain 全部包；无声返回 b""
        cap.close()
    """

    def __init__(self, pid: int, rate: int = 48000, channels: int = 2) -> None:
        self.pid = pid
        self.rate = rate
        self.channels = channels
        self.block_align = channels * 2  # s16
        self._client: object | None = None
        self._capture: object | None = None
        self._event: int | None = None

    def start(self) -> None:
        fmt = WAVEFORMATEX(
            wFormatTag=WAVE_FORMAT_PCM,
            nChannels=self.channels,
            nSamplesPerSec=self.rate,
            nAvgBytesPerSec=self.rate * self.block_align,
            nBlockAlign=self.block_align,
            wBitsPerSample=16,
            cbSize=0,
        )

        params = _AUDIOCLIENT_ACTIVATION_PARAMS(
            ActivationType=AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK,
            ProcessLoopbackParams=_AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(
                TargetProcessId=self.pid,
                ProcessLoopbackMode=PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE,
            ),
        )
        prop = _PROPVARIANT()
        prop.vt = VT_BLOB
        prop.blob.cbSize = ctypes.sizeof(params)
        prop.blob.pBlobData = ctypes.cast(ctypes.byref(params), ctypes.c_void_p)

        handler = _ActivateHandler()
        handler_ptr = handler.QueryInterface(IActivateAudioInterfaceCompletionHandler)
        op = POINTER(IActivateAudioInterfaceAsyncOperation)()
        hr = _ActivateAudioInterfaceAsync(
            VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
            ctypes.byref(GUID(str(_IID_IAudioClient))),
            ctypes.byref(prop),
            ctypes.cast(handler_ptr, ctypes.c_void_p),
            ctypes.byref(op),
        )
        if hr != 0:
            raise OSError(f"ActivateAudioInterfaceAsync failed hr=0x{hr & 0xFFFFFFFF:08X}")
        if not handler.done.wait(timeout=5.0):
            raise OSError("ActivateAudioInterfaceAsync completion timeout")
        hr_activate, punk = op.GetActivateResult()  # type: ignore[attr-defined]
        if hr_activate != 0:
            raise OSError(f"audio interface activation hr=0x{hr_activate & 0xFFFFFFFF:08X}")
        client = punk.QueryInterface(IAudioClient)

        client.Initialize(
            AUDCLNT_SHAREMODE_SHARED,
            AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK,
            _BUFFER_DURATION_HNS,
            0,
            ctypes.byref(fmt),
            None,
        )
        event = _kernel32.CreateEventW(None, False, False, None)
        if not event:
            raise ctypes.WinError(ctypes.get_last_error())
        client.SetEventHandle(event)
        capture = client.GetService(ctypes.byref(_IID_IAudioCaptureClient)).QueryInterface(
            IAudioCaptureClient
        )
        client.Start()

        self._client = client
        self._capture = capture
        self._event = event

    def read(self, timeout_ms: int = 100) -> bytes:
        """等数据 ≤timeout_ms，drain 当前全部包并返回 PCM 字节；无数据返回 b""。"""
        assert self._capture is not None and self._event is not None
        _kernel32.WaitForSingleObject(self._event, timeout_ms)
        chunks: list[bytes] = []
        capture = self._capture
        while True:
            packet = capture.GetNextPacketSize()  # type: ignore[attr-defined]
            if packet == 0:
                break
            data_ptr, n_frames, flags = capture.GetBuffer(None, None)  # type: ignore[attr-defined]
            n_bytes = int(n_frames) * self.block_align
            if flags & AUDCLNT_BUFFERFLAGS_SILENT:
                chunks.append(b"\x00" * n_bytes)
            else:
                chunks.append(ctypes.string_at(data_ptr, n_bytes))
            capture.ReleaseBuffer(n_frames)  # type: ignore[attr-defined]
        return b"".join(chunks)

    def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.Stop()  # type: ignore[attr-defined]
        if self._event is not None:
            _kernel32.CloseHandle(self._event)
        self._client = None
        self._capture = None
        self._event = None
