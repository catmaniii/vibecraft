// 录音 composable — useVoiceInput
//
// 职责：
//   arm()    → 进语音模式就**预热**麦克风管线（getUserMedia + AudioContext +
//              pcm-worklet 常驻采集），按下即录，消除"按下要等管线起来"的开头吞。
//   disarm() → 离开语音模式 / 页面隐藏时拆管线，释放麦克风。
//   start()  → 开始转发音频帧到 server（**同步**，因为 arm 已经预热好了）。
//   stop()   → 松手后**继续静默转发 TAIL_MS**再 sendAudioEnd，救尾字 + 流式右文
//              （paraformer 最后一字要靠后续音频做 look-ahead 才能解码）。
//   cancel() → 上滑取消 → sendAudioCancel（server 丢弃）。
//
// 两个真机痛点的根因与修法（2026-06-10 用户：服务端 PTT 不如原生输入法）：
//   开头吞 —— 旧 start() 是异步的（现 getUserMedia+建 ctx+加载 worklet ~100-500ms），
//             这期间麦克风没采集 → 前小半句丢。修：把这些挪到 arm()，进语音模式就预热，
//             麦克风常驻采集，start 只翻个转发开关 → 按下即录、从按下起不漏。
//             （不预录按下前的音频；用户只要"从按下不漏"。）
//   结尾丢 —— 旧 stop() 立刻 stop 麦克风轨道 → worklet 残留帧丢 + 流式 look-ahead 断 →
//             尾字解不出。修：松手后 forwarding 再续 TAIL_MS 才 audio_end。
//
// 依赖注入（调用方传入 useWs 返回的发送 helper + lastTranscript ref）：
//   避免在此 composable 内二次调用 useWs()，不会产生第二条 WS 连接。

import { ref, computed, watch } from 'vue'
import type { Ref, ComputedRef } from 'vue'
import type { TranscriptFrame } from '@/types'

// ── 外部接口 ────────────────────────────────────────────────────────────────

export interface UseVoiceInputOptions {
  /** useWs 返回的 sendAudioChunk：seq 递增 + base64 PCM16 帧 */
  sendAudioChunk: (seq: number, pcm: string) => void
  /** 松手正常结束 → server 出 final */
  sendAudioEnd: () => void
  /** 上滑取消 → server 丢弃，不出 final */
  sendAudioCancel: () => void
  /** useWs 暴露的 lastTranscript（readonly ref） */
  lastTranscript: Readonly<Ref<TranscriptFrame | null>>
}

export interface UseVoiceInputReturn {
  /** 是否正在录音（UI 用；松手即 false，补尾在后台静默进行） */
  isRecording: Ref<boolean>
  /** 当前草稿（partial）文字；is_final=true 时清空 */
  partial: ComputedRef<string>
  /** 最新定稿（final）文字；每次 is_final transcript 到达时更新 */
  final: Ref<string>
  /** 是否支持语音输入（需 HTTPS secure context + getUserMedia） */
  supported: boolean
  /** 预热麦克风管线（进语音模式时调）；幂等，已预热直接返回 */
  arm: () => Promise<void>
  /** 拆管线释放麦克风（离开语音模式 / 页面隐藏时调） */
  disarm: () => void
  /** 开始转发（同步）：翻转发开关 + 补 PREROLL */
  start: () => void
  /** 松手正常停：续 TAIL_MS 再 sendAudioEnd */
  stop: () => void
  /** 取消录音 → sendAudioCancel（不出 final） */
  cancel: () => void
  /**
   * 读当前实时波形，返回 barCount 个 [0,1] 的条高（每条=一段时域峰值=音量）。
   * 未预热 / 无 analyser 时返回空数组。供浮层 canvas 每帧画跳动波形。
   */
  getLevels: (barCount: number) => number[]
}

// ── 参数 ─────────────────────────────────────────────────────────────────────

// 松手后继续静默采集时长：救尾字 + 给 paraformer 流式 look-ahead(右文 300ms)留料。
const TAIL_MS = 350

// ── 实现 ─────────────────────────────────────────────────────────────────────

