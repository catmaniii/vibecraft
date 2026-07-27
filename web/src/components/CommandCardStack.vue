<script setup lang="ts">
// 命令卡片列表容器（P0f Task 15）
// - 包一圈带标题的 section card，风格同 StandingOrdersCard / ProductionOverridesCard
// - 空态：「暂无指令」灰色提示
// - emit('revoke', id) 冒泡到 CockpitView（Task 16）→ WS revoke_directive 帧
// - drop_act 类型路由到 DropActCard（Task 9）
import type { CommandCardView } from '@/types'
import CommandCard from './CommandCard.vue'
import DropActCard from './DropActCard.vue'
import { t } from '@/i18n'

defineProps<{
  cards: CommandCardView[]
}>()

defineEmits<{
  revoke: [id: string]
}>()
</script>

<template>
  <div class="rounded-xl border border-border bg-surface-2 p-3">
    <p class="text-xs font-semibold text-muted uppercase tracking-wider mb-2">{{ t('cardstack.title') }}</p>

    <!-- 空态 -->
    <p v-if="cards.length === 0" class="text-xs text-muted italic">{{ t('cardstack.empty') }}</p>

    <!-- 卡片堆叠 -->
    <div v-else class="flex flex-col gap-1.5">
      <template v-for="card in cards" :key="card.id">
        <DropActCard
          v-if="card.type === 'drop_act'"
          :card="card"
          @revoke="$emit('revoke', $event)"
        />
        <CommandCard
          v-else
          :card="card"
          @revoke="$emit('revoke', $event)"
        />
      </template>
    </div>
  </div>
</template>
