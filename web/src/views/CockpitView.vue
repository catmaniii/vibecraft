<script setup lang="ts">
// 对局驾驶舱视图(§9.5):
// - 上:资源条占位
// - 中段:左小地图+触摸板 / 右宏观策略(左右等高)
// - bot 内部意图(tactics)一行
// - 推荐 / 硬转确认 — 全宽,在 standing orders 上方
// - standing orders + 决策流 + 最近指令(可 scroll)
// - 输入框:fixed bottom-0 始终可见
import { computed } from 'vue'
import CommandInput from '@/components/CommandInput.vue'
import type { Ref } from 'vue'
import StrategyCard from '@/components/StrategyCard.vue'
import AutoSwitchToast from '@/components/AutoSwitchToast.vue'
import Minimap from '@/components/Minimap.vue'
import MinimapTrackpad from '@/components/MinimapTrackpad.vue'
import M3Placeholder from '@/components/M3Placeholder.vue'
import RecommendationCard from '@/components/RecommendationCard.vue'
import BotDecisionCard from '@/components/BotDecisionCard.vue'
import TacticalDebugBar from '@/components/TacticalDebugBar.vue'
import PendingForceCard from '@/components/PendingForceCard.vue'
import ClarificationOverlay from '@/components/ClarificationOverlay.vue'
import CommandCardStack from '@/components/CommandCardStack.vue'
// TacticsButton 已集成进 BotDecisionCard,这里不再 import
import MacroButton from '@/components/MacroButton.vue'
import StrategyPicker from '@/components/StrategyPicker.vue'
import TechProgressPanel from '@/components/TechProgressPanel.vue'
import VoiceGroupBar from '@/components/VoiceGroupBar.vue'
import StealthReleaseToast from '@/components/StealthReleaseToast.vue'
import type {
  SnapshotFrame,
  EventFrame,
  CommandFrame,
  MinimapFrame,
  CommandEchoFrame,
  CommandReceivedFrame,
  RecommendationView,
  TacticsView,
  PendingForceStrategyView,
  CommandCardView,
  TechProgressItem,
  ProductionBuildingItem,
  UnitCountItem,
  VoiceGroupView,
  RecentCommandView,
  ControlledUnitsView,
  TranscriptFrame,
} from '@/types'

const props = defineProps<{
  strategy: SnapshotFrame['strategy'] | null
  recentCommands: readonly RecentCommandView[]
  events: readonly EventFrame[]
  minimap: MinimapFrame | null
  canSendCommand: boolean
  lastEcho: CommandEchoFrame | null
  // command_received ack（"识别中"反馈）→ CommandInput 命令气泡队列开卡
  lastReceived: CommandReceivedFrame | null
  recommendation: RecommendationView | null
  tactics: TacticsView | null
  tacticalDebug?: import('@/types').TacticalDebugView | null
  pendingForceStrategy: PendingForceStrategyView | null
  pendingClarification: import('@/types').PendingClarificationView | null
  commandCards: readonly CommandCardView[]
  activeTactics?: readonly import('@/types').TacticalObjectiveView[]
  myRace?: 'Protoss' | 'Zerg' | 'Terran'
  // 2026-05-23 用户:bot 自动切持续 doctrine 推荐 toast,显示在宏观策略框内部
  // 类型同 useWs 的 lastAutoSwitch: EventFrame | null(原 App.vue 用 as any 透传)
  lastAutoSwitch?: EventFrame | null
  // 科技进度 + 产能建筑 + 兵种（TechProgressPanel 用）
  techProgress?: readonly TechProgressItem[] | null
  productionBuildings?: readonly ProductionBuildingItem[] | null
  armyUnits?: readonly UnitCountItem[] | null
  // 语音编队（VoiceGroupBar 用，Task G）
  voiceGroups?: readonly VoiceGroupView[] | null
  // 编队上限（可配置，默认 5）
  maxVoiceGroups?: number | null
  // 编队色（队号→RGB）：编队条边框色 = 游戏内圆环色
  groupColors?: Record<string, [number, number, number]> | null
  // 控制归属（我控制 vs bot 自由）：放进放大科技 modal 底部
  controlledUnits?: ControlledUnitsView | null
  // WP-E bot 关键动作自评（transient 旁白，TTL 8s 后 null）
  botSelfEval?: { text: string; kind: string; ts: number } | null
  // WP-D 运营策略层
  miningPriority?: string | null
  workerMode?: string | null
  // WP6 需求2：偷矿基地撤离通知（stealth.cell_released 事件）→ 弹 toast
  lastStealthRelease?: EventFrame | null
  // 语音输入 props（透传 CommandInput → VoiceInput，Task 7）
  sendAudioChunk: (seq: number, pcm: string) => void
  sendAudioEnd: () => void
  sendAudioCancel: () => void
  lastTranscript: TranscriptFrame | null
}>()

