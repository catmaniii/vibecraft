<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted, computed } from 'vue'
import type { MinimapFrame, PixelMapB64 } from '@/types'

// SC2 alert enum 名 → 玩家可读中文(只挑常用几个)
const ALERT_LABELS: Record<string, string> = {
  BuildingUnderAttack: '建筑被攻击!',
  UnitUnderAttack: '部队被攻击!',
  NuclearLaunchDetected: '核弹警告!',
  NydusWormDetected: '虫穴入侵!',
  MineralsExhausted: '矿采空',
  VespeneExhausted: '气矿采空',
}

const props = defineProps<{
  frame: MinimapFrame | null
}>()

// terrain 只在开局第一帧带,缓存到组件状态。
// 用 OffscreenCanvas 渲染一次成 imageBitmap,后续 drawImage 直接 blit。
let terrainBitmap: ImageBitmap | OffscreenCanvas | null = null
let terrainDims: { w: number; h: number } | null = null

function decodeB64(b64: string): Uint8Array {
  const bin = atob(b64)
  const u8 = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i)
  return u8
}

// terrain uint8 → OffscreenCanvas(灰绿色调,h 翻转适应 y-up)
function makeTerrainBitmap(t: PixelMapB64): OffscreenCanvas | HTMLCanvasElement {
  const u8 = decodeB64(t.b64)
  // OffscreenCanvas 不是所有浏览器都有(iOS Safari < 16.4),fallback document.createElement
  const off = typeof OffscreenCanvas !== 'undefined'
    ? new OffscreenCanvas(t.w, t.h)
    : document.createElement('canvas')
  if (!(off instanceof OffscreenCanvas)) {
    off.width = t.w; off.height = t.h
  }
  const ctx = (off as OffscreenCanvas | HTMLCanvasElement).getContext('2d') as
    OffscreenCanvasRenderingContext2D | CanvasRenderingContext2D
  const img = ctx.createImageData(t.w, t.h)
  for (let i = 0; i < u8.length; i++) {
    const h = u8[i]
    // 灰绿色调:低 = 暗绿,高 = 灰白
    img.data[i * 4] = Math.min(255, h * 0.55 + 20)
    img.data[i * 4 + 1] = Math.min(255, h * 0.70 + 30)
    img.data[i * 4 + 2] = Math.min(255, h * 0.45 + 25)
    img.data[i * 4 + 3] = 255
  }
  ctx.putImageData(img, 0, 0)
  return off as OffscreenCanvas | HTMLCanvasElement
}

// visibility uint8 → ImageData alpha mask(0=Hidden 黑不透, 1=Fogged 半透, 2=Visible 全透)
function makeVisionMask(v: PixelMapB64): OffscreenCanvas | HTMLCanvasElement {
  const u8 = decodeB64(v.b64)
  const off = typeof OffscreenCanvas !== 'undefined'
    ? new OffscreenCanvas(v.w, v.h)
    : document.createElement('canvas')
  if (!(off instanceof OffscreenCanvas)) {
    off.width = v.w; off.height = v.h
  }
  const ctx = (off as OffscreenCanvas | HTMLCanvasElement).getContext('2d') as
    OffscreenCanvasRenderingContext2D | CanvasRenderingContext2D
  const img = ctx.createImageData(v.w, v.h)
  for (let i = 0; i < u8.length; i++) {
    const s = u8[i]
    img.data[i * 4] = 0
    img.data[i * 4 + 1] = 0
    img.data[i * 4 + 2] = 0
    // 0=Hidden 全黑覆盖, 1=Fogged 半黑覆盖, 2=Visible 透明
    img.data[i * 4 + 3] = s === 0 ? 200 : s === 1 ? 110 : 0
  }
  ctx.putImageData(img, 0, 0)
  return off as OffscreenCanvas | HTMLCanvasElement
}

const emit = defineEmits<{
  viewMove: [point: [number, number]]
}>()

const wrapper = ref<HTMLDivElement | null>(null)
const canvas = ref<HTMLCanvasElement | null>(null)

// alerts banner 文案(去重 + 中文化)
const alertText = computed(() => {
  if (!props.frame?.alerts) return ''
  const seen = new Set<string>()
  const labels: string[] = []
  for (const a of props.frame.alerts) {
    if (seen.has(a)) continue
    seen.add(a)
    labels.push(ALERT_LABELS[a] || a)
  }
  return labels.join(' · ')
})

// canvas 物理像素 = CSS 显示像素(用 ResizeObserver 同步),
// 杜绝"物理 200×200 拉伸到 350×200 显示"导致的坐标系偏差。
// 上限避免 desktop 大屏幕时铺得太大。
const MIN_SIZE = 120
const MAX_SIZE = 260
const size = ref(200)

