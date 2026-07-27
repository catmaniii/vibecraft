<script setup lang="ts">
// VibeCraft PWA 主组件（P0-11 + WebRTC 直播 + 多人联网入口页）
// - isComplete() === false → EntryView（用户名 + 服务器列表）
// - isComplete() === true  → 主界面（三段式状态链 + CockpitView / LaunchView）
// - LiveView：SC2 实时画面（竖屏顶部 / 横屏左侧）
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { t } from '@/i18n'
import StatusChain from '@/components/StatusChain.vue'
import CockpitView from '@/views/CockpitView.vue'
import LiveView from '@/components/LiveView.vue'
import TechProgressPanel from '@/components/TechProgressPanel.vue'
import EntryView from '@/components/EntryView.vue'
import RoomLobby from '@/components/RoomLobby.vue'
import GameBusyNotice from '@/components/GameBusyNotice.vue'
import ChatPanel from '@/components/ChatPanel.vue'
import { useWs } from '@/composables/useWs'
import { useProfile } from '@/composables/useProfile'
import type { CommandFrame } from '@/types'

// ---- 入口页门控 ----
// adoptUrlRoom：扫码带 ?room= 时，把当前 origin 自动注册为服务器并选中；
// 必须在 useWs() 之前调用，否则 useWs 读不到 selectedServer。
const { adoptUrlRoom, isComplete, profile } = useProfile()
adoptUrlRoom()

// showMain：profile 已完整 → 直接跳过入口页；否则先显示入口页
const showMain = ref(isComplete())

const {
  status, send, sendViewMove, connectNow, token, myRace,
  snapshotStrategy, recentCommands, events, minimap, lastEcho, lastReceived,
  recommendation, tactics, tacticalDebug, confirmRecommendation, dismissRecommendation,
  pendingForceStrategy, confirmForceStrategy, cancelForceStrategy,
  pendingClarification, confirmClarification, cancelClarification,
  standingOrders, productionOverrides, activeTactics, commandCards, revokeDirective,
  sendTacticalAction, sendMacroAction, sendStrategyAction, lastAutoSwitch, endGame,
  sendWebRtcOffer,
  techProgress, productionBuildings, armyUnits, voiceGroups, maxVoiceGroups, groupColors, controlledUnits,
  botSelfEval, workerMode, miningPriority,
  lastStealthRelease,
  // 语音输入（Task 7：透传给 CockpitView → CommandInput → VoiceInput）
  sendAudioChunk, sendAudioEnd, sendAudioCancel, lastTranscript,
  // 多人联网 lobby（Task 9）
  roomState, roomError, amIInRoom, autoJoinInFlight, sendLobby, close,
  // 文字聊天（房间级广播）
  chatMessages, myPid, sendChat, requestChatHistory,
} = useWs()

// isInLobby：我在房间 + 状态 lobby / starting → 显示 RoomLobby
// roomState===null（旧版 server / 单人）→ 保持原有行为，直接进主界面
const isInLobby = computed(
  () =>
    showMain.value &&
    amIInRoom.value &&
    roomState.value !== null &&
    (roomState.value.state === 'lobby' || roomState.value.state === 'starting'),
)

// 游戏进行中提醒（2026-06-17 用户）：后加入者连上发现已有玩家在对局（room.state != lobby）
// → 不弹回入口、不排队，显示 GameBusyNotice。busyPlayerName 取断连前 room_state 里在玩的人名。
const gameBusy = ref(false)
const busyPlayerName = ref<string | null>(null)

// 入口页 [连接] 按钮回调：profile 已写入 → 显示主界面 + 发起 WS 连接
function onEntryConnect() {
  gameBusy.value = false
  showMain.value = true
  connectNow()
}

// GameBusyNotice [重试]：清标志 + 重新连接重查（游戏若已结束则正常进大厅）
function onBusyRetry() {
  gameBusy.value = false
  connectNow()
}

// GameBusyNotice [返回入口]：清标志 + 断连回入口页
function onBusyBack() {
  gameBusy.value = false
  close()
  showMain.value = false
}

