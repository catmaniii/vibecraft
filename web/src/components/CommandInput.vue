<script setup lang="ts">
// 单行指令输入 + 实时状态反馈(spinner / 成功 / 失败)+ 5s 冷却
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import type { CommandFrame, CommandEchoFrame } from '@/types'

const props = defineProps<{
  canSend: boolean
  // 父组件透传 lastEcho,用于检测发送结果(成功 / 失败 / 模糊)
  lastEcho: CommandEchoFrame | null
}>()

const emit = defineEmits<{
  send: [frame: CommandFrame]
}>()

const text = ref('')
const cooldownLeft = ref(0)
const COOLDOWN_S = 5
// 状态机:'idle'(空) | 'sending'(已发,等服务端 echo) | 'done'(成功显示 1s) | 'failed'(失败)
const status = ref<'idle' | 'sending' | 'done' | 'failed'>('idle')
const statusDetail = ref('')  // failed 时的错误文案
let cooldownTimer: ReturnType<typeof setInterval> | null = null
let sendTimeoutTimer: ReturnType<typeof setTimeout> | null = null
let doneClearTimer: ReturnType<typeof setTimeout> | null = null
// 当前 in-flight 指令的发送时间戳,用于匹配 echo
let lastSentAt = 0

const SEND_TIMEOUT_MS = 18000  // 18s 兜底(LLM timeout=15s + 余量)
const DONE_DISPLAY_MS = 1500   // 成功/失败显示停留时长

function makeClientId(): string {
  return 'c_' + Math.random().toString(36).slice(2, 7)
}

function clearAllTimers() {
  if (sendTimeoutTimer !== null) { clearTimeout(sendTimeoutTimer); sendTimeoutTimer = null }
  if (doneClearTimer !== null) { clearTimeout(doneClearTimer); doneClearTimer = null }
}

function startCooldown() {
  cooldownLeft.value = COOLDOWN_S
  if (cooldownTimer !== null) clearInterval(cooldownTimer)
  cooldownTimer = setInterval(() => {
    cooldownLeft.value -= 1
    if (cooldownLeft.value <= 0) {
      cooldownLeft.value = 0
      if (cooldownTimer !== null) { clearInterval(cooldownTimer); cooldownTimer = null }
    }
  }, 1000)
}

function sendText() {
  const t = text.value.trim()
  if (!t || !canSendNow.value) return
  lastSentAt = Date.now() / 1000
  emit('send', {
    type: 'command',
    client_id: makeClientId(),
    issued_at: lastSentAt,
    text: t,
  })
  text.value = ''
  status.value = 'sending'
  statusDetail.value = ''
  startCooldown()
  // 兜底:18s 后还在 sending 视为 timeout
  clearAllTimers()
  sendTimeoutTimer = setTimeout(() => {
    if (status.value === 'sending') {
      status.value = 'failed'
      statusDetail.value = '响应超时(>18s)'
      scheduleClearStatus()
    }
  }, SEND_TIMEOUT_MS)
}

function scheduleClearStatus() {
  if (doneClearTimer !== null) clearTimeout(doneClearTimer)
  doneClearTimer = setTimeout(() => {
    status.value = 'idle'
    statusDetail.value = ''
  }, DONE_DISPLAY_MS)
}

// 监听 lastEcho 变化:发送后第一次新 echo 视作本次结果
watch(
  () => props.lastEcho,
  (echo) => {
    if (!echo || status.value !== 'sending') return
    // echo.ts 在 lastSentAt 之后才算我们这次的回复
    if (echo.ts < lastSentAt - 1) return
    clearAllTimers()
    const interp = echo.interpretation ?? ''
    if (interp.startsWith('[解析失败]') || interp.startsWith('[模糊]') || interp.startsWith('[失败]')) {
      status.value = 'failed'
      statusDetail.value = interp
    } else {
      status.value = 'done'
      statusDetail.value = interp
    }
    scheduleClearStatus()
  }
)

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendText()
  }
}

const canSendNow = computed(
  () => props.canSend && cooldownLeft.value === 0 && text.value.trim().length > 0 && status.value !== 'sending'
)

const buttonContent = computed<{ label: string; icon?: 'spin' | 'check' | 'cross' }>(() => {
  if (status.value === 'sending') return { label: '发送中', icon: 'spin' }
  if (status.value === 'done')    return { label: '已下达', icon: 'check' }
  if (status.value === 'failed')  return { label: '失败', icon: 'cross' }
  if (cooldownLeft.value > 0)     return { label: `${cooldownLeft.value}s` }
  return { label: '发送' }
})

const buttonCls = computed(() => {
  if (status.value === 'sending') return 'bg-accent/60 text-white cursor-wait'
  if (status.value === 'done')    return 'bg-success text-surface'
  if (status.value === 'failed')  return 'bg-danger text-white'
  return 'bg-accent text-surface hover:bg-blue-400 active:scale-95'
})

onBeforeUnmount(() => {
  if (cooldownTimer !== null) clearInterval(cooldownTimer)
  clearAllTimers()
})
</script>

<template>
  <div class="flex flex-col gap-1">
    <div class="flex items-center gap-2">
      <input
        v-model="text"
        type="text"
        placeholder="输入指令,如「切 4bg」"
        class="flex-1 min-w-0 rounded-lg bg-surface-3 border border-border text-sm text-white placeholder-muted
               px-3 py-2 focus:outline-none focus:border-accent transition-colors"
        :disabled="!props.canSend"
        @keydown="onKeydown"
      />
      <button
        type="button"
        class="shrink-0 rounded-lg px-3 py-2 text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed min-w-[88px] flex items-center justify-center gap-1.5"
        :class="buttonCls"
        :disabled="!canSendNow"
        @click="sendText"
      >
        <!-- spinner / check / cross 图标 -->
        <svg v-if="buttonContent.icon === 'spin'" class="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" opacity="0.25"/>
          <path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
        </svg>
        <span v-else-if="buttonContent.icon === 'check'" class="text-base leading-none">✓</span>
        <span v-else-if="buttonContent.icon === 'cross'" class="text-base leading-none">✕</span>
        <span>{{ buttonContent.label }}</span>
      </button>
    </div>
    <!-- 状态详情(failed 时显示错误,done 时显示 LLM 解读) -->
    <p
      v-if="status === 'failed'"
      class="text-[11px] text-danger px-1 truncate"
    >{{ statusDetail }}</p>
    <p
      v-else-if="status === 'done' && statusDetail"
      class="text-[11px] text-success/90 px-1 truncate"
    >→ {{ statusDetail }}</p>
  </div>
</template>