function syncSize() {
  if (!wrapper.value) return
  const w = wrapper.value.clientWidth
  if (w > 0) {
    size.value = Math.max(MIN_SIZE, Math.min(MAX_SIZE, Math.round(w)))
    if (props.frame) render(props.frame)
  }
}

let ro: ResizeObserver | null = null
onMounted(() => {
  syncSize()
  if (wrapper.value && typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(syncSize)
    ro.observe(wrapper.value)
  }
})
onUnmounted(() => ro?.disconnect())

// letterbox 显示区:canvas S×S 内按 playable 长宽比居中,保留 aspect 比例不拉伸。
// playable 比 canvas 横长 → 上下留黑;比 canvas 竖长 → 左右留黑。
function computeDisplayArea(S: number, pw: number, ph: number): {
  ox: number; oy: number; dw: number; dh: number
} {
  const ar = pw / ph
  if (ar >= 1) {
    const dh = S / ar
    return { ox: 0, oy: (S - dh) / 2, dw: S, dh }
  }
  const dw = S * ar
  return { ox: (S - dw) / 2, oy: 0, dw, dh: S }
}

// 世界坐标 → canvas 像素(y 翻转:SC2 y 向上,canvas y 向下;letterbox 居中)
function worldToCanvas(wx: number, wy: number, playable: number[], S: number): [number, number] {
  const [px, py, pw, ph] = playable
  const { ox, oy, dw, dh } = computeDisplayArea(S, pw, ph)
  const cx = ox + ((wx - px) / pw) * dw
  const cy = oy + dh - ((wy - py) / ph) * dh
  return [cx, cy]
}

// canvas 像素 → 世界坐标(反 letterbox 偏移)
function canvasToWorld(cx: number, cy: number, playable: number[], S: number): [number, number] {
  const [px, py, pw, ph] = playable
  const { ox, oy, dw, dh } = computeDisplayArea(S, pw, ph)
  const wx = ((cx - ox) / dw) * pw + px
  const wy = ((oy + dh - cy) / dh) * ph + py
  return [wx, wy]
}

