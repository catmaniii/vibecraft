<script setup lang="ts">
// TacticsButton: 折叠下拉战术按钮(集成在 BotDecisionCard 右上角)。
// 点击主按钮展开 5 个战术选项,每个 issue tactical_objective 绕过 LLM。
// 点击别处自动关闭(click-outside)。
import { ref } from 'vue'
import { useClickOutside } from '@/composables/useClickOutside'

const emit = defineEmits<{
  tacticalAction: [verb: string]
}>()

const expanded = ref(false)
const rootEl = ref<HTMLElement | null>(null)

useClickOutside(rootEl, () => { expanded.value = false }, () => expanded.value)

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
  <div ref="rootEl" class="relative inline-block" data-testid="tactics-button-root">
    <!-- 触发按钮:小图标"切换"(集成在 BotDecisionCard 右上角) -->
    <button
      type="button"
      data-testid="tactics-toggle"
      class="shrink-0 px-2 py-0.5 rounded text-xs text-muted border border-border hover:text-accent hover:border-accent/50 hover:bg-accent/10 transition-colors leading-none"
      :class="{ 'border-accent text-accent bg-accent/10': expanded }"
      :aria-expanded="expanded"
      aria-label="切换战术"
      @click="toggle"
    >切换</button>

    <!-- 下拉菜单 -->
    <div
      v-if="expanded"
      data-testid="tactics-menu"
      class="absolute top-full mt-1 right-0 z-30 min-w-[120px] rounded-lg bg-surface-2 border border-border shadow-lg overflow-hidden"
    >
      <button
        v-for="opt in OPTIONS"
        :key="opt.verb"
        type="button"
        :data-testid="`tactics-option-${opt.verb}`"
        class="w-full px-3 py-2 text-left text-sm text-on-surface hover:bg-accent/10 transition-colors"
        @click="sendAction(opt.verb)"
      >
        {{ opt.label }}
      </button>
    </div>
  </div>
</template>
