<script setup lang="ts">
// VoiceInput.vue — 微信式语音长条（按住说话，上滑取消，松开等定稿）
//
// 形态：一根**长条**（占满中间 flex-1），整条都是按住区域（微信"按住 说话"）。
// 交互（浮层状态机 voiceState，见下方）：
//   按下（pointerdown）  → recording：录音 + 实时波形 + 草稿
//   移动（pointermove）  → 上滑 >60px → 取消区（浮层 + 长条变红）
//   松手（pointerup）    → 取消区/按住太短 → cancelled(红，短暂停留再消失)；
//                          否则 → finalizing（**窗口保留**，草稿继续更新、显"识别中…"）
//   定稿到达             → success（变绿"✓ 已识别"）+ emit('recognized') → 停留一下再消失
//
// 关键（2026-06-10 用户）：松手**不立刻关窗口** —— 后续识别还在更新（尾巴/定稿），
// 等它稳定、变绿确认才消失，才算识别成功；上滑取消则变红消失。
//
// 只用 **pointer 事件**（不同时绑 touch —— 二者并存会在手机上重复触发，
// 导致"点一下就进识别态/录音过早结束"，是之前 partial 不显示的根因）。
// supported=false（非 HTTPS / 无麦克风）→ 渲染 voice-not-supported 占位（上层切文字）。

import { ref, computed, watch, toRef, onMounted, onUnmounted } from 'vue'
import type { TranscriptFrame } from '@/types'
import { useVoiceInput } from '@/composables/useVoiceInput'
import { t } from '@/i18n'

// lastTranscript 传的是**响应式值**(不是 ref) —— 因为父组件模板里 `:last-transcript="ref"`
// 会被 Vue 自动解包成值。用 toRef 把这个响应式 prop 适配回 ref 给 useVoiceInput
// (它内部要 .value)。直接传 props.lastTranscript 给 useVoiceInput 会丢响应式 → partial/
// final 永不更新(2026-06-09 真机踩坑:服务器明明回了 transcript,前端不显示)。
const props = defineProps<{
  sendAudioChunk:  (seq: number, pcm: string) => void
  sendAudioEnd:    () => void
  sendAudioCancel: () => void
  lastTranscript:  TranscriptFrame | null
}>()

const emit = defineEmits<{
  recognized: [text: string]
}>()

const {
  isRecording,
  partial,
  supported,
  arm,
  disarm,
  start,
  stop,
  cancel,
  getLevels,
} = useVoiceInput({
  sendAudioChunk:  props.sendAudioChunk,
  sendAudioEnd:    props.sendAudioEnd,
  sendAudioCancel: props.sendAudioCancel,
  lastTranscript:  toRef(props, 'lastTranscript'),
})

// ── 实时波形（按住说话时浮层里跳动的条，跟音量同步，给"在收音"的反馈）──────────
const waveCanvas = ref<HTMLCanvasElement | null>(null)
const WAVE_BARS = 48           // 条数
// 快攻慢放（attack/release）：音量**上升**用大系数 → 几乎即时跟手（低延迟）；
// **下降**用小系数 → 缓慢回落（顺滑不抖）。对称缓动会让上升也滞后，是延迟感来源。
const WAVE_ATTACK = 0.7   // 上升跟手（越大越即时）
const WAVE_RELEASE = 0.18 // 下降回落（越小越顺滑）
let rafId = 0
// 显示高度（平滑后），跨帧保留，向 getLevels 的目标缓动
let displayLevels: number[] = []

function drawWave(): void {
  const canvas = waveCanvas.value
  if (!canvas) return
  // jsdom 下 getContext 会抛(未装 canvas 包)，try 兜住 → 单测安全
  let ctx: CanvasRenderingContext2D | null = null
  try { ctx = canvas.getContext('2d') } catch { ctx = null }
  if (!ctx) return
  const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1
  const w = canvas.clientWidth || canvas.width
  const h = canvas.clientHeight || canvas.height
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr)
    canvas.height = Math.round(h * dpr)
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, w, h)

  // 目标条高（无 analyser/未 arm 时取 0，让波形平滑落下而不是冻住）
  const target = getLevels(WAVE_BARS)
  if (displayLevels.length !== WAVE_BARS) displayLevels = new Array(WAVE_BARS).fill(0)
  for (let i = 0; i < WAVE_BARS; i++) {
    const t = target.length ? target[i] : 0
    // 快攻慢放：升用 ATTACK(快)、降用 RELEASE(慢) → 跟手又顺滑
    const k = t > displayLevels[i] ? WAVE_ATTACK : WAVE_RELEASE
    displayLevels[i] += (t - displayLevels[i]) * k
  }

  const gap = 2
  const barW = Math.max(1, (w - gap * (WAVE_BARS - 1)) / WAVE_BARS)
  const mid = h / 2
  const rounded = typeof ctx.roundRect === 'function'
  // 取消区变红，正常态白；条以中线为轴上下对称（搜狗那种波形）
  ctx.fillStyle = inCancelZone.value ? '#fecaca' : '#ffffff'
  for (let i = 0; i < WAVE_BARS; i++) {
    const bh = Math.max(2, displayLevels[i] * (h - 4))  // 最小 2px：静音也有细线
    const x = i * (barW + gap)
    const y = mid - bh / 2
    if (rounded) {
      ctx.beginPath()
      ctx.roundRect(x, y, barW, bh, barW / 2)
      ctx.fill()
    } else {
      ctx.fillRect(x, y, barW, bh)
    }
  }
}

