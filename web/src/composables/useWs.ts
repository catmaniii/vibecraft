// WS 连接 composable：
// - 优先从 useProfile selectedServer 取 WS 地址（多人联网入口页）
// - 回退：从 URL query 取 room token，保兼容旧扫码流程
// - 断线指数退避重连（1→2→4→8→8s，§9.6）
// - 暴露 status / send / connectNow（入口页连接按钮调用）/ 关闭能力

import { ref, readonly, computed, onUnmounted } from 'vue'
import { i18n, t } from '@/i18n'
import { useProfile } from '@/composables/useProfile'
import type { ServerEntry } from '@/composables/useProfile'
import type {
  SystemStatus,
  UpFrame,
  GameStatusFrame,
  SnapshotFrame,
  EventFrame,
  CommandEchoFrame,
  CommandReceivedFrame,
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
  MacroActionFrame,
  TechProgressItem,
  ProductionBuildingItem,
  UnitCountItem,
  VoiceGroupView,
  RecentCommandView,
  ControlledUnitsView,
  AudioChunkFrame,
  AudioEndFrame,
  AudioCancelFrame,
  TranscriptFrame,
  RoomStateFrame,
  ChatMsg,
  ChatHistoryFrame,
} from '@/types'
import { DEFAULT_STATUS } from '@/types'

// 退避序列（秒）：1 2 4 8 8 8...
const BACKOFF_SEQ = [1000, 2000, 4000, 8000]

function getBackoffMs(attempt: number): number {
  return BACKOFF_SEQ[Math.min(attempt, BACKOFF_SEQ.length - 1)]
}

