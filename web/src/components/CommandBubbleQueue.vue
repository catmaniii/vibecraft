<script setup lang="ts">
// 命令气泡队列（2026-07-08 用户：非阻塞多条命令并存展示）
//
// 背景：后端每条命令并发解析（asyncio.create_task，LLM 可能 3-8s），
// 玩家可能在第一条还没解析完时就发第二条 —— 单一 status 会被覆盖丢状态。
// 改成气泡队列：每发一条 → 一个气泡（琥珀=识别中），解析完变绿(成功)/红(失败)，
// 停留一下淡出移除；还没解析完的气泡留着，互不覆盖。
//
// 纯展示组件：气泡数组的增删改（何时开卡 / 匹配 echo 更新 / 淡出移除）
// 由 CommandInput.vue 管理（WS command_received / command_echo 驱动）。
import { t } from '@/i18n'

export type CommandBubbleStatus = 'pending' | 'done' | 'failed'

export interface CommandBubble {
  /** 唯一 key，`${ts}_${text}`（ts 来自 command_received 原样回显的 issued_at） */
  id: string
  text: string
  ts: number
  status: CommandBubbleStatus
  /** done/failed 后的详情（LLM 解读 / 失败原因），pending 时为空 */
  detail?: string
}

defineProps<{ bubbles: CommandBubble[] }>()

function label(status: CommandBubbleStatus): string {
  if (status === 'pending') return t('cmdinput.carrierSending')
  if (status === 'done') return t('cmdinput.carrierDone')
  return t('cmdinput.carrierFailed')
}

function cls(status: CommandBubbleStatus): string {
  if (status === 'pending') return 'bg-amber-500/90 border-amber-300'
  if (status === 'done') return 'bg-success border-success/80'
  return 'bg-danger border-danger/80'
}
</script>

<template>
  <TransitionGroup
    v-if="bubbles.length > 0"
    tag="div"
    data-testid="cmd-bubble-queue"
    class="flex flex-col gap-1 max-h-40 overflow-y-auto"
    enter-active-class="transition ease-out duration-200"
    enter-from-class="opacity-0 translate-y-1"
    enter-to-class="opacity-100 translate-y-0"
    leave-active-class="transition ease-in duration-500"
    leave-from-class="opacity-100"
    leave-to-class="opacity-0"
    move-class="transition-transform duration-200"
  >
    <div
      v-for="b in bubbles"
      :key="b.id"
      data-testid="cmd-bubble"
      :data-status="b.status"
      class="rounded-lg border px-3 py-1.5 backdrop-blur shadow-md pointer-events-none shrink-0"
      :class="cls(b.status)"
    >
      <div class="flex items-center justify-between gap-2">
        <span class="text-[10px] text-white/85 uppercase tracking-wider">{{ label(b.status) }}</span>
      </div>
      <p class="text-sm font-medium text-white mt-0.5 truncate" data-testid="cmd-bubble-text">{{ b.text }}</p>
      <p
        v-if="b.detail"
        class="text-[11px] text-white/75 mt-0.5 truncate"
        data-testid="cmd-bubble-detail"
      >→ {{ b.detail }}</p>
    </div>
  </TransitionGroup>
</template>
