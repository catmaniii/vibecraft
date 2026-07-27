<script setup lang="ts">
// drop_act 命令卡片（L4 空投行动）
// - Header: display 文字（后端 _format_drop_act_display 格式化的中文）
// - Status badge: pending(浅蓝) / active(绿) / on_hold(黄橙) / done(灰)
// - Status reason 副行（仅非空时渲染）
// - conditions 列表（done_when 条件）
// - revokable=true + status≠done 时显示 × 撤销按钮
import { computed } from 'vue'
import type { CommandCardView, ConditionView } from '@/types'
import { t } from '@/i18n'

const props = defineProps<{ card: CommandCardView }>()

const emit = defineEmits<{
  revoke: [id: string]
}>()

// 状态 label
const statusLabel = computed(() => {
  switch (props.card.status) {
    case 'pending':  return t('drop.statusPending')
    case 'active':   return t('drop.statusActive')
    case 'on_hold':  return t('drop.statusOnHold')
    case 'done':     return t('drop.statusDone')
    default:         return props.card.status
  }
})

// badge 文字颜色：pending 浅蓝 / active 绿 / on_hold 黄橙 / done 灰
const statusCls = computed(() => {
  switch (props.card.status) {
    case 'pending':  return 'text-sky-400'
    case 'active':   return 'text-success'
    case 'on_hold':  return 'text-amber-400'
    case 'done':     return 'text-muted'
    default:         return 'text-muted'
  }
})

// 卡片整体框样式：done 半透明 / 其他 L4 橙色系
const cardCls = computed(() => {
  if (props.card.status === 'done') return 'bg-surface-3/40 border-border/50 opacity-60'
  if (props.card.status === 'pending') return 'bg-muted/10 border-border/60'
  return 'bg-orange-500/10 border-orange-500/50'
})

// 条件灯颜色（复用 CommandCard 逻辑）
function lightCls(cond: ConditionView): string {
  if (cond.state) {
    switch (cond.state) {
      case 'blocked':   return 'bg-danger shadow-[0_0_4px] shadow-danger/60'
      case 'waiting':   return 'bg-amber-400 shadow-[0_0_4px] shadow-amber-400/60'
      case 'producing': return 'bg-success shadow-[0_0_4px] shadow-success/60'
      case 'done':      return 'bg-success/70'
      default:          return 'bg-muted'
    }
  }
  return cond.met ? 'bg-success shadow-[0_0_4px] shadow-success/60' : 'bg-muted/60'
}

function lightTitle(cond: ConditionView): string {
  if (cond.state) {
    const map: Record<string, string> = {
      blocked: t('drop.condBlocked'),
      waiting: t('drop.condWaiting'),
      producing: t('drop.condProducing'),
      done: t('drop.condDone'),
    }
    const label = map[cond.state] ?? cond.state
    return cond.state_reason ? `${label}：${cond.state_reason}` : label
  }
  return cond.met ? t('drop.condMet') : t('drop.condUnmet')
}

function fmtProgress(p: { current: number; target: number; unit: string }): string {
  return `${p.current}/${p.target} ${p.unit}`
}
</script>

<template>
  <div
    class="rounded-md border px-2.5 py-2 transition-opacity"
    :class="cardCls"
  >
    <!-- 标题行："空投" 标签 + display 文字 + × 按钮 -->
    <div class="flex items-center gap-2">
      <span class="shrink-0 font-mono text-[10px] px-1.5 py-0.5 rounded border leading-none bg-orange-500/25 text-orange-200 border-orange-500/50">
        {{ t('drop.label') }}
      </span>
      <span class="flex-1 min-w-0 text-sm font-medium text-white/90 truncate">
        {{ card.display }}
      </span>
      <button
        v-if="card.revokable && card.status !== 'done'"
        type="button"
        data-testid="revoke-btn"
        class="shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted hover:text-danger hover:bg-danger/10 transition-colors text-xs leading-none"
        :aria-label="t('drop.cancelAria', { name: card.display })"
        @click="emit('revoke', card.id)"
      >×</button>
    </div>

    <!-- status 行：badge + 可选原因副行 -->
    <div class="flex items-center gap-1 mt-0.5">
      <span
        data-testid="status-badge"
        class="text-[11px] font-semibold"
        :class="statusCls"
      >{{ statusLabel }}</span>
      <span
        v-if="card.status_reason"
        data-testid="status-reason"
        class="text-[11px] text-muted/80"
      >— {{ card.status_reason }}</span>
    </div>

    <!-- 条件清单（done_when 条件存在时显示） -->
    <ul
      v-if="card.conditions && card.conditions.length > 0"
      class="mt-1.5 space-y-1"
    >
      <li
        v-for="(cond, idx) in card.conditions"
        :key="idx"
        class="text-[11px] text-white/85"
      >
        <div class="flex items-center gap-1.5">
          <span
            class="shrink-0 w-2 h-2 rounded-full inline-block"
            :class="lightCls(cond)"
            :title="lightTitle(cond)"
          ></span>
          <span class="flex-1 min-w-0">{{ cond.text }}</span>
          <span v-if="cond.progress" class="font-mono text-[10px] text-white/70 shrink-0">
            {{ fmtProgress(cond.progress) }}
          </span>
        </div>
        <div v-if="cond.state_reason" class="pl-3.5 text-[10px] text-white/55">
          {{ cond.state_reason }}
        </div>
      </li>
    </ul>
  </div>
</template>
