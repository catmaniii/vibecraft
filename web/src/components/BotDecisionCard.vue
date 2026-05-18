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

const props = defineProps<{
  tactics: TacticsView | null
  activeTactics?: readonly TacticalObjectiveView[]
}>()

const emit = defineEmits<{
  tacticalAction: [verb: string]
  revokeOverride: [directiveId: string]
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

const VERB_LABELS: Record<string, string> = {
  attack: '全军进攻',
  defend: '全军防守',
  retreat: '全军撤退',
  recon: '火力侦查',
  scout: '派单位探路',
  harass: '骚扰',
  vision: '视野',
  expand: '扩张',
  drop: '空投',
  raze: '拆迁',
  regroup: '集结',
  split: '分散',
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
      <p class="text-xs font-semibold text-muted uppercase tracking-wider">
        bot 当前决策
      </p>
      <TacticsButton @tactical-action="(v) => emit('tacticalAction', v)" />
    </div>

    <!-- 玩家覆盖:显示玩家选的战术 + X 撤销 -->
    <div v-if="override" class="space-y-0.5">
      <div class="flex items-center gap-2">
        <span class="text-[10px] px-1.5 py-0.5 rounded border bg-success/20 text-success border-success/40 leading-none">
          玩家
        </span>
        <p class="text-base font-bold flex-1" :class="meta.cls">
          {{ VERB_LABELS[override.verb] ?? override.verb }}
          <span v-if="override.target_area" class="text-sm text-white/60 font-normal">
            → {{ override.target_area }}
          </span>
        </p>
        <button
          type="button"
          data-testid="revoke-tactical-override-btn"
          class="shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted hover:text-danger hover:bg-danger/10 transition-colors text-xs leading-none"
          aria-label="取消玩家覆盖,交回 bot"
          @click="onRevoke"
        >×</button>
      </div>
      <p class="text-xs text-white/60 italic">玩家覆盖了 bot 决策,× 交回 bot</p>
    </div>

    <!-- bot 自主决策:无覆盖时显示 -->
    <div v-else-if="tactics" class="space-y-0.5">
      <p class="text-base font-bold" :class="meta.cls">{{ tactics.label }}</p>
      <p class="text-xs text-white/70">{{ tactics.reason }}</p>
    </div>

    <!-- 都没:等待 bot -->
    <p v-else class="text-sm text-muted italic">（等待 bot 上报)</p>
  </div>
</template>
