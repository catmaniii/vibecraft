<script setup lang="ts">
// 触摸板绝对偏移模式:
// - 按下:记录起点 + 当前 viewport.center 作为基准
// - move:计算 dx/dy = current - start(纯绝对偏移,与起点的差值)
//         emit('absolute', baseX + dx, baseY + dy)
//         所以反复 move 不会累加 — 永远基于起点
// - 松开:reset 起点 / 基准
// 节流:rAF 每帧最多 emit 一次
import { ref, computed, onBeforeUnmount } from 'vue'
import type { MinimapFrame } from '@/types'
import { t } from '@/i18n'

const props = defineProps<{
  minimap: MinimapFrame | null
}>()

const emit = defineEmits<{
  absolute: [x: number, y: number]
}>()

const trackpadRef = ref<HTMLDivElement | null>(null)
const dragging = ref(false)
const start = ref<{ x: number; y: number } | null>(null)
const cursor = ref<{ x: number; y: number } | null>(null)
// 按下时 viewport.center 的 snapshot,作为后续偏移基准
let baseCenter: [number, number] | null = null
let rafId: number | null = null

// 1 px → 多少世界单位(敏感度调高:100px ≈ 18 世界单位,约 1.5 个屏幕宽)
const PIXELS_TO_WORLD = 0.18

const canUse = computed(() => props.minimap !== null)

function scheduleEmit() {
  if (rafId !== null) return
  rafId = requestAnimationFrame(flush)
}

function flush() {
  rafId = null
  if (!start.value || !cursor.value || !baseCenter || !trackpadRef.value) return
  const dxPx = cursor.value.x - start.value.x
  const dyPx = cursor.value.y - start.value.y
  // 屏幕 dy 向下为正 → SC2 Y 向上为正,翻号
  const targetX = baseCenter[0] + dxPx * PIXELS_TO_WORLD
  const targetY = baseCenter[1] - dyPx * PIXELS_TO_WORLD
  emit('absolute', targetX, targetY)
}

function onStart(e: PointerEvent) {
  if (!canUse.value) return
  const c = props.minimap?.viewport.center
  if (!c) return
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  dragging.value = true
  start.value = { x: e.clientX, y: e.clientY }
  cursor.value = { x: e.clientX, y: e.clientY }
  baseCenter = [c[0], c[1]]  // 锁定按下时的视野中心
}

function onMove(e: PointerEvent) {
  if (!dragging.value) return
  cursor.value = { x: e.clientX, y: e.clientY }
  scheduleEmit()
}

function onEnd(e: PointerEvent) {
  if (dragging.value) {
    ;(e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId)
    if (rafId !== null) {
      cancelAnimationFrame(rafId)
      rafId = null
    }
    flush()  // 最后一次 emit
  }
  dragging.value = false
  start.value = null
  cursor.value = null
  baseCenter = null
}

onBeforeUnmount(() => {
  if (rafId !== null) cancelAnimationFrame(rafId)
})

// 触摸板可视化:起点圆 + 当前位置圆 + 连线
const indicator = computed(() => {
  if (!dragging.value || !start.value || !cursor.value || !trackpadRef.value) return null
  const rect = trackpadRef.value.getBoundingClientRect()
  return {
    sx: start.value.x - rect.left,
    sy: start.value.y - rect.top,
    cx: cursor.value.x - rect.left,
    cy: cursor.value.y - rect.top,
  }
})
</script>

<template>
  <div
    ref="trackpadRef"
    class="relative w-full h-full rounded-lg bg-surface-3 border border-border select-none touch-none overflow-hidden transition-colors"
    :class="{ 'bg-accent/10 border-accent/40': dragging, 'opacity-50': !canUse }"
    @pointerdown="onStart"
    @pointermove="onMove"
    @pointerup="onEnd"
    @pointercancel="onEnd"
  >
    <div
      v-if="!dragging"
      class="absolute inset-0 flex flex-col items-center justify-center gap-0.5 text-center pointer-events-none"
    >
      <p class="text-xs text-muted">{{ t('minimap.trackpadTitle') }}</p>
      <p class="text-[10px] text-border">{{ t('minimap.trackpadHint') }}</p>
    </div>

    <svg
      v-if="indicator"
      class="absolute inset-0 w-full h-full pointer-events-none"
    >
      <line
        :x1="indicator.sx"
        :y1="indicator.sy"
        :x2="indicator.cx"
        :y2="indicator.cy"
        class="stroke-accent"
        stroke-width="2"
        stroke-dasharray="3,3"
      />
      <circle :cx="indicator.sx" :cy="indicator.sy" r="4" class="fill-accent/60" />
      <circle :cx="indicator.cx" :cy="indicator.cy" r="6" class="fill-accent" />
    </svg>
  </div>
</template>
