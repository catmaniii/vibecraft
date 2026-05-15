<script setup lang="ts">
// Bot 决策流组件（P1-8）
// 接 events: EventFrame[]（已按 [newest, ...] 倒序存储）
// 内含 kind → 中文文案映射表
import type { EventFrame } from '@/types'

defineProps<{
  events: readonly EventFrame[]
}>()

// 游戏内秒 → M:SS 格式
function fmtTs(ts: number): string {
  const m = Math.floor(ts / 60)
  const s = Math.floor(ts % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

// §9.4 taxonomy kind → 中文文案（template 函数，按 payload 丰富）
function kindToText(ev: EventFrame): string {
  const p = ev.payload
  switch (ev.kind) {
    case 'strategy.set': {
      const display = (p.display as string) || (p.strategy_id as string) || ''
      const stage = (p.stage as string) || ''
      return `切到 ${display}${stage ? ` [${stage}]` : ''}`
    }
    case 'strategy.phase_change': {
      const stage = (p.stage as string) || (p.to_stage as string) || ''
      return `进入 ${stage} 阶段`
    }
    case 'directive.committed': {
      const text = (p.user_text as string) || (p.strategy_id as string) || ''
      return text ? `指令已生效：${text}` : '指令已生效'
    }
    case 'directive.released': {
      const text = (p.user_text as string) || ''
      return text ? `standing order 结束：${text}` : 'standing order 结束'
    }
    case 'directive.rejected': {
      const reason = (p.reason as string) || ''
      return reason ? `指令被拒：${reason}` : '指令被拒绝'
    }
    case 'decision.autopilot_phase': {
      const msg = (p.message as string) || '开局 build 跑完，转入自动运营'
      return msg
    }
    default:
      return ev.kind
  }
}
</script>

<template>
  <div class="space-y-1">
    <div
      v-for="ev in events"
      :key="`${ev.kind}-${ev.ts}`"
      class="flex items-start gap-2 text-xs text-muted"
    >
      <span class="shrink-0 font-mono text-border">{{ fmtTs(ev.ts) }}</span>
      <span class="leading-snug">{{ kindToText(ev) }}</span>
    </div>
    <p v-if="events.length === 0" class="text-xs text-muted italic">暂无决策记录</p>
  </div>
</template>
