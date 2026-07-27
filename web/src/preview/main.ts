// 组件视觉预览 harness：用 mock fixture 挂载任意注册组件，供 Playwright 手机分辨率截图判读
// 中英布局。用法（vite build 后 server serve）：/preview.html?c=<name>&locale=<zh|en>
import { createApp, h, type Component } from 'vue'
import '../style.css'
import { setLocale, type Locale } from '@/i18n'

import RoomLobby from '@/components/RoomLobby.vue'
import GameBusyNotice from '@/components/GameBusyNotice.vue'
import StatusChain from '@/components/StatusChain.vue'
import TacticsButton from '@/components/TacticsButton.vue'
import CommandInput from '@/components/CommandInput.vue'
import CommandBubbleQueue from '@/components/CommandBubbleQueue.vue'
import VoiceGroupBar from '@/components/VoiceGroupBar.vue'
import TechProgressPanel from '@/components/TechProgressPanel.vue'
import ClarificationOverlay from '@/components/ClarificationOverlay.vue'
import CommandHistoryItem from '@/components/CommandHistoryItem.vue'
import MacroButton from '@/components/MacroButton.vue'

// 先取 locale（fixture 里动态文案按语言出，模拟后端按 locale 预渲染的 name_zh/display）
const _q = new URLSearchParams(location.search)
const _loc = (_q.get('locale') as Locale) || 'zh'
/** 动态内容（后端预渲染串）按当前 locale 取——模拟真实后端 Localizer 输出。 */
const L = (zh: string, en: string): string => (_loc === 'en' ? en : zh)

// ---- mock fixtures ----
const mockStatus = { phone: 'connected', server: 'connected', sc2: 'connected', bot: 'connected' }

function mkRoomState(over: Record<string, unknown> = {}) {
  return {
    type: 'room_state',
    state: 'lobby',
    map: 'DaybreakLE',
    host_player_id: 'pid_host',
    match_id: 'vibecraft-dev',
    realtime: true,
    slots: [
      { index: 0, kind: 'bot', team: 1, race: 'Protoss', difficulty: 'VeryHard', player_id: 'pid_host', name: 'Alice', ready: true },
      { index: 1, kind: 'bot', team: 2, race: 'Zerg', difficulty: 'VeryHard', player_id: 'pid_me', name: 'Bob', ready: false },
      { index: 2, kind: 'computer', team: 1, race: 'Terran', difficulty: 'Hard', player_id: '', name: '', ready: true },
      { index: 3, kind: 'open', team: 2, race: 'Protoss', difficulty: 'VeryHard', player_id: '', name: '', ready: false },
    ],
    ...over,
  }
}