function render(f: MinimapFrame) {
  const ctx = canvas.value?.getContext('2d')
  if (!ctx) return
  const S = size.value
  ctx.clearRect(0, 0, S, S)

  const pl = f.map.playable
  // letterbox 显示矩形:playable 居中,保留长宽比(不拉伸)
  const { ox, oy, dw, dh } = computeDisplayArea(S, pl[2], pl[3])

  // 1) 全 canvas 背景(letterbox 黑边区也填充)
  ctx.fillStyle = '#0a0a14'
  ctx.fillRect(0, 0, S, S)
  // 2) 显示区背景兜底(terrain 未到时显示)
  ctx.fillStyle = '#1a1a2e'
  ctx.fillRect(ox, oy, dw, dh)

  // 3) 地形(开局第一帧带,后续缓存)。画到 letterbox 显示区,保留长宽比。
  if (f.terrain && terrainBitmap === null) {
    terrainBitmap = makeTerrainBitmap(f.terrain)
    terrainDims = { w: f.terrain.w, h: f.terrain.h }
  }
  if (terrainBitmap && terrainDims) {
    // pixelmap row 0 是世界 y=0(底),canvas y=0 是顶 → 上下翻转(只翻显示区内)
    ctx.save()
    ctx.imageSmoothingEnabled = false
    ctx.translate(ox, oy + dh)
    ctx.scale(1, -1)
    ctx.drawImage(terrainBitmap as CanvasImageSource, 0, 0, dw, dh)
    ctx.restore()
  }

  // 3.5) 中立资源点(在迷雾之前画 → 未探索区被 fog 压暗、已探索的亮,和游戏内一致)。
  //      配色对齐 SC2 小地图:水晶矿浅亮蓝(游戏内偏亮、很浅的蓝)、气矿绿色。
  if (f.resources) {
    for (const [x, y, k] of f.resources) {
      const [cx, cy] = worldToCanvas(x, y, pl, S)
      ctx.fillStyle = k === 'G' ? '#2ecc40' : '#bce3ff'  // 气矿绿 / 水晶矿浅亮蓝(对齐游戏内)
      ctx.fillRect(cx - 1, cy - 1, 2, 2)
    }
  }

  // 4) 战争迷雾(每帧)
  if (f.vision) {
    const visBmp = makeVisionMask(f.vision)
    ctx.save()
    ctx.imageSmoothingEnabled = false
    ctx.translate(ox, oy + dh)
    ctx.scale(1, -1)
    ctx.drawImage(visBmp as CanvasImageSource, 0, 0, dw, dh)
    ctx.restore()
  }

  // 5) playable 边框(画在 letterbox 显示区四周)
  ctx.strokeStyle = '#444'
  ctx.lineWidth = 1
  ctx.strokeRect(ox, oy, dw, dh)

  for (const [x, y, k] of f.units_own) {
    const [cx, cy] = worldToCanvas(x, y, pl, S)
    ctx.fillStyle =
      k === 'N' ? '#4a9eff' :
      k === 'P' ? '#a0c8ff' :
      k === 'B' ? '#2a6eef' :
      '#4a9eff'
    const r = k === 'N' ? 5 : k === 'B' ? 4 : 3
    ctx.fillRect(cx - r / 2, cy - r / 2, r, r)
  }

  for (const [x, y, k] of f.units_enemy_visible) {
    const [cx, cy] = worldToCanvas(x, y, pl, S)
    ctx.fillStyle = k === 'W' ? '#ff8080' : '#ff3030'
    ctx.fillRect(cx - 1.5, cy - 1.5, 3, 3)
  }

  // viewport 黄框(尺寸按 letterbox 显示区 dw/dh 算,跟 playable 实际比例匹配)
  const [vx, vy] = worldToCanvas(f.viewport.center[0], f.viewport.center[1], pl, S)
  const vw = (f.viewport.size[0] / pl[2]) * dw
  const vh = (f.viewport.size[1] / pl[3]) * dh
  ctx.strokeStyle = '#ffea00'
  ctx.lineWidth = 1.5
  ctx.strokeRect(vx - vw / 2, vy - vh / 2, vw, vh)

  // 被攻击位置:红色脉冲圈(用收到帧的 ts 算 phase,4.5Hz 自然闪)
  if (f.under_attack && f.under_attack.length > 0) {
    const phase = (Math.sin(f.ts * 6.0) + 1) / 2  // 0..1
    const alpha = 0.5 + 0.5 * phase
    const radius = 6 + 3 * phase
    ctx.strokeStyle = `rgba(255, 60, 60, ${alpha.toFixed(2)})`
    ctx.lineWidth = 2
    for (const [wx, wy] of f.under_attack) {
      const [cx, cy] = worldToCanvas(wx, wy, pl, S)
      ctx.beginPath()
      ctx.arc(cx, cy, radius, 0, Math.PI * 2)
      ctx.stroke()
    }
  }
}

watch(() => props.frame, (f) => { if (f) render(f) })

// 拖拽
let dragging = false
let lastSentMs = 0
const THROTTLE_MS = 100

function onPointerDown(e: PointerEvent) {
  dragging = true
  canvas.value?.setPointerCapture(e.pointerId)
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
  sendIfInFrame(e)
}

function sendIfInFrame(e: PointerEvent) {
  if (!canvas.value || !props.frame) return
  // canvas 物理像素 = CSS 显示像素(syncSize 保证),
  // 所以 getBoundingClientRect 的 CSS 坐标 = canvas 物理像素坐标,
  // 不需要再按比例归一化。
  const rect = canvas.value.getBoundingClientRect()
  const cx = e.clientX - rect.left
  const cy = e.clientY - rect.top
  const S = size.value
  const ccx = Math.max(0, Math.min(S, cx))
  const ccy = Math.max(0, Math.min(S, cy))
  const [wx, wy] = canvasToWorld(ccx, ccy, props.frame.map.playable, S)
  emit('viewMove', [wx, wy])
}
</script>

<template>
  <!-- wrapper 由父组件决定宽度(应该是 50%/with max-w);canvas 物理像素 = CSS 显示像素 -->
  <div ref="wrapper" class="rounded-lg overflow-hidden border border-border bg-surface-3 relative">
    <canvas
      ref="canvas"
      :width="size"
      :height="size"
      :style="{ width: size + 'px', height: size + 'px' }"
      class="block touch-none select-none"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerUp"
    />
    <!-- 全局 alert banner(BuildingUnderAttack 等),浮在小地图上方 -->
    <div
      v-if="frame && frame.alerts && frame.alerts.length > 0"
      class="absolute top-1 left-1 right-1 bg-danger/90 text-white text-xs font-bold
             px-2 py-1 rounded text-center animate-pulse pointer-events-none"
    >
      {{ alertText }}
    </div>
    <p v-if="!frame" class="text-xs text-muted italic text-center py-2">
      等待小地图数据...
    </p>
  </div>
</template>
