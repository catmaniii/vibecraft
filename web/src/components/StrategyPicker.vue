<script setup lang="ts">
// StrategyPicker: 三族 18 个策略 chip 列表，点击直接切剧本（绕过 LLM / voice）。
// strategies 来自 snapshot.strategy_library（后续接入）或者静态硬编码（MVP）。

const emit = defineEmits<{
  strategyAction: [strategyId: string]
}>()

interface StrategyChip {
  id: string
  display: string
  race: 'protoss' | 'zerg' | 'terran'
  stage: 'opening' | 'midgame' | 'lategame'
}

// 三族 18 个策略静态列表（与 strategies/ YAML 对齐）
const STRATEGIES: StrategyChip[] = [
  // 神族 6
  { id: '1g_robo',           display: '1门Robo 不朽开',   race: 'protoss', stage: 'opening' },
  { id: '4bg_pressure',      display: '4BG 推进',          race: 'protoss', stage: 'opening' },
  { id: 'iac_2base',         display: '双矿 IAC',          race: 'protoss', stage: 'midgame' },
  { id: 'phoenix_2base',     display: '两矿凤凰',          race: 'protoss', stage: 'midgame' },
  { id: 'skytoss',           display: 'Skytoss 天空流',    race: 'protoss', stage: 'lategame' },
  { id: 'blink_stalker',     display: '闪追压制',          race: 'protoss', stage: 'midgame' },
  // 虫族 6
  { id: '12pool',            display: '12孵化池 暴兵',     race: 'zerg', stage: 'opening' },
  { id: 'macro_hatch',       display: '宏观孵化 经济',     race: 'zerg', stage: 'opening' },
  { id: 'roach_hydra',       display: '蟑螂飞龙',          race: 'zerg', stage: 'midgame' },
  { id: 'mutalisk_harass',   display: '飞龙骚扰',          race: 'zerg', stage: 'midgame' },
  { id: 'brood_corruptor',   display: '巢虫 + 腐化者',     race: 'zerg', stage: 'lategame' },
  { id: 'zerg_sustain',      display: '虫族运营',          race: 'zerg', stage: 'opening' },
  // 人族 6
  { id: 'marine_rush',       display: '速兵冲',            race: 'terran', stage: 'opening' },
  { id: 'reaper_expand',     display: '收割者开矿',        race: 'terran', stage: 'opening' },
  { id: 'bio_stim',          display: '生化 + 刺激',       race: 'terran', stage: 'midgame' },
  { id: 'two_base_tanks',    display: '双矿坦克',          race: 'terran', stage: 'midgame' },
  { id: 'bc_late',           display: '战列巡洋舰',        race: 'terran', stage: 'lategame' },
  { id: 'terran_sustain',    display: '人族运营',          race: 'terran', stage: 'opening' },
]

const RACE_LABELS: Record<string, string> = {
  protoss: '神',
  zerg: '虫',
  terran: '人',
}

const RACE_COLORS: Record<string, string> = {
  protoss: 'bg-accent/20 text-accent border-accent/30',
  zerg:    'bg-purple-500/20 text-purple-300 border-purple-500/30',
  terran:  'bg-yellow-500/20 text-yellow-200 border-yellow-500/30',
}

function pickStrategy(id: string) {
  emit('strategyAction', id)
}
</script>

<template>
  <div data-testid="strategy-picker" class="flex flex-col gap-2">
    <p class="text-xs font-semibold text-muted uppercase tracking-wider">快速切剧本</p>
    <div class="flex flex-wrap gap-1.5">
      <button
        v-for="strat in STRATEGIES"
        :key="strat.id"
        type="button"
        :data-testid="`strategy-chip-${strat.id}`"
        :class="[
          'flex items-center gap-1 px-2.5 py-1 rounded-full border text-xs font-medium transition-colors',
          'hover:brightness-110 active:scale-95',
          RACE_COLORS[strat.race],
        ]"
        @click="pickStrategy(strat.id)"
      >
        <span class="opacity-70 font-mono">{{ RACE_LABELS[strat.race] }}</span>
        <span>{{ strat.display }}</span>
      </button>
    </div>
  </div>
</template>
