<script setup lang="ts">
// 两层架构（2026-05-19 P3 Step 11）：bot 自动切到 persistent doctrine 时的
// toast 提示。Director 收到玩家 cancel / 开局完成时调
// `_apply_auto_persistent_switch`，发 `strategy.auto_switch` 事件。
// PWA 监听该事件，弹 3 秒 toast 显示"已切到 X，理由 Y，备选 Z"。
//
// reason 字段语义：
//   - cancel_redirected: 玩家说了 cancel，被 bot 拦截 + 自动切
//   - opening_completed: 开局完成自动切（Step 10 还没实现 completion 检测）
//   - parse_fail_redirected: LLM 解析失败兜底（未来）
import { computed, ref, watch } from 'vue'

// strategy id → 中文显示名（保持跟 StrategyPicker.vue 一致）
const STRATEGY_DISPLAY: Record<string, string> = {
  persistent_skytoss: '天空神族',
  persistent_brood_corruptor: '巢虫腐化运营',
  persistent_skyterran: '战巡空军',
}

const REASON_DISPLAY: Record<string, string> = {
  cancel_redirected: '玩家取消',
  opening_completed: '开局完成',
  parse_fail_redirected: '指令解析失败',
}

const props = defineProps<{
  // payload 来自 event.payload，可能 null
  switchEvent: {
    ts: number
    payload: {
      reason: string
      chosen_id: string
      cost: number
      alternatives: Array<{ id: string; cost: number }>
      enemy_tags_hit: string[]
      // 2026-05-20: True = bot 已真换 plan;False = 仅推荐,玩家自己挑时机切
      swap_plan?: boolean
    }
  } | null
}>()

// 3s 自动消失：watch switchEvent 变化时启动 timer
const visible = ref(false)
let dismissTimer: ReturnType<typeof setTimeout> | null = null

watch(
  () => props.switchEvent?.ts,
  (newTs) => {
    if (newTs == null) return
    visible.value = true
    if (dismissTimer != null) clearTimeout(dismissTimer)
    dismissTimer = setTimeout(() => {
      visible.value = false
    }, 5000)
  }
)

// 显示
const chosenDisplay = computed(() =>
  STRATEGY_DISPLAY[props.switchEvent?.payload.chosen_id ?? ''] ??
  props.switchEvent?.payload.chosen_id ?? ''
)
const reasonDisplay = computed(() =>
  REASON_DISPLAY[props.switchEvent?.payload.reason ?? ''] ??
  props.switchEvent?.payload.reason ?? ''
)
const alternativesDisplay = computed(() => {
  const alts = props.switchEvent?.payload.alternatives ?? []
  return alts.slice(0, 2).map((a) => STRATEGY_DISPLAY[a.id] ?? a.id).join(' / ')
})

// swap_plan=true → "已切换";false → "推荐切换"。
// 2026-05-20: opening_completed reason 走推荐模式(plan 不动,让 gate4 攻击继续),
// cancel_redirected 仍是真换 plan。
const titleDisplay = computed(() => {
  const swap = props.switchEvent?.payload.swap_plan
  return swap === false ? 'ⓘ 建议切换持续策略' : 'ⓘ 已自动切换策略'
})
</script>

<template>
  <Transition
    enter-active-class="transition ease-out duration-200"
    enter-from-class="opacity-0 -translate-y-2"
    enter-to-class="opacity-100 translate-y-0"
    leave-active-class="transition ease-in duration-300"
    leave-from-class="opacity-100"
    leave-to-class="opacity-0"
  >
    <div
      v-if="visible && switchEvent != null"
      data-testid="auto-switch-toast"
      class="fixed top-16 left-1/2 -translate-x-1/2 z-40 max-w-[90%]
             rounded-lg bg-accent/15 border border-accent/50
             backdrop-blur px-4 py-3 shadow-lg pointer-events-auto"
    >
      <p class="text-xs text-muted uppercase tracking-wider">{{ titleDisplay }}</p>
      <p class="text-base font-bold text-accent mt-1">{{ chosenDisplay }}</p>
      <p class="text-xs text-white/80 mt-1">
        原因: {{ reasonDisplay }}
        <span v-if="alternativesDisplay" class="text-white/50">
          / 备选: {{ alternativesDisplay }}
        </span>
      </p>
    </div>
  </Transition>
</template>
