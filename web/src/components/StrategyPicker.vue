<script setup lang="ts">
// StrategyPicker: 按种族过滤的剧本选择浮层。
// 默认折叠为一个"切换"小按钮(集成在"当前宏观策略"框右上角),
// 点击展开下拉菜单显示当前种族对应的策略 chip 列表。
// 点击别处自动关闭(click-outside)。

import { ref } from 'vue'
import { useClickOutside } from '@/composables/useClickOutside'

const props = defineProps<{
  race: 'protoss' | 'zerg' | 'terran'
}>()

const emit = defineEmits<{
  strategyAction: [strategyId: string]
}>()

interface StrategyChip {
  id: string
  display: string
  race: 'protoss' | 'zerg' | 'terran'
  stage: 'opening' | 'midgame' | 'lategame'
}

// 与 strategies/<race>/*.yaml 完全对齐(id 必须精确匹配)
const ALL_STRATEGIES: StrategyChip[] = [
  // 神族 8
  { id: '4bg',                display: '4 门 BG 早压',    race: 'protoss', stage: 'opening' },
  { id: '1g_robo_immortal',   display: '1 门 Robo 不朽',  race: 'protoss', stage: 'opening' },
  { id: 'dt_rush',            display: '暗使偷家',         race: 'protoss', stage: 'opening' },
  { id: 'phoenix_2base',      display: '两矿凤凰',         race: 'protoss', stage: 'opening' },
  { id: 'blink_stalker',      display: '闪追压制',         race: 'protoss', stage: 'opening' },
  { id: 'cannon_rush',        display: '炮塔速攻',         race: 'protoss', stage: 'opening' },
  { id: 'iac_2base',          display: '叉球一波',         race: 'protoss', stage: 'midgame' },
  { id: 'dt_drop_iac',        display: '空投隐刀转叉球',   race: 'protoss', stage: 'midgame' },
  { id: 'persistent_skytoss', display: '天空神族',         race: 'protoss', stage: 'lategame' },
  // 虫族 5
  { id: '12pool',             display: '12 池 rush',       race: 'zerg', stage: 'opening' },
  { id: 'macro_hatch',        display: '三矿 macro',       race: 'zerg', stage: 'opening' },
  { id: 'roach_hydra',        display: '蟑螂刺蛇',         race: 'zerg', stage: 'midgame' },
  { id: 'mutalisk_harass',    display: '飞龙骚扰',         race: 'zerg', stage: 'midgame' },
  { id: 'persistent_brood_corruptor', display: '巢虫腐化运营', race: 'zerg', stage: 'lategame' },
  // 人族 5
  { id: 'marine_rush',        display: '双兵营 rush',      race: 'terran', stage: 'opening' },
  { id: 'reaper_expand',      display: '死神扩张',         race: 'terran', stage: 'opening' },
  { id: 'bio_stim',           display: 'bio 推进',         race: 'terran', stage: 'midgame' },
  { id: 'two_base_tanks',     display: '双矿坦克',         race: 'terran', stage: 'midgame' },
  { id: 'persistent_skyterran', display: '战巡空军',       race: 'terran', stage: 'lategame' },
]

const STAGE_LABELS: Record<string, string> = {
  opening: '开局',
  midgame: '中期',
  lategame: '后期',
}

const STAGE_COLORS: Record<string, string> = {
  opening:  'bg-accent/20 text-accent border-accent/30',
  midgame:  'bg-yellow-500/20 text-yellow-200 border-yellow-500/30',
  lategame: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
}

const expanded = ref(false)
const rootEl = ref<HTMLElement | null>(null)

useClickOutside(rootEl, () => { expanded.value = false }, () => expanded.value)

function strategiesByRace() {
  return ALL_STRATEGIES.filter((s) => s.race === props.race)
}

function pickStrategy(id: string) {
  emit('strategyAction', id)
  expanded.value = false  // 选完自动收起
}
</script>

<template>
  <div ref="rootEl" data-testid="strategy-picker" class="relative inline-block">
    <!-- 触发按钮:小图标"切换"(集成在 strategy 框右上角) -->
    <button
      type="button"
      data-testid="strategy-picker-toggle"
      class="shrink-0 px-2 py-0.5 rounded text-xs text-muted border border-border hover:text-accent hover:border-accent/50 hover:bg-accent/10 transition-colors leading-none"
      :aria-label="expanded ? '关闭剧本菜单' : '切换剧本'"
      @click="expanded = !expanded"
    >切换</button>

    <!-- 展开后的下拉菜单 -->
    <div
      v-if="expanded"
      data-testid="strategy-picker-popup"
      class="absolute right-0 top-full mt-1 z-30 bg-surface-2 border border-border rounded-lg shadow-lg p-2 min-w-[240px]"
    >
      <div
        v-for="stage in ['opening', 'midgame', 'lategame']"
        :key="stage"
        class="mb-2 last:mb-0"
      >
        <p class="text-[10px] font-semibold text-muted uppercase tracking-wider px-1 pb-1">
          {{ STAGE_LABELS[stage] }}
        </p>
        <div class="flex flex-wrap gap-1">
          <button
            v-for="strat in strategiesByRace().filter((s) => s.stage === stage)"
            :key="strat.id"
            type="button"
            :data-testid="`strategy-chip-${strat.id}`"
            :class="[
              'px-2 py-1 rounded-full border text-xs font-medium transition-colors',
              'hover:brightness-110 active:scale-95',
              STAGE_COLORS[stage],
            ]"
            @click="pickStrategy(strat.id)"
          >
            {{ strat.display }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
