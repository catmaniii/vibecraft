<script setup lang="ts">
// 单行指令输入 + 实时承载 UI + 历史 (2026-05-23 用户:文字指令视觉反馈强化)
// Task 7 (2026-06-09): 语音/文字微信式切换 + 语音 final 接入现有 command 管线
//
// 布局逻辑：
//   - 默认语音模式（按住麦克风说话，不弹输入法）；localStorage 'vibecraft.input_mode' 记忆。
//   - 点左侧切换按钮 → 文字模式（现有输入框 + 发送按钮）
//   - 非 HTTPS（voiceSupported=false）→ 强制文字模式 + 一次性提示
//
// 发送路径统一：
//   - 文字模式：sendText() → submitCommand(t)
//   - 语音模式：VoiceInput @recognized → onVoiceRecognized(t) → submitCommand(t)
//   - submitCommand 走同一 emit('send', CommandFrame) 管线 + 冷却；
//     发送反馈走命令气泡队列(CommandBubbleQueue，见下方"指令发送公共逻辑"注释)。
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import type { Ref } from 'vue'
import type {
  CommandFrame,
  CommandEchoFrame,
  CommandReceivedFrame,
  RecentCommandView,
  TranscriptFrame,
} from '@/types'
import CommandHistoryItem from '@/components/CommandHistoryItem.vue'
import CommandBubbleQueue from '@/components/CommandBubbleQueue.vue'
import type { CommandBubble } from '@/components/CommandBubbleQueue.vue'
import VoiceInput from '@/components/VoiceInput.vue'
import { t } from '@/i18n'

const props = defineProps<{
  canSend: boolean
  lastEcho: CommandEchoFrame | null
  // command_received ack（server 收到即回，"识别中"反馈）→ 驱动气泡队列开卡
  lastReceived: CommandReceivedFrame | null
  // 历史指令(后端富化:输入文本 + 识别解读 + 各 directive 状态)。点时钟按钮弹出。
  recentCommands: readonly RecentCommandView[]
  // 语音输入 props（来自 useWs，透传给 VoiceInput；不在此处调 useWs 以免二次连接）
  sendAudioChunk: (seq: number, pcm: string) => void
  sendAudioEnd: () => void
  sendAudioCancel: () => void
  lastTranscript: TranscriptFrame | null
}>()

const emit = defineEmits<{
  send: [frame: CommandFrame]
}>()

// ── 语音/文字模式切换（微信式，Task 7）────────────────────────────────────────

// 语音支持检测：需 HTTPS（secure context）+ getUserMedia API
// 在 setup 阶段执行一次，非响应式（浏览器加载后不会变）
const voiceSupported: boolean = !!(
  typeof window !== 'undefined' &&
  window.isSecureContext &&
  typeof navigator !== 'undefined' &&
  navigator.mediaDevices?.getUserMedia
)

const STORAGE_KEY = 'vibecraft.input_mode'

// localStorage 读取上次选择，**默认文字模式**（2026-06-10 用户：FunASR 体验不如
// 原生输入法，默认走文字；仍保留切换到语音）。voiceSupported=false 强制文字。
const storedMode: 'voice' | 'text' | null =
  typeof localStorage !== 'undefined'
    ? (localStorage.getItem(STORAGE_KEY) as 'voice' | 'text' | null)
    : null

const inputMode = ref<'voice' | 'text'>(
  !voiceSupported ? 'text' : (storedMode ?? 'text')
)

// 非 HTTPS 时的一次性提示（可手动关闭）
const showHttpsHint = ref(!voiceSupported)

function toggleInputMode() {
  inputMode.value = inputMode.value === 'voice' ? 'text' : 'voice'
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(STORAGE_KEY, inputMode.value)
  }
}

function dismissHttpsHint() {
  showHttpsHint.value = false
}

