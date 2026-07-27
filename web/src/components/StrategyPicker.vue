<script setup lang="ts">
// StrategyPicker: 按种族过滤的剧本选择浮层。
// 2026-05-23 用户:所有 UI 操作要立刻反馈"处理中"→ 成功/失败状态。
// 点击 chip 后:
//   - 立刻 pending(主"切换"按钮变 spinner + "切换中...")
//   - watch snapshotStrategy 变化:某 slot.id 匹配 → success(✓ 1.5s 绿)
//   - 8s 超时无变化 → failed(✕ 3s 红)

import { ref, onMounted, watch } from 'vue'
import { useClickOutside } from '@/composables/useClickOutside'
import type { SnapshotFrame } from '@/types'
import { t, i18n } from '@/i18n'

const props = defineProps<{
  race: 'protoss' | 'zerg' | 'terran'
  // 2026-05-23:watch strategy slot 变化作为 success 信号
  currentStrategy?: SnapshotFrame['strategy'] | null
}>()

const emit = defineEmits<{
  strategyAction: [strategyId: string]
}>()

interface StrategyChip {
  id: string
  display: string
  race: 'protoss' | 'zerg' | 'terran'
  stage: 'opening' | 'persistent'
  summary_zh?: string
  aliases?: string[]
}

const STAGE_COLORS: Record<string, string> = {
  opening:  'bg-accent/20 text-accent border-accent/30',
  persistent: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
}

function getStageLabel(stage: string): string {
  if (stage === 'opening') return t('strategy.stageLabelOpening')
  if (stage === 'persistent') return t('strategy.stageLabelPersistent')
  return stage
}

const expanded = ref(false)
const rootEl = ref<HTMLElement | null>(null)

const strategies = ref<StrategyChip[]>([])
const fetchError = ref<string | null>(null)
const fetchLoading = ref(true)

// 2026-05-23 用户:操作反馈状态
type ActionStatus = 'idle' | 'pending' | 'success' | 'failed'
const actionStatus = ref<ActionStatus>('idle')
const pendingId = ref('')
const pendingLabel = ref('')
const failedDetail = ref('')
const PENDING_TIMEOUT_MS = 8000  // 8s 兜底
const SUCCESS_HOLD_MS = 1500
const FAILED_HOLD_MS = 3000
let pendingTimer: ReturnType<typeof setTimeout> | null = null
let clearTimer: ReturnType<typeof setTimeout> | null = null

useClickOutside(rootEl, () => { expanded.value = false }, () => expanded.value)