// lobby [退出房间] 按钮回调（2026-06-12 用户）：告知 server 清 slot → 断连 → 回入口页
// （server 端连接与入房已解耦，刷新后不会被拉回房）
function onLobbyLeave() {
  sendLobby({ type: 'lobby_leave' })
  // 给 lobby_leave 帧一拍发送时间再断连（同一连接顺序发送，几乎必达；
  // 即便丢失，server 的 10s 断线宽限 leave 也会兜底清位）
  setTimeout(() => {
    close()
    showMain.value = false
  }, 150)
}

// 兜底（2026-06-12 用户：不要中间"加入房间"页）：已连接但不在房（刷新进来 /
// 被踢 / 被超时清位）→ 直接断连回入口页，由用户重新点 [连接] 进房。
// 注意排除"自动 join 在飞"的瞬间（连上 → 收到空房预览 → join 响应还没回来）。
watch(
  [() => status.value.link, roomState, amIInRoom],
  ([link, rs, inRoom]) => {
    if (
      showMain.value &&
      link === 'connected' &&
      rs !== null &&
      !inRoom &&
      !autoJoinInFlight.value
    ) {
      if (rs.state !== 'lobby') {
        // 游戏进行中：已有玩家在对局（lobby_join 被 server 以"对局进行中"拒绝）→
        // 不弹回入口、不排队，显示 GameBusyNotice 提醒玩家稍后再试（2026-06-17 用户）。
        // 先从 room_state 取在玩的人名（host slot）做快照，再断连。
        const host = rs.slots.find((s) => s.player_id && s.player_id === rs.host_player_id)
        busyPlayerName.value = host?.name ?? null
        gameBusy.value = true
        close()
      } else {
        // lobby 态却不在房（刷新/被踢/被超时清位）→ 断连回入口页，由用户重新点 [连接]
        close()
        showMain.value = false
      }
    }
  },
)

// 2026-05-24 用户:webui 顶部"结束本局"按钮(仅房主或旧单人模式可点)
function onEndGame() {
  if (!confirm(t('app.confirmEndGame'))) return
  endGame()
}

// #7: 非房主玩家的"认输"按钮（确认后发 surrender 帧）
function onSurrender() {
  if (!confirm(t('app.confirmSurrender'))) return
  sendLobby({ type: 'surrender' })
}

// 游戏是否可发指令（WS 已连 + SC2 playing 阶段）
const canSendCommand = computed(
  () => status.value.link === 'connected' && status.value.sc2 === 'playing'
)

// 对局中视图判据（sc2 === 'playing'）
const isPlaying = computed(() => status.value.sc2 === 'playing')

// 连接中门控（2026-06-17 用户：点连接先闪出上一把残留界面）：用户主动连接（connectNow 已
// resetSessionState 清空 roomState/sc2）后、room_state / 游戏状态到达前，显示"连接中"占位，
// 不让主界面用（已被清空的）状态渲染。中途断线 auto-retry 不走 resetSessionState，roomState/
// isPlaying 仍在 → 此门控不触发 → 不会闪掉对局中的 cockpit。
const isConnecting = computed(
  () =>
    showMain.value &&
    (status.value.link === 'connecting' || status.value.link === 'reconnecting') &&
    roomState.value === null &&
    !isPlaying.value,
)

// 实时画面折叠状态（由 header 中央的开关控制；折叠 → LiveView 停流）
const liveCollapsed = ref(false)

