<script setup lang="ts">
/**
 * LiveView.vue — SC2 实时画面 WebRTC 直播组件
 *
 * 功能：
 * - 建立 WebRTC 连接，把 PC 上 SC2 的画面 + 系统声音（游戏声）流式传输到 <video>
 * - 折叠由父组件控制（collapsed prop，开关在 header 中央）：折叠时
 *   close() RTCPeerConnection → 服务端停采集，零开销；展开时重新建连
 * - 信令端口 = WebSocket 端口 + 1（BotService 约定）
 * - 声音：优先带声自动播放；被浏览器自动播放策略拦截时先静音播画面，再监听
 *   「首次任意手势」解除静音（玩家点 cockpit 任意按钮即恢复，无需专门按钮）。
 *   服务端音频走隔离子进程采集（webrtc.py / audio_capture.py，方案 A+2）。
 *
 * 连接流程：
 * 1. new RTCPeerConnection
 * 2. addTransceiver('video' / 'audio', {direction: 'recvonly'})
 * 3. createOffer → setLocalDescription
 * 4. 等 ICE gathering 完成（iceGatheringState === 'complete'）
 * 5. POST /webrtc/offer → 获取 answer SDP
 * 6. setRemoteDescription(answer)
 * 7. ontrack → 挂到 <video>
 */
import { ref, onUnmounted, watch, onBeforeUnmount } from 'vue'
import type { MinimapFrame } from '@/types'
import { t } from '@/i18n'

const props = defineProps<{
  /** 服务端主机名(2026-05-24 已废弃,signaling 走 WS frame;保留向后兼容) */
  serverHost: string
  /** 服务端 WS 端口(2026-05-24 已废弃,同上) */
  serverPort: number
  /** 连接是否可用(WS 已连时为 true,断连时不尝试 WebRTC) */
  connected: boolean
  /** 是否折叠(由父组件 header 中央开关控制;折叠时停流,省空间) */
  collapsed: boolean
  /**
   * 2026-05-24: signaling 走 WS frame(替代 fetch HTTP +1 端口),
   * 单端口反代(Tailscale Funnel / nginx)场景也能用。
   * 父组件传入 sendWebRtcOffer 函数(从 useWs 拿)。
   */
  sendOffer: (sdp: string, sdpType: string) => Promise<{ sdp: string; sdp_type: string }>
  /**
   * 2026-05-25 用户:直播画面手指拖动移视野。需要 minimap.viewport.center
   * 作 base + viewport.size 算 pixel→world 比例。
   */
  minimap?: MinimapFrame | null
}>()

// 2026-05-25 emit view-move(同 MinimapTrackpad,直接给 useWs.viewMove 用)
const emit = defineEmits<{
  'view-move': [point: [number, number]]
}>()

// 连接状态
const connecting = ref(false)
const error = ref<string | null>(null)
// 响应式 WebRTC 连接状态（驱动连接提示 overlay 显示）
const connState = ref<'idle' | 'connecting' | 'connected' | 'failed'>('idle')

// 视频元素
const videoEl = ref<HTMLVideoElement | null>(null)
// 活跃 PeerConnection
let pc: RTCPeerConnection | null = null

// ----------------------------------------------------------------
// 2026-05-25 手指拖动视频区域 → 移视野(Google Maps 模式)
// 按住的 game-world 点拖动过程中保持不变(跟手指走),松开 reset。
// 数学:屏幕 dx 像素 → world center 反向偏移 dx*scale,scale = viewport_world_w / video_clientWidth。
// ----------------------------------------------------------------
const dragging = ref(false)
let startPx: { x: number; y: number } | null = null
let baseCenter: [number, number] | null = null
let videoW = 1
let videoH = 1
let viewportW = 24  // SC2 一屏 grid 数, fallback 24
let viewportH = 18
let rafId: number | null = null
let cursorPx: { x: number; y: number } | null = null

