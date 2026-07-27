// useVoiceInput composable 单测（2026-06-10 重构后：arm 预热 / start 同步翻转发 /
// stop 续 TAIL_MS 补尾 / disarm 停轨道 / analyser 实时波形）。
//
// jsdom 环境下无真实 AudioWorklet / MediaDevices / AnalyserNode，全部 mock。
// 重点验：
//   supported 判定（secure context + getUserMedia）
//   arm()    → getUserMedia + AudioContext/worklet/analyser 建立（预热）
//   start()  → 同步翻 forwarding；worklet 帧 → sendAudioChunk(seq递增, base64)
//   stop()   → 续 TAIL_MS 后 sendAudioEnd；isRecording 立即 false
//   cancel() → sendAudioCancel，不调 sendAudioEnd
//   disarm() → 停麦克风轨道 + 关 ctx
//   getLevels() → 从 analyser 时域读条高
//   partial / final 响应式

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ref, nextTick } from 'vue'
import { useVoiceInput } from '@/composables/useVoiceInput'
import type { TranscriptFrame } from '@/types'

// ── 工具：Int16Array → base64（与 composable 内实现相同） ─────────────────────

function int16ToBase64 (int16: Int16Array): string {
  const uint8 = new Uint8Array(int16.buffer)
  let binary = ''
  for (let i = 0; i < uint8.length; i++) {
    binary += String.fromCharCode(uint8[i])
  }
  return btoa(binary)
}

// ── 工具：快速构建默认 options ─────────────────────────────────────────────────

function makeOptions (overrides?: {
  sendAudioChunk?: ReturnType<typeof vi.fn>
  sendAudioEnd?: ReturnType<typeof vi.fn>
  sendAudioCancel?: ReturnType<typeof vi.fn>
  lastTranscript?: ReturnType<typeof ref<TranscriptFrame | null>>
}) {
  return {
    sendAudioChunk:  overrides?.sendAudioChunk  ?? vi.fn(),
    sendAudioEnd:    overrides?.sendAudioEnd    ?? vi.fn(),
    sendAudioCancel: overrides?.sendAudioCancel ?? vi.fn(),
    lastTranscript:  overrides?.lastTranscript  ?? ref<TranscriptFrame | null>(null),
  }
}

// ── Mock AudioContext + AudioWorkletNode + AnalyserNode 工厂 ──────────────────

interface AudioMocks {
  mockAddModule:         ReturnType<typeof vi.fn>
  mockWorkletPort:       { onmessage: ((e: MessageEvent<ArrayBuffer>) => void) | null }
  mockWorkletDisconnect: ReturnType<typeof vi.fn>
  mockCtxClose:          ReturnType<typeof vi.fn>
  mockTrackStop:         ReturnType<typeof vi.fn>
  mockGetUserMedia:      ReturnType<typeof vi.fn>
  mockAnalyser:          { fftSize: number; getByteTimeDomainData: ReturnType<typeof vi.fn> }
  mockTrack:             { stop: ReturnType<typeof vi.fn>; readyState: string; onended: (() => void) | null }
}

function makeAudioMocks (): AudioMocks {
  const mockAddModule       = vi.fn().mockResolvedValue(undefined)
  const mockCtxClose        = vi.fn().mockResolvedValue(undefined)
  const mockWorkletDisconnect = vi.fn()
  const mockWorkletPort: AudioMocks['mockWorkletPort'] = { onmessage: null }
  const mockTrackStop       = vi.fn()

  const mockWorkletNode = {
    port:       mockWorkletPort,
    connect:    vi.fn(),
    disconnect: mockWorkletDisconnect,
  }
  const mockSource = { connect: vi.fn() }
  // analyser：getByteTimeDomainData 填入"半幅方波"样数据，便于 getLevels 断言非零
  const mockAnalyser = {
    fftSize: 1024,
    getByteTimeDomainData: vi.fn((buf: Uint8Array) => {
      for (let i = 0; i < buf.length; i++) buf[i] = i % 2 === 0 ? 192 : 64  // 偏离 128 = 有音量
    }),
  }
  const mockAudioCtx = {
    audioWorklet:            { addModule: mockAddModule },
    createMediaStreamSource: vi.fn().mockReturnValue(mockSource),
    createAnalyser:          vi.fn().mockReturnValue(mockAnalyser),
    destination:             {},
    state:                   'running' as AudioContextState,
    close:                   mockCtxClose,
  }

  // track 带 readyState（'live'=健康）+ onended（死亡回调），供 #527 自愈用例操控。
  const mockTrack: AudioMocks['mockTrack'] = { stop: mockTrackStop, readyState: 'live', onended: null }
  const mockStream = {
    getTracks:      () => [mockTrack],
    getAudioTracks: () => [mockTrack],
  }
  const mockGetUserMedia = vi.fn().mockResolvedValue(mockStream)

  // 注册全局 mock（jsdom 里这些 API 不存在）
  vi.stubGlobal('AudioContext',     vi.fn().mockReturnValue(mockAudioCtx))
  vi.stubGlobal('AudioWorkletNode', vi.fn().mockReturnValue(mockWorkletNode))

  Object.defineProperty(navigator, 'mediaDevices', {
    value:        { getUserMedia: mockGetUserMedia },
    writable:     true,
    configurable: true,
  })
  Object.defineProperty(window, 'isSecureContext', {
    value:        true,
    writable:     true,
    configurable: true,
  })

  return {
    mockAddModule,
    mockWorkletPort,
    mockWorkletDisconnect,
    mockCtxClose,
    mockTrackStop,
    mockGetUserMedia,
    mockAnalyser,
    mockTrack,
  }
}

