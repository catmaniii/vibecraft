<script setup lang="ts">
// bot 当前决策(独立大卡片,放在 standing orders 上方)
// stance 来自 vibecraft 状态机:attacking/defending/expanding/scouting/harassing/sustaining
// 未来 plan 加新行为(偷矿/空投 等)→ stance 扩展,这里 stanceMeta 加新条目
import { computed } from 'vue'
import type { TacticsView } from '@/types'

const props = defineProps<{
  tactics: TacticsView | null
}>()

// stance → 视觉风格(配色 + 强调感)
const stanceMeta: Record<string, { cls: string; ring: string }> = {
  attacking: { cls: 'text-danger', ring: 'border-danger/50 bg-danger/5' },
  defending: { cls: 'text-amber-400', ring: 'border-amber-500/50 bg-amber-500/5' },
  expanding: { cls: 'text-success', ring: 'border-success/50 bg-success/5' },
  scouting: { cls: 'text-blue-300', ring: 'border-blue-500/50 bg-blue-500/5' },
  harassing: { cls: 'text-purple-300', ring: 'border-purple-500/50 bg-purple-500/5' },
  sustaining: { cls: 'text-white/80', ring: 'border-border bg-surface-3' },
}

const meta = computed(() => stanceMeta[props.tactics?.stance ?? 'sustaining'] ?? stanceMeta.sustaining)
</script>

<template>
  <div
    class="rounded-xl border p-3 transition-colors"
    :class="meta.ring"
  >
    <p class="text-xs font-semibold text-muted uppercase tracking-wider mb-2">
      🎯 bot 当前决策
    </p>
    <div v-if="tactics" class="space-y-0.5">
      <p class="text-base font-bold" :class="meta.cls">{{ tactics.label }}</p>
      <p class="text-xs text-white/70">{{ tactics.reason }}</p>
    </div>
    <p v-else class="text-sm text-muted italic">（等待 bot 上报)</p>
  </div>
</template>
