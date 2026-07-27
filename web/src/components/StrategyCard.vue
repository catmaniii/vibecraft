<script setup lang="ts">
// 单张剧本卡片(P0-10 + phase stepper + 来源 badge + 完成态)
import { computed } from 'vue'
import type { StrategySlotView } from '@/types'
import { t } from '@/i18n'

const props = defineProps<{
  stage: 'opening' | 'midgame' | 'lategame'
  slot: StrategySlotView | null
  isActive?: boolean
}>()

function getStageLabel(stage: string): string {
  if (stage === 'opening') return t('strategy.stageOpening')
  if (stage === 'midgame') return t('strategy.stageMidgame')
  if (stage === 'lategame') return t('strategy.stageLategame')
  return stage
}

// 当前 phase index;后端 PhaseTracker 推断 current_phase_id 决定哪些已完成
const currentPhaseIdx = computed(() => {
  if (!props.slot?.phases || !props.slot.current_phase_id) return -1
  return props.slot.phases.findIndex(p => p.id === props.slot!.current_phase_id)
})

// set_by 来源对应的标签 + 颜色(玩家=绿,自动转=蓝,bot 默认=灰)
const setByBadge = computed(() => {
  if (!props.slot) return null
  const map: Record<string, { text: string; cls: string }> = {
    voice: { text: t('strategy.setByVoice'), cls: 'bg-success/20 text-success border-success/40' },
    auto_transition: { text: t('strategy.setByAuto'), cls: 'bg-blue-500/20 text-blue-300 border-blue-500/40' },
    bot_internal: { text: t('strategy.setByBot'), cls: 'bg-muted/20 text-muted border-muted/40' },
    abort: { text: t('strategy.setByAbort'), cls: 'bg-danger/20 text-danger border-danger/40' },
  }
  return map[props.slot.set_by] ?? null
})

// phase 全完成态:卡片暗化 + 显示"✓ 已完成,等待下一阶段"
const allComplete = computed(() => Boolean(props.slot?.all_phases_complete))
</script>

<template>
  <!-- 无外层 border/bg/padding:由父容器承担,避免双层圆角嵌套挤占空间 -->
  <div
    class="transition-opacity"
    :class="{ 'opacity-70': allComplete }"
  >
    <!-- 阶段标题行:stage label + 来源 badge + 当前/完成 标签 -->
    <div class="flex items-center justify-between mb-1 gap-1">
      <div class="flex items-center gap-1.5">
        <span class="text-xs font-semibold text-muted uppercase tracking-wider">
          {{ getStageLabel(props.stage) }}
        </span>
        <span
          v-if="setByBadge && props.slot"
          class="text-[10px] px-1.5 py-0.5 rounded border leading-none"
          :class="setByBadge.cls"
        >{{ setByBadge.text }}</span>
      </div>
      <span v-if="allComplete" class="text-xs font-bold text-success">{{ t('strategy.allComplete') }}</span>
      <span v-else-if="props.isActive" class="text-xs font-bold text-accent">{{ t('strategy.current') }}</span>
    </div>

    <!-- 剧本名 / 未设置 -->
    <div v-if="props.slot">
      <p class="text-sm font-semibold text-white">{{ props.slot.display }}</p>

      <!-- Phases stepper -->
      <div
        v-if="props.slot.phases && props.slot.phases.length > 0"
        class="flex flex-wrap items-center gap-x-1.5 gap-y-1 mt-1"
      >
        <template v-for="(phase, idx) in props.slot.phases" :key="phase.id">
          <span
            class="inline-flex items-center gap-1 text-xs leading-none"
            :class="
              currentPhaseIdx < 0
                ? 'text-white/70'
                : allComplete
                  ? 'text-success'
                  : idx < currentPhaseIdx
                    ? 'text-success'
                    : idx === currentPhaseIdx
                      ? 'text-accent font-bold'
                      : 'text-white/60'
            "
          >
            <span class="font-mono">{{
              currentPhaseIdx < 0
                ? '·'
                : allComplete || idx < currentPhaseIdx
                  ? '✓'
                  : idx === currentPhaseIdx
                    ? '▶'
                    : '○'
            }}</span>
            <span>{{ phase.display }}</span>
          </span>
          <span v-if="idx < props.slot.phases!.length - 1" class="text-xs text-white/40">›</span>
        </template>
      </div>

      <!-- 当前 phase subtitle 或完成提示 -->
      <p
        v-if="allComplete"
        class="text-xs text-success/80 mt-0.5 italic"
      >{{ t('strategy.allCompleteHint') }}</p>
      <p
        v-else-if="currentPhaseIdx >= 0 && props.slot.phases && props.slot.phases[currentPhaseIdx].subtitle"
        class="text-xs text-accent/80 mt-0.5 italic"
      >{{ props.slot.phases[currentPhaseIdx].subtitle }}</p>

      <!-- M5: attack_window(midgame_stance)-->
      <div
        v-if="props.slot.attack_window"
        class="mt-1 text-xs text-amber-400"
      >
        {{ t('strategy.attackWindow', { open: props.slot.attack_window.open_at, close: props.slot.attack_window.close_at }) }}
      </div>
      <!-- M5: micro_doctrine -->
      <ul
        v-if="props.slot.micro_doctrine && props.slot.micro_doctrine.length > 0"
        class="mt-1 space-y-0.5"
      >
        <li
          v-for="(rule, idx) in props.slot.micro_doctrine"
          :key="idx"
          class="text-xs text-muted"
        >· {{ rule }}</li>
      </ul>
    </div>
    <div v-else>
      <p class="text-sm text-muted italic">{{ t('strategy.notSet') }}</p>
    </div>
  </div>
</template>
