// WS 帧协议类型定义，对应设计文档 §9.3

// ---- 上行帧（手机 → Bot）----

export interface StartGameFrame {
  type: 'start_game'
  config?: {
    map?: string
    opponent_race?: string
    opponent_difficulty?: string
  }
}

export interface CommandFrame {
  type: 'command'
  client_id: string
  issued_at: number
  text: string
}

// 上行：view_move（手机拖小地图 → bot 切视野）
export interface ViewMoveFrame {
  type: 'view_move'
  target_point: [number, number]   // [x, y] 世界坐标
}

// 上行：玩家在 PWA 点 [确认] / [忽略] bot 推荐的下一阶段剧本
export interface ConfirmRecommendationFrame {
  type: 'confirm_recommendation'
}
export interface DismissRecommendationFrame {
  type: 'dismiss_recommendation'
}

// 上行：玩家 voice 切剧本时机已过被拦下 → 点 [硬转] / [取消]
export interface ConfirmForceStrategyFrame {
  type: 'confirm_force_strategy'
}
export interface CancelForceStrategyFrame {
  type: 'cancel_force_strategy'
}

// 上行：玩家撤销 standing order（P1.4）
export interface RevokeDirectiveFrame {
  type: 'revoke_directive'
  directive_id: string
}

export type UpFrame =
  | StartGameFrame
  | CommandFrame
  | ViewMoveFrame
  | ConfirmRecommendationFrame
  | DismissRecommendationFrame
  | ConfirmForceStrategyFrame
  | CancelForceStrategyFrame
  | RevokeDirectiveFrame

// ---- 下行帧（Bot → 手机）----

// 三段式系统状态链（§9.3 / §9.6）
export type LinkState = 'connecting' | 'connected' | 'reconnecting' | 'disconnected'
export type Sc2State = 'idle' | 'launching' | 'in_game' | 'playing' | 'ended' | 'crashed'
export type BotState = 'idle' | 'running' | 'error'

export interface GameStatusFrame {
  type: 'game_status'
  ts: number
  link: string
  sc2: Sc2State
  bot: BotState
  detail?: string
}

export interface PingFrame {
  type: 'ping'
  ts: number
}

// P0：snapshot 帧（Bot → 手机）—— 设计文档 §9.3 MVP 子集
export interface StrategySlotView {
  id: string
  display: string
  // 来源标识:voice(玩家显式) / auto_transition(bot 自动转) / bot_internal(开局默认)
  set_by: 'voice' | 'auto_transition' | 'bot_internal' | 'abort'
  phases?: { id: string; display: string; subtitle: string }[]  // 仅 opening 有
  // phase stepper:后端 PhaseTracker 推断的当前 phase id
  current_phase_id?: string
  // phase 全完成标志(current = 最后一个 phase id):
  // PWA 据此把卡片暗化为"完成态",bot 自动转下一阶段或等待玩家显式指令
  all_phases_complete?: boolean
  // M5: midgame/lategame 字段透传
  attack_window?: { open_at: string; close_at: string }
  micro_doctrine?: string[]
}

// bot 推荐下一阶段剧本(等玩家 confirm,不自动 submit)
export interface RecommendationView {
  stage: 'opening' | 'midgame' | 'lategame'
  strategy_id: string
  display_name: string
  reason: string  // 推荐理由,UI 直接显示
  source: 'default' | 'abort' | 'llm'
}

// bot 内部宏观意图(rule-based 推断,attacking/defending/expanding/scouting/sustaining)
export interface TacticsView {
  stance: string
  label: string  // 中文 + emoji,UI 一行展示
  reason: string
}

// 待玩家确认的"硬转":voice 切剧本但时机已过被 Director 拦下
export interface PendingForceStrategyView {
  stage: 'opening' | 'midgame' | 'lategame'
  strategy_id: string
  display_name: string
  source_text: string  // 玩家原话(若有)
  reasons: string[]    // 偏差原因列表(已造 X / supply 已 N / ...)
}

// P1.3 新增：standing order 单条视图（来自 Director._standing_order_view）
export interface StandingOrderView {
  id: string
  display: string         // 中文人话，如 "Phoenix patrol natural"
  issued_at: number
  selector: Record<string, unknown>
  task_summary: string
}

export interface SnapshotFrame {
  type: 'snapshot'
  ts: number
  strategy: {
    current_stage: 'opening' | 'midgame' | 'lategame'
    opening: StrategySlotView | null
    midgame: StrategySlotView | null
    lategame: StrategySlotView | null
  }
  recent_commands: { text: string; ts: number }[]
  // P1.3 新增：L3 standing orders 列表
  standing_orders: StandingOrderView[]
  // bot 推荐(玩家未 confirm 前一直 carry,confirm 后清掉)
  recommendation?: RecommendationView
  // bot 内部意图(进攻/守家/开矿/探路/运营)
  tactics?: TacticsView
  // 时机已过被拦的硬转 directive(玩家未 confirm/cancel 前一直 carry)
  pending_force_strategy?: PendingForceStrategyView
}

// P1：event 帧（Bot → 手机）—— 设计文档 §9.4 taxonomy
export interface EventFrame {
  type: 'event'
  kind: string
  ts: number
  payload: Record<string, unknown>
}

// command_echo 帧（已有但 types.ts 漏了，顺手补）
export interface CommandEchoFrame {
  type: 'command_echo'
  user_text: string
  interpretation: string
  ts: number
}

// PixelMap base64 编码(切 playable 区域)
export interface PixelMapB64 {
  w: number   // playable width
  h: number   // playable height
  b64: string // uint8 row-major bytes,base64
}

// 下行：minimap（bot → 手机，5Hz）—— 设计文档 §9.3 MVP 子集
export interface MinimapFrame {
  type: 'minimap'
  ts: number
  map: {
    playable: [number, number, number, number]   // [x, y, w, h] 世界坐标
    size: [number, number]                       // [w, h] pathing grid 尺寸
  }
  viewport: {
    center: [number, number]   // observation_raw.player.camera.{x,y}
    size: [number, number]     // 固定估算 [24, 18]，spike S3
  }
  units_own: [number, number, string][]          // [x, y, kind]
  units_enemy_visible: [number, number, string][]
  // 战争迷雾(每帧):0=Hidden / 1=Fogged / 2=Visible
  vision: PixelMapB64
  // 地形高度(开局第一帧):0-255 高度。后续帧 omit,前端缓存
  terrain?: PixelMapB64
  // 被攻击位置:本帧 (health+shield) 降低的 own 单位/建筑位置
  under_attack?: [number, number][]
  // SC2 全局 alerts:"BuildingUnderAttack" / "UnitUnderAttack" / "NuclearLaunchDetected" 等
  alerts?: string[]
}

export type DownFrame = GameStatusFrame | PingFrame | SnapshotFrame | EventFrame | CommandEchoFrame | MinimapFrame

// ---- 本地连接状态 ----

export interface SystemStatus {
  link: LinkState
  sc2: Sc2State
  bot: BotState
  detail: string
}

export const DEFAULT_STATUS: SystemStatus = {
  link: 'connecting',
  sc2: 'idle',
  bot: 'idle',
  detail: '',
}
