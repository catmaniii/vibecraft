<script setup lang="ts">
// 2026-05-28 用户:tactical debug overlay。玩家按按钮后立即看 intent/stance/mode
// 是不是真的变了 + PlanZoneAttack.status,定位"按完没反应"是哪一层断。
// 默认折叠成一个 chip,点开展开。
import { computed, ref } from 'vue'
import type { TacticalDebugView } from '@/types'

const props = defineProps<{
  debug?: TacticalDebugView | null
}>()

const expanded = ref(false)

const summary = computed(() => {
  const d = props.debug
  if (!d) return '—'
  const intent = d.intent ?? '·'
  const stance = d.stance ?? '·'
  const mode = d.mode ?? '·'
  return `${intent}/${stance}/${mode}`
})

const summaryColor = computed(() => {
  const d = props.debug
  if (!d) return 'text-muted'
  const i = d.intent
  if (i === 'attack') return 'text-danger'
  if (i === 'retreat') return 'text-amber-400'
  if (i === 'defend' || i === 'hold') return 'text-blue-300'
  return 'text-success/70'
})
</script>

<template>
  <div v-if="props.debug" class="flex items-center gap-2 text-[10px] leading-none">
    <button
      type="button"
      class="px-2 py-0.5 rounded border border-border bg-surface-3 hover:bg-surface-2 font-mono"
      :class="summaryColor"
      :title="expanded ? '收起诊断' : '展开诊断(实时 intent/stance/mode/PlanZoneAttack.status)'"
      @click="expanded = !expanded"
    >
      <span class="text-muted">DBG</span>
      <span class="ml-1">{{ summary }}</span>
    </button>
    <div
      v-if="expanded"
      class="font-mono text-muted bg-surface-3 border border-border rounded px-2 py-1 flex flex-wrap gap-x-3 gap-y-0.5"
    >
      <span><span class="text-muted/70">intent:</span> {{ props.debug.intent ?? 'null' }}</span>
      <span><span class="text-muted/70">stance:</span> {{ props.debug.stance ?? 'null' }}</span>
      <span><span class="text-muted/70">mode:</span> {{ props.debug.mode ?? 'null' }}</span>
      <span><span class="text-muted/70">target_set:</span> {{ props.debug.target_set }}</span>
      <span v-if="props.debug.plan_status"><span class="text-muted/70">pza.status:</span> {{ props.debug.plan_status }}</span>
      <span v-if="props.debug.attack_retreat_started !== null">
        <span class="text-muted/70">ret_started:</span> {{ props.debug.attack_retreat_started }}s
      </span>
    </div>
  </div>
</template>
