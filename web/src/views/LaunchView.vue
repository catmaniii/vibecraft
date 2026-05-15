<script setup lang="ts">
// 启动视图（P0-8）：SC2 未进行中时显示
// - SC2 / Bot 状态卡片
// - 「开始对局」按钮
// - 话语示例
//
// 关键约束：status 必须由父组件（App.vue）通过 props 传入，
// 不能在这里自己再调 useWs()——同一 token 第二个连接会顶掉父组件的连接，
// 导致父组件 isPlaying 永远不再更新、视图永远切不到 CockpitView。
import type { SystemStatus } from '@/types'

const props = defineProps<{
  canStartGame: boolean
  sc2Label: string
  status: SystemStatus
}>()

const emit = defineEmits<{
  startGame: []
}>()
</script>

<template>
  <div class="flex-1 flex flex-col gap-4 px-4 py-4 overflow-y-auto">

    <!-- SC2 / Bot 状态卡片 -->
    <div class="rounded-xl bg-surface-2 border border-border p-4">
      <div class="flex items-center justify-between mb-3">
        <span class="text-sm font-semibold text-muted uppercase tracking-wider">对局状态</span>
        <span
          class="inline-block w-2.5 h-2.5 rounded-full"
          :class="{
            'bg-success': props.status.sc2 === 'playing',
            'bg-warn animate-pulse': props.status.sc2 === 'launching' || props.status.sc2 === 'in_game',
            'bg-danger': props.status.sc2 === 'crashed',
            'bg-muted': props.status.sc2 === 'idle' || props.status.sc2 === 'ended',
          }"
        ></span>
      </div>

      <div class="flex flex-col gap-2">
        <div class="flex items-center gap-2 text-sm">
          <span class="w-16 text-muted shrink-0">SC2</span>
          <span
            :class="{
              'text-success': props.status.sc2 === 'playing',
              'text-warn': props.status.sc2 === 'launching' || props.status.sc2 === 'in_game',
              'text-danger': props.status.sc2 === 'crashed',
              'text-muted': props.status.sc2 === 'idle' || props.status.sc2 === 'ended',
            }"
          >{{ props.sc2Label }}</span>
        </div>
        <div class="flex items-center gap-2 text-sm">
          <span class="w-16 text-muted shrink-0">Bot</span>
          <span
            :class="{
              'text-success': props.status.bot === 'running',
              'text-danger': props.status.bot === 'error',
              'text-muted': props.status.bot === 'idle',
            }"
          >{{ props.status.bot === 'running' ? '运行中' : props.status.bot === 'error' ? '出错' : '待机' }}</span>
        </div>
        <p v-if="props.status.detail" class="text-xs text-danger mt-1">
          {{ props.status.detail }}
        </p>
      </div>

      <button
        class="mt-4 w-full rounded-xl py-3 text-base font-bold transition-all
               bg-accent text-surface active:scale-95
               disabled:opacity-40 disabled:cursor-not-allowed disabled:active:scale-100"
        :disabled="!props.canStartGame"
        @click="emit('startGame')"
      >
        开始对局
      </button>

      <p
        v-if="props.status.sc2 === 'launching' || props.status.sc2 === 'in_game'"
        class="mt-2 text-center text-xs text-warn"
      >
        SC2 正在启动，请稍等...
      </p>
    </div>

  </div>
</template>
