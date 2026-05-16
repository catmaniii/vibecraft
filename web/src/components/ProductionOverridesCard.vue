<script setup lang="ts">
// P2 产能调整卡片：列出 production_overrides，每条带 × 撤销按钮
// - 空态：「暂无产能调整」灰色提示
// - directive_type badge: production_override / tech_override / expansion_override
// - emit('revoke', id) → 复用 revokeDirective(id) → WS revoke_directive 帧
import type { ProductionOverrideView } from '@/types'

defineProps<{
  orders: readonly ProductionOverrideView[]
}>()

const emit = defineEmits<{
  revoke: [id: string]
}>()

function fmtTs(ts: number): string {
  const m = Math.floor(ts / 60)
  const s = Math.floor(ts % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

function directiveIcon(type: ProductionOverrideView['directive_type']): string {
  const icons: Record<ProductionOverrideView['directive_type'], string> = {
    production_override: '⚙️',
    tech_override: '🔬',
    expansion_override: '🏗️',
  }
  return icons[type]
}
</script>

<template>
  <div class="rounded-xl border border-border bg-surface-2 p-3">
    <p class="text-xs font-semibold text-muted uppercase tracking-wider mb-2">Production Overrides</p>

    <!-- 空态 -->
    <p v-if="orders.length === 0" class="text-xs text-muted italic">暂无产能调整</p>

    <!-- 列表 -->
    <ul v-else class="space-y-1.5">
      <li
        v-for="order in orders"
        :key="order.id"
        class="flex items-center gap-2 rounded-md bg-surface-3 border border-border/60 px-2.5 py-1.5"
      >
        <span class="shrink-0 text-sm" :aria-label="order.directive_type">{{ directiveIcon(order.directive_type) }}</span>
        <span class="flex-1 min-w-0 text-sm text-white/90 truncate">{{ order.display }}</span>
        <span class="shrink-0 font-mono text-[10px] text-muted">{{ fmtTs(order.issued_at) }}</span>
        <button
          type="button"
          data-testid="revoke-btn"
          class="shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted hover:text-danger hover:bg-danger/10 transition-colors text-xs leading-none"
          :aria-label="`撤销 ${order.display}`"
          @click="emit('revoke', order.id)"
        >×</button>
      </li>
    </ul>
  </div>
</template>
