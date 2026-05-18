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

// 上行：UI 战术按钮直接下战术指令（绕过 LLM）
export interface TacticalActionFrame {
  type: 'tactical_action'
  verb: 'attack' | 'defend' | 'retreat' | 'recon' | 'scout'
}

// 上行：UI 剧本 chip 直接切剧本（绕过 LLM / voice）
export interface StrategyActionFrame {
  type: 'strategy_action'
  strategy_id: string
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
  | TacticalActionFrame
  | StrategyActionFrame

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

// P2 新增：production override 单条视图（来自 Director.production_overrides）
export interface ProductionOverrideView {
  id: string
  display: string         // 中文，如 "出 2 哨兵" / "研 Blink" / "开 3 矿"
  issued_at: number
  directive_type: 'production_override' | 'tech_override' | 'expansion_override'
}

// P3.5 新增：active tactical objective 单条视图（来自 Director._in_flight TACTICAL_OBJECTIVE）
export interface TacticalObjectiveView {
  id: string
  display: string         // 中文人话，如 "进攻 enemy_natural"
  verb: string            // 11 verb 之一（attack/defend/scout/...）
  target_area: string | null
  issued_at: number
}

/**
 * P0f Task 10/14: 统一命令卡片 view（4 层 directive 透传）。
 * 后端 build_snapshot._build_command_cards 构造。
 * 前端 CommandCardStack (Task 15) 消费。
 */
/** 单条完成条件 + 当前进度（来自 task_monitor._done_when + counters） */
export interface ConditionView {
  /** 中文条件描述，e.g. "造 4 个 叉子" / "30 秒后" / "侦察到 enemy_main" */
  text: string
  /** 是否已满足（unit_count_built_since / time_elapsed_since 可算；其它 kind 后端给 false） */
  met: boolean
  /** 有进度的条件（counter / 倒计时）才有此字段 */
  progress?: {
    current: number
    target: number
    unit: string  // "个" / "秒" / ...
  }
  /**
   * L4 production_override 多兵种合并时每条进度行的"当前在等什么"状态。
   * blocked  → 缺前置（"需要 Cybernetics Core"）
   * waiting  → 资源/建筑不足（"资源不足"）
   * producing → 正在生产（"队列 N 等出"或空）
   * done     → 已造满
   * 其它 condition kind 该字段缺省。
   */
  state?: 'blocked' | 'waiting' | 'producing' | 'done'
  /** state 对应的人话描述（"需要 X" / "资源不足" / "队列 N 等出"） */
  state_reason?: string
}

export interface CommandCardView {
  /** 直接来自 directive.id (L2/L3/L4) 或 L1 的 "l1_{stage}" 占位 */
  id: string
  layer: 'L1' | 'L2' | 'L3' | 'L4'
  /** directive type enum value, e.g. "strategy_set" / "tactical_objective" / "unit_claim" / "production_override" / "structure_override" */
  type: string
  /** 中文短摘要，e.g. "midgame: iac_2base" / "attack enemy_natural" / "Probe patrol" / "Sentry ×2" / "补 8 Gateway" */
  display: string
  /** 游戏内秒（issued_at 或 set_at） */
  issued_at: number
  status: 'pending' | 'active' | 'on_hold' | 'done'
  /** 状态原因（仅 on_hold/done 有意义，e.g. "资源不足 (120/400 矿)" / "被新指令覆盖"） */
  status_reason: string
  /** MVP 全部 true。未来某些 directive 可能不可 revoke */
  revokable: boolean
  /** done_when 条件列表（L2/L3/L4 有 done_when 时附带；L1 不带，[] 表无条件） */
  conditions?: ConditionView[]
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
  // P2 新增：production overrides 列表
  production_overrides?: ProductionOverrideView[]
  // P3.5 新增：active tactical objectives（L2 in-flight）
  active_tactics: TacticalObjectiveView[]
  // bot 推荐(玩家未 confirm 前一直 carry,confirm 后清掉)
  recommendation?: RecommendationView
  // bot 内部意图(进攻/守家/开矿/探路/运营)
  tactics?: TacticsView
  // 时机已过被拦的硬转 directive(玩家未 confirm/cancel 前一直 carry)
  pending_force_strategy?: PendingForceStrategyView
  // P0f Task 14：4 层 directive 统一命令卡片列表（Task 15 CommandCardStack 消费）
  command_cards: CommandCardView[]
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
