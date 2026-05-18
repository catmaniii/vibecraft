<script setup lang="ts">
// StrategyPicker: 按种族过滤的剧本选择浮层。
// 默认折叠为一个按钮，点击展开当前种族对应的策略 chip 列表。
//
// race prop 决定显示哪一族的策略 — 神族 8 个 / 虫族 5 个 / 人族 5 个。
// 不显示其他种族的策略（玩家选的种族就是 service 启动时的种族，跨族 chip 是噪音）。

import { ref } from 'vue'

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

// 与 strategies/<race>/*.yaml 完全对齐（id 必须精确匹配）
const ALL_STRATEGIES: StrategyChip[] = [
  // 神族 8
  { id: '4bg',                display: '4 门 BG 早压',    race: 'protoss', stage: 'opening' },
  { id: '1g_robo_immortal',   display: '1 门 Robo 不朽',  race: 'protoss', stage: 'opening' },
  { id: 'dt_rush',            display: '暗使偷家',         race: 'protoss', stage: 'opening' },
  { id: 'phoenix_2base',      display: '两矿凤凰',         race: 'protoss', stage: 'opening' },
  { id: 'blink_stalker',      display: '闪追压制',         race: 'protoss', stage: 'opening' },
  { id: 'cannon_rush',        display: '炮塔速攻',         race: 'protoss', stage: 'opening' },
  { id: 'iac_2base',          display: '叉球一波',         race: 'protoss', stage: 'midgame' },
  { id: 'skytoss',            display: '后期空军',         race: 'protoss', stage: 'lategame' },
  // 虫族 5
  { id: '12pool',             display: '12 池 rush',       race: 'zerg', stage: 'opening' },
  { id: 'macro_hatch',        display: '三矿 macro',       race: 'zerg', stage: 'opening' },
  { id: 'roach_hydra',        display: '蟑螂刺蛇',         race: 'zerg', stage: 'midgame' },
  { id: 'mutalisk_harass',    display: '飞龙骚扰',         race: 'zerg', stage: 'midgame' },
  { id: 'brood_corruptor',    display: '巢虫 + 腐化',      race: 'zerg', stage: 'lategame' },
  // 人族 5
  { id: 'marine_rush',        display: '双兵营 rush',      race: 'terran', stage: 'opening' },
  { id: 'reaper_expand',      display: '死神扩张',         race: 'terran', stage: 'opening' },
  { id: 'bio_stim',           display: 'bio 推进',         race: 'terran', stage: 'midgame' },
  { id: 'two_base_tanks',     display: '双矿坦克',         race: 'terran', stage: 'midgame' },
  { id: 'bc_late',            display: '战巡终结',         race: 'terran', stage: 'lategame' },
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

function strategiesByRace() {
  return ALL_STRATEGIES.filter((s) => s.race === props.race)
}

function pickStrategy(id: string) {
  emit('strategyAction', id)
  expanded.value = false  // 选完自动收起
}
</script>

<template>
  <div data-testid="strategy-picker" class="relative inline-block">
    <!-- 触发按钮：折叠时只显示这一个按钮 -->
    <button
      type="button"
      data-testid="strategy-picker-toggle"
      :class="[
        'flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium transition-colors',
        'bg-surface-2 border-border text-fg hover:bg-surface-3 active:scale-95',
      ]"
      @click="expanded = !expanded"
    >
      <span>剧本</span>
      <span class="opacity-60">{{ expanded ? '▲' : '▼' }}</span>
    </button>

    <!-- 展开后的浮层 -->
    <div
      v-if="expanded"
      data-testid="strategy-picker-popup"
      class="absolute right-0 top-full mt-1 z-20 bg-surface-2 border border-border rounded-lg shadow-lg p-2 min-w-[240px]"
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