async function loadStrategies() {
  try {
    // 带 locale → 后端按玩家语言出 display/summary(en 缺→回退 zh)。切语言重拉。
    const res = await fetch(`/api/strategies?locale=${i18n.locale}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json() as { strategies: StrategyChip[] }
    strategies.value = data.strategies
  } catch (err) {
    fetchError.value = err instanceof Error ? err.message : String(err)
  } finally {
    fetchLoading.value = false
  }
}

onMounted(loadStrategies)
// 切语言后重拉，让 chip 的 display/summary 跟着切（catalog 由后端按 locale 渲染）。
watch(() => i18n.locale, loadStrategies)

function strategiesByRace() {
  return strategies.value.filter((s) => s.race === props.race)
}

function clearTimers() {
  if (pendingTimer !== null) { clearTimeout(pendingTimer); pendingTimer = null }
  if (clearTimer !== null) { clearTimeout(clearTimer); clearTimer = null }
}

function markSuccess() {
  if (actionStatus.value !== 'pending') return
  clearTimers()
  actionStatus.value = 'success'
  clearTimer = setTimeout(() => {
    actionStatus.value = 'idle'
    pendingId.value = ''
    pendingLabel.value = ''
  }, SUCCESS_HOLD_MS)
}

function markFailed(detail: string) {
  if (actionStatus.value !== 'pending') return
  clearTimers()
  actionStatus.value = 'failed'
  failedDetail.value = detail
  clearTimer = setTimeout(() => {
    actionStatus.value = 'idle'
    pendingId.value = ''
    pendingLabel.value = ''
    failedDetail.value = ''
  }, FAILED_HOLD_MS)
}

function pickStrategy(strat: StrategyChip) {
  clearTimers()
  pendingId.value = strat.id
  pendingLabel.value = strat.display
  actionStatus.value = 'pending'
  emit('strategyAction', strat.id)
  expanded.value = false
  pendingTimer = setTimeout(() => {
    if (actionStatus.value === 'pending') markFailed(t('tactics.timeout'))
  }, PENDING_TIMEOUT_MS)
}

// watch:任一 slot.id 匹配 pending → success
watch(
  () => props.currentStrategy,
  (newStrategy) => {
    if (actionStatus.value !== 'pending') return
    if (!newStrategy) return
    const slotIds = [
      newStrategy.opening?.id,
      newStrategy.midgame?.id,
      newStrategy.lategame?.id,
    ]
    if (slotIds.includes(pendingId.value)) markSuccess()
  },
  { deep: true }
)

// 按钮显示文案 / 样式
const toggleContent = (): { label: string; icon?: 'spin' | 'check' | 'cross' } => {
  if (actionStatus.value === 'pending') return { label: t('tactics.switching'), icon: 'spin' }
  if (actionStatus.value === 'success') return { label: '✓', icon: 'check' }
  if (actionStatus.value === 'failed') return { label: '✕', icon: 'cross' }
  return { label: t('tactics.switch') }
}

const toggleCls = (): string => {
  if (actionStatus.value === 'pending') return 'text-blue-300 border-blue-300/40 bg-blue-300/10'
  if (actionStatus.value === 'success') return 'text-success border-success/40 bg-success/10'
  if (actionStatus.value === 'failed')  return 'text-danger border-danger/40 bg-danger/10'
  return 'text-muted border-border hover:text-accent hover:border-accent/50 hover:bg-accent/10'
}
</script>

<template>
  <div ref="rootEl" data-testid="strategy-picker" class="relative inline-block">
    <!-- 触发按钮:根据 actionStatus 切样式/图标 -->
    <button
      type="button"
      data-testid="strategy-picker-toggle"
      class="shrink-0 px-2 py-0.5 rounded text-xs border transition-colors leading-none flex items-center gap-1 whitespace-nowrap"
      :class="toggleCls()"
      :title="actionStatus === 'failed' ? failedDetail : ''"
      :aria-label="expanded ? t('strategy.closeMenu') : t('strategy.switchMenu')"
      :disabled="actionStatus === 'pending'"
      @click="expanded = !expanded"
    >
      <svg v-if="toggleContent().icon === 'spin'" class="animate-spin h-3 w-3" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" opacity="0.25"/>
        <path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
      </svg>
      <span>{{ toggleContent().label }}</span>
    </button>

    <!-- pending 时显示正在切的策略名(挂在按钮下面,短暂) -->
    <Transition
      enter-active-class="transition ease-out duration-150"
      enter-from-class="opacity-0 -translate-y-1"
      enter-to-class="opacity-100 translate-y-0"
      leave-active-class="transition ease-in duration-300"
      leave-from-class="opacity-100"
      leave-to-class="opacity-0"
    >
      <div
        v-if="actionStatus !== 'idle' && pendingLabel"
        data-testid="strategy-action-feedback"
        class="absolute right-0 top-full mt-1 z-20 whitespace-nowrap px-2 py-0.5 rounded text-[10px]"
        :class="toggleCls()"
      >
        <template v-if="actionStatus === 'pending'">→ {{ pendingLabel }}</template>
        <template v-else-if="actionStatus === 'success'">✓ {{ pendingLabel }}</template>
        <template v-else>✕ {{ pendingLabel }} {{ failedDetail }}</template>
      </div>
    </Transition>

    <!-- 展开后的下拉菜单 -->
    <div
      v-if="expanded"
      data-testid="strategy-picker-popup"
      class="absolute right-0 top-full mt-1 z-30 bg-surface-2 border border-border rounded-lg shadow-lg p-2 min-w-[240px]"
    >
      <p
        v-if="fetchLoading"
        class="text-xs text-muted px-1 py-2"
        data-testid="strategy-picker-loading"
      >{{ t('strategy.loading') }}</p>
      <p
        v-else-if="fetchError"
        class="text-xs text-red-400 px-1 py-2"
        data-testid="strategy-picker-error"
      >{{ t('strategy.loadError', { err: fetchError }) }}</p>
      <template v-else>
        <div
          v-for="stage in ['opening', 'persistent']"
          :key="stage"
          class="mb-2 last:mb-0"
        >
          <p class="text-[10px] font-semibold text-muted uppercase tracking-wider px-1 pb-1">
            {{ getStageLabel(stage) }}
          </p>
          <div class="flex flex-wrap gap-1">
            <button
              v-for="strat in strategiesByRace().filter((s) => s.stage === stage)"
              :key="strat.id"
              type="button"
              :data-testid="`strategy-chip-${strat.id}`"
              :class="[
                'px-2 py-1 rounded-full border text-xs font-medium transition-colors',
                'hover:brightness-110 active:scale-95',
                STAGE_COLORS[stage],
              ]"
              @click="pickStrategy(strat)"
            >
              {{ strat.display }}
            </button>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>
