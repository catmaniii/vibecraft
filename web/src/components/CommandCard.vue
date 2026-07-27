<script setup lang="ts">
// 单张命令卡片：
// - 框颜色按 **layer 固定不变**（L2 蓝 / L3 紫 / L4 青），跟状态解耦，
//   让玩家一眼分清"这是战术 / 单位 / 产能"
// - 每个 condition 前一颗"灯"显示当前状态（红=缺前置/绿=生产中或满足/黄=等资源/灰=未知）
// - revokable=true 时显示 × 按钮 → emit('revoke', card.id)
import { computed } from 'vue'
import type { CommandCardView } from '@/types'
import { t } from '@/i18n'

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

// 2026-05-28 用户:B 类 squad 单位全死 → 后端 _release_directive_done(reason='units_lost')
// → status_reason='units_lost' → 卡片切"单位全失"暗红文案,沿用 done 的 2s grace 后消失
const isUnitsLost = computed(
  () => props.card.status === 'done' && props.card.status_reason === 'units_lost',
)

const statusLabel = computed(() => {
  if (isUnitsLost.value) return '单位全失'
  if (props.card.status === 'done') return '已完成'
  if (props.card.status === 'waiting') return '未激活'
  if (props.card.status === 'pending') return '等待生效'
  if (props.card.status === 'on_hold') return '已暂停'
  // status === 'active'
  return allConditionsMet.value ? '执行中' : '等待条件'
})

// 框样式：**layer 决定颜色，固定不变**（done 半透明，waiting=未激活灰显，pending 灰一点）
const cardCls = computed(() => {
  if (isUnitsLost.value) return 'bg-rose-900/30 border-rose-700/50 opacity-70'
  if (props.card.status === 'done') return 'bg-surface-3/40 border-border/50 opacity-60'
  // waiting：activate_when 未满足 → 明显灰显 + 暗淡，表示"不活跃"（2026-06-02 用户）
  if (props.card.status === 'waiting') return 'bg-surface-3/30 border-border/40 opacity-50 grayscale'
  if (props.card.status === 'pending') return 'bg-muted/10 border-border/60'
  switch (props.card.layer) {
    case 'L2': return 'bg-blue-500/10 border-blue-500/50'      // L2 战术 - 蓝
    case 'L3': return 'bg-purple-500/10 border-purple-500/50'  // L3 单位 - 紫
    case 'L4': return 'bg-orange-500/10 border-orange-500/50'  // L4 产能 - 橙
    default:   return 'bg-surface-3 border-border'
  }
})

// layer 标签颜色（跟框同色系，加深）
const layerTagCls = computed(() => {
  switch (props.card.layer) {
    case 'L2': return 'bg-blue-500/25 text-blue-200 border-blue-500/50'
    case 'L3': return 'bg-purple-500/25 text-purple-200 border-purple-500/50'
    case 'L4': return 'bg-orange-500/25 text-orange-200 border-orange-500/50'
    default:   return 'bg-surface-3/60 text-muted border-border/60'
  }
})

// 2026-05-24 用户:layer 标签改中文(原 L2/L3/L4 不直观)
const layerLabel = computed(() => {
  switch (props.card.layer) {
    case 'L2': return '战术'
    case 'L3': return '持久'
    case 'L4': return '产能'
    default:   return props.card.layer
  }
})

// status label 颜色（保留状态语义提示）
const statusCls = computed(() => {
  if (isUnitsLost.value) return 'text-rose-300'
  if (props.card.status === 'done') return 'text-muted'
  if (props.card.status === 'waiting') return 'text-muted'
  if (props.card.status === 'on_hold') return 'text-warn'
  // active
  return allConditionsMet.value ? 'text-success' : 'text-amber-400'
})

// 进度文本（兼顾计数和倒计时）："2/4 个" / "12/30 秒"
function fmtProgress(c: { current: number; target: number; unit: string }): string {
  return `${c.current}/${c.target} ${c.unit}`
}

// 2026-06-07 用户:卡片压成三行(激活条件 / 完成条件 / 进展 各一行),不再每条一行 UL。
// 多条 condition 合并成一行文字(、分隔)+一颗聚合灯。
function joinText(conds?: { text: string }[]): string {
  if (!conds || conds.length === 0) return ''
  return conds.map((c) => c.text).join('、')
}

