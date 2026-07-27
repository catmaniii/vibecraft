<script setup lang="ts">
// 语音编队条（Task G）：横排 5 格，显示每队兵种构成（中文+数量）
// 数据来自 snapshot.voice_groups，随 snapshot 更新。
// 没有编队的组灰显占位（"—"）；整条常显——空槽位的"N队"本身就是
// "可以编队"的提醒（2026-06-05 用户）。
import { computed } from 'vue'
import type { VoiceGroupView } from '@/types'
import { t } from '@/i18n'
import { unitEntryParts } from '@/utils/unitNames'

const props = withDefaults(
  defineProps<{
    voiceGroups: readonly VoiceGroupView[]
    // 编队上限（可配置，默认 5）：决定渲染多少个槽位
    maxVoiceGroups?: number
    // 编队色（队号字符串→RGB）：已编队槽边框色 = 游戏内圆环色
    groupColors?: Record<string, [number, number, number]> | null
  }>(),
  { maxVoiceGroups: 5, groupColors: null },
)

// 某队已编队时返回它的边框色 inline style（= 游戏内圆环色），未编队/无色则返回 {}
function slotStyle(gid: number): Record<string, string> {
  if (!groupMapHas(gid)) return {}
  const c = props.groupColors?.[String(gid)]
  if (!c) return {}
  return { borderColor: `rgb(${c[0]}, ${c[1]}, ${c[2]})` }
}
function groupMapHas(gid: number): boolean {
  return groupMap.value.has(gid)
}

// 兵种名 → 显示名 + units dict → "名×数量" 列表：走共享 locale-aware 工具（utils/unitNames）。

// group_id → VoiceGroupView（用于 O(1) 查）
const groupMap = computed<Map<number, VoiceGroupView>>(() => {
  const m = new Map<number, VoiceGroupView>()
  for (const g of props.voiceGroups) {
    m.set(g.group_id, g)
  }
  return m
})

// 槽位数跟随可配置上限（1..maxVoiceGroups）
const GROUP_IDS = computed(() =>
  Array.from({ length: Math.max(1, props.maxVoiceGroups) }, (_, i) => i + 1),
)
</script>

<template>
  <div
    class="rounded-xl bg-surface-2 border border-border px-3 py-2"
    data-testid="voice-group-bar"
  >
    <div class="flex gap-2 items-stretch">
      <div
        v-for="gid in GROUP_IDS"
        :key="gid"
        class="flex-1 min-w-0 rounded-lg px-2 py-1.5 flex flex-col gap-0.5"
        :class="groupMap.has(gid)
          ? 'bg-surface-3 border-2 border-border/80'
          : 'bg-surface-3/30 border border-border/30'"
        :style="slotStyle(gid)"
        :data-testid="`voice-group-${gid}`"
      >
        <!-- 队号（已编队用队色，和边框/游戏内圆环一致） -->
        <span
          class="text-[10px] font-bold tracking-wider leading-none"
          :class="groupMap.has(gid) ? 'text-accent' : 'text-muted/40'"
          :style="slotStyle(gid).borderColor ? { color: slotStyle(gid).borderColor } : {}"
        >{{ t('group.slotLabel', { n: gid }) }}</span>

        <!-- 兵种列表或空占位 -->
        <template v-if="groupMap.has(gid) && unitEntryParts(groupMap.get(gid)!.units).length > 0">
          <span
            v-for="part in unitEntryParts(groupMap.get(gid)!.units)"
            :key="part.key"
            class="text-[10px] text-white/80 leading-tight flex items-baseline gap-0.5"
          ><span class="truncate min-w-0">{{ part.name }}</span><span class="shrink-0">×{{ part.count }}</span></span>
        </template>
        <template v-else-if="groupMap.has(gid)">
          <!-- 编队存在但单位全死了（count>0 但 units 空） -->
          <span class="text-[10px] text-muted/50 italic leading-tight">{{ t('group.emptyUnits') }}</span>
        </template>
        <template v-else>
          <!-- 未编队 -->
          <span class="text-[10px] text-muted/30 leading-tight">—</span>
        </template>
      </div>
    </div>
  </div>
</template>
