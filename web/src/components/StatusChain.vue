<script setup lang="ts">
// 三段式系统状态链：手机●━服务端●━SC2●━Bot●
// 全绿折叠成单点；任一异常展开成完整一行（§9.5 / §9.6）
import { computed } from 'vue'
import type { SystemStatus, LinkState, Sc2State, BotState } from '@/types'
import { t } from '@/i18n'

const props = defineProps<{
  status: SystemStatus
  /** 等距撑满分布（对局大厅用，2026-06-12 用户）：节点均布、连线拉伸、整体居中。 */
  stretch?: boolean
  /** 全绿也不折叠成"系统正常"单点，始终显示四节链（对局大厅用，2026-06-13 用户）。 */
  expanded?: boolean
}>()

// ---- 每段的颜色判定 ----

type SegColor = 'green' | 'yellow' | 'red' | 'gray'

function linkColor(s: LinkState): SegColor {
  if (s === 'connected') return 'green'
  if (s === 'connecting' || s === 'reconnecting') return 'yellow'
  return 'red'
}

function sc2Color(s: Sc2State): SegColor {
  if (s === 'playing' || s === 'in_game') return 'green'
  if (s === 'launching') return 'yellow'
  if (s === 'crashed') return 'red'
  // idle / ended = 灰色（正常待机态）
  return 'gray'
}

function botColor(s: BotState): SegColor {
  if (s === 'running') return 'green'
  if (s === 'error') return 'red'
  return 'gray'
}

const segments = computed(() => [
  { label: t('status.phone'), color: 'green' as SegColor },  // 手机自身——能看到 UI 就是在线
  { label: t('status.server'), color: linkColor(props.status.link) },
  { label: 'SC2', color: sc2Color(props.status.sc2) },
  { label: 'Bot', color: botColor(props.status.bot) },
])

// 是否全绿（折叠状态）
const allGreen = computed(() =>
  segments.value.every(s => s.color === 'green')
)

// 颜色 → Tailwind 样式
const colorClass: Record<SegColor, string> = {
  green: 'bg-success text-surface',
  yellow: 'bg-warn text-surface',
  red: 'bg-danger text-white',
  gray: 'bg-muted text-surface',
}

const dotClass: Record<SegColor, string> = {
  green: 'bg-success',
  yellow: 'bg-warn',
  red: 'bg-danger animate-pulse',
  gray: 'bg-muted',
}
</script>

<template>
  <!-- 折叠：全绿时只显示一个绿点（stretch 模式下居中）；expanded 时永不折叠 -->
  <div
    v-if="allGreen && !expanded"
    class="flex items-center gap-1 text-xs text-success"
    :class="stretch ? 'justify-center w-full' : ''"
  >
    <span class="inline-block w-2.5 h-2.5 rounded-full bg-success"></span>
    <span>{{ t('status.ok') }}</span>
  </div>

  <!-- 展开：任一异常时显示完整四节链。
       标签文字必须显式给色（2026-06-12 用户:大厅里"手机/服务端"字隐形）——
       非灰节点之前继承父容器文字色，父容器没设 text-white 时黑底黑字看不见。 -->
  <div
    v-else
    class="flex items-center text-xs"
    :class="stretch ? 'w-full' : 'gap-0.5 flex-wrap'"
  >
    <template v-for="(seg, i) in segments" :key="seg.label">
      <!-- 节点 -->
      <div class="flex items-center gap-1 shrink-0">
        <span
          class="inline-block w-2.5 h-2.5 rounded-full"
          :class="dotClass[seg.color]"
        ></span>
        <span :class="seg.color === 'gray' ? 'text-muted' : 'text-white'">{{ seg.label }}</span>
      </div>
      <!-- 连线（最后一节后不加）；stretch 模式连线拉伸 → 四节等距分布 -->
      <span
        v-if="i < segments.length - 1"
        :class="stretch ? 'flex-1 border-t border-border mx-2' : 'text-border mx-0.5'"
      >{{ stretch ? '' : '━' }}</span>
    </template>
    <!-- 错误详情 -->
    <span v-if="status.detail" class="w-full mt-0.5 text-danger basis-full">
      {{ status.detail }}
    </span>
  </div>
</template>