// 聚合灯:多条 condition 取"最该提醒"的状态显示一颗(红>黄>绿生产>绿完成>灰)。
// 返回一个合成 condition 给 lightCls/lightTitle 复用。
function aggCond(
  conds?: { met: boolean; state?: string; state_reason?: string }[],
): { met: boolean; state?: string } | null {
  if (!conds || conds.length === 0) return null
  if (conds.some((c) => c.state === 'blocked')) return { met: false, state: 'blocked' }
  if (conds.some((c) => c.state === 'waiting')) return { met: false, state: 'waiting' }
  const allMet = conds.every((c) => c.met)
  if (conds.some((c) => c.state === 'producing')) return { met: allMet, state: 'producing' }
  if (allMet) return { met: true }
  return { met: false }
}

// 激活条件:合并文字 + 聚合灯
const prereqText = computed(() => joinText(props.card.prerequisites))
const prereqAgg = computed(() => aggCond(props.card.prerequisites))
// 完成条件:合并文字 + 聚合灯
const doneText = computed(() => joinText(props.card.conditions))
const doneAgg = computed(() => aggCond(props.card.conditions))
// 进展:把有 progress 的 condition 数字串起来(state_reason 兜底),无则空串隐藏整行
const progressText = computed(() => {
  const cs = props.card.conditions
  if (!cs || cs.length === 0) return ''
  const parts: string[] = []
  for (const c of cs) {
    if (c.progress) parts.push(fmtProgress(c.progress))
    else if (c.state_reason) parts.push(c.state_reason)
  }
  return parts.join('、')
})

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
        class="shrink-0 text-[10px] px-1.5 py-0.5 rounded border leading-none"
        :class="layerTagCls"
      >{{ layerLabel }}</span>
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
      <!-- units_lost 是 token,statusLabel 已显示"单位全失",副文本隐藏避免 token 露 -->
      <span v-if="card.status_reason && !isUnitsLost" class="text-[11px] text-muted/80">— {{ card.status_reason }}</span>
    </div>

    <!-- 偷矿农民数（type=stealth_mine 时）：采矿 N 采气 N -->
    <div
      v-if="card.stealth_workers != null"
      class="flex items-center gap-2 mt-0.5 text-[11px]"
      data-testid="stealth-workers"
    >
      <span class="shrink-0 text-[10px] text-muted">{{ t('card.workers') }}</span>
      <span class="text-white/80">{{ t('card.mining') }} {{ card.stealth_workers.mineral }}</span>
      <span v-if="card.stealth_workers.gas > 0" class="text-teal-400">{{ t('card.gas') }} {{ card.stealth_workers.gas }}</span>
    </div>

    <!-- 紧凑三行：激活条件 / 完成条件 / 进展 各一行（2026-06-07 用户:压缩卡高） -->
    <div
      v-if="prereqText || doneText || progressText"
      class="mt-1 space-y-0.5"
    >
      <!-- 激活条件（有 activate_when 时）：标签 + 聚合灯 + 合并文字一行 -->
      <div
        v-if="prereqText"
        class="flex items-center gap-1.5 text-[11px]"
        data-testid="card-prerequisites"
      >
        <span class="shrink-0 w-12 text-[10px] text-muted">{{ t('card.prerequisites') }}</span>
        <span
          v-if="prereqAgg"
          class="shrink-0 w-2 h-2 rounded-full inline-block"
          :class="lightCls(prereqAgg)"
          :title="lightTitle(prereqAgg)"
        ></span>
        <span class="flex-1 min-w-0 truncate text-white/85" :title="prereqText">{{ prereqText }}</span>
      </div>

      <!-- 完成条件（有 done_when 时）：标签 + 聚合灯 + 合并文字一行 -->
      <div
        v-if="doneText"
        class="flex items-center gap-1.5 text-[11px]"
        data-testid="card-conditions"
      >
        <span class="shrink-0 w-12 text-[10px] text-muted">{{ t('card.conditions') }}</span>
        <span
          v-if="doneAgg"
          class="shrink-0 w-2 h-2 rounded-full inline-block"
          :class="lightCls(doneAgg)"
          :title="lightTitle(doneAgg)"
        ></span>
        <span class="flex-1 min-w-0 truncate text-white/85" :title="doneText">{{ doneText }}</span>
      </div>

      <!-- 进展（有计数/倒计时进度时）：标签 + 合并进度数字一行 -->
      <div
        v-if="progressText"
        class="flex items-center gap-1.5 text-[11px]"
        data-testid="card-progress"
      >
        <span class="shrink-0 w-12 text-[10px] text-muted">{{ t('card.progress') }}</span>
        <span class="flex-1 min-w-0 truncate font-mono text-[10px] text-white/70" :title="progressText">{{ progressText }}</span>
      </div>
    </div>
  </div>
</template>
