// WS 连接 composable：
// - 从 URL query 取 room token
// - 连接 ws(s)://host/ws?room=<token>
// - 断线指数退避重连（1→2→4→8→8s，§9.6）
// - 暴露 status / send / 关闭能力

import { ref, readonly, onUnmounted } from 'vue'
import type {
  SystemStatus,
  UpFrame,
  GameStatusFrame,
  SnapshotFrame,
  EventFrame,
  CommandEchoFrame,
  MinimapFrame,
  ViewMoveFrame,
  RecommendationView,
  TacticsView,
  PendingForceStrategyView,
  StandingOrderView,
  ProductionOverrideView,
  TacticalObjectiveView,
  CommandCardView,
  RevokeDirectiveFrame,
  TacticalActionFrame,
  StrategyActionFrame,
} from '@/types'
import { DEFAULT_STATUS } from '@/types'

// 退避序列（秒）：1 2 4 8 8 8...
const BACKOFF_SEQ = [1000, 2000, 4000, 8000]

function getBackoffMs(attempt: number): number {
  return BACKOFF_SEQ[Math.min(attempt, BACKOFF_SEQ.length - 1)]
}

// 从当前页面 URL 推断 WS 地址
function buildWsUrl(token: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws?room=${encodeURIComponent(token)}`
}

// 从 URL 取 room token（可为空串——调用方决定如何处理）
export function getRoomToken(): string {
  return new URLSearchParams(location.search).get('room') ?? ''
}

export function useWs() {
  // 系统状态链（响应式）
  const status = ref<SystemStatus>({ ...DEFAULT_STATUS })

  // 我方种族(game_status 推):用于过滤剧本列表显示
  const myRace = ref<'Protoss' | 'Zerg' | 'Terran'>('Protoss')

  // P0：snapshot strategy + recent_commands（响应式）
  const snapshotStrategy = ref<SnapshotFrame['strategy'] | null>(null)
  const recentCommands = ref<{ text: string; ts: number }[]>([])

  // bot 推荐(snapshot 透传,玩家未 confirm 前一直 carry)
  const recommendation = ref<RecommendationView | null>(null)
  // bot 内部意图(进攻/守家/...)
  const tactics = ref<TacticsView | null>(null)
  // 时机过被拦的硬转 directive(玩家未 confirm/cancel 前一直 carry)
  const pendingForceStrategy = ref<PendingForceStrategyView | null>(null)

  // P1：event ring buffer（最近 30 条，响应式）
  const events = ref<EventFrame[]>([])
  // 两层架构（2026-05-19 P3 Step 11）：最新一次 strategy.auto_switch 事件
  // CockpitView 监听 ts 变化触发 toast 显示
  const lastAutoSwitch = ref<EventFrame | null>(null)

  // P1.5：L3 standing orders（snapshot 透传）
  const standingOrders = ref<StandingOrderView[]>([])

  // P2：production overrides（snapshot 透传）
  const productionOverrides = ref<ProductionOverrideView[]>([])

  // P3.5：active tactical objectives（snapshot 透传）
  const activeTactics = ref<TacticalObjectiveView[]>([])

  // P0f Task 16：统一命令卡片列表（snapshot 透传）
  const commandCards = ref<CommandCardView[]>([])

  // command_echo（最新一条）
  const lastEcho = ref<CommandEchoFrame | null>(null)

  // minimap（最新一帧，5Hz 高频流）
  const minimap = ref<MinimapFrame | null>(null)

  // 内部 WS 实例 + 重连计数
  let ws: WebSocket | null = null
  let retryCount = 0
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let closed = false   // 手动关闭标志，不触发重连

  const token = getRoomToken()
  const wsUrl = buildWsUrl(token)

  function connect() {
    if (closed) return

    status.value = {
      ...status.value,
      link: retryCount === 0 ? 'connecting' : 'reconnecting',
    }

    ws = new WebSocket(wsUrl)

    ws.onopen = () => {
      retryCount = 0
      status.value = { ...status.value, link: 'connected' }
    }

    ws.onmessage = (evt: MessageEvent) => {
      try {
        const frame = JSON.parse(evt.data as string) as { type: string }
        switch (frame.type) {
          case 'game_status': {
            const f = frame as GameStatusFrame
            status.value = {
              link: status.value.link,   // link 由 WS 事件自己管理
              sc2: f.sc2,
              bot: f.bot,
              detail: f.detail ?? '',
            }
            // M6 / 改: my_race 字段用于过滤剧本 chip 列表
            if (f.my_race) {
              myRace.value = f.my_race
            }
            break
          }
          case 'snapshot': {
            // P0：更新剧本状态 + 最近指令 + 推荐 + 内部意图 + 待硬转
            const f = frame as SnapshotFrame
            snapshotStrategy.value = f.strategy
            recentCommands.value = f.recent_commands
            recommendation.value = f.recommendation ?? null
            tactics.value = f.tactics ?? null
            pendingForceStrategy.value = f.pending_force_strategy ?? null
            standingOrders.value = f.standing_orders ?? []
            productionOverrides.value = f.production_overrides ?? []
            activeTactics.value = f.active_tactics ?? []
            commandCards.value = f.command_cards ?? []
            break
          }
          case 'event': {
            // P1：push 进 ring buffer（最多 30 条）
            const f = frame as EventFrame
            events.value = [f, ...events.value].slice(0, 30)
            // 两层架构 P3 Step 11：strategy.auto_switch 事件单独提出来给 toast 用
            if (f.kind === 'strategy.auto_switch') {
              lastAutoSwitch.value = f
            }
            break
          }
          case 'command_echo': {
            // 更新最近 echo
            lastEcho.value = frame as CommandEchoFrame
            break
          }
          case 'minimap': {
            // minimap 高频流：整帧替换 ref，触发 Minimap.vue 的 watch redraw
            minimap.value = frame as MinimapFrame
            break
          }
          case 'ping':
            // 静默忽略（保活用）
            break
          default:
            // 未知帧类型，静默忽略
            break
        }
      } catch {
        console.warn('[vibecraft] WS 帧解析失败', evt.data)
      }
    }

    ws.onclose = () => {
      ws = null
      if (closed) {
        status.value = { ...status.value, link: 'disconnected' }
        return
      }
      // 断线重连
      status.value = { ...status.value, link: 'reconnecting' }
      const delay = getBackoffMs(retryCount)
      retryCount++
      retryTimer = setTimeout(connect, delay)
    }

    ws.onerror = () => {
      // onerror 之后 onclose 一定会触发，重连逻辑交给 onclose
      console.warn('[vibecraft] WebSocket error，等待 onclose 触发重连')
    }
  }

  // 便捷上行函数：发 view_move 帧（不改 send 签名）
  function sendViewMove(point: [number, number]) {
    const frame: ViewMoveFrame = { type: 'view_move', target_point: point }
    send(frame)
  }

  // 玩家点 [确认] / [忽略] bot 推荐
  function confirmRecommendation() {
    send({ type: 'confirm_recommendation' })
  }
  function dismissRecommendation() {
    send({ type: 'dismiss_recommendation' })
  }

  // 玩家点 [硬转] / [取消硬转](voice 切剧本时机已过被拦下)
  function confirmForceStrategy() {
    send({ type: 'confirm_force_strategy' })
  }
  function cancelForceStrategy() {
    send({ type: 'cancel_force_strategy' })
  }

  // 玩家撤销一条 standing order（P1.5）
  function revokeDirective(directiveId: string) {
    const frame: RevokeDirectiveFrame = { type: 'revoke_directive', directive_id: directiveId }
    send(frame)
  }

  // UI 战术按钮直接下战术指令（绕过 LLM）
  function sendTacticalAction(verb: string) {
    send({ type: 'tactical_action', verb } as TacticalActionFrame)
  }

  // UI 剧本 chip 直接切剧本（绕过 LLM / voice）
  function sendStrategyAction(strategyId: string) {
    send({ type: 'strategy_action', strategy_id: strategyId } as StrategyActionFrame)
  }

  function send(frame: UpFrame) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(frame))
    } else {
      console.warn('[vibecraft] WS 未连接，帧丢弃', frame.type)
    }
  }

  function close() {
    closed = true
    if (retryTimer !== null) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
    ws?.close()
    ws = null
  }

  // 组件卸载时自动关
  onUnmounted(close)

  // 启动连接（token 为空时暂不连，UI 会提示）
  if (token) {
    connect()
  } else {
    status.value = { ...status.value, link: 'disconnected' }
  }

  return {
    status: readonly(status),
    myRace: readonly(myRace),
    snapshotStrategy: readonly(snapshotStrategy),
    recentCommands: readonly(recentCommands),
    events: readonly(events),
    lastAutoSwitch: readonly(lastAutoSwitch),
    lastEcho: readonly(lastEcho),
    minimap: readonly(minimap),
    recommendation: readonly(recommendation),
    tactics: readonly(tactics),
    pendingForceStrategy: readonly(pendingForceStrategy),
    standingOrders: readonly(standingOrders),
    productionOverrides: readonly(productionOverrides),
    activeTactics: readonly(activeTactics),
    commandCards: readonly(commandCards),
    send,
    sendViewMove,
    confirmRecommendation,
    dismissRecommendation,
    confirmForceStrategy,
    cancelForceStrategy,
    revokeDirective,
    sendTacticalAction,
    sendStrategyAction,
    close,
    token,
  }
}
