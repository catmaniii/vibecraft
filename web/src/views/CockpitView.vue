<script setup lang="ts">
// 对局驾驶舱视图（§9.5 终版布局）
// - 资源状态条占位（M3）
// - 小地图（本里程碑核心）
// - 当前剧本 / Standing Orders 占位 / Bot 决策流 / 最近指令（中部 scrollable）
// - 快捷区占位（M3）
// - 指令输入区（固定底部）
import CommandInput from '@/components/CommandInput.vue'
import StrategyCard from '@/components/StrategyCard.vue'
import DecisionFeed from '@/components/DecisionFeed.vue'
import Minimap from '@/components/Minimap.vue'
import M3Placeholder from '@/components/M3Placeholder.vue'
import type { SnapshotFrame, EventFrame, CommandFrame, MinimapFrame } from '@/types'

const props = defineProps<{
  strategy: SnapshotFrame['strategy'] | null
  recentCommands: readonly { text: string; ts: number }[]
  events: readonly EventFrame[]
  minimap: MinimapFrame | null
  canSendCommand: boolean
}>()

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

    <!-- Minimap（本里程碑核心）固定区域 -->
    <div class="px-4 py-2 shrink-0">
      <Minimap :frame="props.minimap" @view-move="(p) => emit('viewMove', p)" />
    </div>

    <!-- 中部 scrollable 区域 -->
    <div class="flex-1 flex flex-col gap-3 px-4 py-2 overflow-y-auto min-h-0">

      <!-- 当前剧本（现状） -->
      <div class="rounded-xl bg-surface-2 border border-border p-4">
        <p class="text-sm font-semibold text-muted uppercase tracking-wider mb-3">当前剧本</p>
        <div class="flex flex-col gap-2">
          <StrategyCard
            stage="opening"
            :slot="props.strategy?.opening ?? null"
            :is-active="props.strategy?.current_stage === 'opening'"
          />
          <StrategyCard
            stage="midgame"
            :slot="props.strategy?.midgame ?? null"
            :is-active="props.strategy?.current_stage === 'midgame'"
          />
          <StrategyCard
            stage="lategame"
            :slot="props.strategy?.lategame ?? null"
            :is-active="props.strategy?.current_stage === 'lategame'"
          />
        </div>
      </div>

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
