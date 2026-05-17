<script setup lang="ts">
// 单张命令卡片：
// - 框颜色按 **layer 固定不变**（L2 蓝 / L3 紫 / L4 青），跟状态解耦，
//   让玩家一眼分清"这是战术 / 单位 / 产能"
// - 每个 condition 前一颗"灯"显示当前状态（红=缺前置/绿=生产中或满足/黄=等资源/灰=未知）
// - revokable=true 时显示 × 按钮 → emit('revoke', card.id)
import { computed } from 'vue'
import type { CommandCardView } from '@/types'

const props = defineProps<{ card: CommandCardView }>()

const emit = defineEmits<{
  revoke: [id: string]
}>()

// 是否所有条件都满足（无 conditions 或全 met=true）
const allConditionsMet = computed(() => {
  const cs = props.card.conditions
  if (!cs || cs.length === 0) return true
  return cs.every(c => c.met)
})

const statusLabel = computed(() => {
  if (props.card.status === 'done') return '已完成'
  if (props.card.status === 'pending') return '等待生效'
  if (props.card.status === 'on_hold') return '已暂停'
  // status === 'active'
  return allConditionsMet.value ? '执行中' : '等待条件'
})

// 框样式：**layer 决定颜色，固定不变**（done 单独半透明，pending 灰一点）
const cardCls = computed(() => {
  if (props.card.status === 'done') return 'bg-surface-3/40 border-border/50 opacity-60'
  if (props.card.status === 'pending') return 'bg-muted/10 border-border/60'
  switch (props.card.layer) {
    case 'L2': return 'bg-blue-500/10 border-blue-500/50'    // L2 战术 - 蓝
    case 'L3': return 'bg-purple-500/10 border-purple-500/50'  // L3 单位 - 紫
    case 'L4': return 'bg-cyan-500/10 border-cyan-500/50'    // L4 产能 - 青
    default:   return 'bg-surface-3 border-border'
  }
})

// layer 标签颜色（跟框同色系，加深）
const layerTagCls = computed(() => {
  switch (props.card.layer) {
    case 'L2': return 'bg-blue-500/25 text-blue-200 border-blue-500/50'
    case 'L3': return 'bg-purple-500/25 text-purple-200 border-purple-500/50'
    case 'L4': return 'bg-cyan-500/25 text-cyan-200 border-cyan-500/50'
    default:   return 'bg-surface-3/60 text-muted border-border/60'
  }
})

// status label 颜色（保留状态语义提示）
const statusCls = computed(() => {
  if (props.card.status === 'done') return 'text-muted'
  if (props.card.status === 'on_hold') return 'text-warn'
  // active
  return allConditionsMet.value ? 'text-success' : 'text-amber-400'
})

// 进度文本（兼顾计数和倒计时）："2/4 个" / "12/30 秒"
function fmtProgress(c: { current: number; target: number; unit: string }): string {
  return `${c.current}/${c.target} ${c.unit}`
}

// 状态灯：每条 condition 前一颗圆点
// 优先级：state (per-item) > met (counter 进度)
function lightCls(cond: { met: boolean; state?: string }): string {
  if (cond.state) {
    switch (cond.state) {
      case 'blocked':   return 'bg-danger shadow-[0_0_4px] shadow-danger/60'           // 红 缺前置
      case 'waiting':   return 'bg-amber-400 shadow-[0_0_4px] shadow-amber-400/60'    // 黄 等资源
      case 'producing': return 'bg-success shadow-[0_0_4px] shadow-success/60'         // 绿 生产中
      case 'done':      return 'bg-success/70'                                          // 浅绿 完成
      default:          return 'bg-muted'
    }
  }
  // 无 state 字段（time_elapsed_since / tech_done 等），用 met 决定
  return cond.met ? 'bg-success shadow-[0_0_4px] shadow-success/60' : 'bg-muted/60'
}

// 状态文字（鼠标 hover title，可访问性）
function lightTitle(cond: { met: boolean; state?: string; state_reason?: string }): string {
  if (cond.state) {
    const stateMap: Record<string, string> = {
      blocked: '缺前置',
      waiting: '等资源',
      producing: '生产中',
      done: '已完成',
    }
    const label = stateMap[cond.state] ?? cond.state
    return cond.state_reason ? `${label}：${cond.state_reason}` : label
  }
  return cond.met ? '已满足' : '未满足'
}
</script>

<template>
  <div
    class="rounded-md border px-2.5 py-2 transition-opacity"
    :class="cardCls"
  >
    <!-- 标题行：layer tag + display + 撤销按钮 -->
    <div class="flex items-center gap-2">
      <span
        class="shrink-0 font-mono text-[10px] px-1.5 py-0.5 rounded border leading-none"
        :class="layerTagCls"
      >{{ card.layer }}</span>
      <span class="flex-1 min-w-0 text-sm font-medium text-white/90 truncate">
        {{ card.display }}
      </span>
      <button
        v-if="card.revokable && card.status !== 'done'"
        type="button"
        data-testid="revoke-btn"
        class="shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted hover:text-danger hover:bg-danger/10 transition-colors text-xs leading-none"
        :aria-label="`取消指令: ${card.display}`"
        @click="emit('revoke', card.id)"
      >×</button>
    </div>

    <!-- status 行：状态 label + 可选原因 -->
    <div class="flex items-center gap-1 mt-0.5">
      <span class="text-[11px] font-semibold" :class="statusCls">{{ statusLabel }}</span>
      <span v-if="card.status_reason" class="text-[11px] text-muted/80">— {{ card.status_reason }}</span>
    </div>

    <!-- 条件清单（有 done_when 时显示） -->
    <ul
      v-if="card.conditions && card.conditions.length > 0"
      class="mt-1.5 space-y-1"
    >
      <li
        v-for="(cond, idx) in card.conditions"
        :key="idx"
        class="text-[11px] text-white/85"
      >
        <!-- 主行：[●灯] 条件文字 + 进度数字 -->
        <div class="flex items-center gap-1.5">
          <span
            class="shrink-0 w-2 h-2 rounded-full inline-block"
            :class="lightCls(cond)"
            :title="lightTitle(cond)"
          ></span>
          <span class="flex-1 min-w-0">{{ cond.text }}</span>
          <span v-if="cond.progress" class="font-mono text-[10px] text-white/70 shrink-0">
            {{ fmtProgress(cond.progress) }}
          </span>
        </div>
        <!-- 副行：state_reason（"需要 Cybernetics Core" / "资源不足" / "队列 1 等出"） -->
        <div
          v-if="cond.state_reason"
          class="pl-3.5 text-[10px] text-white/55"
        >{{ cond.state_reason }}</div>
      </li>
    </ul>
  </div>
</template>
