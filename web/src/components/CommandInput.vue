<script setup lang="ts">
// 单行指令输入:input + 发送按钮(10s 冷却);去掉了按住说话和"发号施令"标题
import { ref, computed, onBeforeUnmount } from 'vue'
import type { CommandFrame } from '@/types'

const props = defineProps<{
  canSend: boolean
}>()

const emit = defineEmits<{
  send: [frame: CommandFrame]
}>()

const text = ref('')
const cooldownLeft = ref(0)  // 秒,>0 表示发送按钮在冷却
const COOLDOWN_S = 5

let cooldownTimer: ReturnType<typeof setInterval> | null = null

function makeClientId(): string {
  return 'c_' + Math.random().toString(36).slice(2, 7)
}

function startCooldown() {
  cooldownLeft.value = COOLDOWN_S
  if (cooldownTimer !== null) clearInterval(cooldownTimer)
  cooldownTimer = setInterval(() => {
    cooldownLeft.value -= 1
    if (cooldownLeft.value <= 0) {
      cooldownLeft.value = 0
      if (cooldownTimer !== null) {
        clearInterval(cooldownTimer)
        cooldownTimer = null
      }
    }
  }, 1000)
}

function sendText() {
  const t = text.value.trim()
  if (!t || !canSendNow.value) return
  emit('send', {
    type: 'command',
    client_id: makeClientId(),
    issued_at: Date.now() / 1000,
    text: t,
  })
  text.value = ''
  startCooldown()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendText()
  }
}

const canSendNow = computed(
  () => props.canSend && cooldownLeft.value === 0 && text.value.trim().length > 0
)

const buttonLabel = computed(() => {
  if (cooldownLeft.value > 0) return `${cooldownLeft.value}s`
  return '发送'
})

onBeforeUnmount(() => {
  if (cooldownTimer !== null) clearInterval(cooldownTimer)
})
</script>

<template>
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
      class="shrink-0 rounded-lg px-4 py-2 text-sm font-medium transition-colors
             bg-accent text-surface disabled:opacity-40 disabled:cursor-not-allowed
             hover:bg-blue-400 active:scale-95 min-w-[64px]"
      :disabled="!canSendNow"
      @click="sendText"
    >
      {{ buttonLabel }}
    </button>
  </div>
</template>