// 从 profile selectedServer 或当前页面 URL 推断 WS 地址。
// 优先 selectedServer：url http(s)→ws(s) + /ws?room=<token>&player=<username>&pid=<deviceId>
// 回退：location.host + ?room= query param（旧扫码兼容路径）
function buildWsUrlFromProfile(
  srv: ServerEntry | null,
  username: string,
  deviceId: string,
  fallbackToken: string,
): string {
  if (srv) {
    const proto = srv.url.startsWith('https') ? 'wss' : 'ws'
    const base = srv.url.replace(/^https?/, proto)
    return (
      `${base}/ws` +
      `?room=${encodeURIComponent(srv.token)}` +
      `&player=${encodeURIComponent(username)}` +
      `&pid=${encodeURIComponent(deviceId)}` +
      `&locale=${i18n.locale}` // 玩家语言 → 后端 GameConfig.locale → interpretation 语言
    )
  }
  if (fallbackToken) {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    return `${proto}://${location.host}/ws?room=${encodeURIComponent(fallbackToken)}&locale=${i18n.locale}`
  }
  return ''
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
  const recentCommands = ref<RecentCommandView[]>([])

  // bot 推荐(snapshot 透传,玩家未 confirm 前一直 carry)
  const recommendation = ref<RecommendationView | null>(null)
  // bot 内部意图(进攻/守家/...)
  const tactics = ref<TacticsView | null>(null)
  // 2026-05-28 诊断 overlay:per-snapshot intent/stance/mode + PlanZoneAttack.status
  const tacticalDebug = ref<import('@/types').TacticalDebugView | null>(null)
  // 时机过被拦的硬转 directive(玩家未 confirm/cancel 前一直 carry)
  const pendingForceStrategy = ref<PendingForceStrategyView | null>(null)
  // 2026-05-24 LLM clarification (玩家从选项里选)
  const pendingClarification = ref<PendingClarificationView | null>(null)

  // P1：event ring buffer（最近 30 条，响应式）
  const events = ref<EventFrame[]>([])
  // 两层架构（2026-05-19 P3 Step 11）：最新一次 strategy.auto_switch 事件
  // CockpitView 监听 ts 变化触发 toast 显示
  const lastAutoSwitch = ref<EventFrame | null>(null)
  // WP6 需求2：最新一次偷矿基地撤离事件（stealth.cell_released）→ 弹通知 toast
  const lastStealthRelease = ref<EventFrame | null>(null)

  // P1.5：L3 standing orders（snapshot 透传）
  const standingOrders = ref<StandingOrderView[]>([])

  // P2：production overrides（snapshot 透传）
  const productionOverrides = ref<ProductionOverrideView[]>([])

  // P3.5：active tactical objectives（snapshot 透传）
  const activeTactics = ref<TacticalObjectiveView[]>([])

  // P0f Task 16：统一命令卡片列表（snapshot 透传）
  const commandCards = ref<CommandCardView[]>([])

  // 科技进度 + 产能建筑 + 兵种（snapshot 透传）
  const techProgress = ref<TechProgressItem[]>([])
  const productionBuildings = ref<ProductionBuildingItem[]>([])
  const armyUnits = ref<UnitCountItem[]>([])

  // 语音编队（snapshot 透传，Task G）
  const voiceGroups = ref<VoiceGroupView[]>([])
  // 编队上限（可配置，默认 5）
  const maxVoiceGroups = ref<number>(5)
  // 编队色（队号→RGB），手机编队条边框色 = 游戏内圆环色
  const groupColors = ref<Record<string, [number, number, number]>>({})
  // 控制归属（snapshot 透传；放进放大科技 modal 底部展示）
  const controlledUnits = ref<ControlledUnitsView | null>(null)
  // WP-E bot 关键动作自评（transient，TTL 8s 后后端发 null）
  const botSelfEval = ref<{ text: string; kind: string; ts: number } | null>(null)
  // WP-D 运营策略层
  const workerMode = ref<string | null>(null)
  const miningPriority = ref<string | null>(null)
  // WP6 偷矿 cell 列表
  const stealthCells = ref<import('@/types').StealthCellView[]>([])

  // command_echo（最新一条）
  const lastEcho = ref<CommandEchoFrame | null>(null)
  // command_received（最新一条 ack；给 CommandInput 命令气泡队列开卡用）
  const lastReceived = ref<CommandReceivedFrame | null>(null)

  // ASR 转录结果（最新一帧；is_final=false=草稿，is_final=true=定稿）
  const lastTranscript = ref<TranscriptFrame | null>(null)

  // minimap（最新一帧，5Hz 高频流）
  const minimap = ref<MinimapFrame | null>(null)

  // 多人联网 lobby（Task 9）：server 推送的房间状态；null = server 未推送过（旧版 server 兼容）
  const roomState = ref<RoomStateFrame | null>(null)

  // 文字聊天：消息列表（按 id 升序，去重，上限 200 防移动端内存涨）；myPid 标本人消息
  const chatMessages = ref<ChatMsg[]>([])
  const myPid = computed(() => profile.value.deviceId)
  const _CHAT_MAX = 200
  function _mergeChat(incoming: ChatMsg[]): void {
    const byId = new Map<number, ChatMsg>()
    for (const m of chatMessages.value) byId.set(m.id, m)
    for (const m of incoming) byId.set(m.id, m)
    chatMessages.value = [...byId.values()].sort((a, b) => a.id - b.id).slice(-_CHAT_MAX)
  }
  // room_error：房间操作被拒时 server 推送，5s 后自动清零
  const roomError = ref<string | null>(null)

  // profile composable（模块级单例，安全多次调用）
  const { profile, selectedServer: getSelectedServer } = useProfile()

  // amIInRoom：roomState.slots 中有 player_id===deviceId 的 bot 位
  // 用于 gate 链（JoinRoomView vs RoomLobby vs 主界面）+ 重连直进驾驶舱判断
  const amIInRoom = computed(
    () =>
      roomState.value !== null &&
      roomState.value.slots.some(
        s => s.kind === 'bot' && s.player_id === profile.value.deviceId,
      ),
  )

  // pendingAutoJoin：connectNow() 时置 true；连上后自动发一次 lobby_join（用户点"连接"=想进房）
  let pendingAutoJoin = false
  // autoJoinInFlight（响应式，App gate 用）：join 已发、结果未到的窗口。
  // 收到"含我的 room_state"或 room_error 时清零；5s 超时兜底清零（防 server 不响应
  // 时 gate 卡死）。App 的"不在房 → 回入口页"watch 必须排除这个窗口，否则
  // 连上后第一帧空房预览会把用户瞬间弹回入口页。
  const autoJoinInFlight = ref(false)
  let autoJoinTimer: ReturnType<typeof setTimeout> | null = null

  function _clearAutoJoin() {
    autoJoinInFlight.value = false
    if (autoJoinTimer !== null) {
      clearTimeout(autoJoinTimer)
      autoJoinTimer = null
    }
  }

  // 内部 WS 实例 + 重连计数
  let ws: WebSocket | null = null
  let retryCount = 0
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let closed = false   // 手动关闭标志，不触发重连

  // 兼容旧扫码流程：token 取 selectedServer 或 URL query
  const token = getSelectedServer()?.token ?? getRoomToken()

  /** 每次 connect() 时实时计算，确保入口页填完后立刻用新 profile。 */
  function getCurrentWsUrl(): string {
    return buildWsUrlFromProfile(
      getSelectedServer(),
      profile.value.username,
      profile.value.deviceId,
      getRoomToken(),
    )
  }

  function connect() {
    if (closed) return
    const wsUrl = getCurrentWsUrl()
    if (!wsUrl) {
      status.value = { ...status.value, link: 'disconnected' }
      return
    }

    status.value = {
      ...status.value,
      link: retryCount === 0 ? 'connecting' : 'reconnecting',
    }

    // 代际守卫：捕获本次创建的 socket 实例。
    // onopen/onmessage/onclose/onerror 开头校验 sock===ws；
    // connectNow() 先把 ws 置 null 再 close 旧 socket，旧 onclose 看到 sock!==ws 直接忽略，
    // 不再触发重连定时器，从根本上消除"两个连接互顶"导致的房间名单狂闪问题。
    const sock = new WebSocket(wsUrl)
    ws = sock

    sock.onopen = () => {
      if (sock !== ws) return  // 旧代 socket，忽略
      retryCount = 0
      status.value = { ...status.value, link: 'connected' }
      // 用户点 [连接] 时自动加入房间（只发一次；room_error 不重发）
      if (pendingAutoJoin) {
        pendingAutoJoin = false
        autoJoinInFlight.value = true
        autoJoinTimer = setTimeout(_clearAutoJoin, 5000) // 兜底：server 不响应不卡 gate
        sendLobby({ type: 'lobby_join' })
      }
    }

    sock.onmessage = (evt: MessageEvent) => {
      if (sock !== ws) return  // 旧代 socket，忽略
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
            tacticalDebug.value = f.tactical_debug ?? null
            pendingForceStrategy.value = f.pending_force_strategy ?? null
            pendingClarification.value = f.pending_clarification ?? null
            standingOrders.value = f.standing_orders ?? []
            productionOverrides.value = f.production_overrides ?? []
            activeTactics.value = f.active_tactics ?? []
            commandCards.value = f.command_cards ?? []
            techProgress.value = f.tech_progress ?? []
            productionBuildings.value = f.production_buildings ?? []
            armyUnits.value = f.army_units ?? []
            voiceGroups.value = f.voice_groups ?? []
            maxVoiceGroups.value = f.max_voice_groups ?? 5
            groupColors.value = f.group_colors ?? {}
            controlledUnits.value = f.controlled_units ?? null
            botSelfEval.value = f.bot_self_eval ?? null
            workerMode.value = f.worker_mode ?? null
            miningPriority.value = f.mining_priority ?? null
            stealthCells.value = f.stealth_cells ?? []
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
            // WP6 需求2：偷矿基地撤离事件 → 弹通知 toast
            if (f.kind === 'stealth.cell_released') {
              lastStealthRelease.value = f
            }
            break
          }
          case 'command_echo': {
            // 更新最近 echo
            lastEcho.value = frame as CommandEchoFrame
            break
          }
          case 'command_received': {
            // server 收到文字/语音指令的即时 ack("识别中") → 命令气泡队列开卡
            lastReceived.value = frame as CommandReceivedFrame
            break
          }
          case 'transcript': {
            // ASR 草稿（partial）/ 定稿（final）更新
            lastTranscript.value = frame as TranscriptFrame
            break
          }
          case 'minimap': {
            // minimap 高频流：整帧替换 ref，触发 Minimap.vue 的 watch redraw
            minimap.value = frame as MinimapFrame
            break
          }
          case 'webrtc_answer': {
            // 2026-05-24 LiveView 通过 sendWebRtcOffer 发了 offer,server 用此帧回答
            const f = frame as { sdp?: string; sdp_type?: string; error?: string }
            if (_pendingWebRtcOffer) {
              clearTimeout(_pendingWebRtcOffer.timer)
              if (f.error) {
                _pendingWebRtcOffer.reject(new Error(f.error))
              } else if (f.sdp && f.sdp_type) {
                _pendingWebRtcOffer.resolve({ sdp: f.sdp, sdp_type: f.sdp_type })
              } else {
                _pendingWebRtcOffer.reject(new Error('webrtc_answer 缺少 sdp'))
              }
              _pendingWebRtcOffer = null
            }
            break
          }
          case 'room_state': {
            // 多人联网 lobby：更新房间状态
            roomState.value = frame as RoomStateFrame
            // join 在飞窗口：收到"含我的 room_state"= join 成功，清窗口
            if (autoJoinInFlight.value && amIInRoom.value) {
              _clearAutoJoin()
            }
            break
          }
          case 'chat_msg': {
            // 单条聊天消息（自己发的也会经 server 广播回来，靠 myPid 标本人）
            _mergeChat([frame as unknown as ChatMsg])
            break
          }
          case 'chat_history': {
            // 进房/重连时一次性补历史
            _mergeChat((frame as unknown as ChatHistoryFrame).messages ?? [])
            break
          }
          case 'room_error': {
            // 房间操作被拒：展示 5s 后自动清零；清 pendingAutoJoin/在飞窗口防重发
            const f = frame as { message: string }
            roomError.value = f.message
            pendingAutoJoin = false
            _clearAutoJoin()
            setTimeout(() => { roomError.value = null }, 5000)
            break
          }
          case 'asr_unavailable': {
            // 语音识别不可用（如英文模型加载失败）：复用 roomError toast 提示玩家，
            // 别让用户对着麦克风说半天没反馈。message 已由后端按 locale 本地化。
            const f = frame as { message?: string }
            roomError.value = f.message || t('voice.asrUnavailable')
            setTimeout(() => { roomError.value = null }, 5000)
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

    sock.onclose = () => {
      if (sock !== ws) return  // 旧代 socket，忽略（connectNow 造成的 stale close）
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

    sock.onerror = () => {
      if (sock !== ws) return  // 旧代 socket，忽略
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
  // 2026-05-25:mode 区分"强制全体进攻"(all_in)/"试探性进攻"(probe)
  function sendTacticalAction(verb: string, mode?: 'all_in' | 'probe') {
    const frame: TacticalActionFrame = { type: 'tactical_action', verb: verb as TacticalActionFrame['verb'] }
    if (mode) frame.mode = mode
    send(frame)
  }

  // WP-D 运营策略双维度控件（绕过 LLM）
  function sendMacroAction(dim: MacroActionFrame['dim'], value: MacroActionFrame['value']) {
    const frame: MacroActionFrame = { type: 'macro_action', dim, value }
    send(frame)
  }

  // UI 剧本 chip 直接切剧本（绕过 LLM / voice）
  function sendStrategyAction(strategyId: string) {
    send({ type: 'strategy_action', strategy_id: strategyId } as StrategyActionFrame)
  }

  // 2026-05-24 用户:webui 顶部"结束本局"按钮
  function endGame() {
    send({ type: 'end_game' })
  }

  // 2026-05-24 LLM clarification 玩家点选某选项 / ❌ 取消
  function confirmClarification(optionIndex: number) {
    send({ type: 'confirm_clarification', option_index: optionIndex })
  }
  function cancelClarification() {
    send({ type: 'cancel_clarification' })
  }

  // ASR 音频发送 helper（Task 4）
  // seq 递增帧序号；pcm = base64 的 16kHz mono PCM16 帧数据
  function sendAudioChunk(seq: number, pcm: string) {
    const frame: AudioChunkFrame = { type: 'audio_chunk', seq, pcm }
    send(frame)
  }
  // 松手正常结束 → server 走 2pass final
  function sendAudioEnd() {
    const frame: AudioEndFrame = { type: 'audio_end' }
    send(frame)
  }
  // 上滑取消 → server 丢弃该段，不出 final
  function sendAudioCancel() {
    const frame: AudioCancelFrame = { type: 'audio_cancel' }
    send(frame)
  }

  // 2026-05-24 WebRTC signaling 走 WS frame(替代 HTTP POST /webrtc/offer)
  // 单端口反代场景(Tailscale Funnel 等)也能用。
  // 多个并发 offer 暂不支持(实际只一个 LiveView,一次只一个 offer)。
  let _pendingWebRtcOffer: {
    resolve: (val: { sdp: string; sdp_type: string }) => void
    reject: (err: Error) => void
    timer: ReturnType<typeof setTimeout>
  } | null = null

  function sendWebRtcOffer(sdp: string, sdpType: string, timeoutMs = 8000): Promise<{ sdp: string; sdp_type: string }> {
    // 上一条 pending 没回就先 cancel(防 race)
    if (_pendingWebRtcOffer) {
      clearTimeout(_pendingWebRtcOffer.timer)
      _pendingWebRtcOffer.reject(new Error('superseded by new offer'))
      _pendingWebRtcOffer = null
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (_pendingWebRtcOffer) {
          _pendingWebRtcOffer = null
          reject(new Error('WebRTC signaling timeout'))
        }
      }, timeoutMs)
      _pendingWebRtcOffer = { resolve, reject, timer }
      send({ type: 'webrtc_offer', sdp, sdp_type: sdpType } as any)
    })
  }

  function send(frame: UpFrame) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(frame))
    } else {
      console.warn('[vibecraft] WS 未连接，帧丢弃', frame.type)
    }
  }

  /** 发送 lobby 上行帧（lobby_set_race / lobby_ready / lobby_start 等）。
   * 与 send() 分离：不受 UpFrame 联合类型约束，lobby 帧走独立通道。
   */
  function sendLobby(frame: object) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(frame))
    } else {
      console.warn('[vibecraft] WS 未连接，lobby 帧丢弃', (frame as { type?: string }).type)
    }
  }

  // 文字聊天上行：发一条消息 / 请求历史。不本地 append——靠 server 广播回显
  // （避免双显 + 拿到 server 分配的 id/ts）。走 sendLobby 绕过 UpFrame 联合类型约束。
  function sendChat(text: string) {
    const t = text.trim()
    if (!t) return
    sendLobby({ type: 'chat_send', text: t })
  }
  function requestChatHistory() {
    sendLobby({ type: 'chat_history_req' })
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

  /**
   * 重置上一把对局的所有 reactive 内容（2026-06-17 用户：点连接时先闪出上一把残留界面）。
   * 只在用户主动 connectNow() 时调（中途断线 auto-retry 走 connect()，不 reset → 不闪掉对局中
   * cockpit）。status.link 保留（连接进度由 WS 事件管），只清游戏内容字段。
   */
  function resetSessionState() {
    status.value = { ...DEFAULT_STATUS, link: status.value.link }
    myRace.value = 'Protoss'
    snapshotStrategy.value = null
    recentCommands.value = []
    recommendation.value = null
    tactics.value = null
    tacticalDebug.value = null
    pendingForceStrategy.value = null
    pendingClarification.value = null
    events.value = []
    lastAutoSwitch.value = null
    lastStealthRelease.value = null
    standingOrders.value = []
    productionOverrides.value = []
    activeTactics.value = []
    commandCards.value = []
    techProgress.value = []
    productionBuildings.value = []
    armyUnits.value = []
    voiceGroups.value = []
    maxVoiceGroups.value = 5
    groupColors.value = {}
    controlledUnits.value = null
    botSelfEval.value = null
    workerMode.value = null
    miningPriority.value = null
    stealthCells.value = []
    lastEcho.value = null
    lastReceived.value = null
    lastTranscript.value = null
    minimap.value = null
    roomState.value = null
    roomError.value = null
    chatMessages.value = []
  }

  /**
   * 入口页 [连接] 按钮调用：重置关闭标志 + 立刻发起连接。
   * 返回用户（profile 已完整）也可在初始化后手动触发。
   */
  function connectNow() {
    // 先清上一把残留，避免连接窗口内主界面用旧 status/snapshot 渲染出上一把游戏（2026-06-17 用户）
    resetSessionState()
    if (retryTimer !== null) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
    // 先把 ws 置 null（代际守卫），再 close 旧 socket；
    // 旧 socket 的 onclose 触发时 sock!==ws → 直接 return，不再排重连定时器。
    const oldWs = ws
    ws = null
    oldWs?.close()
    closed = false
    retryCount = 0
    // 用户主动连接：连上后自动发一次 lobby_join（想进房）
    pendingAutoJoin = true
    connect()
  }

  // 自动连接：profile 已完整（selectedServer + username）或旧扫码 URL 有 token → 直连
  // profile 不完整 → 等入口页 [连接] 按钮调 connectNow()
  if (getCurrentWsUrl()) {
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
    lastStealthRelease: readonly(lastStealthRelease),
    lastEcho: readonly(lastEcho),
    lastReceived: readonly(lastReceived),
    lastTranscript: readonly(lastTranscript),
    minimap: readonly(minimap),
    recommendation: readonly(recommendation),
    tactics: readonly(tactics),
    tacticalDebug: readonly(tacticalDebug),
    pendingForceStrategy: readonly(pendingForceStrategy),
    pendingClarification: readonly(pendingClarification),
    standingOrders: readonly(standingOrders),
    productionOverrides: readonly(productionOverrides),
    activeTactics: readonly(activeTactics),
    commandCards: readonly(commandCards),
    techProgress: readonly(techProgress),
    productionBuildings: readonly(productionBuildings),
    armyUnits: readonly(armyUnits),
    voiceGroups: readonly(voiceGroups),
    maxVoiceGroups: readonly(maxVoiceGroups),
    groupColors: readonly(groupColors),
    controlledUnits: readonly(controlledUnits),
    botSelfEval: readonly(botSelfEval),
    workerMode: readonly(workerMode),
    miningPriority: readonly(miningPriority),
    stealthCells: readonly(stealthCells),
    send,
    sendViewMove,
    confirmRecommendation,
    dismissRecommendation,
    confirmForceStrategy,
    cancelForceStrategy,
    revokeDirective,
    sendTacticalAction,
    sendMacroAction,
    sendStrategyAction,
    endGame,
    confirmClarification,
    cancelClarification,
    sendAudioChunk,
    sendAudioEnd,
    sendAudioCancel,
    sendWebRtcOffer,
    close,
    connectNow,
    token,
    // 多人联网 lobby（Task 9）
    roomState: readonly(roomState),
    roomError: readonly(roomError),
    amIInRoom: readonly(amIInRoom),
    autoJoinInFlight: readonly(autoJoinInFlight),
    sendLobby,
    // 文字聊天
    chatMessages: readonly(chatMessages),
    myPid,
    sendChat,
    requestChatHistory,
  }
}