// 全屏切换（2026-07-26 用户：竖屏 + 宽屏都要）。用 Fullscreen API 对根元素切换；
// 监听 fullscreenchange 同步图标状态（系统 ESC / 手势退出也能同步）。iOS Safari 不支持
// 元素全屏 → requestFullscreen 可能 undefined，用可选链兜底不报错。
const isFullscreen = ref(false)
function toggleFullscreen() {
  const doc = document as Document & { webkitFullscreenElement?: Element }
  const el = document.documentElement as HTMLElement & { webkitRequestFullscreen?: () => Promise<void> }
  const d = document as Document & { webkitExitFullscreen?: () => Promise<void> }
  const active = document.fullscreenElement || doc.webkitFullscreenElement
  if (!active) {
    ;(el.requestFullscreen?.() ?? el.webkitRequestFullscreen?.())?.catch?.(() => {})
  } else {
    ;(document.exitFullscreen?.() ?? d.webkitExitFullscreen?.())?.catch?.(() => {})
  }
}
function syncFullscreen() {
  const doc = document as Document & { webkitFullscreenElement?: Element }
  isFullscreen.value = !!(document.fullscreenElement || doc.webkitFullscreenElement)
}
onMounted(() => {
  document.addEventListener('fullscreenchange', syncFullscreen)
  document.addEventListener('webkitfullscreenchange', syncFullscreen)
})
onUnmounted(() => {
  document.removeEventListener('fullscreenchange', syncFullscreen)
  document.removeEventListener('webkitfullscreenchange', syncFullscreen)
})

function onCommand(frame: CommandFrame) {
  send(frame)
}

function onViewMove(pt: [number, number]) {
  sendViewMove(pt)
}

// SC2 状态显示文案
const sc2Label = computed(() => {
  const map: Record<string, string> = {
    idle: t('app.sc2Idle'),
    launching: t('app.sc2Launching'),
    in_game: t('app.sc2InGame'),
    playing: t('app.sc2Playing'),
    ended: t('app.sc2Ended'),
    crashed: t('app.sc2Crashed'),
  }
  return map[status.value.sc2] ?? status.value.sc2
})

// WebRTC 信令所用的服务端主机 + 端口
// 信令端口 = WebSocket 端口 + 1（BotService 约定，ADR 0013）
const serverHost = computed(() => window.location.hostname)
const serverPort = computed(() => Number(window.location.port) || 80)
const wsConnected = computed(() => status.value.link === 'connected')
</script>

