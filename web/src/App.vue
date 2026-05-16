<script setup lang="ts">
// VibeCraft PWA 主组件（P0-11）
// - 三段式系统状态链（header 常驻）
// - sc2 === 'playing' → CockpitView（对局驾驶舱）
// - 其他 → LaunchView（启动 + 话语示例）
// - 无 room token 时显示引导提示
import { computed } from 'vue'
import StatusChain from '@/components/StatusChain.vue'
import LaunchView from '@/views/LaunchView.vue'
import CockpitView from '@/views/CockpitView.vue'
import { useWs } from '@/composables/useWs'
import type { CommandFrame } from '@/types'

const {
  status, send, sendViewMove, token,
  snapshotStrategy, recentCommands, events, minimap, lastEcho,
  recommendation, tactics, confirmRecommendation, dismissRecommendation,
  pendingForceStrategy, confirmForceStrategy, cancelForceStrategy,
  standingOrders, productionOverrides, revokeDirective,
} = useWs()

// 游戏是否可发指令（WS 已连 + SC2 playing 阶段）
const canSendCommand = computed(
  () => status.value.link === 'connected' && status.value.sc2 === 'playing'
)

// 「开始对局」按钮可用条件：已连服务端、SC2 尚未启动（idle / ended）
const canStartGame = computed(
  () =>
    status.value.link === 'connected' &&
    (status.value.sc2 === 'idle' || status.value.sc2 === 'ended')
)

// 对局中视图判据（sc2 === 'playing'）
const isPlaying = computed(() => status.value.sc2 === 'playing')

function startGame() {
  send({ type: 'start_game' })
}

function onCommand(frame: CommandFrame) {
  send(frame)
}

function onViewMove(pt: [number, number]) {
  sendViewMove(pt)
}

// SC2 状态显示文案
const sc2Label = computed(() => {
  const map: Record<string, string> = {
    idle: '等待开局',
    launching: '正在启动 SC2...',
    in_game: 'SC2 载入中...',
    playing: '对局进行中',
    ended: '对局结束',
    crashed: 'SC2 崩溃',
  }
  return map[status.value.sc2] ?? status.value.sc2
})
</script>

<template>
  <!-- 全屏深色背景，移动端竖向 flex 布局 -->
  <div class="min-h-screen bg-surface text-white flex flex-col select-none">

    <!-- 顶栏：Logo + 状态链（常驻） -->
    <header class="flex items-center justify-between px-4 py-3 bg-surface-2 border-b border-border">
      <span class="font-bold tracking-wide text-accent">VibeCraft</span>
      <StatusChain :status="status" />
    </header>

    <!-- 无 token 引导提示 -->
    <div v-if="!token" class="flex-1 flex flex-col items-center justify-center gap-4 px-6 text-center">
      <p class="text-2xl font-bold text-accent">扫码启动</p>
      <p class="text-muted text-sm leading-relaxed">
        请在 PC 端运行 <code class="bg-surface-3 px-1 rounded">vibecraft serve</code>，<br/>
        然后用手机扫码或输入显示的地址访问。
      </p>
    </div>

    <!-- 主内容区（有 token 时展示）-->
    <template v-else>
      <!-- 对局中 → CockpitView -->
      <CockpitView
        v-if="isPlaying"
        :strategy="snapshotStrategy"
        :recent-commands="recentCommands"
        :events="events"
        :minimap="minimap"
        :can-send-command="canSendCommand"
        :last-echo="lastEcho"
        :recommendation="recommendation"
        :tactics="tactics"
        :pending-force-strategy="pendingForceStrategy"
        :standing-orders="standingOrders"
        :production-overrides="productionOverrides"
        @command="onCommand"
        @view-move="onViewMove"
        @confirm-recommendation="confirmRecommendation"
        @dismiss-recommendation="dismissRecommendation"
        @confirm-force-strategy="confirmForceStrategy"
        @cancel-force-strategy="cancelForceStrategy"
        @revoke-standing="revokeDirective"
        @revoke-production="revokeDirective"
      />

      <!-- 其他状态 → LaunchView -->
      <LaunchView
        v-else
        :can-start-game="canStartGame"
        :sc2-label="sc2Label"
        :status="status"
        @start-game="startGame"
      />
    </template>

  </div>
</template>
