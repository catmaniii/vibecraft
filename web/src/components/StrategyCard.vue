<script setup lang="ts">
// 单张剧本卡片（P0-10）
// props: stage（'opening'|'midgame'|'lategame'）、slot（StrategySlotView | null）、isActive
import type { StrategySlotView } from '@/types'

const props = defineProps<{
  stage: 'opening' | 'midgame' | 'lategame'
  slot: StrategySlotView | null
  isActive?: boolean
}>()

const stageLabel: Record<string, string> = {
  opening: '开局',
  midgame: '中期',
  lategame: '后期',
}
</script>

<template>
  <div
    class="rounded-lg border p-3 transition-all"
    :class="{
      'border-accent bg-surface-2': props.isActive,
      'border-border bg-surface-3': !props.isActive,
    }"
  >
    <!-- 阶段标题行 -->
    <div class="flex items-center justify-between mb-1">
      <span class="text-xs font-semibold text-muted uppercase tracking-wider">
        {{ stageLabel[props.stage] ?? props.stage }}
      </span>
      <span
        v-if="props.isActive"
        class="text-xs font-bold text-accent"
      >当前</span>
    </div>

    <!-- 剧本名 / 未设置 -->
    <div v-if="props.slot">
      <p class="text-sm font-semibold text-white">{{ props.slot.display }}</p>
      <!-- Phases 横排展示（仅 opening 有 phases）-->
      <div
        v-if="props.slot.phases && props.slot.phases.length > 0"
        class="flex flex-wrap gap-x-1.5 mt-1"
      >
        <template v-for="(phase, idx) in props.slot.phases" :key="phase.id">
          <span class="text-xs text-muted">{{ phase.display }}</span>
          <span v-if="idx < props.slot.phases!.length - 1" class="text-xs text-border">·</span>
        </template>
      </div>
    </div>
    <div v-else>
      <p class="text-sm text-muted italic">（未设置）</p>
    </div>
  </div>
</template>
