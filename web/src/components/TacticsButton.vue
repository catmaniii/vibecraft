<script setup lang="ts">
// TacticsButton: 折叠浮层战术按钮
// 点击主按钮展开 5 个战术选项，每个选项直接 issue tactical_objective（绕过 LLM）。
import { ref } from 'vue'

const emit = defineEmits<{
  tacticalAction: [verb: string]
}>()

const expanded = ref(false)

function toggle() {
  expanded.value = !expanded.value
}

function sendAction(verb: string) {
  emit('tacticalAction', verb)
  expanded.value = false
}

interface TacticOption {
  verb: string
  label: string
}

const OPTIONS: TacticOption[] = [
  { verb: 'attack',  label: '全军进攻' },
  { verb: 'defend',  label: '全军防守' },
  { verb: 'retreat', label: '全军撤退' },
  { verb: 'recon',   label: '火力侦查' },
  { verb: 'scout',   label: '派单位探路' },
]
</script>

<template>
  <div class="relative inline-block" data-testid="tactics-button-root">
    <!-- 主按钮（折叠态图标） -->
    <button
      type="button"
      data-testid="tactics-toggle"
      class="w-9 h-9 flex items-center justify-center rounded-lg bg-surface-2 border border-border text-muted hover:text-accent hover:border-accent transition-colors text-sm font-bold"
      :class="{ 'border-accent text-accent': expanded }"
      :aria-expanded="expanded"
      aria-label="战术指令"
      @click="toggle"
    >
      ⚔
    </button>

    <!-- 浮层：展开后显示 5 个选项 -->
    <div
      v-if="expanded"
      data-testid="tactics-menu"
      class="absolute bottom-full mb-2 right-0 z-50 min-w-[120px] rounded-xl bg-surface-2 border border-border shadow-lg overflow-hidden"
    >
      <button
        v-for="opt in OPTIONS"
        :key="opt.verb"
        type="button"
        :data-testid="`tactics-option-${opt.verb}`"
        class="w-full px-4 py-2 text-left text-sm text-on-surface hover:bg-accent/10 transition-colors"
        @click="sendAction(opt.verb)"
      >
        {{ opt.label }}
      </button>
    </div>
  </div>
</template>
