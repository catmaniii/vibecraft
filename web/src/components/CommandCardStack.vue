<script setup lang="ts">
// 命令卡片列表容器（P0f Task 15）
// - 包一圈带标题的 section card，风格同 StandingOrdersCard / ProductionOverridesCard
// - 空态：「暂无指令」灰色提示
// - emit('revoke', id) 冒泡到 CockpitView（Task 16）→ WS revoke_directive 帧
import type { CommandCardView } from '@/types'
import CommandCard from './CommandCard.vue'

defineProps<{
  cards: CommandCardView[]
}>()

defineEmits<{
  revoke: [id: string]
}>()
</script>

<template>
  <div class="rounded-xl border border-border bg-surface-2 p-3">
    <p class="text-xs font-semibold text-muted uppercase tracking-wider mb-2">指令列表</p>

    <!-- 空态 -->
    <p v-if="cards.length === 0" class="text-xs text-muted italic">暂无指令</p>

    <!-- 卡片堆叠 -->
    <div v-else class="flex flex-col gap-1.5">
      <CommandCard
        v-for="card in cards"
        :key="card.id"
        :card="card"
        @revoke="$emit('revoke', $event)"
      />
    </div>
  </div>
</template>