// ─────────────────────────────────────────────────────────────────────────────

describe('useVoiceInput', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  // ── supported 判定 ─────────────────────────────────────────────────────────

  describe('supported', () => {
    it('secure context + getUserMedia 存在 → true', () => {
      makeAudioMocks()
      const { supported } = useVoiceInput(makeOptions())
      expect(supported).toBe(true)
    })

    it('isSecureContext = false → false', () => {
      makeAudioMocks()
      Object.defineProperty(window, 'isSecureContext', {
        value: false, writable: true, configurable: true,
      })
      const { supported } = useVoiceInput(makeOptions())
      expect(supported).toBe(false)
    })

    it('navigator.mediaDevices 不存在 → false', () => {
      makeAudioMocks()
      Object.defineProperty(navigator, 'mediaDevices', {
        value: undefined, writable: true, configurable: true,
      })
      const { supported } = useVoiceInput(makeOptions())
      expect(supported).toBe(false)
    })

    it('getUserMedia 不存在（mediaDevices 无该方法）→ false', () => {
      makeAudioMocks()
      Object.defineProperty(navigator, 'mediaDevices', {
        value: {}, writable: true, configurable: true,
      })
      const { supported } = useVoiceInput(makeOptions())
      expect(supported).toBe(false)
    })
  })

  // ── arm() 预热 ───────────────────────────────────────────────────────────────

  describe('arm()', () => {
    it('arm() 调用 getUserMedia + addModule + createAnalyser（预热管线）', async () => {
      const { mockGetUserMedia, mockAddModule } = makeAudioMocks()
      const { arm } = useVoiceInput(makeOptions())
      await arm()
      expect(mockGetUserMedia).toHaveBeenCalledWith({ audio: true })
      expect(mockAddModule).toHaveBeenCalledWith('/pcm-worklet.js')
    })

    it('arm() 幂等：多次调用只建一次管线（管线健康时）', async () => {
      const { mockGetUserMedia } = makeAudioMocks()
      const { arm } = useVoiceInput(makeOptions())
      await arm()
      await arm()
      await arm()
      expect(mockGetUserMedia).toHaveBeenCalledOnce()
    })
  })

  // ── #527 track 死亡自愈 ───────────────────────────────────────────────────────
  // 真机：armed=true 后 track 被 OS 回收（readyState='ended'）却不触发
  // visibilitychange → 旧 arm() 永不重建 → 语音永久静默。修后：arm()/start() 发现
  // track 不健康会拆掉重建。
  describe('track 死亡自愈（#527）', () => {
    it('armed 后 track 死亡（readyState=ended）→ 再 arm() 拆旧重建（getUserMedia 再调）', async () => {
      const { mockGetUserMedia, mockTrack, mockTrackStop } = makeAudioMocks()
      const { arm } = useVoiceInput(makeOptions())
      await arm()
      expect(mockGetUserMedia).toHaveBeenCalledOnce()

      // 模拟 OS 回收麦克风：track 变 ended
      mockTrack.readyState = 'ended'
      await arm()  // 旧逻辑会因 armed=true 直接返回；新逻辑应拆旧（stop track）+ 重建
      expect(mockTrackStop).toHaveBeenCalled()       // 旧管线被 disarm 拆掉
      expect(mockGetUserMedia).toHaveBeenCalledTimes(2)  // 重新申请麦克风
    })

    it('track.onended 触发 → 复位 armed → 下次 start() 自愈重建', async () => {
      const { mockGetUserMedia, mockTrack } = makeAudioMocks()
      const { arm, start } = useVoiceInput(makeOptions())
      await arm()
      expect(typeof mockTrack.onended).toBe('function')  // arm() 给 track 挂了 onended

      // track 死亡回调触发（armed 复位），并把 track 标记 ended
      mockTrack.onended!()
      mockTrack.readyState = 'ended'

      start()              // 无条件 void arm()：发现不健康 → 重建
      await Promise.resolve()
      await Promise.resolve()
      expect(mockGetUserMedia).toHaveBeenCalledTimes(2)
    })

    it('start() 在 track 健康时不重建（仅健康检查，不重复 getUserMedia）', async () => {
      const { mockGetUserMedia } = makeAudioMocks()
      const { arm, start } = useVoiceInput(makeOptions())
      await arm()
      start()
      await Promise.resolve()
      expect(mockGetUserMedia).toHaveBeenCalledOnce()  // 健康 → arm() 秒返回
    })
  })

  // ── start() 转发 ─────────────────────────────────────────────────────────────

  describe('start()', () => {
    it('arm 后 start，isRecording 变 true', async () => {
      makeAudioMocks()
      const { arm, start, isRecording } = useVoiceInput(makeOptions())
      await arm()
      expect(isRecording.value).toBe(false)
      start()
      expect(isRecording.value).toBe(true)
    })

    it('start 后 worklet 发一帧 → sendAudioChunk(0, base64) 被调', async () => {
      const { mockWorkletPort } = makeAudioMocks()
      const sendAudioChunk = vi.fn()
      const { arm, start } = useVoiceInput(makeOptions({ sendAudioChunk }))
      await arm()
      start()

      const int16 = new Int16Array([100, 200, 300, 400, -100, -200, 0, 32767])
      mockWorkletPort.onmessage!({ data: int16.buffer } as MessageEvent<ArrayBuffer>)

      expect(sendAudioChunk).toHaveBeenCalledOnce()
      expect(sendAudioChunk).toHaveBeenCalledWith(0, int16ToBase64(int16))
    })

    it('未 start（仅 arm）时 worklet 帧不转发（麦克风常驻但不发）', async () => {
      const { mockWorkletPort } = makeAudioMocks()
      const sendAudioChunk = vi.fn()
      const { arm } = useVoiceInput(makeOptions({ sendAudioChunk }))
      await arm()

      mockWorkletPort.onmessage!({ data: new Int16Array([1, 2, 3]).buffer } as MessageEvent<ArrayBuffer>)

      expect(sendAudioChunk).not.toHaveBeenCalled()
    })

    it('start 后多帧 → seq 递增', async () => {
      const { mockWorkletPort } = makeAudioMocks()
      const sendAudioChunk = vi.fn()
      const { arm, start } = useVoiceInput(makeOptions({ sendAudioChunk }))
      await arm()
      start()

      mockWorkletPort.onmessage!({ data: new Int16Array([1, 2, 3]).buffer } as MessageEvent<ArrayBuffer>)
      mockWorkletPort.onmessage!({ data: new Int16Array([4, 5, 6]).buffer } as MessageEvent<ArrayBuffer>)

      expect(sendAudioChunk).toHaveBeenCalledTimes(2)
      expect(sendAudioChunk.mock.calls[0][0]).toBe(0)
      expect(sendAudioChunk.mock.calls[1][0]).toBe(1)
    })

    it('start() 后再次 start() 被忽略（isRecording 已 true）', async () => {
      const { mockWorkletPort } = makeAudioMocks()
      const sendAudioChunk = vi.fn()
      const { arm, start } = useVoiceInput(makeOptions({ sendAudioChunk }))
      await arm()
      start()
      start()  // 第二次忽略，不重置 seq
      mockWorkletPort.onmessage!({ data: new Int16Array([1]).buffer } as MessageEvent<ArrayBuffer>)
      expect(sendAudioChunk.mock.calls[0][0]).toBe(0)
    })
  })

  // ── stop() ────────────────────────────────────────────────────────────────

  describe('stop()', () => {
    it('stop() → isRecording 立即 false，续 TAIL_MS 后才 sendAudioEnd', async () => {
      vi.useFakeTimers()
      makeAudioMocks()
      const sendAudioEnd = vi.fn()
      const { arm, start, stop, isRecording } = useVoiceInput(makeOptions({ sendAudioEnd }))
      await arm()
      start()
      expect(isRecording.value).toBe(true)

      stop()
      // UI 立即关，但补尾未到 → 还没 audio_end
      expect(isRecording.value).toBe(false)
      expect(sendAudioEnd).not.toHaveBeenCalled()

      vi.advanceTimersByTime(400)  // > TAIL_MS(350)
      expect(sendAudioEnd).toHaveBeenCalledOnce()
    })

    it('stop() 在 start() 前不调 sendAudioEnd', () => {
      makeAudioMocks()
      const sendAudioEnd = vi.fn()
      const { stop } = useVoiceInput(makeOptions({ sendAudioEnd }))
      stop()
      expect(sendAudioEnd).not.toHaveBeenCalled()
    })
  })

  // ── cancel() ──────────────────────────────────────────────────────────────

  describe('cancel()', () => {
    it('cancel() → sendAudioCancel 被调，不触发 sendAudioEnd，isRecording false', async () => {
      makeAudioMocks()
      const sendAudioEnd = vi.fn()
      const sendAudioCancel = vi.fn()
      const { arm, start, cancel, isRecording } = useVoiceInput(
        makeOptions({ sendAudioEnd, sendAudioCancel })
      )
      await arm()
      start()
      cancel()

      expect(sendAudioCancel).toHaveBeenCalledOnce()
      expect(sendAudioEnd).not.toHaveBeenCalled()
      expect(isRecording.value).toBe(false)
    })

    it('cancel() 未录音时不调 sendAudioCancel', () => {
      makeAudioMocks()
      const sendAudioCancel = vi.fn()
      const { cancel } = useVoiceInput(makeOptions({ sendAudioCancel }))
      cancel()
      expect(sendAudioCancel).not.toHaveBeenCalled()
    })
  })

  // ── disarm() ────────────────────────────────────────────────────────────────

  describe('disarm()', () => {
    it('disarm() → 停麦克风轨道 + 关 AudioContext', async () => {
      const { mockTrackStop, mockCtxClose } = makeAudioMocks()
      const { arm, disarm } = useVoiceInput(makeOptions())
      await arm()
      disarm()
      expect(mockTrackStop).toHaveBeenCalledOnce()
      expect(mockCtxClose).toHaveBeenCalledOnce()
    })
  })

  // ── getLevels() 实时波形 ──────────────────────────────────────────────────────

  describe('getLevels()', () => {
    it('arm 后 getLevels(n) 返回 n 个 [0,1] 条高（有音量 → 非零）', async () => {
      makeAudioMocks()
      const { arm, getLevels } = useVoiceInput(makeOptions())
      await arm()
      const levels = getLevels(16)
      expect(levels).toHaveLength(16)
      expect(levels.every((v) => v >= 0 && v <= 1)).toBe(true)
      expect(Math.max(...levels)).toBeGreaterThan(0)  // mock analyser 偏离 128 → 有条高
    })

    it('未 arm（无 analyser）→ getLevels 返空数组', () => {
      makeAudioMocks()
      const { getLevels } = useVoiceInput(makeOptions())
      expect(getLevels(16)).toEqual([])
    })
  })

  // ── partial / final 响应式 ────────────────────────────────────────────────

  describe('partial', () => {
    it('lastTranscript 为 null → partial 为空串', () => {
      makeAudioMocks()
      const lastTranscript = ref<TranscriptFrame | null>(null)
      const { partial } = useVoiceInput(makeOptions({ lastTranscript }))
      expect(partial.value).toBe('')
    })

    it('is_final=false → partial 有草稿文字', () => {
      makeAudioMocks()
      const lastTranscript = ref<TranscriptFrame | null>({
        type: 'transcript', text: '切4bg', is_final: false,
      })
      const { partial } = useVoiceInput(makeOptions({ lastTranscript }))
      expect(partial.value).toBe('切4bg')
    })

    it('is_final=true → partial 为空串（定稿不显示为草稿）', () => {
      makeAudioMocks()
      const lastTranscript = ref<TranscriptFrame | null>({
        type: 'transcript', text: '切4bg', is_final: true,
      })
      const { partial } = useVoiceInput(makeOptions({ lastTranscript }))
      expect(partial.value).toBe('')
    })

    it('transcript 从 null → partial → final，partial 随之更新', async () => {
      makeAudioMocks()
      const lastTranscript = ref<TranscriptFrame | null>(null)
      const { partial } = useVoiceInput(makeOptions({ lastTranscript }))

      lastTranscript.value = { type: 'transcript', text: '切4', is_final: false }
      await nextTick()
      expect(partial.value).toBe('切4')

      lastTranscript.value = { type: 'transcript', text: '切4bg', is_final: true }
      await nextTick()
      expect(partial.value).toBe('')
    })
  })

  describe('final', () => {
    it('初始为空串', () => {
      makeAudioMocks()
      const { final } = useVoiceInput(makeOptions())
      expect(final.value).toBe('')
    })

    it('is_final=true transcript → final 更新为定稿文字', async () => {
      makeAudioMocks()
      const lastTranscript = ref<TranscriptFrame | null>(null)
      const { final } = useVoiceInput(makeOptions({ lastTranscript }))

      lastTranscript.value = { type: 'transcript', text: '刷两个叉子', is_final: true }
      await nextTick()
      expect(final.value).toBe('刷两个叉子')
    })

    it('is_final=false transcript 不写入 final', async () => {
      makeAudioMocks()
      const lastTranscript = ref<TranscriptFrame | null>(null)
      const { final } = useVoiceInput(makeOptions({ lastTranscript }))

      lastTranscript.value = { type: 'transcript', text: '草稿', is_final: false }
      await nextTick()
      expect(final.value).toBe('')
    })
  })
})
