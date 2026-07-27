// WS 帧协议类型定义，对应设计文档 §9.3

// ---- 上行帧（手机 → Bot）----

export interface StartGameFrame {
  type: 'start_game'
  config?: {
    map?: string
    my_race?: 'Protoss' | 'Terran' | 'Zerg'
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
  // 2026-05-25:verb=attack 时区分"强制全体进攻"(all_in,不撤退)/"试探性进攻"(probe,劣势撤退)
  mode?: 'all_in' | 'probe'
}

// 上行：UI 剧本 chip 直接切剧本（绕过 LLM / voice）
export interface StrategyActionFrame {
  type: 'strategy_action'
  strategy_id: string
}

// 上行：WP-D 运营策略控件（绕过 LLM）
// dim=="expand"         value: "one_more"
// dim=="workers"        value: "stop"|"max"|"default"
// dim=="mining"         value: "mineral"|"gas"|"default"
// dim=="upgrade_target" value: { family: <15族攻防升级线之一>, level: 0|1|2|3|"auto" }
export interface MacroActionFrame {
  type: 'macro_action'
  dim: 'expand' | 'workers' | 'mining' | 'upgrade_target'
  value: number | string | { family: string; level: number | 'auto' }
}

// 上行：2026-05-24 用户:结束本局游戏
export interface EndGameFrame {
  type: 'end_game'
}

// 2026-05-24 LLM clarification 选项
export interface ConfirmClarificationFrame {
  type: 'confirm_clarification'
  option_index: number
}

export interface CancelClarificationFrame {
  type: 'cancel_clarification'
}

// 上行：语音输入音频帧（ASR，Task 4）
// pcm = base64 编码的 16kHz mono PCM16 一帧（~100ms，约 3200 字节原始）
export interface AudioChunkFrame {
  type: 'audio_chunk'
  seq: number    // 帧序号，递增；后端容忍不连续（流式容错）
  pcm: string    // base64 字符串
}
export interface AudioEndFrame {
  type: 'audio_end'  // 松手正常结束 → server 出 final transcript
}
export interface AudioCancelFrame {
  type: 'audio_cancel'  // 上滑取消 → server 丢弃该段，不出 final
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
  | MacroActionFrame
  | EndGameFrame
  | ConfirmClarificationFrame
  | CancelClarificationFrame
  | AudioChunkFrame
  | AudioEndFrame
  | AudioCancelFrame

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
  // PWA 用它过滤剧本列表(只显示当前种族的策略)
  my_race?: 'Protoss' | 'Zerg' | 'Terran'
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

// 2026-05-28 诊断:per-snapshot vibecraft override 状态(给玩家实时看是不是真生效了)
export interface TacticalDebugView {
  intent: string | null
  stance: string | null
  mode: string | null
  target_set: boolean
  plan_status: string | null
  attack_retreat_started: number | null
}

// 待玩家确认的"硬转":voice 切剧本但时机已过被 Director 拦下
export interface PendingForceStrategyView {
  stage: 'opening' | 'midgame' | 'lategame'
  strategy_id: string
  display_name: string
  source_text: string  // 玩家原话(若有)
  reasons: string[]    // 偏差原因列表(已造 X / supply 已 N / ...)
}

// 2026-05-24 LLM clarification: 不确定时给玩家 2-4 个具体选项
export interface ClarificationOptionView {
  index: number
  label: string
  interpretation_zh: string
  directive_count: number
}

export interface PendingClarificationView {
  question: string
  source_text: string  // 玩家原话
  options: ClarificationOptionView[]
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
  // 2026-05-25 verb=attack 时的子模式("all_in"=强制不撤,"probe"=试探劣势撤,null=plan 默认)
  attack_mode: 'all_in' | 'probe' | null
  // 来源标识（voice=玩家 / bot_internal=BOT / auto_transition=自动 / abort=中止回退）
  issued_by?: 'voice' | 'bot_internal' | 'auto_transition' | 'abort'
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

// #4 历史三层展开:单条历史里一个 directive 的状态视图
export type HistoryDirectiveStatus =
  | 'active' // 进行中（带 progress）
  | 'waiting' // 等待初始条件激活中
  | 'pending' // 等待生效
  | 'completed' // 已完成
  | 'cancelled' // 已手动取消
  | 'terminated' // 已终止（单位死光）
  | 'ended' // 已结束（清理掉了，无终态记录）

export interface HistoryDirectiveView {
  id: string
  display: string
  status: HistoryDirectiveStatus
  progress?: { current: number; target: number; unit: string } | null
}

// 整条历史指令的聚合状态（后端算，前端按它给条目上色）
export type HistoryCommandStatus =
  | 'failed' // 识别失败（ParseError）
  | 'active' // 执行中
  | 'pending' // 等待生效 / 等待激活
  | 'completed' // 已完成
  | 'terminated' // 已终止（单位死光）
  | 'cancelled' // 已手动取消

// #4 历史三层:输入文本 → 中文解读 → 这些 directive 的状态
export interface RecentCommandView {
  text: string
  ts: number
  interpretation_zh: string
  directives: HistoryDirectiveView[]
  status: HistoryCommandStatus
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
  /** 前置条件列表（payload.activate_when 展开；无 activate_when 时 [] / 缺省） */
  prerequisites?: ConditionView[]
  /** 偷矿指令（type="stealth_mine"）附带：实时采矿/采气农民数 */
  stealth_workers?: { mineral: number; gas: number }
}

// 科技进度单条（snapshot.tech_progress[] 元素）
// kind='leveled'：分级升级（+1/+2/+3 攻防护），后端按 track 合并
// kind='single' ：非分级单一升级（冲锋/闪现/兴奋剂等），兼容旧协议（kind 缺省视为 single）
export interface TechProgressItemLeveled {
  kind: 'leveled'
  track_en: string            // 去级 track key，如 'PROTOSSGROUNDWEAPONS'，用作 v-for key
  name_zh: string             // track 中文，如 '+攻'（已去掉级数）
  level: number               // 已完成的最高级 0-3
  status: 'done' | 'researching'
  progress: number            // 0-100，researching 时有意义
  researching_level: number | null  // 正在研究的级，done 时 null
  icon_en: string             // 要显示哪一级的图标 key（researching=下一级图标，否则=当前级）
  chrono: boolean             // 是否星空加速中
  target: number | null       // 玩家设定的攻防升级目标等级：null=自动，0-3=手动封顶
}

export interface TechProgressItemSingle {
  kind?: 'single'             // 缺省兼容旧协议
  upgrade_id: number
  name_en: string
  name_zh: string
  status: 'done' | 'researching'
  progress: number            // 0-100
  icon_en?: string            // = name_en，可选（缺省用 name_en）
  chrono?: boolean            // 是否星空加速中
}

// 关键科技建筑：显示已建成数(count) + 建造中数(pending)，像产能建筑一样
// （2026-06-08 用户：科技建筑也要看到"有几个、几个在建造中"）
export interface TechProgressItemBuilding {
  kind: 'building'
  name_en: string
  name_zh: string             // 中文 hotkey，如 'BY'（控制核心）
  status: 'done' | 'building'  // 有 ready 的 → done / 否则全在建 → building
  progress: number            // 0-100，最接近完工那个的进度（底部黄条）
  icon_en: string             // = name_en，BUILDING_ICONS 的 key
  count: number               // 已建成（ready）数 —— 蓝色右上角标
  pending: number             // 建造中（not_ready）数 —— 黄色右下角标
}

export type TechProgressItem =
  | TechProgressItemLeveled
  | TechProgressItemSingle
  | TechProgressItemBuilding

// 语音编队单组视图（snapshot.voice_groups[] 元素，Task G）
export interface VoiceGroupView {
  group_id: number
  units: Record<string, number>  // 兵种英文名 → 数量
  count: number
}

// WP-A 控制边界：单条受控组视图（来自 Director._standing_order_tags / _voice_groups）
export interface ControlGroupView {
  source: 'command' | 'group'
  directive_id: string
  group_id: number | null
  label: string
  color: string            // 'cyan' | 'g1'..'g5'
  count: number
  composition: Record<string, number>
}

// WP-A 控制边界：全量受控单位视图（snapshot.controlled_units 元素）
export interface ControlledUnitsView {
  controlled: ControlGroupView[]
  bot_free: { count: number; composition: Record<string, number> }
}

// 兵种数量单条（snapshot.army_units[] 元素）
export interface UnitCountItem {
  name_en: string    // UnitTypeId.name 大写，如 'ZEALOT'
  name_zh: string    // 中文，如 '叉子'
  count: number      // 已有数量（ready）
  pending: number    // 建造中+折跃中数量（already_pending）
}

// WP6 偷矿 cell 单条视图（snapshot.stealth_cells[] 元素）
export interface StealthCellView {
  cell_id: number
  location: [number, number]  // [x, y] 世界坐标（tile）
  worker_count: number
  mineral_workers?: number    // 采矿农民数（= worker_count - gas_workers）
  gas_workers?: number        // 采气农民数
  state: 'pending' | 'building' | 'mining' | 'released' | 'destroyed'
  has_gas: boolean
}

// 产能建筑单条（snapshot.production_buildings[] 元素）
export interface ProductionBuildingItem {
  building_id: number
  name_en: string
  name_zh: string
  count: number          // 已造好数（xN 文本）
  pending: number        // 建造中数（红角标）
  in_production: number  // 在产单位数（仅 tooltip）
  queue: Array<{ unit: string; progress: number }>
  // 挂件明细（人族产能楼）：ready 建筑里各挂件数。none=没挂件 / techlab=科技 / reactor=双倍。
  addons?: { none: number; techlab: number; reactor: number }
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
  recent_commands: RecentCommandView[]
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
  // 2026-05-28 诊断 overlay(玩家可关):vibecraft override 实时状态 + PlanZoneAttack.status
  tactical_debug?: TacticalDebugView
  // 时机已过被拦的硬转 directive(玩家未 confirm/cancel 前一直 carry)
  pending_force_strategy?: PendingForceStrategyView
  pending_clarification?: PendingClarificationView
  // P0f Task 14：4 层 directive 统一命令卡片列表（Task 15 CommandCardStack 消费）
  command_cards: CommandCardView[]
  // 科技进度（已研究 + 研究中）
  tech_progress?: TechProgressItem[]
  // 产能建筑（已有建筑 + 在产进度）
  production_buildings?: ProductionBuildingItem[]
  // 语音编队（Task G）：已编队的组
  voice_groups?: VoiceGroupView[]
  // 编队上限（可配置，默认 5）：编队条据此渲染槽位数量
  max_voice_groups?: number
  // 编队色（WP-A）：每队 RGB，手机编队条边框色 = 游戏内圆环色。键为队号字符串。
  group_colors?: Record<string, [number, number, number]>
  // 兵种数量行（第三行）：已有+建造中
  army_units?: UnitCountItem[]
  // WP-A 控制边界：受控组 + bot 自由桶
  controlled_units?: ControlledUnitsView
  // WP-E bot 关键动作自评（transient，TTL 8s 后后端发 null）
  bot_self_eval?: { text: string; kind: string; ts: number } | null
  // WP-D 运营策略层
  worker_mode?: string | null
  mining_priority?: string | null
  // WP6 偷矿 cell 列表
  stealth_cells?: StealthCellView[]
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

// command_received 帧：server 收到文字/语音指令的即时 ack（"识别中"反馈，非阻塞队列用它
// 给命令气泡开卡；ts 就是客户端发送时的 issued_at 原样回显，见 ws.py:880-896）
export interface CommandReceivedFrame {
  type: 'command_received'
  text: string
  ts: number
}

// 下行：ASR 转录结果（server → 手机，Task 4）
// is_final=false → 草稿（partial，实时刷显示）
// is_final=true  → 定稿（2pass final），PWA 据此 emit command 帧进现有管线
export interface TranscriptFrame {
  type: 'transcript'
  text: string
  is_final: boolean
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
  // 中立资源点:[x, y, kind] kind='M'(水晶矿) / 'G'(气矿)。在迷雾前画(未探索被压暗)
  resources?: [number, number, string][]
  // 战争迷雾(每帧):0=Hidden / 1=Fogged / 2=Visible
  vision: PixelMapB64
  // 地形高度(开局第一帧):0-255 高度。后续帧 omit,前端缓存
  terrain?: PixelMapB64
  // 被攻击位置:本帧 (health+shield) 降低的 own 单位/建筑位置
  under_attack?: [number, number][]
  // SC2 全局 alerts:"BuildingUnderAttack" / "UnitUnderAttack" / "NuclearLaunchDetected" 等
  alerts?: string[]
}

export type DownFrame = GameStatusFrame | PingFrame | SnapshotFrame | EventFrame | CommandEchoFrame | CommandReceivedFrame | MinimapFrame | TranscriptFrame | RoomStateFrame | RoomErrorFrame

// ---- 多人联网：下行 room_state / room_error 帧 + 上行 lobby 帧（Task 9）----

export interface RoomSlot {
  index: number
  kind: 'open' | 'bot' | 'computer' | 'closed'
  team: number
  race: string
  difficulty: string
  player_id: string
  name: string
  ready: boolean
}

/**
 * room_state 下行帧：对应 room.py Room.to_frame() 的形状。
 * 权威 schema 见 src/vibecraft/server/room.py
 */
export interface RoomStateFrame {
  type: 'room_state'
  state: 'lobby' | 'starting' | 'in_game'
  map: string
  host_player_id: string
  match_id: string
  realtime: boolean
  slots: RoomSlot[]
}

/** room_error 下行帧：房间操作被拒时 server 发送（WS 层转 toast 展示）。 */
export interface RoomErrorFrame {
  type: 'room_error'
  message: string
}

// ---- 文字聊天（全局，server 经 registry.broadcast 推所有连接）----

/** 一条聊天消息。id=server 自增（客户端去重/排序）；pid=发送者 player_id（标本人/防伪）；
 * ts=server epoch 秒。名字 name 来自握手 ?player=（可伪造，不做身份认证）。 */
export interface ChatMsg {
  type?: 'chat_msg'
  id: number
  name: string
  pid: string
  text: string
  ts: number
}

/** chat_history 下行帧：客户端发 chat_history_req 后 server 回最近 N 条。 */
export interface ChatHistoryFrame {
  type: 'chat_history'
  messages: ChatMsg[]
}

// 上行 lobby 帧（手机 → server，全部带 type 字段）
export interface LobbySetRaceFrame { type: 'lobby_set_race'; race: string }
export interface LobbySetTeamFrame { type: 'lobby_set_team'; team: number }
export interface LobbyReadyFrame { type: 'lobby_ready'; ready: boolean }
export interface LobbyAddComputerFrame { type: 'lobby_add_computer'; race: string; difficulty: string }
export interface LobbyRemoveSlotFrame { type: 'lobby_remove_slot'; index: number }
export interface LobbyStartFrame { type: 'lobby_start' }
export interface LobbyLeaveFrame { type: 'lobby_leave' }
/** 房主切换 realtime 模式（lobby 阶段）。 */
export interface LobbySetRealtimeFrame { type: 'lobby_set_realtime'; realtime: boolean }

export type LobbyUpFrame =
  | LobbySetRaceFrame
  | LobbySetTeamFrame
  | LobbyReadyFrame
  | LobbyAddComputerFrame
  | LobbyRemoveSlotFrame
  | LobbyStartFrame
  | LobbyLeaveFrame
  | LobbySetRealtimeFrame

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