const currentStage = computed<'opening' | 'midgame' | 'lategame'>(
  () => props.strategy?.current_stage ?? 'opening'
)
const currentSlot = computed(() => {
  if (!props.strategy) return null
  return props.strategy[currentStage.value]
})

// L1 剧本卡片不进统一 CommandCardStack：× 直接放在宏观策略框右上角
const tacticalCards = computed(() => props.commandCards.filter(c => c.layer !== 'L1'))

// 宏观策略框的 × 关闭：撤销当前 stage 的 L1 directive
function revokeCurrentStrategy() {
  emit('revokeCard', `l1_${currentStage.value}`)
}

// 2026-05-24 用户:删除常驻红框,失败信息走 CommandInput 自带的承载 UI
// (3s 淡出) + 历史按钮回看。

const emit = defineEmits<{
  command: [frame: CommandFrame]
  viewMove: [point: [number, number]]
  confirmRecommendation: []
  dismissRecommendation: []
  confirmForceStrategy: []
  cancelForceStrategy: []
  revokeCard: [id: string]
  tacticalAction: [verb: string, mode?: 'all_in' | 'probe']
  macroAction: [dim: string, value: number | string | { family: string; level: number | 'auto' }]
  strategyAction: [strategyId: string]
  // 2026-05-24 LLM clarification
  'clarification-select': [optionIndex: number]
  'clarification-cancel': []
}>()

// 触摸板 emit absolute 已是绝对坐标(基于按下时的基准 + dx/dy),直接转 viewMove
function onTrackpadAbsolute(x: number, y: number) {
  emit('viewMove', [x, y])
}

