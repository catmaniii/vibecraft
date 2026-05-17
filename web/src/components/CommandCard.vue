<script setup lang="ts">
// 单张命令卡片：
// - 状态视觉区分：执行中绿 / 等待中橙 / pending 灰 / done 半透明
//   执行中 vs 等待中 由 conditions 决定（任一未满足 → 等待中，全满足 / 无条件 → 执行中）
// - conditions 列表：每条带 ✓/○ 图标 + 进度（造 2/4 个 叉子）
// - revokable=true 时显示 × 按钮 → emit('revoke', card.id)
import { computed } from 'vue'
import type { CommandCardView } from '@/types'

const props = defineProps<{ card: CommandCardView }>()

const emit = defineEmits<{
  revoke: [id: string]
}>()

// 是否所有条件都满足（无 conditions 或全 met=true）
const allConditionsMet = computed(() => {
  const cs = props.card.conditions
  if (!cs || cs.length === 0) return true
  return cs.every(c => c.met)
})

// 显示态：等待中（status=active 但有条件未满足）vs 执行中（无条件或全满足）
const displayStatus = computed<'pending' | 'waiting' | 'executing' | 'on_hold' | 'done'>(() => {
  if (props.card.status === 'done') return 'done'
  if (props.card.status === 'pending') return 'pending'
  if (props.card.status === 'on_hold') return 'on_hold'
  // status === 'active'
  if (allConditionsMet.value) return 'executing'
  return 'waiting'
})

const statusLabel = computed(() => {
  switch (displayStatus.value) {
    case 'executing': return '执行中'
    case 'waiting':   return '等待条件'
    case 'on_hold':   return '已暂停'
    case 'pending':   return '等待生效'
    case 'done':      return '已完成'
    default:          return ''
  }
})

const cardCls = computed(() => {
  switch (displayStatus.value) {
    case 'executing': return 'bg-success/10 border-success/40'
    case 'waiting':   return 'bg-amber-500/10 border-amber-500/40'
    case 'on_hold':   return 'bg-warn/10 border-warn/30'
    case 'pending':   return 'bg-muted/10 border-border'
    case 'done':      return 'bg-surface-3/40 border-border/50 opacity-60'
    default:          return 'bg-surface-3 border-border'
  }
})

const statusCls = computed(() => {
  switch (displayStatus.value) {
    case 'executing': return 'text-success'
    case 'waiting':   return 'text-amber-400'
    case 'on_hold':   return 'text-warn'
    default:          return 'text-muted'
  }
})

// 进度文本（兼顾计数和倒计时）："2/4 个" / "12/30 秒"
function fmtProgress(c: { current: number; target: number; unit: string }): string {
  return `${c.current}/${c.target} ${c.unit}`
}
</script>

<template>
  <div
    class="rounded-md border px-2.5 py-2 transition-opacity"
    :class="cardCls"
  >
    <!-- 标题行：layer tag + display + 撤销按钮 -->
    <div class="flex items-center gap-2">
      <span class="shrink-0 font-mono text-[10px] px-1.5 py-0.5 rounded bg-surface-3/60 text-muted border border-border/60 leading-none">
        {{ card.layer }}
      </span>
      <span class="flex-1 min-w-0 text-sm font-medium text-white/90 truncate">
        {{ card.display }}
      </span>
      <button
        v-if="card.revokable && card.status !== 'done'"
        type="button"
        data-testid="revoke-btn"
        class="shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted hover:text-danger hover:bg-danger/10 transition-colors text-xs leading-none"
        :aria-label="`取消指令: ${card.display}`"
        @click="emit('revoke', card.id)"
      >×</button>
    </div>

    <!-- status 行：状态 label + 可选原因 -->
    <div class="flex items-center gap-1 mt-0.5">
      <span class="text-[11px] font-semibold" :class="statusCls">{{ statusLabel }}</span>
      <span v-if="card.status_reason" class="text-[11px] text-muted/80">— {{ card.status_reason }}</span>
    </div>

    <!-- 条件清单（有 done_when 时显示） -->
    <ul
      v-if="card.conditions && card.conditions.length > 0"
      class="mt-1 space-y-0.5"
    >
      <li
        v-for="(cond, idx) in card.conditions"
        :key="idx"
        class="flex items-center gap-1.5 text-[11px]"
        :class="cond.met ? 'text-success' : 'text-muted'"
      >
        <span class="font-mono w-3 shrink-0">{{ cond.met ? '✓' : '○' }}</span>
        <span class="flex-1 min-w-0">{{ cond.text }}</span>
        <span v-if="cond.progress" class="font-mono text-[10px] text-white/70">
          {{ fmtProgress(cond.progress) }}
        </span>
      </li>
    </ul>
  </div>
</template>