// ── 指令发送公共逻辑（2026-07-08 用户：非阻塞命令气泡队列）───────────────────
//
// 后端每条命令并发解析（LLM 3-8s），玩家可能在第一条还没解析完就发第二条。
// 改前：单一 status ref 只能显示"最后一条"，第二条覆盖第一条状态就丢了。
// 改后：每条命令一个气泡（bubbles 数组），互不覆盖：
//   1. submitCommand emit('send', ...) → 后端立即回 command_received{text,ts}
//      （ts = 原样回显的 issued_at）→ watch(lastReceived) 用 `${ts}_${text}` 开一个
//      pending(琥珀) 气泡。
//   2. 解析完成后端回 command_echo{user_text, interpretation, ts}（ts 是完成时刻，
//      跟 issued_at 无关，且不带 client_id/原 ts）→ 只能按文本匹配：watch(lastEcho)
//      在当前 pending 气泡里找 text 相同的最旧一条（FIFO；查无精确匹配退化到最旧
//      pending 气泡，避免更新静默丢失）→ 标 done(绿)/failed(红) + 停留后淡出移除。
//   3. pending 超过 SEND_TIMEOUT_MS 还没等到 echo → 兜底标 failed（超时提示）。
//
// 发送侧不再等前一条 resolve 才能发下一条（去掉旧 status==='sending' 门槛），
// 冷却(COOLDOWN_S)仍保留作为简单防连点限频，跟气泡状态无关。

const text = ref('')
const cooldownLeft = ref(0)
const COOLDOWN_S = 5

// 历史 modal 显示(内容用后端 recentCommands,见 template)
const showHistory = ref(false)

let cooldownTimer: ReturnType<typeof setInterval> | null = null

const SEND_TIMEOUT_MS = 18000   // 18s 兜底(LLM timeout 15s + 余量)：pending 气泡无 echo 判失败
const BUBBLE_FADE_MS = 1800     // done/failed 气泡停留后淡出（用户原话"停留一下~1.5-2s"）
const MAX_BUBBLES = 4           // 最多同时显示几个气泡，超了先移最旧的

const bubbles = ref<CommandBubble[]>([])
// 每个气泡两类计时器：fade（done/failed 后淡出移除）/ timeout（pending 太久无 echo 兜底判失败）
const bubbleFadeTimers = new Map<string, ReturnType<typeof setTimeout>>()
const bubbleTimeoutTimers = new Map<string, ReturnType<typeof setTimeout>>()

function makeClientId(): string {
  return 'c_' + Math.random().toString(36).slice(2, 7)
}

function clearBubbleFadeTimer(id: string): void {
  const timer = bubbleFadeTimers.get(id)
  if (timer !== undefined) { clearTimeout(timer); bubbleFadeTimers.delete(id) }
}
function clearBubbleTimeoutTimer(id: string): void {
  const timer = bubbleTimeoutTimers.get(id)
  if (timer !== undefined) { clearTimeout(timer); bubbleTimeoutTimers.delete(id) }
}

function removeBubble(id: string): void {
  bubbles.value = bubbles.value.filter((b) => b.id !== id)
  clearBubbleFadeTimer(id)
  clearBubbleTimeoutTimer(id)
}

function scheduleBubbleFade(id: string): void {
  clearBubbleFadeTimer(id)
  bubbleFadeTimers.set(id, setTimeout(() => removeBubble(id), BUBBLE_FADE_MS))
}

// pending → done/failed：清超时兜底计时器、写状态+详情、排淡出
function resolveBubble(id: string, status: 'done' | 'failed', detail: string): void {
  clearBubbleTimeoutTimer(id)
  bubbles.value = bubbles.value.map((b) => (b.id === id ? { ...b, status, detail } : b))
  scheduleBubbleFade(id)
}

function addBubble(bubbleText: string, ts: number): void {
  const id = `${ts}_${bubbleText}`
  if (bubbles.value.some((b) => b.id === id)) return  // 防御性去重
  bubbles.value = [...bubbles.value, { id, text: bubbleText, ts, status: 'pending' }]
  // 超上限：先移最旧的（含清它的计时器）
  while (bubbles.value.length > MAX_BUBBLES) {
    removeBubble(bubbles.value[0].id)
  }
  bubbleTimeoutTimers.set(
    id,
    setTimeout(() => {
      const b = bubbles.value.find((x) => x.id === id)
      if (b && b.status === 'pending') {
        resolveBubble(id, 'failed', t('cmdinput.timeoutError'))
      }
    }, SEND_TIMEOUT_MS),
  )
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

// submitCommand：文字模式 sendText 和语音模式 onVoiceRecognized 的公共路径。
// 气泡由 command_received ack 驱动开卡（watch(lastReceived)），这里只发送 + 冷却。
function submitCommand(t: string) {
  emit('send', {
    type: 'command',
    client_id: makeClientId(),
    issued_at: Date.now() / 1000,
    text: t,
  })
  startCooldown()
}

function sendText() {
  const t = text.value.trim()
  if (!t || !canSendNow.value) return
  text.value = ''
  submitCommand(t)
}

// 语音识别出 final → 走公共 command 管线（等价 sendText，复用气泡队列 / 冷却）
function onVoiceRecognized(recognizedText: string) {
  const t = recognizedText.trim()
  // 空识别不发；WS 未连 / 非 playing 阶段 / 冷却中也不发（不再等上一条 resolve，允许并发）
  if (!t || !props.canSend || cooldownLeft.value > 0) return
  submitCommand(t)
}

// server 确认收到 → 开一个 pending 气泡
watch(
  () => props.lastReceived,
  (received) => {
    if (!received) return
    addBubble(received.text, received.ts)
  },
)

// echo 到达 → 按文本匹配最旧的 pending 气泡（FIFO），切 done/failed + 更新历史 entry
watch(
  () => props.lastEcho,
  (echo) => {
    if (!echo) return
    const pending = bubbles.value.filter((b) => b.status === 'pending')
    if (pending.length === 0) return
    const match = pending.find((b) => b.text === echo.user_text) ?? pending[0]
    const interp = echo.interpretation ?? ''
    const failed = interp.startsWith('[解析失败]') ||
                   interp.startsWith('[模糊]') ||
                   interp.startsWith('[失败]')
    resolveBubble(match.id, failed ? 'failed' : 'done', interp)
  },
)

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendText()
  }
}

