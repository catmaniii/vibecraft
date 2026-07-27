<script setup lang="ts">
// 科技 / 产能 / 兵种跟踪 panel（薄壳）。
// - 三行渲染委托给 <TechRows>（常规小尺寸内联）。
// - 右上角放大按钮 → teleport 全屏 modal，用 <TechRows :big> 渲染大图标版（更清晰）。
// 只显示已有数据行；三行均无数据时整个 panel 隐藏。
import { computed, ref } from 'vue'
import type {
  TechProgressItem,
  ProductionBuildingItem,
  UnitCountItem,
  ControlledUnitsView,
} from '@/types'
import TechRows from './TechRows.vue'
import UpgradeTargetPanel from './UpgradeTargetPanel.vue'
import { unitEntries } from '@/utils/unitNames'
import { t } from '@/i18n'

const props = defineProps<{
  tech?: TechProgressItem[] | null
  production?: ProductionBuildingItem[] | null
  units?: UnitCountItem[] | null
  // 控制归属（只在放大 modal 底部展示：我控制 vs bot 自由）
  controlledUnits?: ControlledUnitsView | null
}>()

// 攻防升级目标控件（UpgradeTargetPanel）只读转发上抛，走同一条 macroAction 通道
// （CockpitView -> App.vue sendMacroAction，dim="upgrade_target"）。
const emit = defineEmits<{
  macroAction: [dim: string, value: { family: string; level: number | 'auto' }]
}>()

const zoomed = ref(false)

// 色键 → CSS rgb（对齐后端 _CONTROL_COLORS：cyan=普通指令 / g1-g5=编队）
const CTRL_COLOR_MAP: Record<string, string> = {
  cyan: 'rgb(0, 220, 255)',
  g1: 'rgb(255, 230, 0)',
  g2: 'rgb(255, 140, 0)',
  g3: 'rgb(255, 0, 200)',
  g4: 'rgb(150, 90, 255)',
  g5: 'rgb(0, 255, 120)',
}
function ctrlDot(colorKey: string): string {
  return CTRL_COLOR_MAP[colorKey] ?? 'rgb(128, 128, 128)'
}

// 控制归属：有受控编队/指令 或 有 bot 自由单位时才展示
const controlSummary = computed(() => {
  const cu = props.controlledUnits
  if (!cu) return null
  if (!cu.controlled.length && !cu.bot_free.count) return null
  return cu
})
// 我控制的单位总数（各受控组之和）
const myControlledTotal = computed(() =>
  (props.controlledUnits?.controlled ?? []).reduce((sum, g) => sum + (g.count ?? 0), 0),
)

const techItems = computed(() =>
  (props.tech ?? []).filter(
    (t) => t.status === 'done' || t.status === 'researching' || t.status === 'building',
  ),
)
const productionItems = computed(() => props.production ?? [])
const unitItems = computed(() => props.units ?? [])

const hasAny = computed(
  () =>
    techItems.value.length > 0 ||
    productionItems.value.length > 0 ||
    unitItems.value.length > 0,
)
</script>