<template>
  <!-- 游戏进行中提醒（2026-06-17 用户）：后加入者连上发现已有玩家在对局 → 显示提醒,
       不弹回入口、不排队。最高优先级（盖过入口/大厅/主界面）。 -->
  <GameBusyNotice
    v-if="gameBusy"
    :player-name="busyPlayerName"
    @retry="onBusyRetry"
    @back="onBusyBack"
  />

  <!-- 入口页：profile 未完整时全屏显示（无 header），用户填完点 [连接] 再进主界面 -->
  <EntryView v-else-if="!showMain" @connect="onEntryConnect" />

  <!-- 连接中占位（2026-06-17 用户）：用户主动连接后、room_state/游戏状态到达前显示，
       避免主界面用上一把残留状态渲染出旧游戏画面。 -->
  <div
    v-else-if="isConnecting"
    class="min-h-screen bg-surface flex flex-col items-center justify-center gap-4"
    data-testid="connecting-screen"
  >
    <div class="w-10 h-10 rounded-full border-2 border-border border-t-accent animate-spin"></div>
    <p class="text-sm text-muted">{{ t('app.connecting') }}</p>
  </div>

  <!-- 房间大厅：我在 slots 里 + state=lobby/starting
       roomState===null（旧 server / 单人）→ 跳过，保持旧行为 -->
  <RoomLobby
    v-else-if="isInLobby"
    :room-state="roomState!"
    :my-player-id="profile.deviceId"
    :room-error="roomError"
    :status="status"
    @lobby="sendLobby"
    @leave="onLobbyLeave"
  />

  <!-- 主界面：全屏深色背景，移动端竖向 flex 布局 -->
  <!-- 横屏锁定视口高度(用 dvh 排除移动端地址栏高度，否则整页会溢出可滚→左视频被带着滑，
       2026-07-25 用户报此 bug) + overflow-hidden，让右操作面板独立滚、左列(视频+科技面板)固定；
       竖屏保持 min-h-screen 可整页滚（sticky 视频）不变 -->
  <div v-else class="min-h-screen landscape:h-[100dvh] landscape:overflow-hidden bg-surface text-white flex flex-col select-none">

    <!-- 顶栏：Logo + 实时画面开关(紧邻 Logo) | 结束本局 + 状态链 -->
    <!-- 2026-05-24 用户:不再 absolute 居中,避免窄屏跟"结束本局"重叠 -->
    <!-- 顶部状态栏：py-3(~80px)→py-1.5(~50px)，给 TechProgressPanel 留出空间 -->
    <header class="flex items-center justify-between px-4 py-1.5 bg-surface-2 border-b border-border">
      <div class="flex items-center gap-3 min-w-0">
        <span class="font-bold tracking-wide text-accent shrink-0">VibeCraft</span>
        <!-- 实时画面收起/展开开关 — 仅对局中显示,挪到左侧 logo 旁 -->
        <button
          v-if="isPlaying"
          type="button"
          class="flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-semibold text-muted hover:text-white hover:bg-surface-3 transition-colors shrink-0"
          @click="liveCollapsed = !liveCollapsed"
        >
          <span>{{ t('app.liveToggle') }}</span>
          <span
            class="text-[10px] transition-transform duration-200"
            :class="liveCollapsed ? 'rotate-180' : ''"
          >▼</span>
        </button>
      </div>

      <div class="flex items-center gap-2 shrink-0">
        <!-- #7: 房主（或 roomState=null 旧单人模式）→ 结束本局 -->
        <button
          v-if="isPlaying && (!roomState || profile.deviceId === roomState.host_player_id)"
          type="button"
          data-testid="end-game-btn"
          class="px-2.5 py-1 rounded text-xs font-semibold bg-danger/15 border border-danger/50 text-danger hover:bg-danger/25 transition-colors"
          @click="onEndGame"
        >{{ t('app.endGame') }}</button>
        <!-- #7: 非房主 → 认输 -->
        <button
          v-if="isPlaying && roomState && profile.deviceId !== roomState.host_player_id"
          type="button"
          data-testid="surrender-btn"
          class="px-2.5 py-1 rounded text-xs font-semibold bg-danger/15 border border-danger/50 text-danger hover:bg-danger/25 transition-colors"
          @click="onSurrender"
        >{{ t('app.surrender') }}</button>
        <!-- 全屏切换（2026-07-26 用户：竖屏 + 宽屏都要）。始终显示，不限对局中。 -->
        <button
          type="button"
          data-testid="fullscreen-btn"
          class="flex items-center justify-center w-7 h-7 rounded text-muted hover:text-white hover:bg-surface-3 transition-colors shrink-0"
          :title="isFullscreen ? '退出全屏' : '全屏'"
          :class="isFullscreen ? 'text-accent' : ''"
          @click="toggleFullscreen"
        >
          <span class="text-base leading-none">⛶</span>
        </button>
        <StatusChain :status="status" />
      </div>
    </header>

    <!-- 2026-05-23 用户:AutoSwitchToast 从全局 fixed 浮层迁到 CockpitView 的
         "当前宏观策略"框内 → 改为通过 prop lastAutoSwitch 传递,不在 App.vue
         挂载组件。-->

    <!--
      响应式布局（ADR 0013）：单个 LiveView，flex 方向随朝向切换。
      竖屏 flex-col：LiveView 在最上；横屏 flex-row：LiveView 占左。
      LiveView 仅在对局中（sc2 playing）渲染——开始界面不显示，且不会
      因为重复 mount 多开 WebRTC 连接。折叠由 header 中央开关控制。
    -->
    <!-- #5: portrait 去掉 overflow-hidden，允许 body 滚动 + LiveView sticky；
         landscape 保留 overflow-hidden 维持原全屏布局 -->
    <div class="flex flex-col landscape:flex-row flex-1 landscape:min-h-0 landscape:overflow-hidden">
      <!-- 左列(横屏)：视频(上,top 对齐) + 科技/产能/兵种面板(视频下方)。用**始终存在的 wrapper**
           (flex-1) 包住 → 即使关闭实时画面(LiveView v-show 隐藏)，左列仍占着左边空间，右操作面板 +
           输入框自然留在右边不动(2026-07-26 用户 req2)。竖屏用 contents 不生成盒子 → LiveView 自身
           sticky top-0 照旧(包含块仍是高 row)；科技面板竖屏由 CockpitView 那份负责(此处 landscape:block)。 -->
      <div
        v-if="isPlaying"
        class="contents landscape:flex landscape:flex-1 landscape:min-w-0 landscape:min-h-0 landscape:flex-col landscape:overflow-hidden"
      >
        <LiveView
          :server-host="serverHost"
          :server-port="serverPort"
          :connected="wsConnected"
          :collapsed="liveCollapsed"
          :send-offer="sendWebRtcOffer"
          :minimap="minimap"
          @view-move="onViewMove"
          class="landscape:flex-1 landscape:min-w-0 landscape:min-h-0"
        />
        <div class="hidden landscape:block landscape:shrink-0 px-3 py-2 border-t border-border bg-surface">
          <TechProgressPanel
            :tech="techProgress"
            :production="productionBuildings"
            :units="armyUnits"
            :controlled-units="controlledUnits ?? null"
            @macro-action="(dim, val) => sendMacroAction(dim, val)"
          />
        </div>
      </div>

      <!-- 现有 UI：竖屏 flex-1（视频下方铺满，整页 sticky 滚）；横屏固定宽 + 独立上下滚
           （2026-07-26 用户：科技面板回右面板，右面板略加宽到 24rem 容纳它，左视频仍占大头）-->
      <div class="flex-1 min-w-0 min-h-0 flex flex-col landscape:flex-none landscape:w-[24rem]">
        <CockpitView
          v-if="isPlaying"
          :strategy="snapshotStrategy"
          :recent-commands="recentCommands"
          :events="events"
          :minimap="minimap"
          :can-send-command="canSendCommand"
          :last-echo="lastEcho"
          :last-received="lastReceived"
          :recommendation="recommendation"
          :tactics="tactics"
          :tactical-debug="tacticalDebug"
          :pending-force-strategy="pendingForceStrategy"
          :pending-clarification="pendingClarification"
          :command-cards="commandCards"
          :active-tactics="activeTactics"
          :my-race="myRace"
          :last-auto-switch="lastAutoSwitch as any"
          :tech-progress="techProgress"
          :production-buildings="productionBuildings"
          :army-units="armyUnits"
          :voice-groups="voiceGroups"
          :max-voice-groups="maxVoiceGroups"
          :group-colors="groupColors"
          :controlled-units="controlledUnits"
          :bot-self-eval="botSelfEval"
          :mining-priority="miningPriority"
          :worker-mode="workerMode"
          :last-stealth-release="lastStealthRelease"
          :send-audio-chunk="sendAudioChunk"
          :send-audio-end="sendAudioEnd"
          :send-audio-cancel="sendAudioCancel"
          :last-transcript="lastTranscript"
          @command="onCommand"
          @view-move="onViewMove"
          @confirm-recommendation="confirmRecommendation"
          @dismiss-recommendation="dismissRecommendation"
          @confirm-force-strategy="confirmForceStrategy"
          @cancel-force-strategy="cancelForceStrategy"
          @clarification-select="confirmClarification"
          @clarification-cancel="cancelClarification"
          @revoke-card="revokeDirective"
          @tactical-action="sendTacticalAction"
          @macro-action="sendMacroAction"
          @strategy-action="sendStrategyAction"
        />
        <!-- 2026-06-12 用户：删 LaunchView（三族按钮+开始对局页）——开局统一走
             房间大厅（单人=自己+电脑）。非 playing 时显示简单等待文案。 -->
        <div
          v-else
          class="flex-1 flex items-center justify-center text-muted text-sm"
        >{{ sc2Label }}</div>
      </div>
    </div>

  </div>

  <!-- 文字聊天浮层：进房后（大厅 + 对局中）可用；入口页不挂载。
       平台级 sibling（不嵌在互斥的 EntryView/RoomLobby/主界面分支里），
       position:fixed 自悬浮，切大厅↔驾驶舱时保持挂载、消息不丢。 -->
  <ChatPanel
    v-if="showMain && amIInRoom"
    :messages="chatMessages"
    :my-pid="myPid"
    :send-chat="sendChat"
    :request-history="requestChatHistory"
    :raised="isPlaying"
  />
</template>