function waveLoop(): void {
  drawWave()
  rafId = requestAnimationFrame(waveLoop)
}

function startWave(): void {
  if (rafId !== 0) return
  displayLevels = new Array(WAVE_BARS).fill(0)  // 每次按下从平线起，不残留上次
  rafId = requestAnimationFrame(waveLoop)
}

function stopWave(): void {
  if (rafId !== 0) { cancelAnimationFrame(rafId); rafId = 0 }
}

// 录音中跑波形，松手停（isRecording 录音时 true → 跑；松手后立即停）
watch(isRecording, (rec) => {
  if (rec) startWave()
  else stopWave()
})

// ── 浮层状态机 ────────────────────────────────────────────────────────────────
// 2026-06-10 用户：松手后别立刻关窗口 —— 后续识别还在更新（尾巴/定稿），要等它稳定。
//   recording  按住说话中（波形 + 实时草稿）
//   finalizing 松手后等定稿（窗口保留、草稿继续更新、显"识别中…"）
//   success    定稿有内容 → 变绿"✓ 已识别" → 停留一下再消失（识别成功）
//   failed     定稿空 / 超时 → 变红"✕ 识别失败" → 停留一下再消失（识别失败）
//   cancelled  上滑/误触取消 → 变红"已取消" → 短暂停留再消失
//   idle       隐藏
type VoiceState = 'idle' | 'recording' | 'finalizing' | 'success' | 'failed' | 'cancelled'
const voiceState = ref<VoiceState>('idle')

const SUCCESS_HIDE_MS = 900      // 变绿"已识别"停留时长
const FAILED_HIDE_MS = 1200      // 变红"识别失败"停留时长（多停一会让人看清）
const CANCEL_HIDE_MS = 400       // 变红"已取消"停留时长
const FINALIZE_TIMEOUT_MS = 4500 // 定稿迟迟不来的兜底：转"识别失败"
// 识别结果文字（success 显示用；与 partial 解耦）
const resultText = ref('')

let hideTimer: ReturnType<typeof setTimeout> | null = null
let finalizeTimer: ReturnType<typeof setTimeout> | null = null
function clearHideTimer(): void { if (hideTimer) { clearTimeout(hideTimer); hideTimer = null } }
function clearFinalizeTimer(): void { if (finalizeTimer) { clearTimeout(finalizeTimer); finalizeTimer = null } }
function scheduleHide(ms: number): void {
  clearHideTimer()
  hideTimer = setTimeout(() => { voiceState.value = 'idle'; hideTimer = null }, ms)
}

// ── 手势 ──────────────────────────────────────────────────────────────────────
const CANCEL_THRESHOLD = 60   // px：上滑超过此距离进取消区
const MIN_HOLD_MS = 300       // 按住不足此时长 = 误触（如轻点）→ 松手取消不发

const startY = ref(0)
const inCancelZone = ref(false)
let pressStartTime = 0
// pressing：同步按住标志（手势状态机用）。start() 现在是同步的（arm 已预热好麦克风，
// start 只翻转发开关），松手直接 stop/cancel，不再有"start 异步没起来"的竞态。
let pressing = false

function onPressStart(e: PointerEvent): void {
  e.preventDefault()
  if (pressing) return
  pressing = true
  startY.value = e.clientY
  // setPointerCapture：手指滑出长条范围也继续收到 move/up，保证上滑取消可靠
  ;(e.currentTarget as HTMLElement)?.setPointerCapture?.(e.pointerId)
  inCancelZone.value = false
  clearHideTimer()
  clearFinalizeTimer()
  voiceState.value = 'recording'
  pressStartTime = Date.now()
  start()  // 同步：麦克风已常驻，立即开始转发，不漏开头
}

