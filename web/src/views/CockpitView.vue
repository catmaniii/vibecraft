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
import StrategyCard from '@/components/StrategyCard.vue'
import Minimap from '@/components/Minimap.vue'
import MinimapTrackpad from '@/components/MinimapTrackpad.vue'
import M3Placeholder from '@/components/M3Placeholder.vue'
import RecommendationCard from '@/components/RecommendationCard.vue'
import BotDecisionCard from '@/components/BotDecisionCard.vue'
import PendingForceCard from '@/components/PendingForceCard.vue'
import CommandCardStack from '@/components/CommandCardStack.vue'
import type {
  SnapshotFrame,
  EventFrame,
  CommandFrame,
  MinimapFrame,
  CommandEchoFrame,
  RecommendationView,
  TacticsView,
  PendingForceStrategyView,
  CommandCardView,
} from '@/types'

const props = defineProps<{
  strategy: SnapshotFrame['strategy'] | null
  recentCommands: readonly { text: string; ts: number }[]
  events: readonly EventFrame[]
  minimap: MinimapFrame | null
  canSendCommand: boolean
  lastEcho: CommandEchoFrame | null
  recommendation: RecommendationView | null
  tactics: TacticsView | null
  pendingForceStrategy: PendingForceStrategyView | null
  commandCards: readonly CommandCardView[]
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

const failedEcho = computed(() => {
  const t = props.lastEcho?.interpretation ?? ''
  if (t.startsWith('[解析失败]') || t.startsWith('[模糊]')) return t
  return ''
})

const emit = defineEmits<{
  command: [frame: CommandFrame]
  viewMove: [point: [number, number]]
  confirmRecommendation: []
  dismissRecommendation: []
  confirmForceStrategy: []
  cancelForceStrategy: []
  revokeCard: [id: string]
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
  <div class="flex-1 overflow-y-auto pb-[72px]">
    <div class="flex flex-col">

      <!-- 左:小地图 + 触摸板 / 右:宏观策略(左右等高;右侧不再嵌套 StrategyCard 边框)
           资源 / 人口 / 时间 SC2 游戏内部 HUD 已有,手机端不重复 -->
      <div class="px-4 pt-2"></div>
      <div class="px-4 py-2 shrink-0 flex gap-3 items-stretch">
        <div class="w-1/2 max-w-[260px] shrink-0 flex flex-col gap-2">
          <Minimap :frame="props.minimap" @view-move="(p) => emit('viewMove', p)" />
          <div class="flex-1 min-h-[70px]">
            <MinimapTrackpad :minimap="props.minimap" @absolute="onTrackpadAbsolute" />
          </div>
        </div>
        <div class="flex-1 min-w-0 min-h-[260px] rounded-xl bg-surface-2 border border-border p-3 flex flex-col relative">
          <div class="flex items-center justify-between mb-2">
            <p class="text-xs font-semibold text-muted uppercase tracking-wider">当前宏观策略</p>
            <button
              v-if="currentSlot"
              type="button"
              data-testid="revoke-strategy-btn"
              class="shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted hover:text-danger hover:bg-danger/10 transition-colors text-xs leading-none"
              :aria-label="`取消当前${currentStage}剧本`"
              @click="revokeCurrentStrategy"
            >×</button>
          </div>
          <StrategyCard
            v-if="currentSlot"
            :stage="currentStage"
            :slot="currentSlot"
            :is-active="true"
          />
          <div v-else class="text-sm text-muted italic">（bot 未选定）</div>
        </div>
      </div>

      <!-- 推荐 / 硬转确认 — 全宽,放在 standing orders 之上 -->
      <div
        v-if="props.recommendation || props.pendingForceStrategy"
        class="px-4 pb-2 shrink-0"
      >
        <PendingForceCard
          v-if="props.pendingForceStrategy"
          :pending="props.pendingForceStrategy"
          @confirm="emit('confirmForceStrategy')"
          @cancel="emit('cancelForceStrategy')"
        />
        <RecommendationCard
          v-if="props.recommendation"
          :recommendation="props.recommendation"
          @confirm="emit('confirmRecommendation')"
          @dismiss="emit('dismissRecommendation')"
        />
      </div>

      <!-- 下方内容(可 scroll,本身已经在外层 scroll 容器内) -->
      <div class="flex flex-col gap-3 px-4 py-2">
        <!-- bot 当前决策(独立大卡片) -->
        <BotDecisionCard :tactics="props.tactics" />

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

  <!-- LLM 解析失败 / 模糊指令的错误提示：紧贴输入框上方（fixed） -->
  <div
    v-if="failedEcho"
    class="fixed bottom-[64px] left-0 right-0 z-40 px-3 pb-1 pointer-events-none"
  >
    <div class="rounded-md bg-danger/15 border border-danger/50 px-3 py-2 text-xs text-danger backdrop-blur shadow-lg pointer-events-auto">
      <p class="font-semibold">指令未生效</p>
      <p class="opacity-90 break-words">{{ failedEcho }}</p>
    </div>
  </div>

  <!-- 指令输入区:fixed 始终悬浮在屏幕最底部(z-50,极简单行) -->
  <div class="fixed bottom-0 left-0 right-0 z-50 bg-surface/95 backdrop-blur border-t border-border px-3 py-2 shadow-[0_-8px_16px_-4px_rgba(0,0,0,0.5)]">
    <CommandInput
      :can-send="props.canSendCommand"
      :last-echo="props.lastEcho"
      @send="(f) => emit('command', f)"
    />
  </div>
</template>
