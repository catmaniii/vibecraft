<script setup lang="ts">
// voice 切剧本时机已过 → 提醒玩家 + 请求硬转确认
import type { PendingForceStrategyView } from '@/types'
import { t } from '@/i18n'

defineProps<{
  pending: PendingForceStrategyView
}>()

const emit = defineEmits<{
  confirm: []
  cancel: []
}>()
</script>

<template>
  <!-- 2026-05-24 用户:剧本时机已过提示应覆盖"宏观策略"框,不是 BOT 决策框。
       absolute inset-0 + z-20 临时遮罩 StrategyCard,玩家点 硬转/取消 后消失。
       z-20 高于 AutoSwitchToast (z-10) 防同时叠加。-->
  <div
    data-testid="pending-force-card"
    class="absolute inset-0 z-20 flex flex-col justify-center
           rounded-xl border border-amber-500/40 bg-amber-500/15
           backdrop-blur px-4 py-3 shadow-lg pointer-events-auto"
  >
    <div class="flex items-center justify-between gap-2 mb-1.5">
      <div class="flex items-center gap-1.5">
        <span class="text-xs font-semibold text-amber-400 uppercase tracking-wider">
          {{ t('pending.title') }}
        </span>
      </div>
      <span class="text-[10px] text-muted uppercase">{{ pending.stage }}</span>
    </div>

    <p class="text-sm font-semibold text-white">
      {{ t('pending.wantSwitch', { name: pending.display_name }) }}
    </p>
    <p v-if="pending.source_text" class="text-[11px] text-muted italic mt-0.5">
      {{ t('clarify.sourceText', { text: pending.source_text }) }}
    </p>

    <!-- 偏差原因列表 -->
    <ul class="mt-1.5 space-y-0.5">
      <li
        v-for="(r, idx) in pending.reasons"
        :key="idx"
        class="text-xs text-amber-300/90"
      >· {{ r }}</li>
    </ul>

    <p class="text-[11px] text-muted mt-1.5 italic">
      {{ t('pending.hint') }}
    </p>

    <div class="flex gap-2 mt-2">
      <button
        type="button"
        class="flex-1 rounded-md bg-amber-500/20 hover:bg-amber-500/30 border border-amber-500/40 text-amber-300 text-xs font-semibold py-1.5 transition-colors"
        @click="emit('confirm')"
      >{{ t('pending.confirmBtn') }}</button>
      <button
        type="button"
        class="flex-1 rounded-md bg-surface-3 hover:bg-surface-2 border border-border text-muted text-xs font-semibold py-1.5 transition-colors"
        @click="emit('cancel')"
      >{{ t('pending.cancelBtn') }}</button>
    </div>
  </div>
</template>
