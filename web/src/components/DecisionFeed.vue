<script setup lang="ts">
// Bot 决策流组件（P1-8）
// 接 events: EventFrame[]（已按 [newest, ...] 倒序存储）
// 内含 kind → 文案映射表
import type { EventFrame } from '@/types'
import { t } from '@/i18n'

defineProps<{
  events: readonly EventFrame[]
}>()

// 游戏内秒 → M:SS 格式
function fmtTs(ts: number): string {
  const m = Math.floor(ts / 60)
  const s = Math.floor(ts % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

// §9.4 taxonomy kind → 文案（template 函数，按 payload 丰富）
function kindToText(ev: EventFrame): string {
  const p = ev.payload
  switch (ev.kind) {
    case 'strategy.set': {
      const display = (p.display as string) || (p.strategy_id as string) || ''
      const stage = (p.stage as string) || ''
      const stagePart = stage ? ` [${stage}]` : ''
      return t('decision.strategySet', { display, stagePart })
    }
    case 'strategy.phase_change': {
      const stage = (p.stage as string) || (p.to_stage as string) || ''
      return t('decision.phaseChange', { stage })
    }
    case 'directive.committed': {
      const text = (p.user_text as string) || (p.strategy_id as string) || ''
      return text ? t('decision.directiveCommittedWith', { text }) : t('decision.directiveCommitted')
    }
    case 'directive.released': {
      const text = (p.user_text as string) || ''
      return text ? t('decision.directiveReleasedWith', { text }) : t('decision.directiveReleased')
    }
    case 'directive.rejected': {
      const reason = (p.reason as string) || ''
      return reason ? t('decision.directiveRejectedWith', { reason }) : t('decision.directiveRejected')
    }
    case 'decision.autopilot_phase': {
      // p.message 来自后端(服务端下发),有则用后端值,否则用前端默认
      const msg = (p.message as string) || t('decision.autopilotPhase')
      return msg
    }
    case 'decision.bot_action': {
      // bot 自动决策(造建筑/扩张/升级/build 完成),DecisionWatcher 推
      return (p.text as string) || t('decision.botAction')
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
    <p v-if="events.length === 0" class="text-xs text-muted italic">{{ t('decision.empty') }}</p>
  </div>
</template>
