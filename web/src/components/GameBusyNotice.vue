<script setup lang="ts">
// 游戏进行中提醒页（2026-06-17 用户）：后加入者连上 server 发现已有玩家在对局
// （room.state != 'lobby'）→ 不弹回入口、不排队，只显示这个提醒，让玩家稍后重试。
// 触发判据 + 连接处理在 App.vue（兜底 watcher 分流：state!=lobby → 置 gameBusy 标志）。
import { t } from '@/i18n'

defineProps<{
  // 正在游戏的玩家名（从断连前的 room_state 快照取，可空 → 用泛称）
  playerName?: string | null
}>()

const emit = defineEmits<{
  retry: []
  back: []
}>()
</script>

<template>
  <div class="min-h-screen bg-surface flex flex-col items-center justify-center px-5 py-8">
    <div class="w-full max-w-sm flex flex-col gap-6 text-center">
      <!-- 图标 + 标题 -->
      <div class="flex flex-col items-center gap-3">
        <div class="w-16 h-16 rounded-full bg-accent/15 border border-accent/40 flex items-center justify-center">
          <svg viewBox="0 0 24 24" class="h-8 w-8 text-accent" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <circle cx="12" cy="12" r="9" stroke-linecap="round" stroke-linejoin="round" />
            <path d="M12 7v5l3 2" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </div>
        <p class="text-xl font-bold text-white">{{ t('busy.title') }}</p>
      </div>

      <!-- 说明文案 -->
      <p class="text-sm text-muted leading-relaxed">
        <template v-if="playerName">{{ t('busy.descWithName', { name: playerName }) }}</template>
        <template v-else>{{ t('busy.descNoName') }}</template>
      </p>

      <!-- 操作按钮 -->
      <div class="flex flex-col gap-2.5">
        <button
          type="button"
          class="w-full py-3 rounded-xl text-base font-bold bg-accent text-surface hover:bg-accent/90 active:scale-[0.98] transition-colors"
          @click="emit('retry')"
          data-testid="game-busy-retry"
        >
          {{ t('busy.retry') }}
        </button>
        <button
          type="button"
          class="w-full py-2.5 rounded-lg border border-border bg-surface-2 text-muted text-sm font-medium hover:text-white hover:border-accent/50 active:scale-[0.99] transition-colors"
          @click="emit('back')"
          data-testid="game-busy-back"
        >
          {{ t('busy.back') }}
        </button>
      </div>
    </div>
  </div>
</template>