// ----------------------------------------------------------------
// 2026-07-26 用户：点击/拖动"游戏画面里的 SC2 小地图" → 跳视野到该点(像游戏内点小地图)。
// SC2 小地图在**游戏画面内容**中的位置占比(从截图 07896b87 实测；不同分辨率可能要微调)。
// 运行时先用视频固有宽高算出游戏内容在 video 元素内的真实矩形(object-contain letterbox 感知)，
// 再套这组占比得小地图区，点击归一化 → playable 边界换算世界坐标(y 翻转)。
// ----------------------------------------------------------------
const MM_L = 0.014, MM_R = 0.233, MM_T = 0.746, MM_B = 0.966
let onMinimap = false

// 游戏内容在 video 元素内的实际矩形(object-contain：按固有宽高比 letterbox 居中)
function gameContentRect(el: HTMLVideoElement): { ox: number; oy: number; w: number; h: number } {
  const cw = el.clientWidth || 1
  const ch = el.clientHeight || 1
  const vw = el.videoWidth || cw
  const vh = el.videoHeight || ch
  const elAR = cw / ch
  const cAR = vw / vh
  if (cAR > elAR) {
    const h = cw / cAR
    return { ox: 0, oy: (ch - h) / 2, w: cw, h }
  }
  const w = ch * cAR
  return { ox: (cw - w) / 2, oy: 0, w, h: ch }
}

// 点在小地图区 → 返回对应世界坐标，否则 null。clamp=true 时把点夹进小地图区(拖动中用，出界也跟)。
function minimapWorld(e: PointerEvent, clamp: boolean): [number, number] | null {
  const el = videoEl.value
  const pl = props.minimap?.map?.playable
  if (!el || !pl || pl.length < 4) return null
  const rect = el.getBoundingClientRect()
  let px = e.clientX - rect.left
  let py = e.clientY - rect.top
  const g = gameContentRect(el)
  const mmL = g.ox + MM_L * g.w
  const mmR = g.ox + MM_R * g.w
  const mmT = g.oy + MM_T * g.h
  const mmB = g.oy + MM_B * g.h
  if (clamp) {
    px = Math.max(mmL, Math.min(mmR, px))
    py = Math.max(mmT, Math.min(mmB, py))
  } else if (px < mmL || px > mmR || py < mmT || py > mmB) {
    return null
  }
  const fx = (px - mmL) / (mmR - mmL || 1)
  const fy = (py - mmT) / (mmB - mmT || 1)
  const wx = pl[0] + fx * pl[2]
  const wy = pl[1] + (1 - fy) * pl[3]  // y 翻转:小地图顶=世界高 y
  return [wx, wy]
}

function scheduleDragEmit() {
  if (rafId !== null) return
  rafId = requestAnimationFrame(flushDrag)
}

function flushDrag() {
  rafId = null
  if (!startPx || !cursorPx || !baseCenter) return
  const dx = cursorPx.x - startPx.x
  const dy = cursorPx.y - startPx.y
  const scaleX = viewportW / videoW
  const scaleY = viewportH / videoH
  // Google Maps 模式:中心反向偏移让按住的点跟手指走。
  // SC2 y 向上为正 → 屏幕 dy 向下为正,中心反而要"加"dy*scale。
  const cx = baseCenter[0] - dx * scaleX
  const cy = baseCenter[1] + dy * scaleY
  emit('view-move', [cx, cy])
}

function onDragStart(e: PointerEvent) {
  if (!videoEl.value) return
  // 先判是否点在"游戏画面里的小地图"区 → 跳视野(而非拖拽平移主画面)
  const mmw = minimapWorld(e, false)
  if (mmw) {
    onMinimap = true
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    emit('view-move', mmw)
    return
  }
  const vp = props.minimap?.viewport
  if (!vp) return  // 还没收到 minimap 帧:无 base 无法计算
  const rect = videoEl.value.getBoundingClientRect()
  videoW = rect.width || 1
  videoH = rect.height || 1
  viewportW = vp.size[0] || 24
  viewportH = vp.size[1] || 18
  baseCenter = [vp.center[0], vp.center[1]]
  startPx = { x: e.clientX, y: e.clientY }
  cursorPx = { x: e.clientX, y: e.clientY }
  dragging.value = true
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
}

