<script setup lang="ts">
// L3 持久指令列表卡片（P1.5）
// - 列出当前所有 standing orders，每条带 × 撤销按钮
// - 空态：「暂无持久指令」灰色提示
// - emit('revokeOrder', id) → useWs.revokeDirective(id) → WS revoke_directive 帧
import type { StandingOrderView } from '@/types'

defineProps<{
  orders: readonly StandingOrderView[]
}>()

const emit = defineEmits<{
  revokeOrder: [id: string]
}>()

function fmtTs(ts: number): string {
  const m = Math.floor(ts / 60)
  const s = Math.floor(ts % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}
</script>

<template>
  <div class="rounded-xl border border-border bg-surface-2 p-3">
    <p class="text-xs font-semibold text-muted uppercase tracking-wider mb-2">持久指令</p>

    <!-- 空态 -->
    <p v-if="orders.length === 0" class="text-xs text-muted italic">暂无持久指令</p>

    <!-- 列表 -->
    <ul v-else class="space-y-1.5">
      <li
        v-for="order in orders"
        :key="order.id"
        class="flex items-center gap-2 rounded-md bg-surface-3 border border-border/60 px-2.5 py-1.5"
      >
        <span class="flex-1 min-w-0 text-sm text-white/90 truncate">{{ order.display }}</span>
        <span class="shrink-0 font-mono text-[10px] text-muted">{{ fmtTs(order.issued_at) }}</span>
        <button
          type="button"
          data-testid="revoke-btn"
          class="shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted hover:text-danger hover:bg-danger/10 transition-colors text-xs leading-none"
          :aria-label="`撤销 ${order.display}`"
          @click="emit('revokeOrder', order.id)"
        >×</button>
      </li>
    </ul>
  </div>
</template>
