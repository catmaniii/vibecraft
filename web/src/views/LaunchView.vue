<script setup lang="ts">
// 启动视图（P0-8）：SC2 未进行中时显示
// - SC2 / Bot 状态卡片
// - 「开始对局」按钮
// - 话语示例
import { computed } from 'vue'
import CommandInput from '@/components/CommandInput.vue'
import { useWs } from '@/composables/useWs'
import type { CommandFrame } from '@/types'

const props = defineProps<{
  canStartGame: boolean
  canSendCommand: boolean
  sc2Label: string
}>()

const emit = defineEmits<{
  startGame: []
  command: [frame: CommandFrame]
}>()

// 引入 status 只用于内部渲染
const { status } = useWs()
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
            'bg-success': status.sc2 === 'playing',
            'bg-warn animate-pulse': status.sc2 === 'launching' || status.sc2 === 'in_game',
            'bg-danger': status.sc2 === 'crashed',
            'bg-muted': status.sc2 === 'idle' || status.sc2 === 'ended',
          }"
        ></span>
      </div>

      <div class="flex flex-col gap-2">
        <div class="flex items-center gap-2 text-sm">
          <span class="w-16 text-muted shrink-0">SC2</span>
          <span
            :class="{
              'text-success': status.sc2 === 'playing',
              'text-warn': status.sc2 === 'launching' || status.sc2 === 'in_game',
              'text-danger': status.sc2 === 'crashed',
              'text-muted': status.sc2 === 'idle' || status.sc2 === 'ended',
            }"
          >{{ props.sc2Label }}</span>
        </div>
        <div class="flex items-center gap-2 text-sm">
          <span class="w-16 text-muted shrink-0">Bot</span>
          <span
            :class="{
              'text-success': status.bot === 'running',
              'text-danger': status.bot === 'error',
              'text-muted': status.bot === 'idle',
            }"
          >{{ status.bot === 'running' ? '运行中' : status.bot === 'error' ? '出错' : '待机' }}</span>
        </div>
        <p v-if="status.detail" class="text-xs text-danger mt-1">
          {{ status.detail }}
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
        v-if="status.sc2 === 'launching' || status.sc2 === 'in_game'"
        class="mt-2 text-center text-xs text-warn"
      >
        SC2 正在启动，请稍等...
      </p>
    </div>

    <!-- 指令输入区 -->
    <div class="rounded-xl bg-surface-2 border border-border p-4">
      <p class="text-sm font-semibold text-muted uppercase tracking-wider mb-3">
        发号施令
      </p>
      <CommandInput :can-send="props.canSendCommand" @send="(f) => emit('command', f)" />
    </div>

    <!-- 话语示例 -->
    <div class="rounded-xl bg-surface-3 border border-border p-3 text-xs text-muted">
      <p class="font-semibold mb-1">话语示例</p>
      <ul class="space-y-0.5 leading-relaxed">
        <li>「切 IAC」「切到双矿凤凰」</li>
        <li>「叉子全压上去」「追猎偷矿」</li>
        <li>「我要 DT 骚扰」「先撑到 Skytoss」</li>
      </ul>
    </div>
  </div>
</template>
