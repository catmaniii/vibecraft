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

export type UpFrame = StartGameFrame | CommandFrame | ViewMoveFrame

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
  phases?: { id: string; display: string; subtitle: string }[]  // 仅 opening 有
  // M5: midgame/lategame 字段透传，供剧本卡片展示进攻时机
  attack_window?: { open_at: string; close_at: string }  // midgame_stance 进攻窗口
  micro_doctrine?: string[]                               // midgame 口令 / lategame engagement_doctrine
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
