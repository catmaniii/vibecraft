<script setup lang="ts">
// VoiceCraft PWA 主组件（M1.3 最小壳）
// - 三段式系统状态链
// - 「开始对局」按钮 → start_game 帧
// - 指令输入 → command 帧
// - 无 room token 时显示引导提示
import { computed } from 'vue'
import StatusChain from '@/components/StatusChain.vue'
import CommandInput from '@/components/CommandInput.vue'
import { useWs } from '@/composables/useWs'
import type { CommandFrame } from '@/types'

const { status, send, token } = useWs()

// 游戏是否可发指令（WS 已连 + SC2 playing 阶段）
const canSendCommand = computed(
  () => status.value.link === 'connected' && status.value.sc2 === 'playing'
)

// 「开始对局」按钮可用条件：已连服务端、SC2 尚未启动（idle / ended）
const canStartGame = computed(
  () =>
    status.value.link === 'connected' &&
    (status.value.sc2 === 'idle' || status.value.sc2 === 'ended')
)

function startGame() {
  send({ type: 'start_game' })
}

function onCommand(frame: CommandFrame) {
  send(frame)
}

// SC2 状态显示文案
const sc2Label = computed(() => {
  const map: Record<string, string> = {
    idle: '等待开局',
    launching: '正在启动 SC2...',
    in_game: 'SC2 载入中...',
    playing: '对局进行中',
    ended: '对局结束',
    crashed: 'SC2 崩溃',
  }
  return map[status.value.sc2] ?? status.value.sc2
})
</script>

<template>
  <!-- 全屏深色背景，移动端竖向 flex 布局 -->
  <div class="min-h-screen bg-surface text-white flex flex-col select-none">

    <!-- 顶栏：Logo + 状态链 -->
    <header class="flex items-center justify-between px-4 py-3 bg-surface-2 border-b border-border">
      <span class="font-bold tracking-wide text-accent">VoiceCraft</span>
      <!-- Vue 模板自动 unwrap ref，传 status 即传 SystemStatus 对象 -->
      <StatusChain :status="status" />
    </header>

    <!-- 无 token 引导提示 -->
    <div v-if="!token" class="flex-1 flex flex-col items-center justify-center gap-4 px-6 text-center">
      <p class="text-2xl font-bold text-accent">扫码启动</p>
      <p class="text-muted text-sm leading-relaxed">
        请在 PC 端运行 <code class="bg-surface-3 px-1 rounded">voicecraft serve</code>，<br/>
        然后用手机扫码或输入显示的地址访问。
      </p>
    </div>

    <!-- 主内容区（有 token 时展示）-->
    <main v-else class="flex-1 flex flex-col gap-4 px-4 py-4 overflow-y-auto">

      <!-- SC2 / Bot 状态卡片 -->
      <div class="rounded-xl bg-surface-2 border border-border p-4">
        <div class="flex items-center justify-between mb-3">
          <span class="text-sm font-semibold text-muted uppercase tracking-wider">对局状态</span>
          <!-- 状态指示点 -->
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

        <!-- 三段状态阶段显示（启动链：服务端 → SC2 → Bot）-->
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
            >{{ sc2Label }}</span>
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
          <!-- 错误详情（crashed / error 时显示）-->
          <p v-if="status.detail" class="text-xs text-danger mt-1">
            {{ status.detail }}
          </p>
        </div>

        <!-- 「开始对局」按钮 -->
        <button
          class="mt-4 w-full rounded-xl py-3 text-base font-bold transition-all
                 bg-accent text-surface active:scale-95
                 disabled:opacity-40 disabled:cursor-not-allowed disabled:active:scale-100"
          :disabled="!canStartGame"
          @click="startGame"
        >
          开始对局
        </button>

        <!-- 启动中 / 进行中的进度提示 -->
        <p
          v-if="status.sc2 === 'launching' || status.sc2 === 'in_game'"
          class="mt-2 text-center text-xs text-warn"
        >
          SC2 正在启动，请稍等...
        </p>
      </div>

      <!-- 指令输入区（playing 阶段才开放）-->
      <div class="rounded-xl bg-surface-2 border border-border p-4">
        <p class="text-sm font-semibold text-muted uppercase tracking-wider mb-3">
          发号施令
        </p>
        <CommandInput :can-send="canSendCommand" @send="onCommand" />
      </div>

      <!-- 底部说明（给玩家的话语提示，M3 完整驾驶舱再做成可折叠 overlay）-->
      <div class="rounded-xl bg-surface-3 border border-border p-3 text-xs text-muted">
        <p class="font-semibold mb-1">话语示例</p>
        <ul class="space-y-0.5 leading-relaxed">
          <li>「切 IAC」「切到双矿凤凰」</li>
          <li>「叉子全压上去」「追猎偷矿」</li>
          <li>「我要 DT 骚扰」「先撑到 Skytoss」</li>
        </ul>
      </div>
    </main>

  </div>
</template>
