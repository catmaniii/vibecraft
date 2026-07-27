<script setup lang="ts">
// 文字聊天浮层（房间级，经 VPS/PC server 广播转发）。
// - 进房后（大厅 + 对局中）可用；入口页不挂载（App 用 showMain && amIInRoom gate）。
// - 折叠为右下角气泡，展开为消息列表 + 输入框。
// - 不本地 append：发出去靠 server 广播回显（含 server 分配的 id/ts），myPid 标本人。
// - 文本一律 {{ }} 渲染，绝不 v-html（防 XSS：name/text 都来自不可信客户端）。
import { ref, nextTick, watch, onMounted, computed } from 'vue'
import type { ChatMsg } from '@/types'
import { t } from '@/i18n'

const props = defineProps<{
  messages: readonly ChatMsg[]
  myPid: string
  sendChat: (text: string) => void
  requestHistory: () => void
  // 对局中（底部有指令输入栏 + 语音/文字切换按钮）→ 气泡抬高让位，避免遮挡
  raised?: boolean
}>()

const open = ref(false)
const draft = ref('')
const listEl = ref<HTMLElement | null>(null)
const bubbleEl = ref<HTMLButtonElement | null>(null)

// 可拖动位置（2026-07-26 用户：聊天气泡挡东西，改成可拖走）。存 right/bottom 距边偏移(px)到
// localStorage;null=默认右下角(raised 时抬高让指令栏)。拖拽区分点击(toggle)/移动(挪位)。
type Pos = { right: number; bottom: number }
function loadPos(): Pos | null {
  try {
    const raw = localStorage.getItem('vc_chat_pos')
    if (raw) {
      const p = JSON.parse(raw)
      if (typeof p?.right === 'number' && typeof p?.bottom === 'number') return p
    }
  } catch { /* ignore */ }
  return null
}
const chatPos = ref<Pos | null>(loadPos())
function defaultPos(): Pos {
  return { right: 12, bottom: props.raised ? 64 : 12 }
}
const effPos = computed<Pos>(() => chatPos.value ?? defaultPos())

let dragStart: { x: number; y: number; right: number; bottom: number } | null = null
let dragMoved = false
const _DRAG_THRESH = 6
function _clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v))
}
function onBubbleDown(e: PointerEvent) {
  const cur = effPos.value
  dragStart = { x: e.clientX, y: e.clientY, right: cur.right, bottom: cur.bottom }
  dragMoved = false
  bubbleEl.value?.setPointerCapture(e.pointerId)
}
function onBubbleMove(e: PointerEvent) {
  if (!dragStart) return
  const dx = e.clientX - dragStart.x
  const dy = e.clientY - dragStart.y
  if (Math.abs(dx) > _DRAG_THRESH || Math.abs(dy) > _DRAG_THRESH) dragMoved = true
  if (dragMoved) {
    // 手指右移 → right 偏移减小;下移 → bottom 偏移减小。clamp 进视口。
    chatPos.value = {
      right: _clamp(dragStart.right - dx, 4, window.innerWidth - 56),
      bottom: _clamp(dragStart.bottom - dy, 4, window.innerHeight - 56),
    }
  }
}
function onBubbleUp(e: PointerEvent) {
  bubbleEl.value?.releasePointerCapture(e.pointerId)
  if (dragStart && !dragMoved) {
    toggle()  // 没移动 = 点击 → 开关聊天
  } else if (dragMoved && chatPos.value) {
    try { localStorage.setItem('vc_chat_pos', JSON.stringify(chatPos.value)) } catch { /* ignore */ }
  }
  dragStart = null
}

// 未读计数：折叠时来新消息累加，展开时清零
const lastSeenId = ref(0)
const unread = computed(() => {
  if (open.value) return 0
  return props.messages.filter(m => m.id > lastSeenId.value).length
})

