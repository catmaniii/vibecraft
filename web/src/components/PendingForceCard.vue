<script setup lang="ts">
// voice 切剧本时机已过 → 提醒玩家 + 请求硬转确认
import type { PendingForceStrategyView } from '@/types'

defineProps<{
  pending: PendingForceStrategyView
}>()

const emit = defineEmits<{
  confirm: []
  cancel: []
}>()
</script>

<template>
  <div class="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 mt-2">
    <div class="flex items-center justify-between gap-2 mb-1.5">
      <div class="flex items-center gap-1.5">
        <span class="text-xs font-semibold text-amber-400 uppercase tracking-wider">
          ⚠️ 剧本时机已过
        </span>
      </div>
      <span class="text-[10px] text-muted uppercase">{{ pending.stage }}</span>
    </div>

    <p class="text-sm font-semibold text-white">
      你想切到:{{ pending.display_name }}
    </p>
    <p v-if="pending.source_text" class="text-[11px] text-muted italic mt-0.5">
      原话:"{{ pending.source_text }}"
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
      硬转后 bot 会按这个剧本的剩余部分跑(已过的步骤会被跳过,效果不保证)。
    </p>

    <div class="flex gap-2 mt-2">
      <button
        type="button"
        class="flex-1 rounded-md bg-amber-500/20 hover:bg-amber-500/30 border border-amber-500/40 text-amber-300 text-xs font-semibold py-1.5 transition-colors"
        @click="emit('confirm')"
      >⚠ 硬转</button>
      <button
        type="button"
        class="flex-1 rounded-md bg-surface-3 hover:bg-surface-2 border border-border text-muted text-xs font-semibold py-1.5 transition-colors"
        @click="emit('cancel')"
      >× 取消</button>
    </div>
  </div>
</template>