function onPressMove(e: PointerEvent): void {
  if (!pressing) return
  inCancelZone.value = (startY.value - e.clientY) >= CANCEL_THRESHOLD
}

function onPressEnd(_e: PointerEvent): void {
  if (!pressing) return
  pressing = false
  const heldMs = Date.now() - pressStartTime
  // 取消区 或 按住太短(误触/轻点) → 丢弃，不发
  const doCancel = inCancelZone.value || heldMs < MIN_HOLD_MS
  inCancelZone.value = false
  if (doCancel) {
    voiceState.value = 'cancelled'
    cancel()
    scheduleHide(CANCEL_HIDE_MS)
  } else {
    // 松手进"识别中"：窗口保留、草稿继续更新，等定稿到达（watch lastTranscript）。
    voiceState.value = 'finalizing'
    resultText.value = ''
    stop()  // stop 内部续 TAIL_MS 静默补尾再 audio_end，救尾字
    clearFinalizeTimer()
    finalizeTimer = setTimeout(() => {
      // 定稿迟迟不来（网络/异常）→ 当识别失败，变红
      if (voiceState.value === 'finalizing') {
        voiceState.value = 'failed'
        scheduleHide(FAILED_HIDE_MS)
      }
      finalizeTimer = null
    }, FINALIZE_TIMEOUT_MS)
  }
}

// 定稿帧（is_final）到达：仅在"识别中"接受（取消/idle 时忽略迟到帧）。
// 有内容 → success 变绿 + emit；空 → failed 变红（识别失败）。
// 注：直接 watch lastTranscript 帧而非 final 文字 —— 空定稿时 final 不变（恒""），
// 监听不到；watch 帧能在"空定稿"那一刻就判失败，不用等超时。
watch(() => props.lastTranscript, (frame) => {
  if (!frame || !frame.is_final) return
  if (voiceState.value !== 'finalizing') return
  clearFinalizeTimer()
  const text = (frame.text || '').trim()
  if (text) {
    resultText.value = text
    voiceState.value = 'success'
    emit('recognized', text)
    scheduleHide(SUCCESS_HIDE_MS)
  } else {
    voiceState.value = 'failed'
    scheduleHide(FAILED_HIDE_MS)
  }
})

// ── 状态驱动的浮层外观 ────────────────────────────────────────────────────────
const overlayClass = computed(() => {
  switch (voiceState.value) {
    case 'success':   return 'bg-green-600 border border-green-400'
    case 'failed':    return 'bg-red-600 border border-red-400'
    case 'cancelled': return 'bg-red-600 border border-red-400'
    case 'recording': return inCancelZone.value
      ? 'bg-red-600 border border-red-400'
      : 'bg-surface-2 border border-border'
    case 'finalizing': return 'bg-surface-2 border border-accent/60'
    default:          return 'bg-surface-2 border border-border'
  }
})

// 候选文字：success 显识别结果，failed/cancelled 显对应提示，其余显实时草稿
const candidateText = computed(() => {
  if (voiceState.value === 'success') return resultText.value || t('voice.success')
  if (voiceState.value === 'failed') return t('voice.failed')
  if (voiceState.value === 'cancelled') return t('voice.cancelled')
  return partial.value || t('voice.listening')
})

const hintText = computed(() => {
  switch (voiceState.value) {
    case 'recording':  return inCancelZone.value ? t('voice.hintCancelZone') : t('voice.hintRelease')
    case 'finalizing': return t('voice.finalizing')
    case 'success':    return t('voice.hintSuccess')
    case 'failed':     return t('voice.hintFailed')
    case 'cancelled':  return t('voice.hintCancelled')
    default:           return ''
  }
})

// ── 麦克风预热生命周期 ────────────────────────────────────────────────────────
// 进语音模式（本组件 v-if 挂载）就 arm 预热麦克风常驻采集；离开（卸载）/页面隐藏
// 时 disarm 释放，别让麦克风一直亮着。页面回前台再 arm。
function onVisibilityChange(): void {
  if (typeof document === 'undefined') return
  if (document.hidden) disarm()
  else void arm()
}

onMounted(() => {
  if (supported) {
    void arm()
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibilityChange)
    }
  }
})

onUnmounted(() => {
  if (typeof document !== 'undefined') {
    document.removeEventListener('visibilitychange', onVisibilityChange)
  }
  clearHideTimer()
  clearFinalizeTimer()
  stopWave()
  disarm()
})
</script>

