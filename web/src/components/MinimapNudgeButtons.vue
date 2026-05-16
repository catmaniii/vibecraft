<script setup lang="ts">
// 小地图下方 4 个方向按钮:微调当前 SC2 大屏视角
// 点击 → emit('nudge', dx, dy):dx/dy 是世界坐标偏移(8 = 屏幕半屏)
import { computed } from 'vue'
import type { MinimapFrame } from '@/types'

const props = defineProps<{
  minimap: MinimapFrame | null
}>()

const emit = defineEmits<{
  nudge: [dx: number, dy: number]
}>()

// 微调步长(世界坐标格);太小手感钝、太大跳过头。屏幕半屏宽约 12,8 较合适
const STEP = 8

const canNudge = computed(() => props.minimap !== null)

function nudge(dx: number, dy: number) {
  if (!canNudge.value) return
  emit('nudge', dx * STEP, dy * STEP)
}
</script>

<template>
  <div class="grid grid-cols-3 grid-rows-3 gap-1 w-full h-full">
    <div></div>
    <button
      type="button"
      class="rounded bg-surface-3 hover:bg-surface-2 border border-border text-muted hover:text-white text-lg leading-none flex items-center justify-center transition-colors disabled:opacity-40"
      :disabled="!canNudge"
      @click="nudge(0, 1)"
    >↑</button>
    <div></div>
    <button
      type="button"
      class="rounded bg-surface-3 hover:bg-surface-2 border border-border text-muted hover:text-white text-lg leading-none flex items-center justify-center transition-colors disabled:opacity-40"
      :disabled="!canNudge"
      @click="nudge(-1, 0)"
    >←</button>
    <div class="flex items-center justify-center text-[10px] text-border">视角</div>
    <button
      type="button"
      class="rounded bg-surface-3 hover:bg-surface-2 border border-border text-muted hover:text-white text-lg leading-none flex items-center justify-center transition-colors disabled:opacity-40"
      :disabled="!canNudge"
      @click="nudge(1, 0)"
    >→</button>
    <div></div>
    <button
      type="button"
      class="rounded bg-surface-3 hover:bg-surface-2 border border-border text-muted hover:text-white text-lg leading-none flex items-center justify-center transition-colors disabled:opacity-40"
      :disabled="!canNudge"
      @click="nudge(0, -1)"
    >↓</button>
    <div></div>
  </div>
</template>