// 注册表：name → { component, props }
const REGISTRY: Record<string, { comp: Component; props: Record<string, unknown> }> = {
  RoomLobby: {
    comp: RoomLobby,
    props: { roomState: mkRoomState(), myPlayerId: 'pid_me', roomError: null, status: mockStatus },
  },
  RoomLobbyStarting: {
    comp: RoomLobby,
    props: { roomState: mkRoomState({ state: 'starting' }), myPlayerId: 'pid_me', roomError: null, status: mockStatus },
  },
  GameBusyNotice: {
    comp: GameBusyNotice,
    props: { playerName: 'Alice' },
  },
  StatusChain: {
    comp: StatusChain,
    props: { status: mockStatus, stretch: true, expanded: true },
  },
  TacticsButton: { comp: TacticsButton, props: {} },
  CommandInput: { comp: CommandInput, props: {} },

  // 命令气泡队列(2026-07-08):多条并存,验证琥珀(识别中)/绿(成功)/红(失败)三态 +
  // 长文本/长解读不溢出(390px 手机宽) + 中英文案对齐。
  CommandBubbleQueue: {
    comp: CommandBubbleQueue,
    props: {
      bubbles: [
        { id: 'b1', text: L('派两个农民去对方11点修水晶然后修两个星门', 'send two probes to enemy 11 to build a pylon then two stargates'), ts: 1, status: 'pending' },
        { id: 'b2', text: L('切4bg', 'switch to 4bg'), ts: 2, status: 'done', detail: L('切到 4bg 开局', 'Switched to 4bg opening') },
        { id: 'b3', text: L('乱七八糟听不懂的话', 'some gibberish command'), ts: 3, status: 'failed', detail: L('[解析失败] 没听懂,请再说一次', '[Parse failed] Could not understand, please try again') },
        { id: 'b4', text: L('全军撤退回家', 'all units retreat home'), ts: 4, status: 'pending' },
      ],
    },
  },
  MacroButton: { comp: MacroButton, props: { miningPriority: 'mineral', workerMode: null } },

  // 语音编队条：兵种名前端按 locale 切（英文官方名）。多队 + 混编验证不溢出。
  VoiceGroupBar: {
    comp: VoiceGroupBar,
    props: {
      voiceGroups: [
        { group_id: 1, units: { VOIDRAY: 6, HIGHTEMPLAR: 2 }, count: 8 },
        { group_id: 2, units: { ZEALOT: 8, STALKER: 6, IMMORTAL: 2 }, count: 16 },
        { group_id: 3, units: { WARPPRISM: 1 }, count: 1 },
      ],
      maxVoiceGroups: 5,
      groupColors: { 1: [255, 230, 0], 2: [255, 140, 0], 3: [255, 0, 200] },
    },
  },

  // 科技/产能/兵种面板：面板标题(t)切换 + 动态 name_zh 模拟后端按 locale 预渲染。
  TechProgressPanel: {
    comp: TechProgressPanel,
    props: {
      tech: [
        { kind: 'leveled', track_en: 'PROTOSSGROUNDWEAPONS', name_zh: L('+攻', '+Atk'), level: 2, status: 'researching', progress: 60, researching_level: 3, icon_en: 'PROTOSSGROUNDWEAPONSLEVEL3', chrono: true, target: null },
        { kind: 'leveled', track_en: 'PROTOSSGROUNDARMORS', name_zh: L('+防', '+Def'), level: 1, status: 'done', progress: 100, researching_level: null, icon_en: 'PROTOSSGROUNDARMORSLEVEL1', chrono: false, target: 1 },
        { kind: 'leveled', track_en: 'PROTOSSSHIELDS', name_zh: L('+盾', '+Shield'), level: 0, status: 'researching', progress: 30, researching_level: 1, icon_en: 'PROTOSSSHIELDSLEVEL1', chrono: false, target: 3 },
        { kind: 'leveled', track_en: 'PROTOSSAIRWEAPONS', name_zh: L('+空攻', '+Air Atk'), level: 1, status: 'done', progress: 100, researching_level: null, icon_en: 'PROTOSSAIRWEAPONSLEVEL1', chrono: false, target: 0 },
        { kind: 'leveled', track_en: 'PROTOSSAIRARMORS', name_zh: L('+空防', '+Air Def'), level: 0, status: 'researching', progress: 10, researching_level: 1, icon_en: 'PROTOSSAIRARMORSLEVEL1', chrono: false, target: null },
        { kind: 'single', upgrade_id: 86, name_en: 'BLINKTECH', name_zh: L('闪烁', 'Blink'), status: 'done', progress: 100 },
        { kind: 'building', name_en: 'TWILIGHTCOUNCIL', name_zh: 'VC', status: 'done', progress: 100, icon_en: 'TWILIGHTCOUNCIL', count: 1, pending: 0 },
      ],
      production: [
        { building_id: 1, name_en: 'GATEWAY', name_zh: 'BG', count: 4, pending: 1, in_production: 3, queue: [], addons: { none: 0, techlab: 0, reactor: 0 } },
        { building_id: 2, name_en: 'STARGATE', name_zh: 'VS', count: 2, pending: 0, in_production: 2, queue: [] },
      ],
      units: [
        { name_en: 'VOIDRAY', name_zh: L('虚空', 'Void Ray'), count: 6, pending: 2 },
        { name_en: 'IMMORTAL', name_zh: L('不朽', 'Immortal'), count: 3, pending: 1 },
        { name_en: 'HIGHTEMPLAR', name_zh: L('电兵', 'High Templar'), count: 2, pending: 0 },
      ],
      controlledUnits: {
        controlled: [
          { source: 'group', directive_id: 'g1', group_id: 1, label: L('一队', 'Group 1'), color: 'g1', count: 8, composition: { VOIDRAY: 6, HIGHTEMPLAR: 2 } },
        ],
        bot_free: { count: 16, composition: { ZEALOT: 8, STALKER: 6, IMMORTAL: 2 } },
      },
    },
  },

  // 澄清弹窗：标题/取消(t) + 选项 label/interpretation 模拟后端按 locale 出（英文较长，验证换行）。
  ClarificationOverlay: {
    comp: ClarificationOverlay,
    props: {
      pending: {
        question: L('补2个兵营没说挂件,挂法?', 'Adding 2 Barracks — no add-on specified. Which add-on?'),
        source_text: L('补两个兵营', 'add two barracks'),
        options: [
          { index: 0, label: L('不挂附件', 'No add-on'), interpretation_zh: L('2个兵营全部不挂附件', '2 Barracks, no add-ons'), directive_count: 1 },
          { index: 1, label: L('推荐:1科技+1双倍', 'Recommended: 1 Tech Lab + 1 Reactor'), interpretation_zh: L('1个挂科技实验室,1个挂反应堆', '1 with Tech Lab, 1 with Reactor'), directive_count: 2 },
          { index: 2, label: L('取消', 'Cancel'), interpretation_zh: L('不执行此指令,重新说', "Don't run this; say it again"), directive_count: 0 },
        ],
      },
    },
  },

  // 历史指令条：识别/指令卡 等 chrome(t) + 解读/directive display 模拟后端预渲染。
  CommandHistoryItem: {
    comp: CommandHistoryItem,
    props: {
      cmd: {
        text: L('虚空全部进攻对方主矿', 'all void rays attack their main'),
        ts: 372,
        interpretation_zh: L('所有虚空进攻 enemy_main', 'All Void Rays attack enemy_main'),
        status: 'active',
        directives: [
          { id: 'd1', display: L('进攻 enemy_main', 'Attack enemy_main'), status: 'active', progress: { current: 2, target: 5, unit: L('个', '' ) } },
        ],
      },
    },
  },
}

const q = new URLSearchParams(location.search)
const locale = (q.get('locale') as Locale) || 'zh'
setLocale(locale)
const name = q.get('c') || 'RoomLobby'
const entry = REGISTRY[name]

const root = document.getElementById('preview')!
if (!entry) {
  root.textContent = `Unknown component "${name}". Available: ${Object.keys(REGISTRY).join(', ')}`
} else {
  createApp({ render: () => h(entry.comp, entry.props) }).mount(root)
}