const canSendNow = computed(
  () => props.canSend && cooldownLeft.value === 0 && text.value.trim().length > 0
)

// 微信式：文字模式下发送按钮默认不显示，输入框有内容才出现（2026-06-10 用户）。
const hasText = computed(() => text.value.trim().length > 0)

// 发送按钮不再跟单条命令的 sending/done/failed 状态挂钩（那套状态现在由气泡队列展示，
// 一个按钮已经代表不了多条并发命令）；只保留冷却倒计时 / 常态"发送"两态。
const buttonLabel = computed(() => (cooldownLeft.value > 0 ? `${cooldownLeft.value}s` : t('cmdinput.statusSend')))

onBeforeUnmount(() => {
  if (cooldownTimer !== null) clearInterval(cooldownTimer)
  for (const id of [...bubbleFadeTimers.keys()]) clearBubbleFadeTimer(id)
  for (const id of [...bubbleTimeoutTimers.keys()]) clearBubbleTimeoutTimer(id)
})
</script>

<template>
  <div class="flex flex-col gap-1 relative">
    <!-- 非 HTTPS 一次性提示（voiceSupported=false 时显示，可关闭） -->
    <div
      v-if="showHttpsHint"
      data-testid="https-hint"
      class="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-surface-3 border border-border/50 text-xs text-muted"
    >
      <span class="flex-1">{{ t('cmdinput.httpsHint') }}</span>
      <button
        type="button"
        data-testid="https-hint-close"
        class="shrink-0 text-muted hover:text-white text-base leading-none"
        :aria-label="t('cmdinput.httpsHintClose')"
        @click="dismissHttpsHint"
      >×</button>
    </div>

    <!-- 命令气泡队列(2026-07-08 用户:每条命令一个气泡,识别中/成功/失败互不覆盖,
         非阻塞并存;不挡输入框——放在输入框正上方一小块可滚区域) -->
    <CommandBubbleQueue :bubbles="bubbles" />

    <!-- 输入行（从左到右）：历史 | 中间(输入框/语音长条) | 语音文字切换 | 发送(仅文字模式) -->
    <div class="flex items-center gap-2">
      <!-- 1. 历史按钮（最左） -->
      <button
        type="button"
        data-testid="cmd-history-btn"
        class="shrink-0 w-9 h-9 flex items-center justify-center rounded-lg
               bg-surface-3 border border-border text-muted hover:text-white
               hover:bg-surface-4 transition-colors"
        :aria-label="t('cmdinput.historyAria')"
        @click="showHistory = true"
      >
        <svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="9"/>
          <path d="M12 7v5l3 2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>

      <!-- 2. 中间：语音模式 = 长条(按住说话) / 文字模式 = 输入框 -->
      <VoiceInput
        v-if="inputMode === 'voice'"
        :send-audio-chunk="props.sendAudioChunk"
        :send-audio-end="props.sendAudioEnd"
        :send-audio-cancel="props.sendAudioCancel"
        :last-transcript="props.lastTranscript"
        @recognized="onVoiceRecognized"
      />
      <input
        v-else
        v-model="text"
        type="text"
        :placeholder="t('cmdinput.placeholder')"
        class="flex-1 min-w-0 rounded-lg bg-surface-3 border border-border text-sm text-white placeholder-muted
               px-3 py-2 focus:outline-none focus:border-accent transition-colors"
        :disabled="!props.canSend"
        @keydown="onKeydown"
      />

      <!-- 3. 语音/文字切换按钮（中间右侧；仅 voiceSupported 时显示） -->
      <button
        v-if="voiceSupported"
        type="button"
        data-testid="input-mode-toggle"
        class="shrink-0 w-9 h-9 flex items-center justify-center rounded-lg
               bg-surface-3 border border-border text-muted hover:text-white
               hover:bg-surface-4 transition-colors"
        :aria-label="inputMode === 'voice' ? t('cmdinput.switchToText') : t('cmdinput.switchToVoice')"
        @click="toggleInputMode"
      >
        <!-- 语音模式中：显示键盘图标（点击 → 切文字） -->
        <svg v-if="inputMode === 'voice'" viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="2" y="6" width="20" height="12" rx="2"/>
          <line x1="6" y1="10" x2="6" y2="10" stroke-linecap="round" stroke-width="2.5"/>
          <line x1="10" y1="10" x2="10" y2="10" stroke-linecap="round" stroke-width="2.5"/>
          <line x1="14" y1="10" x2="14" y2="10" stroke-linecap="round" stroke-width="2.5"/>
          <line x1="18" y1="10" x2="18" y2="10" stroke-linecap="round" stroke-width="2.5"/>
          <line x1="6" y1="14" x2="6" y2="14" stroke-linecap="round" stroke-width="2.5"/>
          <line x1="10" y1="14" x2="18" y2="14" stroke-linecap="round" stroke-width="2"/>
        </svg>
        <!-- 文字模式中：显示麦克风图标（点击 → 切语音） -->
        <svg v-else viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="9" y="2" width="6" height="11" rx="3" fill="none"/>
          <path d="M5 10a7 7 0 0 0 14 0" stroke-linecap="round"/>
          <line x1="12" y1="17" x2="12" y2="21" stroke-linecap="round"/>
          <line x1="9" y1="21" x2="15" y2="21" stroke-linecap="round"/>
        </svg>
      </button>

      <!-- 4. 发送按钮（最右；仅文字模式 + 输入框有内容才显示，微信式；语音模式靠松手发送）
           不再阻塞等上一条命令 resolve——发送反馈现在看气泡队列，这里只剩冷却倒计时。 -->
      <button
        v-if="inputMode === 'text' && hasText"
        type="button"
        data-testid="cmd-send-btn"
        class="shrink-0 rounded-lg px-3 py-2 text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed min-w-[72px] flex items-center justify-center gap-1.5
               bg-accent text-surface hover:bg-blue-400 active:scale-95"
        :disabled="!canSendNow"
        @click="sendText"
      >
        <span>{{ buttonLabel }}</span>
      </button>
    </div>

    <!-- 历史 modal(2026-05-23 用户) -->
    <Teleport to="body">
      <div
        v-if="showHistory"
        class="fixed inset-0 z-50 bg-black/60 flex items-end sm:items-center justify-center overscroll-contain"
        data-testid="cmd-history-modal"
        @click.self="showHistory = false"
        @touchmove.self.prevent
        @wheel.self.prevent
      >
        <div class="w-full sm:max-w-md max-h-[80vh] bg-surface-2 border-t sm:border border-border
                    rounded-t-2xl sm:rounded-xl flex flex-col">
          <div class="flex items-center justify-between px-4 py-3 border-b border-border">
            <p class="text-sm font-semibold text-white">{{ t('cmdinput.historyTitle') }}</p>
            <button
              type="button"
              class="text-muted hover:text-white text-xl leading-none px-1"
              @click="showHistory = false"
            >×</button>
          </div>
          <div class="overflow-y-auto overscroll-contain flex-1 px-3 py-2">
            <p v-if="props.recentCommands.length === 0" class="text-sm text-muted text-center py-8">
              {{ t('cmdinput.historyEmpty') }}
            </p>
            <!-- 三层展开:输入文本 / 识别解读 / 指令卡 + 状态。最新在上。 -->
            <div v-else class="flex flex-col gap-1.5">
              <CommandHistoryItem
                v-for="(cmd, i) in [...props.recentCommands].reverse()"
                :key="`${cmd.ts}_${i}`"
                :cmd="cmd"
              />
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
