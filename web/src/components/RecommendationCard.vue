<script setup lang="ts">
// bot 推荐下一阶段剧本卡片(玩家可 [确认] / [忽略])
import type { RecommendationView } from '@/types'

defineProps<{
  recommendation: RecommendationView
}>()

const emit = defineEmits<{
  confirm: []
  dismiss: []
}>()

// source → 中文 + 配色
const sourceMap: Record<string, { text: string; cls: string }> = {
  default: { text: '默认转', cls: 'bg-blue-500/20 text-blue-300 border-blue-500/40' },
  abort:   { text: '应急转', cls: 'bg-danger/20 text-danger border-danger/40' },
  llm:     { text: 'LLM 判断', cls: 'bg-purple-500/20 text-purple-300 border-purple-500/40' },
}
</script>

<template>
  <div class="rounded-lg border border-blue-500/40 bg-blue-500/5 p-3 mt-2">
    <div class="flex items-center justify-between gap-2 mb-1.5">
      <div class="flex items-center gap-1.5">
        <span class="text-xs font-semibold text-blue-300 uppercase tracking-wider">
          🤖 bot 推荐
        </span>
        <span
          v-if="sourceMap[recommendation.source]"
          class="text-[10px] px-1.5 py-0.5 rounded border leading-none"
          :class="sourceMap[recommendation.source].cls"
        >{{ sourceMap[recommendation.source].text }}</span>
      </div>
      <span class="text-[10px] text-muted uppercase">{{ recommendation.stage }}</span>
    </div>
    <p class="text-sm font-semibold text-white">{{ recommendation.display_name }}</p>
    <p class="text-xs text-muted italic mt-0.5">{{ recommendation.reason }}</p>
    <div class="flex gap-2 mt-2">
      <button
        type="button"
        class="flex-1 rounded-md bg-success/20 hover:bg-success/30 border border-success/40 text-success text-xs font-semibold py-1.5 transition-colors"
        @click="emit('confirm')"
      >✓ 确认</button>
      <button
        type="button"
        class="flex-1 rounded-md bg-surface-3 hover:bg-surface-2 border border-border text-muted text-xs font-semibold py-1.5 transition-colors"
        @click="emit('dismiss')"
      >× 忽略</button>
    </div>
  </div>
</template>