export function useVoiceInput (options: UseVoiceInputOptions): UseVoiceInputReturn {
  const { sendAudioChunk, sendAudioEnd, sendAudioCancel, lastTranscript } = options

  // 语音支持检测：需要 HTTPS（secure context）+ getUserMedia API
  const supported: boolean = !!(
    (typeof window !== 'undefined' && window.isSecureContext) &&
    (typeof navigator !== 'undefined' && navigator.mediaDevices?.getUserMedia)
  )

  // ── 响应式状态 ────────────────────────────────────────────────────────────

  const isRecording = ref(false)

  const partial = computed<string>(() => {
    const t = lastTranscript.value
    return (t && !t.is_final) ? t.text : ''
  })

  const final = ref<string>('')

  watch(lastTranscript, (frame) => {
    if (frame?.is_final) {
      final.value = frame.text
    }
  })

  // ── 内部音频对象（非响应式） ───────────────────────────────────────────────

  let audioContext: AudioContext | null = null
  let workletNode: AudioWorkletNode | null = null
  let mediaStream: MediaStream | null = null
  let analyser: AnalyserNode | null = null  // 实时波形：读时域数据给浮层画跳动条
  let armed = false
  let arming: Promise<void> | null = null

  // forwarding 与 isRecording 解耦：松手后 UI(isRecording) 立即关，但 forwarding
  // 还要续 TAIL_MS 静默补尾。
  let forwarding = false
  let seq = 0
  let tailTimer: ReturnType<typeof setTimeout> | null = null

  // ── 私有：发一帧 ──────────────────────────────────────────────────────────

  function _sendFrame (int16: Int16Array): void {
    const uint8 = new Uint8Array(int16.buffer)
    let binary = ''
    for (let i = 0; i < uint8.length; i++) {
      binary += String.fromCharCode(uint8[i])
    }
    sendAudioChunk(seq++, btoa(binary))
  }

  // ── 预热 / 拆管线 ──────────────────────────────────────────────────────────

  // 麦克风管线是否健康：track 还活着（readyState==='live'）且 ctx 没关。
  // 真机痛点（2026-06-13 张三一上来语音全废、李四正常）：armed 一旦置 true，旧
  // arm() 的 `if (armed) return` 就再也不重建管线 —— 可手机端 track 会在**不触发
  // visibilitychange** 的情况下"死掉"（OS 回收麦克风 / 别的 app 抢占 / 权限抖动 /
  // 锁屏），此时 armed 仍 true 但 track.readyState 已是 'ended'，按下说话只翻
  // forwarding 开关、worklet 再无帧 → 整条语音静默失效直到刷新页面。
  function isTrackHealthy (): boolean {
    if (!mediaStream || !audioContext || audioContext.state === 'closed') return false
    const tracks = mediaStream.getAudioTracks?.() ?? mediaStream.getTracks?.() ?? []
    const track = tracks[0]
    return !!track && track.readyState === 'live'
  }

  async function arm (): Promise<void> {
    if (!supported) return
    // 已 armed：管线健康就直接返回（幂等）；不健康（track 死了）就拆掉重建自愈。
    if (armed) {
      if (isTrackHealthy()) return
      disarm()
    }
    if (arming) return arming
    arming = (async () => {
      // 1. 申请麦克风权限 + 常驻采集
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
      // track 死亡（OS 回收 / 设备拔出 / 锁屏）→ 复位 armed，下次 arm/start 自愈重建
      const track = mediaStream.getAudioTracks?.()[0] ?? mediaStream.getTracks?.()[0]
      if (track) track.onended = () => { armed = false }
      // 2. AudioContext（无用户手势时可能 suspended，start() 里再 resume）
      audioContext = new AudioContext()
      // 3. 加载 PCM worklet（/pcm-worklet.js 由 Vite 原样复制自 public/）
      await audioContext.audioWorklet.addModule('/pcm-worklet.js')
      // 4. 麦克风 source → worklet
      const source = audioContext.createMediaStreamSource(mediaStream)
      workletNode = new AudioWorkletNode(audioContext, 'pcm-processor')
      // 5. worklet 推来 Int16 帧：forwarding 时才转发到 WS（其余时间麦克风常驻但不发）
      workletNode.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (forwarding) _sendFrame(new Int16Array(event.data))
      }
      // 6. AnalyserNode 旁挂在 source 上，给浮层读实时波形（不影响 worklet 这路）
      analyser = audioContext.createAnalyser()
      // 时域 512 点 ≈ 10ms 窗(@48kHz)：比 1024 更新鲜、延迟更低，足够画 48 条。
      analyser.fftSize = 512
      // smoothingTimeConstant 只作用于频域，对时域无效；时域平滑在组件 attack/release 里做。
      source.connect(analyser)
      // 7. 连图（连 destination 保持图活跃；worklet 不写输出 → 静音）
      source.connect(workletNode)
      workletNode.connect(audioContext.destination)
      armed = true
    })()
    try {
      await arming
    } catch {
      // 预热失败（权限拒绝等）→ 回滚，下次 start 再试
      armed = false
    } finally {
      arming = null
    }
  }

  function disarm (): void {
    if (tailTimer !== null) { clearTimeout(tailTimer); tailTimer = null }
    forwarding = false
    isRecording.value = false
    mediaStream?.getTracks().forEach((t) => t.stop())
    mediaStream = null
    workletNode?.disconnect()
    workletNode = null
    analyser = null
    if (audioContext && audioContext.state !== 'closed') {
      audioContext.close()
    }
    audioContext = null
    armed = false
  }

  // ── 实时波形采样（供浮层 canvas 每帧调） ──────────────────────────────────

  // 复用一个 buffer 避免每帧 new。fftSize=1024 → 时域 1024 点。
  let _waveBuf: Uint8Array | null = null

  function getLevels (barCount: number): number[] {
    if (!analyser || barCount <= 0) return []
    if (_waveBuf === null || _waveBuf.length !== analyser.fftSize) {
      _waveBuf = new Uint8Array(analyser.fftSize)
    }
    analyser.getByteTimeDomainData(_waveBuf)
    // 时域数据中心在 128（静音=平线）；每条取一段内偏离中心的峰值 = 该段音量
    const out: number[] = []
    const slice = Math.max(1, Math.floor(_waveBuf.length / barCount))
    for (let b = 0; b < barCount; b++) {
      let peak = 0
      const base = b * slice
      for (let i = 0; i < slice; i++) {
        const v = Math.abs(_waveBuf[base + i] - 128) / 128  // 0..1
        if (v > peak) peak = v
      }
      out.push(peak)
    }
    return out
  }

  // ── 录音控制 ──────────────────────────────────────────────────────────────

  function start (): void {
    if (!supported || isRecording.value) return
    // 上一段还在补尾（tailTimer 没触发）→ 先收尾关闭它，再开新段
    if (tailTimer !== null) {
      clearTimeout(tailTimer)
      tailTimer = null
      forwarding = false
      sendAudioEnd()
    }
    // iOS：AudioContext 可能 suspended，借这次按下手势 resume
    if (audioContext && audioContext.state === 'suspended') {
      void audioContext.resume()
    }
    // 无条件尝试预热/自愈：arm() 健康则秒返回，track 死了则按下即拆旧管线重建
    // （#527：旧 `if (!armed)` 在 armed=true 但 track 已死时跳过修复 → 永久静默）。
    // 重建是异步的，本次按下的开头小半句可能漏，但后续帧会接上、不再永久失效。
    void arm()

    seq = 0
    isRecording.value = true
    forwarding = true  // 麦克风已常驻采集，翻这个开关即从按下起转发，不漏开头
  }

  function stop (): void {
    if (!isRecording.value) return
    isRecording.value = false  // UI 立即关
    // 续 TAIL_MS 静默转发救尾字，到点才 audio_end
    if (tailTimer !== null) clearTimeout(tailTimer)
    tailTimer = setTimeout(() => {
      forwarding = false
      tailTimer = null
      sendAudioEnd()
    }, TAIL_MS)
  }

  function cancel (): void {
    if (tailTimer !== null) { clearTimeout(tailTimer); tailTimer = null }
    const wasActive = isRecording.value || forwarding
    isRecording.value = false
    forwarding = false
    if (wasActive) sendAudioCancel()
  }

  // 注：arm/disarm 的生命周期绑定（onMounted/onUnmounted/visibilitychange）由
  // 调用方组件负责，本 composable 不依赖 Vue 生命周期钩子，方便单测直接调。

  return {
    isRecording,
    partial,
    final,
    supported,
    arm,
    disarm,
    start,
    stop,
    cancel,
    getLevels,
  }
}
