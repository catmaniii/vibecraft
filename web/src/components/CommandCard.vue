<script setup lang="ts">
// 单张命令卡片（P0f Task 15）
// - 按 status 染色：active 绿 / on_hold 黄 / pending 灰 / done 半透明
// - revokable=true 时显示 × 按钮 → emit('revoke', card.id)
// - 由 CommandCardStack (Task 15) 消费；CockpitView (Task 16) 转 WS revoke_directive 帧
import { computed } from 'vue'
import type { CommandCardView } from '@/types'

const props = defineProps<{ card: CommandCardView }>()

const emit = defineEmits<{
  revoke: [id: string]
}>()

const statusLabel = computed(() => {
  switch (props.card.status) {
    case 'active':  return '执行中'
    case 'on_hold': return '等待中'
    case 'pending': return '等待生效'
    case 'done':    return '已完成'
    default:        return props.card.status
  }
})

// 卡片整体背景 + border
const cardCls = computed(() => {
  switch (props.card.status) {
    case 'active':  return 'bg-success/10 border-success/30'
    case 'on_hold': return 'bg-warn/10 border-warn/30'
    case 'pending': return 'bg-muted/10 border-border'
    case 'done':    return 'bg-surface-3/40 border-border/50 opacity-60'
    default:        return 'bg-surface-3 border-border'
  }
})

// status 标签文字色
const statusCls = computed(() => {
  switch (props.card.status) {
    case 'active':  return 'text-success'
    case 'on_hold': return 'text-warn'
    case 'pending': return 'text-muted'
    case 'done':    return 'text-muted'
    default:        return 'text-muted'
  }
})
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
  </div>
</template>
