<script setup lang="ts">
// TacticsButton: 折叠下拉战术按钮(集成在 BotDecisionCard 右上角)。
// 2026-05-23 用户:操作反馈"处理中" → 成功/失败。
// 点击 verb 后:
//   - 立刻 pending(主按钮变 spinner + "切换中...")
//   - watch tactics(stance 变化 / active_tactics 数量变) → success(1.5s 绿)
//   - 8s 超时 → failed(3s 红)
import { ref, computed, watch } from 'vue'
import { useClickOutside } from '@/composables/useClickOutside'
import type { TacticsView, TacticalObjectiveView } from '@/types'
import { t } from '@/i18n'

const props = defineProps<{
  // 2026-05-23:watch tactics/active_tactics 变化作为 success 信号
  currentTactics?: TacticsView | null
  activeTactics?: readonly TacticalObjectiveView[]
}>()

const emit = defineEmits<{
  tacticalAction: [verb: string, mode?: 'all_in' | 'probe']
}>()

interface TacticOption {
  verb: string
  label: string
  mode?: 'all_in' | 'probe'  // verb=attack 时区分两种进攻
}

// 2026-05-25 用户:"全军进攻"拆"强制全体进攻"(all_in,不撤退)+"试探性进攻"(probe,劣势撤退)
// 2026-05-28 用户:加 hold(全军坚守 — 聚团到 current army_center + 站住不回家)
const OPTIONS = computed<TacticOption[]>(() => [
  { verb: 'attack',  label: t('tactics.allIn'),   mode: 'all_in' },
  { verb: 'attack',  label: t('tactics.probe'),   mode: 'probe' },
  { verb: 'defend',  label: t('tactics.defend') },
  { verb: 'hold',    label: t('tactics.hold') },
  { verb: 'retreat', label: t('tactics.retreat') },
  { verb: 'recon',   label: t('tactics.recon') },
  { verb: 'scout',   label: t('tactics.scout') },
])

const expanded = ref(false)
const rootEl = ref<HTMLElement | null>(null)

// 操作反馈状态
type ActionStatus = 'idle' | 'pending' | 'success' | 'failed'
const actionStatus = ref<ActionStatus>('idle')
const pendingLabel = ref('')
const failedDetail = ref('')
const PENDING_TIMEOUT_MS = 8000
const SUCCESS_HOLD_MS = 1500
const FAILED_HOLD_MS = 3000
let pendingTimer: ReturnType<typeof setTimeout> | null = null
let clearTimer: ReturnType<typeof setTimeout> | null = null

// 记录 pending 时刻的 baseline,用于检测变化
let baselineStance = ''
let baselineActiveLen = 0

useClickOutside(rootEl, () => { expanded.value = false }, () => expanded.value)

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
    pendingLabel.value = ''
    failedDetail.value = ''
  }, FAILED_HOLD_MS)
}

function toggle() {
  expanded.value = !expanded.value
}

function sendAction(opt: TacticOption) {
  clearTimers()
  pendingLabel.value = opt.label
  actionStatus.value = 'pending'
  baselineStance = props.currentTactics?.stance ?? ''
  baselineActiveLen = props.activeTactics?.length ?? 0
  emit('tacticalAction', opt.verb, opt.mode)
  expanded.value = false
  pendingTimer = setTimeout(() => {
    if (actionStatus.value === 'pending') markFailed(t('tactics.timeout'))
  }, PENDING_TIMEOUT_MS)
}

// 2026-05-25 用户(v2):玩家选的战术由 BotDecisionCard override 块完整展示
// (含 verb + target_area + ×),TacticsButton 不再重复展示 chip + ×(避免双 ×)。
// trigger button 永远显示,玩家可随时切换到新战术。

// watch stance 或 active_tactics 长度变化 → success
watch(
  () => [props.currentTactics?.stance, props.activeTactics?.length],
  ([newStance, newLen]) => {
    if (actionStatus.value !== 'pending') return
    if (newStance !== baselineStance || newLen !== baselineActiveLen) {
      markSuccess()
    }
  },
  { deep: false }
)

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
  if (expanded.value) return 'border-accent text-accent bg-accent/10'
  return 'text-muted border-border hover:text-accent hover:border-accent/50 hover:bg-accent/10'
}
</script>

<template>
  <div ref="rootEl" class="relative inline-block" data-testid="tactics-button-root">
    <!-- 触发按钮:永远显示。玩家选战术后,active 状态由 BotDecisionCard
         override 块展示(含唯一 ×),这里只保留"切换/切换中/✓/✕"触发按钮。 -->
    <button
      type="button"
      data-testid="tactics-toggle"
      class="shrink-0 px-2 py-0.5 rounded text-xs border transition-colors leading-none flex items-center gap-1"
      :class="toggleCls()"
      :aria-expanded="expanded"
      :title="actionStatus === 'failed' ? failedDetail : ''"
      :disabled="actionStatus === 'pending'"
      :aria-label="t('tactics.switchAria')"
      @click="toggle"
    >
      <svg v-if="toggleContent().icon === 'spin'" class="animate-spin h-3 w-3" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" opacity="0.25"/>
        <path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
      </svg>
      <span>{{ toggleContent().label }}</span>
    </button>

    <!-- 反馈 chip(浮在按钮下面,显示 pending/success/failed 的 verb 名) -->
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
        data-testid="tactics-action-feedback"
        class="absolute right-0 top-full mt-1 z-20 whitespace-nowrap px-2 py-0.5 rounded text-[10px]"
        :class="toggleCls()"
      >
        <template v-if="actionStatus === 'pending'">→ {{ pendingLabel }}</template>
        <template v-else-if="actionStatus === 'success'">✓ {{ pendingLabel }}</template>
        <template v-else>✕ {{ pendingLabel }} {{ failedDetail }}</template>
      </div>
    </Transition>

    <!-- 下拉菜单 -->
    <div
      v-if="expanded"
      data-testid="tactics-menu"
      class="absolute top-full mt-1 right-0 z-30 min-w-[120px] rounded-lg bg-surface-2 border border-border shadow-lg overflow-hidden"
    >
      <button
        v-for="opt in OPTIONS"
        :key="opt.verb"
        type="button"
        :data-testid="`tactics-option-${opt.verb}`"
        class="w-full px-3 py-2 text-left text-sm text-on-surface hover:bg-accent/10 transition-colors"
        @click="sendAction(opt)"
      >
        {{ opt.label }}
      </button>
    </div>
  </div>
</template>