function onDragMove(e: PointerEvent) {
  if (onMinimap) {
    // 在小地图上拖动 → 连续跳视野(出界夹进小地图区，跟手)
    const mmw = minimapWorld(e, true)
    if (mmw) emit('view-move', mmw)
    return
  }
  if (!dragging.value) return
  cursorPx = { x: e.clientX, y: e.clientY }
  scheduleDragEmit()
}

function onDragEnd(e: PointerEvent) {
  if (onMinimap) {
    onMinimap = false
    try { (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId) } catch { /* noop */ }
    return
  }
  if (dragging.value) {
    ;(e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId)
    if (rafId !== null) {
      cancelAnimationFrame(rafId)
      rafId = null
    }
    flushDrag()  // 最后一次 emit
  }
  dragging.value = false
  startPx = null
  cursorPx = null
  baseCenter = null
}

onBeforeUnmount(() => {
  if (rafId !== null) cancelAnimationFrame(rafId)
})

// 2026-05-24: signaling 通过 WS frame(走 props.sendOffer),不再 fetch +1 端口。
// 删除原 signalingUrl —— 端口/协议拼接全废弃。

/** 等待 ICE gathering 完成（最多 5 秒） */
async function waitIceGathering(peerConn: RTCPeerConnection): Promise<void> {
  if (peerConn.iceGatheringState === 'complete') return
  return new Promise<void>((resolve) => {
    const timeout = window.setTimeout(() => {
      peerConn.removeEventListener('icegatheringstatechange', handler)
      resolve()
    }, 5000)
    function handler() {
      if (peerConn.iceGatheringState === 'complete') {
        clearTimeout(timeout)
        peerConn.removeEventListener('icegatheringstatechange', handler)
        resolve()
      }
    }
    peerConn.addEventListener('icegatheringstatechange', handler)
  })
}

// 「首次手势解除静音」的清理钩子：arm 时挂全局监听，触发或断连时移除
let removeGestureUnmute: (() => void) | null = null

/**
 * 监听任意用户手势，在手势 handler **内**解除静音（浏览器 autoplay 策略要求：带声播放
 * 必须发生在用户手势的瞬时激活期内，iOS 尤其严格 → 必须在 handler 内 muted=false+play）。
 *
 * 关键点（2026-07-05 修「进游戏后要专门点视频框才有声」）：
 * - **capture 阶段**监听：子组件 stopPropagation 的手势也能收到（否则点 cockpit 按钮不解除）。
 * - **connect 时就 arm**（不等 ontrack 失败）：进游戏后任意交互（发指令/点按钮）都能解除，不必专点视频。
 * - **确认真解除（有流且未被重新静音）才撤监听**，否则留着等下次手势（手势早于轨道到达时兜底）。
 */
function armFirstGestureUnmute(): void {
  if (removeGestureUnmute) return // 已 arm，不重复挂
  const events: Array<keyof WindowEventMap> = ['pointerdown', 'touchstart', 'keydown', 'click']
  const handler = () => {
    const el = videoEl.value
    if (el) {
      el.muted = false
      el.play().catch(() => {})
      // 真解除了(有流 + 没被重新静音)才撤；否则留监听等下次手势
      if (el.srcObject && !el.muted) removeGestureUnmute?.()
    }
  }
  removeGestureUnmute = () => {
    events.forEach((e) => window.removeEventListener(e, handler, true))
    removeGestureUnmute = null
  }
  events.forEach((e) => window.addEventListener(e, handler, { passive: true, capture: true }))
}

