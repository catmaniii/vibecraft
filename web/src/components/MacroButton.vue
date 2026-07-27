<script setup lang="ts">
import { computed } from 'vue'
import { t } from '@/i18n'
// MacroPanel (renamed MacroButton): WP-D 实时运营策略层（三维度，2026-07-06 重构）。
// 面板标题"运营策略"。
// 维度1：开矿 — 「多开一个矿」一次性按钮 → emit('macroAction','expand','one_more')
// 维度2：采矿策略 — 优先水晶/优先气/默认 → emit('macroAction','mining',v)
// 维度3：农民生产 — 停 / 全力补 / 默认 → emit('macroAction','workers',v)
// 高亮：采矿策略跟 miningPriority prop（同 workerMode 的 workerActiveCls 模式）。

const props = defineProps<{
  // 维度2：采矿策略（"mineral"/"gas"/null=默认）
  miningPriority?: string | null
  // 维度3：农民模式（"stop"/"max"/null=默认）
  workerMode?: string | null
}>()

const emit = defineEmits<{
  macroAction: [dim: string, value: number | string]
}>()

// 维度2 采矿策略 options
interface MiningOption { value: string; label: string }
const MINING_OPTIONS = computed<MiningOption[]>(() => [
  { value: 'mineral', label: t('macro.miningMineral') },
  { value: 'gas',     label: t('macro.miningGas') },
  { value: 'default', label: t('macro.default') },
])

// 维度3 农民生产 options
interface WorkerOption { value: string; label: string }
const WORKER_OPTIONS = computed<WorkerOption[]>(() => [
  { value: 'stop',    label: t('macro.workerStop') },
  { value: 'max',     label: t('macro.workerMax') },
  { value: 'default', label: t('macro.default') },
])

function miningActiveCls(opt: MiningOption): string {
  const active = props.miningPriority === opt.value
    || (opt.value === 'default' && (props.miningPriority === null || props.miningPriority === undefined))
  return active
    ? 'text-accent border-accent bg-accent/15 font-semibold'
    : 'text-on-surface border-border hover:text-accent hover:border-accent/50 hover:bg-accent/10'
}

function workerActiveCls(opt: WorkerOption): string {
  const active = props.workerMode === opt.value
    || (opt.value === 'default' && (props.workerMode === null || props.workerMode === undefined))
  return active
    ? 'text-accent border-accent bg-accent/15 font-semibold'
    : 'text-on-surface border-border hover:text-accent hover:border-accent/50 hover:bg-accent/10'
}
</script>

<template>
  <div
    class="rounded-xl bg-surface-2 border border-border p-3 flex flex-col gap-2"
    data-testid="macro-panel"
  >
    <!-- 面板标题 -->
    <div class="text-[11px] font-semibold text-muted tracking-wide">{{ t('macro.title') }}</div>

    <!-- 维度1：开矿（一次性按钮，不持久高亮） -->
    <div class="flex items-center gap-1.5">
      <span class="text-xs font-semibold text-muted tracking-wider shrink-0 w-8">{{ t('macro.expandLabel') }}</span>
      <button
        type="button"
        data-testid="expand-one-more-btn"
        class="px-2 py-0.5 rounded text-xs border transition-colors leading-none text-on-surface border-border hover:text-accent hover:border-accent/50 hover:bg-accent/10"
        :aria-label="t('macro.expandOneMoreAria')"
        @click="emit('macroAction', 'expand', 'one_more')"
      >{{ t('macro.expandOneMore') }}</button>
    </div>

    <!-- 维度2：采矿策略 -->
    <div class="flex items-center gap-1.5 flex-wrap">
      <span class="text-xs font-semibold text-muted tracking-wider shrink-0 w-8">{{ t('macro.miningLabel') }}</span>
      <button
        v-for="opt in MINING_OPTIONS"
        :key="opt.value"
        type="button"
        :data-testid="`mining-chip-${opt.value}`"
        class="px-2 py-0.5 rounded text-xs border transition-colors leading-none"
        :class="miningActiveCls(opt)"
        :aria-label="t('macro.miningAria', { label: opt.label })"
        @click="emit('macroAction', 'mining', opt.value)"
      >{{ opt.label }}</button>
    </div>

    <!-- 维度3：农民生产 -->
    <div class="flex items-center gap-1.5 flex-wrap">
      <span class="text-xs font-semibold text-muted tracking-wider shrink-0 w-8">{{ t('macro.workerLabel') }}</span>
      <button
        v-for="opt in WORKER_OPTIONS"
        :key="opt.value"
        type="button"
        :data-testid="`worker-chip-${opt.value}`"
        class="px-2 py-0.5 rounded text-xs border transition-colors leading-none"
        :class="workerActiveCls(opt)"
        :aria-label="t('macro.workerAria', { label: opt.label })"
        @click="emit('macroAction', 'workers', opt.value)"
      >{{ opt.label }}</button>
    </div>
  </div>
</template>
