<script setup lang="ts">
// bot 推荐下一阶段剧本卡片(玩家可 [确认] / [忽略])
import { computed } from 'vue'
import type { RecommendationView } from '@/types'
import { t } from '@/i18n'

defineProps<{
  recommendation: RecommendationView
}>()

const emit = defineEmits<{
  confirm: []
  dismiss: []
}>()

// source → 标签 + 配色(响应 locale 切换)
const sourceMap = computed<Record<string, { text: string; cls: string }>>(() => ({
  default: { text: t('decision.sourceDefault'), cls: 'bg-blue-500/20 text-blue-300 border-blue-500/40' },
  abort:   { text: t('decision.sourceAbort'), cls: 'bg-danger/20 text-danger border-danger/40' },
  llm:     { text: t('decision.sourceLlm'), cls: 'bg-purple-500/20 text-purple-300 border-purple-500/40' },
}))
</script>

<template>
  <!-- 2026-05-25 用户:从全宽 inline 卡 → 改"当前宏观策略"框内 absolute overlay。
       推荐内容是"切宏观策略",叠加在宏观策略卡片上(像 AutoSwitchToast / PendingForceCard)。
       父容器需 position: relative。z-[15] 介于 AutoSwitchToast(z-10)与
       PendingForceCard(z-20)之间:玩家硬转待确认 > bot 推荐 > 已发生的 auto switch toast。 -->
  <div
    class="absolute inset-0 z-[15] flex flex-col justify-center
           rounded-xl border border-blue-500/50 bg-blue-500/15 backdrop-blur px-4 py-3 shadow-lg"
  >
    <div class="flex items-center justify-between gap-2 mb-1.5">
      <div class="flex items-center gap-1.5">
        <span class="text-xs font-semibold text-blue-300 uppercase tracking-wider">
          {{ t('decision.botRecommendTitle') }}
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
    <p class="text-xs text-white/70 italic mt-0.5">{{ recommendation.reason }}</p>
    <div class="flex gap-2 mt-2">
      <button
        type="button"
        class="flex-1 rounded-md bg-success/20 hover:bg-success/30 border border-success/40 text-success text-xs font-semibold py-1.5 transition-colors"
        @click="emit('confirm')"
      >{{ t('decision.confirm') }}</button>
      <button
        type="button"
        class="flex-1 rounded-md bg-surface-3 hover:bg-surface-2 border border-border text-muted text-xs font-semibold py-1.5 transition-colors"
        @click="emit('dismiss')"
      >{{ t('decision.dismiss') }}</button>
    </div>
  </div>
</template>
