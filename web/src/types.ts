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

export type UpFrame = StartGameFrame | CommandFrame

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

export type DownFrame = GameStatusFrame | PingFrame | SnapshotFrame | EventFrame | CommandEchoFrame

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
