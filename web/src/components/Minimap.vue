<script setup lang="ts">
import { ref, watch } from 'vue'
import type { MinimapFrame } from '@/types'

const props = defineProps<{
  frame: MinimapFrame | null
}>()

const emit = defineEmits<{
  viewMove: [point: [number, number]]
}>()

const canvas = ref<HTMLCanvasElement | null>(null)
const CANVAS_W = 280  // dp，§9.5
const CANVAS_H = 280

// 世界坐标 → canvas 像素坐标（y 轴翻转：SC2 y 向上，canvas y 向下）
function worldToCanvas(wx: number, wy: number, playable: number[]): [number, number] {
  const [px, py, pw, ph] = playable
  const cx = ((wx - px) / pw) * CANVAS_W
  const cy = CANVAS_H - ((wy - py) / ph) * CANVAS_H
  return [cx, cy]
}

// canvas 像素坐标 → 世界坐标
function canvasToWorld(cx: number, cy: number, playable: number[]): [number, number] {
  const [px, py, pw, ph] = playable
  const wx = (cx / CANVAS_W) * pw + px
  const wy = ((CANVAS_H - cy) / CANVAS_H) * ph + py
  return [wx, wy]
}

function render(f: MinimapFrame) {
  const ctx = canvas.value?.getContext('2d')
  if (!ctx) return
  ctx.clearRect(0, 0, CANVAS_W, CANVAS_H)

  // 背景
  ctx.fillStyle = '#1a1a2e'
  ctx.fillRect(0, 0, CANVAS_W, CANVAS_H)

  // playable area 边框
  ctx.strokeStyle = '#444'
  ctx.lineWidth = 1
  ctx.strokeRect(0, 0, CANVAS_W, CANVAS_H)

  const pl = f.map.playable

  // 己方单位
  for (const [x, y, k] of f.units_own) {
    const [cx, cy] = worldToCanvas(x, y, pl)
    ctx.fillStyle =
      k === 'N' ? '#4a9eff' :
      k === 'P' ? '#a0c8ff' :
      k === 'B' ? '#2a6eef' :
      '#4a9eff'  // 战斗单位 'A'
    const r = k === 'N' ? 4 : k === 'B' ? 3 : 2
    ctx.fillRect(cx - r / 2, cy - r / 2, r, r)
  }

  // 敌方可见
  for (const [x, y, k] of f.units_enemy_visible) {
    const [cx, cy] = worldToCanvas(x, y, pl)
    ctx.fillStyle = k === 'W' ? '#ff8080' : '#ff3030'
    ctx.fillRect(cx - 1, cy - 1, 2, 2)
  }

  // viewport 矩形（黄色框）
  const [vx, vy] = worldToCanvas(f.viewport.center[0], f.viewport.center[1], pl)
  const vw = (f.viewport.size[0] / pl[2]) * CANVAS_W
  const vh = (f.viewport.size[1] / pl[3]) * CANVAS_H
  ctx.strokeStyle = '#ffea00'
  ctx.lineWidth = 1.5
  ctx.strokeRect(vx - vw / 2, vy - vh / 2, vw, vh)
}

watch(() => props.frame, (f) => { if (f) render(f) })

// 拖拽：pointer events 统一 touch + 鼠标
let dragging = false
let lastSentMs = 0
const THROTTLE_MS = 100

function onPointerDown(e: PointerEvent) {
  dragging = true
  canvas.value?.setPointerCapture(e.pointerId)
  // 立即响应：单击也切视野
  sendIfInFrame(e)
}

function onPointerMove(e: PointerEvent) {
  if (!dragging) return
  const now = performance.now()
  if (now - lastSentMs < THROTTLE_MS) return
  sendIfInFrame(e)
  lastSentMs = now
}

function onPointerUp(e: PointerEvent) {
  if (!dragging) return
  dragging = false
  canvas.value?.releasePointerCapture(e.pointerId)
  // 抬起时再发一次（节流可能漏终点）
  sendIfInFrame(e)
}

function sendIfInFrame(e: PointerEvent) {
  if (!canvas.value || !props.frame) return
  const rect = canvas.value.getBoundingClientRect()
  const cx = e.clientX - rect.left
  const cy = e.clientY - rect.top
  // 钳到 canvas 范围内
  const ccx = Math.max(0, Math.min(CANVAS_W, cx))
  const ccy = Math.max(0, Math.min(CANVAS_H, cy))
  const [wx, wy] = canvasToWorld(ccx, ccy, props.frame.map.playable)
  emit('viewMove', [wx, wy])
}
</script>

<template>
  <div class="rounded-lg overflow-hidden border border-border bg-surface-3">
    <canvas
      ref="canvas"
      :width="CANVAS_W"
      :height="CANVAS_H"
      class="block touch-none select-none w-full"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerUp"
    />
    <p v-if="!frame" class="text-xs text-muted italic text-center py-2">
      等待小地图数据...
    </p>
  </div>
</template>