function fmtTs(ts: number): string {
  const m = Math.floor(ts / 60)
  const s = Math.floor(ts % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}
</script>

<template>
  <!-- 整体 cockpit:内容 scroll,输入框 fixed 在屏幕底部 -->
  <!-- 中部 padding-bottom 给 fixed 输入框留位置(输入框现在简化到 ~64px 高) -->
  <!-- #5: portrait 去掉 overflow-y-auto，让外层 body 滚动（配合 LiveView sticky）；
       landscape 保留 overflow-y-auto，维持分栏各自滚动 -->
  <div class="flex-1 min-h-0 landscape:overflow-y-auto pb-[72px]">
    <div class="flex flex-col">

      <!-- 科技 / 产能 / 兵种跟踪 panel：**竖屏**在此(右面板顶)显示；**横屏隐藏此份**——横屏改由
           App.vue 左列视频下方那份渲染(2026-07-26 用户：科技面板搬回左列视频下方)。 -->
      <div class="px-4 pt-2 landscape:hidden">
        <TechProgressPanel
          :tech="props.techProgress"
          :production="props.productionBuildings"
          :units="props.armyUnits"
          :controlled-units="props.controlledUnits ?? null"
          @macro-action="(dim, val) => emit('macroAction', dim, val)"
        />
      </div>

      <!-- 左:小地图 + 触摸板 / 右:宏观策略(左右等高;右侧不再嵌套 StrategyCard 边框)
           资源 / 人口 / 时间 SC2 游戏内部 HUD 已有,手机端不重复 -->
      <div class="px-4 py-2 shrink-0 flex gap-3 items-stretch">
        <div class="w-1/2 max-w-[260px] shrink-0 flex flex-col gap-2">
          <Minimap :frame="props.minimap" @view-move="(p) => emit('viewMove', p)" />
          <div class="flex-1 min-h-[70px]">
            <MinimapTrackpad :minimap="props.minimap" @absolute="onTrackpadAbsolute" />
          </div>
        </div>
        <!-- 2026-06-13 用户:固定高度+内部滚动;标题/切换按钮同行不换行;
             X 挪到面板右上角(absolute)给标题和切换按钮让位 -->
        <div class="flex-1 min-w-0 h-[280px] rounded-xl bg-surface-2 border border-border p-3 flex flex-col relative">
          <button
            v-if="currentSlot"
            type="button"
            data-testid="revoke-strategy-btn"
            class="absolute top-2 right-2 z-10 w-5 h-5 flex items-center justify-center rounded text-muted hover:text-danger hover:bg-danger/10 transition-colors text-xs leading-none"
            :aria-label="`取消当前${currentStage}宏观策略`"
            @click="revokeCurrentStrategy"
          >×</button>
          <div class="flex items-center justify-between mb-2 gap-2 pr-5">
            <p class="text-xs font-semibold text-muted uppercase tracking-wider whitespace-nowrap shrink-0">宏观策略</p>
            <StrategyPicker
              :race="(props.myRace?.toLowerCase() as 'protoss' | 'zerg' | 'terran') ?? 'protoss'"
              :current-strategy="props.strategy"
              @strategy-action="(id) => emit('strategyAction', id)"
            />
          </div>
          <div class="flex-1 min-h-0 overflow-y-auto">
            <StrategyCard
              v-if="currentSlot"
              :stage="currentStage"
              :slot="currentSlot"
              :is-active="true"
            />
            <div v-else class="text-sm text-muted italic">（bot 未选定）</div>
          </div>
          <!-- 2026-05-23 用户:bot 自动切持续 doctrine 推荐 toast,临时覆盖
               StrategyCard(absolute inset-0 + z-10),5s 后淡出。-->
          <AutoSwitchToast :switch-event="props.lastAutoSwitch as any" />
          <!-- 2026-05-25 用户:bot 推荐切宏观策略卡 (default/abort/llm) 也叠加到此框
               (从全宽 inline 卡迁过来),z-[15] 介于 AutoSwitchToast(z-10) 与
               PendingForceCard(z-20)之间。玩家点 确认/忽略 后清空 → 卡片消失。-->
          <RecommendationCard
            v-if="props.recommendation"
            :recommendation="props.recommendation"
            @confirm="emit('confirmRecommendation')"
            @dismiss="emit('dismissRecommendation')"
          />
          <!-- 2026-05-24 用户:剧本时机已过弹窗也应覆盖宏观策略框(不是 BOT 决策)。
               z-20 高于 AutoSwitchToast,玩家点 硬转/取消 后 _pending_force_strategy 清空 → 卡片消失。-->
          <PendingForceCard
            v-if="props.pendingForceStrategy"
            :pending="props.pendingForceStrategy"
            @confirm="emit('confirmForceStrategy')"
            @cancel="emit('cancelForceStrategy')"
          />
        </div>
      </div>

      <!-- 下方内容(可 scroll,本身已经在外层 scroll 容器内) -->
      <div class="flex flex-col gap-3 px-4 py-2">
        <!-- WP-E bot 关键动作旁白（transient，TTL 由后端控制） -->
        <div
          v-if="props.botSelfEval"
          class="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-surface-2 border border-border/50 text-xs text-muted"
        >
          <span class="shrink-0 text-[10px] font-semibold uppercase tracking-wider text-border">bot</span>
          <span>{{ props.botSelfEval.text }}</span>
        </div>

        <!-- 实时战略两栏：左=战斗策略(BotDecisionCard) / 右=运营策略(MacroButton)
             items-stretch + 两栏 h-full → 左"及时战术"窗口高度始终与右"及时运营策略"对齐 -->
        <div class="flex gap-3 items-stretch">
          <!-- 左栏:bot 当前决策(集成"切换战术"按钮 + 玩家 override + X 撤销)
               flex-[3]:战术面板占更宽(2026-06-07 用户:运营策略窄一点,战术多点空间) -->
          <div class="flex-[3] min-w-0 flex">
            <BotDecisionCard
              class="h-full w-full"
              :tactics="props.tactics"
              :active-tactics="props.activeTactics"
              @tactical-action="(v, m) => emit('tacticalAction', v, m)"
              @revoke-override="(id) => emit('revokeCard', id)"
            />
          </div>
          <!-- 右栏:WP-D 运营策略层（双维度：开矿 + 农民生产）flex-[2]:比左栏窄 -->
          <div class="flex-[2] min-w-0 flex">
            <MacroButton
              class="h-full w-full"
              :mining-priority="props.miningPriority ?? null"
              :worker-mode="props.workerMode ?? null"
              @macro-action="(dim, val) => emit('macroAction', dim, val)"
            />
          </div>
        </div>

        <!-- 2026-05-28 诊断 overlay:实时 intent/stance/mode + PlanZoneAttack.status -->
        <TacticalDebugBar :debug="props.tacticalDebug ?? null" />

        <!-- 语音编队条（Task G）：横排 1-5 队，有编队时显示;位于指令卡列表上方 -->
        <VoiceGroupBar
          :voice-groups="(props.voiceGroups as VoiceGroupView[]) ?? []"
          :max-voice-groups="props.maxVoiceGroups ?? 5"
          :group-colors="props.groupColors ?? null"
        />

        <CommandCardStack
          :cards="tacticalCards"
          @revoke="(id) => emit('revokeCard', id)"
        />

        <div class="rounded-xl bg-surface-2 border border-border p-4">
          <p class="text-sm font-semibold text-muted uppercase tracking-wider mb-2">最近指令</p>
          <div class="space-y-1">
            <div
              v-for="cmd in props.recentCommands"
              :key="cmd.ts"
              class="flex items-center gap-2 text-xs"
            >
              <span class="shrink-0 font-mono text-border">{{ fmtTs(cmd.ts) }}</span>
              <span class="text-muted">{{ cmd.text }}</span>
            </div>
            <p v-if="props.recentCommands.length === 0" class="text-xs text-muted italic">暂无指令记录</p>
          </div>
        </div>
      </div>

    </div>
  </div>

  <!-- WP6 需求2：偷矿基地撤离 toast（fixed 全局，z-60，5s 后消失） -->
  <StealthReleaseToast :release-event="(props.lastStealthRelease as any) ?? null" />

  <!-- 指令输入区:fixed 悬浮在底部(z-50,极简单行)。竖屏全宽;横屏 left-auto + w-24rem 靠右对齐，
       只压右面板(和右面板同位置同宽)，左边视频底部不被盖(2026-07-26 用户)。 -->
  <div class="fixed bottom-0 left-0 right-0 z-50 landscape:left-auto landscape:w-[24rem] bg-surface/95 backdrop-blur border-t border-border px-3 py-2 shadow-[0_-8px_16px_-4px_rgba(0,0,0,0.5)]">
    <!-- 2026-05-24: LLM clarification 弹层(输入框上方,玩家点选/取消) -->
    <ClarificationOverlay
      v-if="props.pendingClarification"
      :pending="props.pendingClarification"
      class="mb-2"
      @select="(idx) => emit('clarification-select', idx)"
      @cancel="() => emit('clarification-cancel')"
    />
    <CommandInput
      :can-send="props.canSendCommand"
      :last-echo="props.lastEcho"
      :last-received="props.lastReceived"
      :recent-commands="props.recentCommands"
      :send-audio-chunk="props.sendAudioChunk"
      :send-audio-end="props.sendAudioEnd"
      :send-audio-cancel="props.sendAudioCancel"
      :last-transcript="props.lastTranscript"
      @send="(f) => emit('command', f)"
    />
  </div>
</template>