/** 建立 WebRTC 连接 */
async function connect(): Promise<void> {
  if (pc) return // 已有连接
  connecting.value = true
  connState.value = 'connecting'
  error.value = null

  // 提前 arm 手势解除静音：进游戏后任意交互（发指令/点按钮）都能开声，不必专门点视频框。
  // （建连/首播期间玩家的手势不会被浪费；ontrack 若带声自动播放失败也会兜底再 arm。）
  armFirstGestureUnmute()

  // 阶段1：取 TURN 中继配置（P2P 打不通时回落，尤其中国手机）。带 room token 过门控；
  // 拿到的 iceServers 含 coturn STUN（中国可达）+ turns:443 中继。fetch 失败/无 TURN →
  // 回退 google STUN。有 TURN 就**不再拼** google STUN（中国连不上它会拖满 5s gather）。
  let iceServers: RTCIceServer[] = []
  try {
    const room = new URLSearchParams(window.location.search).get('room') || ''
    const resp = await fetch(`/api/turn-credential?room=${encodeURIComponent(room)}`, {
      signal: AbortSignal.timeout(2000),
    })
    const data = await resp.json()
    if (Array.isArray(data.iceServers)) iceServers = data.iceServers
  } catch {
    /* 超时/网络失败/不支持 → 空，下面回退 */
  }
  if (iceServers.length === 0) {
    iceServers = [{ urls: 'stun:stun.l.google.com:19302' }]
  }

  const peerConn = new RTCPeerConnection({ iceServers })
  pc = peerConn

  // 只接收画面 + 声音（不发送）。服务端 SC2 画面 + 系统 loopback 游戏声各一条轨。
  peerConn.addTransceiver('video', { direction: 'recvonly' })
  peerConn.addTransceiver('audio', { direction: 'recvonly' })

  // 收到媒体轨 → 挂到 <video>（视频 / 音频同属一个 MediaStream）。
  // 优先带声自动播放；被浏览器自动播放策略拦截（多见于尚未交互的移动端）→
  // 先静音播画面，再 arm 一次"首次任意手势"解除静音（替代旧的"点按开启声音"按钮：
  // 玩家在 cockpit 上点任意按钮 / 发指令即满足手势要求，声音自动恢复，无需专门点）。
  peerConn.ontrack = (ev) => {
    const el = videoEl.value
    if (!el || !ev.streams[0]) return
    el.srcObject = ev.streams[0]
    el.muted = false
    el.play().catch(() => {
      el.muted = true
      el.play().catch(() => {})
      armFirstGestureUnmute()
    })
  }

  // 连接状态监控
  peerConn.onconnectionstatechange = () => {
    const state = peerConn.connectionState
    if (state === 'connected') {
      connecting.value = false
      connState.value = 'connected'
    } else if (state === 'failed' || state === 'closed') {
      connecting.value = false
      connState.value = state === 'failed' ? 'failed' : 'idle'
      if (state === 'failed') {
        error.value = t('live.connFailed')
      }
      // 清理（避免悬挂引用）
      if (pc === peerConn) pc = null
    }
  }

  try {
    // 创建 offer
    const offer = await peerConn.createOffer()
    await peerConn.setLocalDescription(offer)

    // 等 ICE gathering 完成（把 candidate 内联进 SDP）
    await waitIceGathering(peerConn)

    // 2026-05-24 走 WS frame signaling:父组件传入 sendOffer(useWs.sendWebRtcOffer)
    // 通过 ws.send({type:"webrtc_offer", sdp, sdp_type}) → 等 ws frame
    // {type:"webrtc_answer", sdp, sdp_type} → resolve Promise
    // 单端口反代场景也能用(不再需要 +1 端口)
    const answer = await props.sendOffer(
      peerConn.localDescription!.sdp,
      peerConn.localDescription!.type,
    )
    await peerConn.setRemoteDescription(new RTCSessionDescription({
      sdp: answer.sdp,
      type: answer.sdp_type as RTCSdpType,
    }))
    retryNotReadyCount = 0 // 信令成功，重试计数归零
  } catch (err) {
    const msg = err instanceof Error ? err.message : t('live.connFailed')
    error.value = t('live.signalFailed', { msg })
    connecting.value = false
    connState.value = 'failed'
    peerConn.close()
    if (pc === peerConn) pc = null
    // S4(2026-06-12 多人): 多实例下 SC2 PID 尚未就绪时 server 回
    // "sc2 not ready, retry" —— 2.5s 后自动重试(最多 8 次,够 SC2 启动窗口),
    // 不让视频卡死在 failed 态等玩家手动折叠/展开。
    if (msg.includes('not ready') && retryNotReadyCount < 8) {
      retryNotReadyCount++
      setTimeout(() => {
        if (props.connected && !props.collapsed && !pc) connect()
      }, 2500)
    }
  }
}