<template>
  <!-- supported=true：长条本体 + 录音浮层 -->
  <div v-if="supported" class="relative flex-1 min-w-0" data-testid="voice-input-root">

    <!-- 录音浮层（录音中显示 partial 草稿；浮在长条上方，占满长条宽度） -->
    <Transition
      enter-active-class="transition ease-out duration-150"
      enter-from-class="opacity-0 translate-y-1"
      enter-to-class="opacity-100 translate-y-0"
      leave-active-class="transition ease-in duration-200"
      leave-from-class="opacity-100"
      leave-to-class="opacity-0"
    >
      <!-- mb-5 把整块抬高，远离按住的拇指；候选文字放最上方最安全不被挡。
           松手后不立刻关：finalizing 保留窗口续显草稿、success 变绿、cancelled 变红 -->
      <div
        v-if="voiceState !== 'idle'"
        data-testid="voice-overlay"
        class="absolute bottom-full mb-5 left-0 right-0 rounded-xl shadow-lg px-4 py-5
               text-center pointer-events-none select-none transition-colors"
        :class="overlayClass"
      >
        <!-- 候选文字：放最上方（拇指够不到），加大字号更醒目 -->
        <p
          class="text-base font-medium text-white min-h-[1.8em] break-words leading-snug mb-2"
          data-testid="voice-partial-text"
        >
          {{ candidateText }}
        </p>
        <!-- 实时波形：仅录音中显示（松手后无实时音频）；放下方靠近拇指无妨 -->
        <canvas
          v-if="voiceState === 'recording'"
          ref="waveCanvas"
          data-testid="voice-waveform"
          class="w-full h-24"
        ></canvas>
        <!-- 识别中：松手等定稿期间的脉动指示（替代波形位置） -->
        <div
          v-else-if="voiceState === 'finalizing'"
          data-testid="voice-finalizing"
          class="w-full h-24 flex items-center justify-center gap-1.5"
        >
          <span class="w-2 h-2 rounded-full bg-accent animate-pulse" style="animation-delay:0ms"></span>
          <span class="w-2 h-2 rounded-full bg-accent animate-pulse" style="animation-delay:150ms"></span>
          <span class="w-2 h-2 rounded-full bg-accent animate-pulse" style="animation-delay:300ms"></span>
        </div>
        <!-- 提示行放最底（离拇指最近，但只是说明文字，被挡也无所谓） -->
        <p
          class="text-xs font-medium mt-1.5 leading-tight"
          :class="voiceState === 'recording' && inCancelZone ? 'text-red-100' : 'text-white/80'"
          data-testid="voice-overlay-hint"
        >
          {{ hintText }}
        </p>
        <div
          v-if="voiceState === 'recording' && inCancelZone"
          data-testid="voice-cancel-zone"
          class="mt-1 text-red-100 text-xs font-semibold"
        >
          {{ t('voice.releaseCancel') }}
        </div>
      </div>
    </Transition>

    <!-- 长条本体：整条按住说话 -->
    <button
      type="button"
      data-testid="voice-mic-btn"
      :aria-label="t('voice.ariaHoldToSpeak')"
      class="w-full select-none touch-none rounded-lg px-3 py-2 text-sm font-medium border
             flex items-center justify-center gap-2 transition-colors active:scale-[0.99]"
      :class="isRecording
        ? (inCancelZone
            ? 'bg-red-600 border-red-400 text-red-50'
            : 'bg-red-500 border-red-400 text-white')
        : 'bg-surface-3 border-border text-muted hover:text-white hover:bg-surface-4'"
      @pointerdown.prevent="onPressStart"
      @pointermove="onPressMove"
      @pointerup="onPressEnd"
      @pointercancel="onPressEnd"
    >
      <!-- 麦克风图标 -->
      <svg viewBox="0 0 24 24" class="h-4 w-4 shrink-0" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
        <rect x="9" y="2" width="6" height="11" rx="3" :fill="isRecording ? 'currentColor' : 'none'"/>
        <path d="M5 10a7 7 0 0 0 14 0" stroke-linecap="round"/>
        <line x1="12" y1="17" x2="12" y2="21" stroke-linecap="round"/>
        <line x1="9" y1="21" x2="15" y2="21" stroke-linecap="round"/>
      </svg>
      <span>{{ isRecording ? (inCancelZone ? t('voice.releaseCancel') : t('voice.holdRelease')) : t('voice.holdToSpeak') }}</span>
    </button>
  </div>

  <!-- supported=false：不渲染长条（上层切文字模式） -->
  <div v-else data-testid="voice-not-supported" aria-hidden="true" />
</template>
