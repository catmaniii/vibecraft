<script setup lang="ts">
// bot 内部宏观意图(单行,emoji + label + reason)
import { computed } from 'vue'
import type { TacticsView } from '@/types'

const props = defineProps<{
  tactics: TacticsView | null
}>()

// stance → 配色
const stanceCls = computed(() => {
  const map: Record<string, string> = {
    attacking:  'text-danger',
    defending:  'text-amber-400',
    expanding:  'text-success',
    scouting:   'text-blue-300',
    harassing:  'text-purple-300',
    sustaining: 'text-muted',
  }
  return map[props.tactics?.stance ?? 'sustaining'] ?? 'text-muted'
})
</script>

<template>
  <div v-if="tactics" class="flex items-center gap-1.5 text-xs">
    <span class="font-semibold" :class="stanceCls">{{ tactics.label }}</span>
    <span class="text-border">·</span>
    <span class="text-muted truncate">{{ tactics.reason }}</span>
  </div>
</template>
