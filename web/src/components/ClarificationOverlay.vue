<script setup lang="ts">
// 2026-05-24 用户: LLM 不确定时给玩家 2-4 个选项,
// 这个组件覆盖输入框上方(类似确认界面),让玩家点选或 ❌ 取消。
import type { PendingClarificationView } from '@/types'
import { t } from '@/i18n'

defineProps<{
  pending: PendingClarificationView
}>()

const emit = defineEmits<{
  select: [optionIndex: number]
  cancel: []
}>()
</script>

<template>
  <!-- 2026-05-25 用户:之前 bg-cyan-500/10 太透明,玩家看不清选项。
       改 bg-surface-2 实色 + cyan 边框高亮(语义"待玩家选")。 -->
  <div
    data-testid="clarification-overlay"
    class="rounded-xl border border-cyan-500/60 bg-surface-2 px-4 py-3 shadow-lg"
  >
    <div class="flex items-center justify-between gap-2 mb-2">
      <span class="text-xs font-semibold text-cyan-400 uppercase tracking-wider">
        {{ t('clarify.title') }}
      </span>
      <button
        type="button"
        :aria-label="t('clarify.cancel')"
        class="text-muted hover:text-white text-lg leading-none px-1"
        @click="emit('cancel')"
      >×</button>
    </div>

    <p class="text-sm font-semibold text-white mb-1">{{ pending.question }}</p>
    <p v-if="pending.source_text" class="text-[11px] text-muted italic mb-2">
      {{ t('clarify.sourceText', { text: pending.source_text }) }}
    </p>

    <div class="space-y-1.5">
      <button
        v-for="opt in pending.options"
        :key="opt.index"
        type="button"
        class="w-full text-left rounded-md bg-surface-3 hover:bg-cyan-500/20
               border border-border hover:border-cyan-500/40
               px-3 py-2 transition-colors group"
        @click="emit('select', opt.index)"
      >
        <div class="flex items-center justify-between gap-2">
          <span class="text-sm font-semibold text-white group-hover:text-cyan-300">
            {{ opt.label }}
          </span>
          <span
            v-if="opt.directive_count > 0"
            class="text-[10px] text-muted uppercase shrink-0"
          >{{ t('clarify.directiveCount', { n: opt.directive_count }) }}</span>
        </div>
        <p
          v-if="opt.interpretation_zh"
          class="text-[11px] text-muted mt-0.5"
        >{{ opt.interpretation_zh }}</p>
      </button>
    </div>
  </div>
</template>