// "sc2 not ready" 自动重试计数(连接成功或手动断开时归零)
let retryNotReadyCount = 0

/** 关闭 WebRTC 连接（释放 PC，服务端随之停采集） */
function disconnect(): void {
  retryNotReadyCount = 0 // 手动断开，清掉未尽的 not-ready 重试
  if (pc) {
    pc.close()
    pc = null
  }
  if (videoEl.value) {
    videoEl.value.srcObject = null
  }
  removeGestureUnmute?.() // 断连时撤掉可能还挂着的手势监听
  connecting.value = false
  connState.value = 'idle'
}

// WS 连接状态 + 折叠状态共同决定是否建连
watch(
  [() => props.connected, () => props.collapsed],
  ([isConnected, isCollapsed]) => {
    if (isConnected && !isCollapsed && !pc) {
      connect()
    } else if (!isConnected || isCollapsed) {
      disconnect()
    }
  },
  { immediate: true },
)

// 组件卸载时关闭连接
onUnmounted(() => {
  disconnect()
})
</script>

<template>
  <!-- 实时画面区域；折叠由 header 中央开关控制，折叠时整体不渲染（省空间） -->
  <!-- #5: portrait 展开时 sticky top-0 贴视口顶，不管滚多深游戏画面始终可见；
       landscape 回退为 relative（维持原横屏布局，absolute overlay 需要 positioned ancestor）-->
  <!-- 横屏(2026-07-26 用户)：视频**绝对定位填充**左列视频区(脱离文档流→绝不撑高/顶出下方科技面板)，
       object-top **朝上对齐**(letterbox 留在底部)；容器高度由 App flex-1 决定=科技面板以外的全部。
       竖屏保持 sticky 顶 + max-h-40vh 静态流内不变。 -->
  <div v-show="!props.collapsed" class="sticky top-0 z-20 w-full bg-black border-b border-border landscape:relative landscape:w-full landscape:min-h-0 landscape:overflow-hidden landscape:border-b-0">
    <video
      ref="videoEl"
      playsinline
      data-testid="live-video"
      class="w-full max-h-[40vh] object-contain select-none touch-none cursor-grab landscape:absolute landscape:inset-0 landscape:max-h-none landscape:h-full landscape:object-top"
      :class="{ 'cursor-grabbing': dragging }"
      @pointerdown="onDragStart"
      @pointermove="onDragMove"
      @pointerup="onDragEnd"
      @pointercancel="onDragEnd"
    />
    <!-- 覆盖提示：还没连上时显示 -->
    <div
      v-if="connState !== 'connected'"
      class="absolute inset-0 flex items-center justify-center"
    >
      <div class="text-center">
        <p v-if="connecting" class="text-sm text-muted">{{ t('live.connecting') }}</p>
        <p v-else-if="error" class="text-sm text-danger">{{ error }}</p>
        <p v-else-if="!props.connected" class="text-sm text-muted">{{ t('live.waitingServer') }}</p>
        <p v-else class="text-sm text-muted">{{ t('live.waitingSC2') }}</p>
      </div>
    </div>
  </div>
</template>
