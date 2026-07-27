<script setup lang="ts">
// bot 当前决策(独立大卡片)。
// 集成"切换战术"按钮(右上角) + 玩家 override 显示 + X 撤销。
//
// 玩家通过 TacticsButton 下了战术指令后,L2 tactical_objective 进 active_tactics。
// 此时卡片显示"玩家覆盖:进攻"等(替换 bot 自动 stance),X 撤销该 directive 恢复 bot 决策权。
//
// stance 来自 vibecraft 状态机:attacking/defending/expanding/scouting/harassing/sustaining
import { computed } from 'vue'
import type { TacticsView, TacticalObjectiveView } from '@/types'
import TacticsButton from './TacticsButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  tactics: TacticsView | null
  activeTactics?: readonly TacticalObjectiveView[]
}>()

const emit = defineEmits<{
  tacticalAction: [verb: string, mode?: 'all_in' | 'probe']
  revokeOverride: [directiveId: string]
  revokeCard: [directiveId: string]
}>()

// stance → 视觉风格(配色 + 强调感)
const stanceMeta: Record<string, { cls: string; ring: string }> = {
  attacking: { cls: 'text-danger', ring: 'border-danger/50 bg-danger/5' },
  defending: { cls: 'text-amber-400', ring: 'border-amber-500/50 bg-amber-500/5' },
  expanding: { cls: 'text-success', ring: 'border-success/50 bg-success/5' },
  scouting: { cls: 'text-blue-300', ring: 'border-blue-500/50 bg-blue-500/5' },
  harassing: { cls: 'text-purple-300', ring: 'border-purple-500/50 bg-purple-500/5' },
  sustaining: { cls: 'text-white/80', ring: 'border-border bg-surface-3' },
}

// verb → stance 映射(玩家 override 时用)
const verbToStanceKey: Record<string, string> = {
  attack: 'attacking',
  defend: 'defending',
  retreat: 'defending',  // 撤退视觉上=守势
  recon: 'scouting',
  scout: 'scouting',
  harass: 'harassing',
  vision: 'scouting',
  expand: 'expanding',
  drop: 'harassing',
  raze: 'attacking',
  regroup: 'defending',
  split: 'attacking',
}

// 当前 override(active_tactics 取第一个):玩家通过 TacticsButton 下的指令
const override = computed<TacticalObjectiveView | null>(() => {
  if (!props.activeTactics || props.activeTactics.length === 0) return null
  return props.activeTactics[0]
})

// 视觉 meta:override 在时按 verb 决定颜色,否则按 bot stance
const meta = computed(() => {
  if (override.value) {
    const key = verbToStanceKey[override.value.verb] ?? 'sustaining'
    return stanceMeta[key] ?? stanceMeta.sustaining
  }
  return stanceMeta[props.tactics?.stance ?? 'sustaining'] ?? stanceMeta.sustaining
})

// verb 标签 computed(响应 locale 切换)
const VERB_LABELS = computed<Record<string, string>>(() => ({
  attack: t('decision.verbAttack'),
  defend: t('tactics.defend'),
  retreat: t('tactics.retreat'),
  hold: t('tactics.hold'),
  recon: t('tactics.recon'),
  scout: t('tactics.scout'),
  harass: t('tactics.verbHarass'),
  vision: t('decision.verbVision'),
  expand: t('tactics.verbExpand'),
  drop: t('tactics.verbDrop'),
  raze: t('decision.verbRaze'),
  regroup: t('tactics.verbRegroup'),
  split: t('decision.verbSplit'),
}))

// 2026-05-25 attack 时按 attack_mode 区分"强制全体进攻"/"试探性进攻"
function overrideLabel(view: TacticalObjectiveView): string {
  if (view.verb === 'attack') {
    if (view.attack_mode === 'all_in') return t('tactics.allIn')
    if (view.attack_mode === 'probe') return t('tactics.probe')
  }
  return VERB_LABELS.value[view.verb] ?? view.verb
}

function onRevoke() {
  if (override.value) emit('revokeOverride', override.value.id)
}
</script>

<template>
  <div
    class="rounded-xl border p-3 transition-colors"
    :class="meta.ring"
  >
    <div class="flex items-center justify-between mb-2 gap-2">
      <!-- 固定标题 + 决策方标记:bot 自动 = BOT(灰),玩家指定 = 玩家(绿) -->
      <div class="flex items-center gap-1.5 min-w-0">
        <p class="text-xs font-semibold uppercase tracking-wider text-muted shrink-0">{{ t('decision.cardTitle') }}</p>
        <span
          class="text-[10px] px-1.5 py-0.5 rounded border leading-none shrink-0"
          :class="override
            ? 'bg-success/20 text-success border-success/40'
            : 'bg-surface-3 text-muted border-border'"
        >{{ override ? t('tactics.playerBadge') : t('decision.botBadge') }}</span>
      </div>
      <TacticsButton
        :current-tactics="props.tactics"
        :active-tactics="props.activeTactics"
        @tactical-action="(v, m) => emit('tacticalAction', v, m)"
      />
    </div>

    <!-- 玩家覆盖:显示玩家选的战术 + X 撤销 -->
    <div v-if="override" class="space-y-0.5">
      <div class="flex items-center gap-2">
        <p class="text-base font-bold flex-1" :class="meta.cls">
          {{ overrideLabel(override) }}
          <span v-if="override.target_area" class="text-sm text-white/60 font-normal">
            → {{ override.target_area }}
          </span>
        </p>
        <button
          type="button"
          data-testid="revoke-tactical-override-btn"
          class="shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted hover:text-danger hover:bg-danger/10 transition-colors text-xs leading-none"
          :aria-label="t('decision.revokeAria')"
          @click="onRevoke"
        >×</button>
      </div>
      <p class="text-xs text-white/60 italic">{{ t('decision.overrideHint') }}</p>
    </div>

    <!-- bot 自主决策:无覆盖时显示 -->
    <div v-else-if="tactics" class="space-y-0.5">
      <p class="text-base font-bold" :class="meta.cls">{{ tactics.label }}</p>
      <p class="text-xs text-white/70">{{ tactics.reason }}</p>
    </div>

    <!-- 都没:等待 bot -->
    <p v-else class="text-sm text-muted italic">{{ t('decision.waitingBot') }}</p>
  </div>
</template>
