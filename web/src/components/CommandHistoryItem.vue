<script setup lang="ts">
// #4 历史三层展开:一条历史指令。
// 折叠态:时间 + 输入文本 + 各 directive 状态点。
// 展开态:① 输入文本 ② 识别解读(interpretation_zh) ③ 这条话产生的 directive 列表
//         + 每个的当前状态(进行中/等待激活/已完成/已手动取消/已终止...)+ 进度。
import { ref, computed } from 'vue'
import type {
  RecentCommandView,
  HistoryDirectiveStatus,
  HistoryCommandStatus,
} from '@/types'
import { t } from '@/i18n'

const props = defineProps<{ cmd: RecentCommandView }>()

const expanded = ref(false)

function fmtTs(ts: number): string {
  const m = Math.floor(ts / 60)
  const s = Math.floor(ts % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

// 配色与主界面当前指令卡(CommandCard.statusCls)一致：
// 执行中=success(绿) / 等待=amber-400(琥珀) / 已完成·未激活=muted(灰) /
// 已终止=rose-300(玫红)。
const statusMeta: Record<HistoryDirectiveStatus, { label: string; cls: string; dot: string }> = {
  active: { label: 'hist.active', cls: 'text-success', dot: 'bg-success' },
  waiting: { label: 'hist.waiting', cls: 'text-amber-400', dot: 'bg-amber-400' },
  pending: { label: 'hist.pending', cls: 'text-amber-400', dot: 'bg-amber-400' },
  completed: { label: 'hist.completed', cls: 'text-muted', dot: 'bg-muted' },
  cancelled: { label: 'hist.cancelled', cls: 'text-muted', dot: 'bg-muted' },
  terminated: { label: 'hist.terminated', cls: 'text-rose-300', dot: 'bg-rose-400' },
  ended: { label: 'hist.ended', cls: 'text-muted', dot: 'bg-muted/60' },
}

function meta(s: HistoryDirectiveStatus) {
  return statusMeta[s] ?? statusMeta.ended
}

// 整条指令聚合状态 → 左边框色 + 状态徽章色（识别失败/执行中/等待生效/已完成/已终止/已手动取消）
// 配色对齐 CommandCard.statusCls：执行中=绿 / 等待生效=琥珀 / 已完成·已手动取消=灰 /
// 已终止=玫红 / 识别失败=红(卡片无此态,历史专属)。
const CMD_META: Record<
  HistoryCommandStatus,
  { label: string; border: string; chip: string }
> = {
  failed: { label: 'histcmd.failed', border: 'border-l-danger', chip: 'bg-danger/15 text-danger' },
  active: { label: 'histcmd.active', border: 'border-l-success', chip: 'bg-success/15 text-success' },
  pending: { label: 'histcmd.pending', border: 'border-l-amber-400', chip: 'bg-amber-500/15 text-amber-400' },
  completed: { label: 'histcmd.completed', border: 'border-l-muted', chip: 'bg-muted/20 text-muted' },
  terminated: { label: 'histcmd.terminated', border: 'border-l-rose-400', chip: 'bg-rose-500/15 text-rose-300' },
  cancelled: { label: 'histcmd.cancelled', border: 'border-l-muted', chip: 'bg-muted/20 text-muted' },
}

const cmdMeta = computed(() => CMD_META[props.cmd.status] ?? CMD_META.completed)
</script>

<template>
  <div
    class="rounded-md border border-border/60 border-l-2 bg-surface-3/30"
    :class="cmdMeta.border"
    data-testid="history-item"
  >
    <!-- 折叠态 header -->
    <button
      type="button"
      class="w-full flex items-center gap-2 px-2 py-1.5 text-left hover:bg-surface-3/50 rounded-md transition-colors"
      data-testid="history-toggle"
      @click="expanded = !expanded"
    >
      <span class="shrink-0 text-[10px] text-muted/70 transition-transform" :class="expanded ? 'rotate-90' : ''">▶</span>
      <span class="shrink-0 font-mono text-[10px] text-border">{{ fmtTs(cmd.ts) }}</span>
      <span class="flex-1 min-w-0 text-xs text-white/85 truncate">{{ cmd.text }}</span>
      <!-- 折叠态：整条指令聚合状态徽章（颜色区分识别失败/执行中/等待生效/...） -->
      <span
        class="shrink-0 text-[10px] px-1.5 py-0.5 rounded leading-none"
        :class="cmdMeta.chip"
        data-testid="history-status-chip"
      >{{ t(cmdMeta.label) }}</span>
    </button>

    <!-- 展开态：三层 -->
    <div v-if="expanded" class="px-2.5 pb-2 pt-0.5 space-y-2" data-testid="history-detail">
      <!-- ② 识别解读 -->
      <div v-if="cmd.interpretation_zh" class="text-[11px]">
        <span class="text-muted uppercase tracking-wide mr-1">{{ t('history.interpretation') }}</span>
        <span class="text-white/70">{{ cmd.interpretation_zh }}</span>
      </div>

      <!-- ③ directive 卡片 + 状态 -->
      <div>
        <div class="text-[10px] text-muted uppercase tracking-wide mb-1">{{ t('history.directives') }}</div>
        <ul v-if="cmd.directives.length > 0" class="space-y-1">
          <li
            v-for="(d, idx) in cmd.directives"
            :key="`${d.id}_${idx}`"
            class="flex items-center gap-1.5 text-[11px]"
            data-testid="history-directive"
          >
            <span class="shrink-0 w-1.5 h-1.5 rounded-full inline-block" :class="meta(d.status).dot"></span>
            <span class="flex-1 min-w-0 text-white/85 truncate">{{ d.display || '—' }}</span>
            <span class="shrink-0 font-mono text-[10px] text-white/60" v-if="d.progress">
              {{ d.progress.current }}/{{ d.progress.target }} {{ d.progress.unit }}
            </span>
            <span class="shrink-0 text-[10px]" :class="meta(d.status).cls">{{ t(meta(d.status).label) }}</span>
          </li>
        </ul>
        <p v-else class="text-[10px] text-muted/70 italic">{{ t('history.noDirectives') }}</p>
      </div>
    </div>
  </div>
</template>