function scrollToBottom() {
  nextTick(() => {
    const el = listEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function markSeen() {
  const msgs = props.messages
  if (msgs.length) lastSeenId.value = msgs[msgs.length - 1].id
}

function toggle() {
  open.value = !open.value
  if (open.value) {
    markSeen()
    scrollToBottom()
  }
}

function onSend() {
  const t = draft.value.trim()
  if (!t) return
  props.sendChat(t)
  draft.value = ''
}

// 新消息到达：展开态自动滚底 + 标记已读
watch(
  () => props.messages.length,
  () => {
    if (open.value) {
      markSeen()
      scrollToBottom()
    }
  },
)

// 进房挂载时拉一次历史（重连/刷新补全）
onMounted(() => {
  props.requestHistory()
})

// 简单本地时间（HH:MM）
function fmtTime(ts: number): string {
  const d = new Date(ts * 1000)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${hh}:${mm}`
}
</script>

<template>
  <!-- 右下角浮层：folded 气泡 / 展开面板。
       raised（对局中）→ 抬高到指令输入栏之上，避免遮挡语音/文字切换按钮。 -->
  <div
    class="fixed z-50 flex flex-col items-end select-none"
    :style="{ right: effPos.right + 'px', bottom: effPos.bottom + 'px' }"
  >
    <!-- 展开面板 -->
    <div
      v-if="open"
      class="mb-2 w-72 max-w-[80vw] h-80 max-h-[60vh] flex flex-col rounded-lg bg-surface-2 border border-border shadow-xl"
    >
      <div class="flex items-center justify-between px-3 py-2 border-b border-border shrink-0">
        <span class="text-sm font-semibold text-accent">{{ t('chat.title') }}</span>
        <button
          type="button"
          class="text-muted hover:text-white text-lg leading-none"
          @click="toggle"
        >×</button>
      </div>

      <!-- 消息列表 -->
      <div ref="listEl" class="flex-1 min-h-0 overflow-y-auto px-3 py-2 space-y-1.5">
        <div
          v-if="messages.length === 0"
          class="text-xs text-muted text-center mt-4"
        >{{ t('chat.empty') }}</div>
        <div
          v-for="m in messages"
          :key="m.id"
          class="flex flex-col"
          :class="m.pid === myPid ? 'items-end' : 'items-start'"
        >
          <div class="text-[10px] text-muted px-1">
            <span :class="m.pid === myPid ? 'text-accent' : ''">{{ m.name || t('chat.unknownPlayer') }}</span>
            <span class="ml-1">{{ fmtTime(m.ts) }}</span>
          </div>
          <div
            class="max-w-[85%] px-2.5 py-1.5 rounded-lg text-sm break-words whitespace-pre-wrap"
            :class="m.pid === myPid
              ? 'bg-accent/20 text-white rounded-br-sm'
              : 'bg-surface-3 text-white rounded-bl-sm'"
          >{{ m.text }}</div>
        </div>
      </div>

      <!-- 输入框 -->
      <div class="flex items-center gap-2 px-2 py-2 border-t border-border shrink-0">
        <input
          v-model="draft"
          type="text"
          maxlength="500"
          :placeholder="t('chat.placeholder')"
          class="flex-1 min-w-0 px-2.5 py-1.5 rounded bg-surface-3 text-white text-sm border border-border focus:border-accent outline-none"
          @keydown.enter="onSend"
        />
        <button
          type="button"
          class="px-3 py-1.5 rounded text-sm font-semibold bg-accent/80 text-white hover:bg-accent disabled:opacity-40 transition-colors"
          :disabled="!draft.trim()"
          @click="onSend"
        >{{ t('chat.send') }}</button>
      </div>
    </div>

    <!-- 折叠气泡（可拖动挪位；点击=开关，拖动=挪走。2026-07-26 用户）-->
    <button
      ref="bubbleEl"
      type="button"
      class="relative flex items-center gap-1.5 px-3 py-2 rounded-full bg-surface-2 border border-border shadow-lg text-sm font-semibold text-white hover:bg-surface-3 transition-colors touch-none cursor-move"
      @pointerdown="onBubbleDown"
      @pointermove="onBubbleMove"
      @pointerup="onBubbleUp"
      @pointercancel="onBubbleUp"
    >
      <span>💬</span>
      <span>{{ t('chat.title') }}</span>
      <span
        v-if="unread > 0"
        class="absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1 flex items-center justify-center rounded-full bg-danger text-white text-[10px] font-bold"
      >{{ unread > 99 ? '99+' : unread }}</span>
    </button>
  </div>
</template>