<template>
  <div
    v-if="hasAny"
    class="relative rounded-xl bg-surface-2 border border-border px-3 py-2"
    data-testid="tech-progress-panel"
  >
    <!-- 放大按钮（右上角） -->
    <button
      type="button"
      class="absolute top-1 right-1 z-10 w-5 h-5 flex items-center justify-center rounded text-muted hover:text-white hover:bg-surface-3/80 transition-colors"
      data-testid="tech-zoom-btn"
      :aria-label="t('panel.zoomAria')"
      :title="t('panel.zoomTip')"
      @click="zoomed = true"
    >
      <!-- 放大镜 SVG -->
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="w-3.5 h-3.5">
        <circle cx="11" cy="11" r="7" />
        <line x1="16.5" y1="16.5" x2="21" y2="21" stroke-linecap="round" />
        <line x1="11" y1="8" x2="11" y2="14" stroke-linecap="round" />
        <line x1="8" y1="11" x2="14" y2="11" stroke-linecap="round" />
      </svg>
    </button>

    <!-- 常规内联（小尺寸），右上留出按钮空间 -->
    <div class="pr-5">
      <TechRows :tech="tech" :production="production" :units="units" :big="false" />
    </div>
  </div>

  <!-- 放大 modal -->
  <Teleport to="body">
    <div
      v-if="zoomed"
      class="fixed inset-0 z-[200] flex items-center justify-center bg-black/70 p-4 overscroll-contain"
      data-testid="tech-zoom-modal"
      @click.self="zoomed = false"
      @touchmove.self.prevent
      @wheel.self.prevent
    >
      <div class="relative w-full max-w-lg max-h-[88vh] flex flex-col overflow-hidden rounded-2xl bg-surface-2 border border-border shadow-2xl">
        <!-- 固定头部 -->
        <div class="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
          <span class="text-sm font-semibold text-white/90">{{ t('panel.zoomTitle') }}</span>
          <button
            type="button"
            class="w-7 h-7 flex items-center justify-center rounded text-muted hover:text-danger hover:bg-danger/10 transition-colors text-xl leading-none"
            data-testid="tech-zoom-close"
            aria-label="关闭放大"
            @click="zoomed = false"
          >×</button>
        </div>
        <!-- 可滚动正文：三分区卡片（科技 / 产能 / 兵种）。overscroll-contain:
             滚到边界不外溢到背后主页面（避免 scroll chaining）。 -->
        <div class="overflow-y-auto overscroll-contain px-3 py-3">
          <TechRows :tech="tech" :production="production" :units="units" :big="true" />

          <!-- 攻防升级目标（15 族攻防升级线，只在放大 modal 显示，紧凑 icon 行放不下 5 chips） -->
          <UpgradeTargetPanel
            class="mb-2.5"
            :tech="tech"
            @macro-action="(dim, val) => emit('macroAction', dim, val)"
          />

          <!-- 控制归属：兵种下方，我控制 vs bot 自由（只在放大 modal 显示） -->
          <div
            v-if="controlSummary"
            class="mt-2.5 rounded-lg bg-surface-3/30 px-3 py-2.5"
            data-testid="ctrl-attribution"
          >
            <p class="text-sm font-semibold text-white/85 tracking-wide mb-2">{{ t('tech.controlAttribution') }}</p>
            <div class="flex flex-col gap-1.5 text-xs">
              <!-- 我控制：每个受控编队/指令一行 -->
              <div
                v-for="g in controlSummary.controlled"
                :key="g.directive_id"
                class="flex items-start gap-2"
              >
                <span
                  class="mt-0.5 shrink-0 w-2 h-2 rounded-full"
                  :style="{ background: ctrlDot(g.color) }"
                />
                <span class="min-w-0">
                  <span class="text-white/90 font-medium">{{ g.label || g.directive_id }}</span>
                  <span class="text-white/40 mx-1">·</span>
                  <span class="text-white/70">{{ g.count }}</span>
                  <span
                    v-if="unitEntries(g.composition).length"
                    class="text-white/50 ml-1"
                  >{{ unitEntries(g.composition).join(' ') }}</span>
                </span>
              </div>
              <!-- bot 自由调度 -->
              <div
                v-if="controlSummary.bot_free.count > 0"
                class="flex items-start gap-2 text-white/45"
              >
                <span class="mt-0.5 shrink-0 w-2 h-2 rounded-full bg-white/20" />
                <span class="min-w-0">
                  <span>{{ t('tech.botFree') }} · {{ controlSummary.bot_free.count }}</span>
                  <span
                    v-if="unitEntries(controlSummary.bot_free.composition).length"
                    class="ml-1"
                  >{{ unitEntries(controlSummary.bot_free.composition).join(' ') }}</span>
                </span>
              </div>
              <!-- 合计 -->
              <div class="text-white/45 pt-1 mt-0.5 border-t border-border/50">
                {{ t('tech.myControl') }} <span class="text-white/80 font-semibold">{{ myControlledTotal }}</span>
                ·
                {{ t('tech.botControl') }} <span class="text-white/80 font-semibold">{{ controlSummary.bot_free.count }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>
