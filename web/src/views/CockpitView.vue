<script setup lang="ts">
// 对局驾驶舱视图（P0-9 + P1-9）
// - 三档剧本卡片（P0）
// - Bot 决策流（P1）
// - 最近指令
// - 指令输入区
import CommandInput from '@/components/CommandInput.vue'
import StrategyCard from '@/components/StrategyCard.vue'
import DecisionFeed from '@/components/DecisionFeed.vue'
import type { SnapshotFrame, EventFrame, CommandFrame } from '@/types'

const props = defineProps<{
  strategy: SnapshotFrame['strategy'] | null
  recentCommands: readonly { text: string; ts: number }[]
  events: readonly EventFrame[]
  canSendCommand: boolean
}>()

const emit = defineEmits<{
  command: [frame: CommandFrame]
}>()

// 游戏内秒 → M:SS 格式
function fmtTs(ts: number): string {
  const m = Math.floor(ts / 60)
  const s = Math.floor(ts % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}
</script>

<template>
  <div class="flex-1 flex flex-col gap-4 px-4 py-4 overflow-y-auto">

    <!-- 当前剧本区（P0） -->
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

    <!-- Bot 决策流区（P1） -->
    <div class="rounded-xl bg-surface-2 border border-border p-4">
      <p class="text-sm font-semibold text-muted uppercase tracking-wider mb-3">Bot 决策流</p>
      <DecisionFeed :events="props.events" />
    </div>

    <!-- 最近指令区（P0） -->
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

    <!-- 指令输入区 -->
    <div class="rounded-xl bg-surface-2 border border-border p-4">
      <p class="text-sm font-semibold text-muted uppercase tracking-wider mb-3">
        发号施令
      </p>
      <CommandInput :can-send="props.canSendCommand" @send="(f) => emit('command', f)" />
    </div>

  </div>
</template>
