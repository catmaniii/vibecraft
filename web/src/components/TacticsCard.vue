<script setup lang="ts">
// L2 战术指令列表卡片（P3.6）
// - 列出当前所有 active tactical objectives，每条带 × 撤销按钮
// - 空态：「暂无战术指令」灰色提示
// - emit('revoke', id) → useWs.revokeDirective(id) → WS revoke_directive 帧
import type { TacticalObjectiveView } from '@/types'

defineProps<{
  tactics: readonly TacticalObjectiveView[]
}>()

const emit = defineEmits<{
  revoke: [id: string]
}>()

const VERB_ZH: Record<string, string> = {
  attack: '进攻',
  defend: '守',
  scout: '探',
  expand: '扩',
  harass: '骚扰',
  drop: '空投',
  vision: '侦察',
  raze: '拆',
  retreat: '撤',
  regroup: '集结',
  split: '分兵',
}

function verbLabel(verb: string, targetArea: string | null): string {
  const zh = VERB_ZH[verb] ?? verb
  return targetArea ? `${zh} ${targetArea}` : zh
}
</script>

<template>
  <div class="rounded-xl border border-border bg-surface-2 p-3">
    <p class="text-xs font-semibold text-muted uppercase tracking-wider mb-2">战术指令 (L2)</p>

    <!-- 空态 -->
    <p v-if="tactics.length === 0" class="text-xs text-muted italic">暂无战术指令</p>

    <!-- 列表 -->
    <ul v-else class="space-y-1.5">
      <li
        v-for="tac in tactics"
        :key="tac.id"
        class="flex items-center gap-2 rounded-md bg-surface-3 border border-border/60 px-2.5 py-1.5"
      >
        <span class="flex-1 min-w-0 text-sm text-white/90 truncate">{{ verbLabel(tac.verb, tac.target_area) }}</span>
        <button
          type="button"
          data-testid="revoke-btn"
          class="shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted hover:text-danger hover:bg-danger/10 transition-colors text-xs leading-none"
          :aria-label="`撤销 ${verbLabel(tac.verb, tac.target_area)}`"
          @click="emit('revoke', tac.id)"
        >×</button>
      </li>
    </ul>
  </div>
</template>
