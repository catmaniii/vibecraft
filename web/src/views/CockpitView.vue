<script setup lang="ts">
// 对局驾驶舱视图(§9.5):资源条占位 / Minimap + 当前宏观脚本 / 决策流 / 输入
import { computed } from 'vue'
import CommandInput from '@/components/CommandInput.vue'
import StrategyCard from '@/components/StrategyCard.vue'
import DecisionFeed from '@/components/DecisionFeed.vue'
import Minimap from '@/components/Minimap.vue'
import M3Placeholder from '@/components/M3Placeholder.vue'
import type { SnapshotFrame, EventFrame, CommandFrame, MinimapFrame, CommandEchoFrame } from '@/types'

const props = defineProps<{
  strategy: SnapshotFrame['strategy'] | null
  recentCommands: readonly { text: string; ts: number }[]
  events: readonly EventFrame[]
  minimap: MinimapFrame | null
  canSendCommand: boolean
  lastEcho: CommandEchoFrame | null
}>()

// 当前阶段 + 对应 slot
const currentStage = computed<'opening' | 'midgame' | 'lategame'>(
  () => props.strategy?.current_stage ?? 'opening'
)
const currentSlot = computed(() => {
  if (!props.strategy) return null
  return props.strategy[currentStage.value]
})

// 切换失败的 echo 文案(LLM 解析失败 / directive 被拒等都会以 [开头标记)
const failedEcho = computed(() => {
  const t = props.lastEcho?.interpretation ?? ''
  if (t.startsWith('[解析失败]') || t.startsWith('[模糊]')) return t
  return ''
})

const emit = defineEmits<{
  command: [frame: CommandFrame]
  viewMove: [point: [number, number]]
}>()

// 游戏内秒 → M:SS 格式
function fmtTs(ts: number): string {
  const m = Math.floor(ts / 60)
  const s = Math.floor(ts % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}
</script>

<template>
  <div class="flex-1 flex flex-col overflow-hidden">

    <!-- 资源状态条占位（M3）固定顶部 -->
    <div class="px-4 pt-3 pb-1 shrink-0">
      <M3Placeholder label="资源 / 人口 / 时间" hint="M3：资源条 + 时间 + 人口" min-height="48px" />
    </div>

    <!-- Minimap + 右侧当前宏观脚本(左右等高对齐) -->
    <div class="px-4 py-2 shrink-0 flex gap-3 items-stretch">
      <!-- 左:小地图,宽度 = 网页一半 max 260 -->
      <div class="w-1/2 max-w-[260px] shrink-0">
        <Minimap :frame="props.minimap" @view-move="(p) => emit('viewMove', p)" />
      </div>
      <!-- 右:当前宏观脚本(只显示 current_stage 对应的一张) -->
      <div class="flex-1 min-w-0 rounded-xl bg-surface-2 border border-border p-3 flex flex-col">
        <p class="text-xs font-semibold text-muted uppercase tracking-wider mb-2">当前宏观脚本</p>
        <StrategyCard
          v-if="currentSlot"
          :stage="currentStage"
          :slot="currentSlot"
          :is-active="true"
        />
        <div v-else class="text-sm text-muted italic">（bot 未选定）</div>

        <!-- 切换失败提示(LLM 解析失败 / directive 被拒) -->
        <div
          v-if="failedEcho"
          class="mt-2 rounded-md bg-danger/10 border border-danger/40 px-2 py-1.5 text-xs text-danger"
        >
          <p class="font-semibold">切换未生效</p>
          <p class="opacity-90 break-words">{{ failedEcho }}</p>
        </div>
      </div>
    </div>

    <!-- 中部 scrollable 区域 -->
    <div class="flex-1 flex flex-col gap-3 px-4 py-2 overflow-y-auto min-h-0">

      <!-- Standing Orders 占位（M3） -->
      <M3Placeholder
        label="Standing Orders"
        hint="持久指令 + 全撤销"
        min-height="64px"
      />

      <!-- Bot 决策流（现状） -->
      <div class="rounded-xl bg-surface-2 border border-border p-4">
        <p class="text-sm font-semibold text-muted uppercase tracking-wider mb-3">Bot 决策流</p>
        <DecisionFeed :events="props.events" />
      </div>

      <!-- 最近指令（现状） -->
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

    <!-- 快捷区占位（M3）-->
    <div class="px-4 pt-1 pb-2 shrink-0">
      <M3Placeholder label="快捷区" hint="保存的话语 / recipe" min-height="40px" />
    </div>

    <!-- 指令输入区（固定底部） -->
    <div class="px-4 pb-4 pt-2 border-t border-border bg-surface shrink-0">
      <div class="rounded-xl bg-surface-2 border border-border p-4">
        <p class="text-sm font-semibold text-muted uppercase tracking-wider mb-3">发号施令</p>
        <CommandInput :can-send="props.canSendCommand" @send="(f) => emit('command', f)" />
      </div>
    </div>

  </div>
</template>
