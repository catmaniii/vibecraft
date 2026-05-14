<script setup lang="ts">
// 指令输入区：文字输入 + 麦克风按钮（M1.3 录音只触发 Web Speech API；
// M3 完整驾驶舱再做实时 streaming 录音）
import { ref, computed } from 'vue'
import type { CommandFrame } from '@/types'

const props = defineProps<{
  // 是否可发帧（WS 已连 + 游戏已 playing）
  canSend: boolean
}>()

const emit = defineEmits<{
  send: [frame: CommandFrame]
}>()

const text = ref('')
const isRecording = ref(false)
let recognition: SpeechRecognition | null = null

// 生成简单 client_id（非密码级，只用于日志关联）
function makeClientId(): string {
  return 'c_' + Math.random().toString(36).slice(2, 7)
}

function sendText() {
  const t = text.value.trim()
  if (!t || !props.canSend) return
  emit('send', {
    type: 'command',
    client_id: makeClientId(),
    issued_at: Date.now() / 1000,
    text: t,
  })
  text.value = ''
}

function onKeydown(e: KeyboardEvent) {
  // Enter 发送，Shift+Enter 换行
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendText()
  }
}

// ---- 语音输入（Web Speech API）----
// MVP：按住开始识别，松开发送；识别结果填入文本框（而非直接发帧）
// 让玩家有机会确认 / 编辑后再按发送
const speechAvailable = computed(() =>
  'SpeechRecognition' in window || 'webkitSpeechRecognition' in window
)

function startRecording() {
  if (!speechAvailable.value) return
  const SR = (window.SpeechRecognition || (window as any).webkitSpeechRecognition) as typeof SpeechRecognition
  recognition = new SR()
  recognition.lang = 'zh-CN'
  recognition.interimResults = false
  recognition.maxAlternatives = 1

  recognition.onresult = (e: SpeechRecognitionEvent) => {
    const transcript = e.results[0][0].transcript
    text.value = transcript
  }
  recognition.onend = () => {
    isRecording.value = false
  }
  recognition.onerror = () => {
    isRecording.value = false
  }

  recognition.start()
  isRecording.value = true
}

function stopRecording() {
  recognition?.stop()
  isRecording.value = false
}
</script>

<template>
  <div class="flex flex-col gap-2">
    <!-- 文字输入框 -->
    <textarea
      v-model="text"
      rows="2"
      placeholder="输入指令，如「切 IAC」或「两矿凤凰」..."
      class="w-full rounded-lg bg-surface-3 border border-border text-sm text-white placeholder-muted
             px-3 py-2 resize-none focus:outline-none focus:border-accent transition-colors"
      :disabled="!canSend"
      @keydown="onKeydown"
    ></textarea>

    <!-- 操作行：麦克风 + 发送 -->
    <div class="flex gap-2">
      <!-- 录音按钮（按下开始，松开停止）-->
      <button
        v-if="speechAvailable"
        class="flex-none rounded-lg px-3 py-2 text-sm font-medium transition-colors
               border border-border"
        :class="isRecording
          ? 'bg-danger text-white border-danger animate-pulse'
          : 'bg-surface-3 text-muted hover:text-white hover:border-accent'"
        :disabled="!canSend"
        @pointerdown.prevent="startRecording"
        @pointerup.prevent="stopRecording"
        @pointerleave="stopRecording"
        title="按住说话"
      >
        {{ isRecording ? '松开发送' : '按住说话' }}
      </button>

      <!-- 文字发送按钮 -->
      <button
        class="flex-1 rounded-lg px-4 py-2 text-sm font-medium transition-colors
               bg-accent text-surface disabled:opacity-40 disabled:cursor-not-allowed
               hover:bg-blue-400 active:scale-95"
        :disabled="!canSend || !text.trim()"
        @click="sendText"
      >
        发送
      </button>
    </div>

    <!-- 不可发时的原因提示 -->
    <p v-if="!canSend" class="text-xs text-muted text-center">
      等待游戏开始后可发指令
    </p>
  </div>
</template>
