<script setup lang="ts">
// L2 战术指令列表卡片（P3.6）
// - 列出当前所有 active tactical objectives，每条带 × 撤销按钮
// - 空态：「暂无战术指令」灰色提示
// - emit('revoke', id) → useWs.revokeDirective(id) → WS revoke_directive 帧
import { computed } from 'vue'
import type { TacticalObjectiveView } from '@/types'
import { t } from '@/i18n'

defineProps<{
  tactics: readonly TacticalObjectiveView[]
}>()

const emit = defineEmits<{
  revoke: [id: string]
}>()

const VERB_LABELS = computed<Record<string, string>>(() => ({
  attack:  t('tactics.verbAttack'),
  defend:  t('tactics.verbDefend'),
  scout:   t('tactics.verbScout'),
  expand:  t('tactics.verbExpand'),
  harass:  t('tactics.verbHarass'),
  drop:    t('tactics.verbDrop'),
  vision:  t('tactics.verbVision'),
  raze:    t('tactics.verbRaze'),
  retreat: t('tactics.verbRetreat'),
  regroup: t('tactics.verbRegroup'),
  split:   t('tactics.verbSplit'),
  hold:    t('tactics.verbHold'),
}))

function verbLabel(verb: string, targetArea: string | null): string {
  const label = VERB_LABELS.value[verb] ?? verb
  return targetArea ? `${label} ${targetArea}` : label
}

/** issued_by → badge 配置 */
function sourceBadge(issuedBy: string | undefined): { label: string; cls: string } {
  if (issuedBy === 'voice' || issuedBy === 'auto_transition') {
    return { label: t('tactics.playerBadge'), cls: 'bg-emerald-500/20 text-emerald-300' }
  }
  // bot_internal / abort / 未知 → BOT
  return { label: 'BOT', cls: 'bg-sky-500/20 text-sky-300' }
}
</script>

<template>
  <div class="rounded-xl border border-border bg-surface-2 p-3">
    <p class="text-xs font-semibold text-muted uppercase tracking-wider mb-2">{{ t('tactics.cardTitle') }}</p>

    <!-- 空态 -->
    <p v-if="tactics.length === 0" class="text-xs text-muted italic">{{ t('tactics.noDirectives') }}</p>

    <!-- 列表 -->
    <ul v-else class="space-y-1.5">
      <li
        v-for="tac in tactics"
        :key="tac.id"
        class="flex items-center gap-2 rounded-md bg-surface-3 border border-border/60 px-2.5 py-1.5"
      >
        <!-- 来源 badge（玩家=绿 / BOT=蓝） -->
        <span
          :class="['shrink-0 text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wider font-semibold', sourceBadge(tac.issued_by).cls]"
        >{{ sourceBadge(tac.issued_by).label }}</span>
        <span class="flex-1 min-w-0 text-sm text-white/90 truncate">{{ verbLabel(tac.verb, tac.target_area) }}</span>
        <button
          type="button"
          data-testid="revoke-btn"
          class="shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted hover:text-danger hover:bg-danger/10 transition-colors text-xs leading-none"
          :aria-label="t('tactics.revokeAria', { label: verbLabel(tac.verb, tac.target_area) })"
          @click="emit('revoke', tac.id)"
        >×</button>
      </li>
    </ul>
  </div>
</template>
