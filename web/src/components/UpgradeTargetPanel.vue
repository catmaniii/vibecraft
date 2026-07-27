<script setup lang="ts">
// 攻防升级目标等级控件（15 族攻防升级线之一 = leveled tech_progress 条目）。
// 每条线一行：小图标 + 中文名 + 当前 Lv 徽标 + 目标 chips [0][1][2][3][自动]。
// 只有已经开始研究/已完成的线才会出现在 tech（后端 _build_tech_progress 规则），
// 兵种科技（single kind，如闪现/冲锋）没有 target 字段，天然被过滤掉，保持只读。
import { computed } from 'vue'
import type { TechProgressItem, TechProgressItemLeveled } from '@/types'
import { UPGRADE_ICONS } from '@/sc2Icons'
import { t } from '@/i18n'

const props = defineProps<{
  tech?: TechProgressItem[] | null
}>()

const emit = defineEmits<{
  macroAction: [dim: string, value: { family: string; level: number | 'auto' }]
}>()

function isLeveled(item: TechProgressItem): item is TechProgressItemLeveled {
  return (item as TechProgressItemLeveled).kind === 'leveled'
}

// 只有 leveled 条目带 target 字段（15 族攻防升级线）；single/building 天然过滤。
const tracks = computed<TechProgressItemLeveled[]>(() =>
  (props.tech ?? []).filter(isLeveled),
)

const hasAny = computed(() => tracks.value.length > 0)

// chip 候选：0/1/2/3 手动封顶 + 自动
const LEVEL_CHIPS: (number | 'auto')[] = [0, 1, 2, 3, 'auto']

function chipLabel(v: number | 'auto'): string {
  return v === 'auto' ? t('tech.targetAuto') : String(v)
}

function isActive(track: TechProgressItemLeveled, v: number | 'auto'): boolean {
  return v === 'auto' ? track.target === null : track.target === v
}

function chipCls(track: TechProgressItemLeveled, v: number | 'auto'): string {
  return isActive(track, v)
    ? 'text-accent border-accent bg-accent/15 font-semibold'
    : 'text-on-surface border-border hover:text-accent hover:border-accent/50 hover:bg-accent/10'
}

function onClick(track: TechProgressItemLeveled, v: number | 'auto') {
  emit('macroAction', 'upgrade_target', { family: track.track_en, level: v })
}

function icon(item: TechProgressItemLeveled): string {
  return UPGRADE_ICONS[item.icon_en] ?? ''
}
</script>

<template>
  <div
    v-if="hasAny"
    class="rounded-lg bg-surface-3/30 px-3 py-2.5"
    data-testid="upgrade-target-panel"
  >
    <p class="text-sm font-semibold text-white/85 tracking-wide mb-2">{{ t('tech.upgradeTargetTitle') }}</p>
    <div class="flex flex-col gap-1.5">
      <div
        v-for="track in tracks"
        :key="track.track_en"
        class="flex items-center gap-1.5 flex-wrap"
        :data-testid="`upgrade-target-row-${track.track_en}`"
      >
        <!-- 小图标 -->
        <div class="w-4 h-4 shrink-0 rounded overflow-hidden bg-surface-3 border border-border/60 flex items-center justify-center">
          <img v-if="icon(track)" :src="icon(track)" :alt="track.name_zh" class="w-full h-full object-cover" />
        </div>
        <!-- 名称 + 当前等级徽标 -->
        <span class="text-xs text-white/80 shrink-0">{{ track.name_zh }}</span>
        <span
          class="shrink-0 text-[9px] font-bold text-white/70 bg-black/40 rounded px-1 leading-[14px]"
          :data-testid="`upgrade-target-current-${track.track_en}`"
        >Lv{{ track.level }}</span>
        <span class="text-[10px] text-muted shrink-0">{{ t('tech.targetLabel') }}:</span>
        <!-- 目标 chips -->
        <button
          v-for="v in LEVEL_CHIPS"
          :key="String(v)"
          type="button"
          class="px-1.5 py-0.5 rounded text-[10px] border transition-colors leading-none"
          :class="chipCls(track, v)"
          :data-testid="`upgrade-target-chip-${track.track_en}-${v}`"
          :aria-label="t('tech.targetAria', { name: track.name_zh, label: chipLabel(v) })"
          @click="onClick(track, v)"
        >{{ chipLabel(v) }}</button>
      </div>
    </div>
  </div>
</template>
