<script setup lang="ts">
// 科技 / 产能 / 兵种三行渲染（可复用）。
// `big` 控制尺寸：常规面板内联用 big=false(24px 图标)，放大 modal 用 big=true(64px 图标 + 中文名)。
// 渲染逻辑从 TechProgressPanel 抽出，两处共用，DRY。
import { computed } from 'vue'
import type {
  TechProgressItem,
  TechProgressItemLeveled,
  TechProgressItemSingle,
  TechProgressItemBuilding,
  ProductionBuildingItem,
  UnitCountItem,
} from '@/types'
import { UPGRADE_ICONS, BUILDING_ICONS, UNIT_ICONS } from '@/sc2Icons'
import { t } from '@/i18n'

const props = defineProps<{
  tech?: TechProgressItem[] | null
  production?: ProductionBuildingItem[] | null
  units?: UnitCountItem[] | null
  big?: boolean
}>()

// 尺寸 token（big 放大版 vs 常规版）。
// big 图标 2026-06-03 从 64 缩到 44：原来一行只放 3 个太占地，44 一行能放 5-6 个仍清晰。
const S = computed(() =>
  props.big
    ? { box: 44, badgeFont: 10, badgeH: 15, badgeMinW: 15, levelFont: 10, levelBox: 15, pctFont: 9, pctMinW: 18, gap: 'gap-2.5', name: 'text-[10px]', rowMb: 'mb-1' }
    : { box: 24, badgeFont: 8, badgeH: 12, badgeMinW: 12, levelFont: 8, levelBox: 8, pctFont: 7, pctMinW: 14, gap: 'gap-1', name: 'text-[8px]', rowMb: 'mb-1.5' },
)

const boxStyle = computed(() => ({ width: `${S.value.box}px`, height: `${S.value.box}px` }))

// 行标签：big 模式做成独立分区 header（占满一行,显眼）；常规模式内联前缀（省地）
const labelCls = computed(() =>
  props.big
    ? 'basis-full mb-2 self-start text-sm font-semibold text-white/85 tracking-wide'
    : 'shrink-0 mr-0.5 self-center text-[10px] uppercase tracking-wide text-muted',
)

const techItems = computed(() =>
  (props.tech ?? []).filter(
    (t) => t.status === 'done' || t.status === 'researching' || t.status === 'building',
  ),
)
const productionItems = computed(() => props.production ?? [])
const unitItems = computed(() => props.units ?? [])

function isLeveled(item: TechProgressItem): item is TechProgressItemLeveled {
  return (item as TechProgressItemLeveled).kind === 'leveled'
}
function isBuilding(item: TechProgressItem): item is TechProgressItemBuilding {
  return (item as TechProgressItemBuilding).kind === 'building'
}
function isSingle(item: TechProgressItem): item is TechProgressItemSingle {
  const kind = (item as TechProgressItemSingle).kind
  return kind === 'single' || kind === undefined
}

function itemKey(item: TechProgressItem): string {
  if (isLeveled(item)) {
    return `${item.track_en}_${item.status}_${item.level}_${item.researching_level ?? ''}`
  }
  if (isBuilding(item)) {
    return `b_${item.name_en}_${item.status}_${item.count}_${item.pending}_${item.progress}`
  }
  const s = item as TechProgressItemSingle
  return `${s.name_en}_${s.status}`
}

function upgradeIconKey(item: TechProgressItem): string {
  if (isLeveled(item)) return item.icon_en
  const s = item as TechProgressItemSingle
  return s.icon_en ?? s.name_en
}
function upgradeIcon(nameEn: string): string {
  return UPGRADE_ICONS[nameEn] ?? ''
}
function buildingIcon(nameEn: string): string {
  return BUILDING_ICONS[nameEn] ?? ''
}
function unitIcon(nameEn: string): string {
  return UNIT_ICONS[nameEn] ?? ''
}

// 产能楼挂件摘要（人族）：科N=科技实验室N个 / 双N=反应堆N个。全没挂件 → 空串（不显示标签）。
// 2026-06-17 用户：面板要能看出兵营/重工/机场是没挂件 / 挂科技 / 挂双倍。
function addonSummary(item: ProductionBuildingItem): string {
  const a = item.addons
  if (!a) return ''
  const parts: string[] = []
  if (a.techlab > 0) parts.push(`科${a.techlab}`)
  if (a.reactor > 0) parts.push(`双${a.reactor}`)
  return parts.join(' ')
}
// 产能楼 tooltip 追加挂件明细
function buildingAddonTooltip(item: ProductionBuildingItem): string {
  const a = item.addons
  if (!a) return ''
  const total = a.none + a.techlab + a.reactor
  if (total === 0) return ''
  const segs: string[] = []
  if (a.none > 0) segs.push(`${t('addon.none')} ${a.none}`)
  if (a.techlab > 0) segs.push(`${t('addon.techlab')} ${a.techlab}`)
  if (a.reactor > 0) segs.push(`${t('addon.reactor')} ${a.reactor}`)
  return `${t('addon.tooltipPrefix')}${segs.join(t('common.listSep'))}`
}

function techName(item: TechProgressItem): string {
  return (item as any).name_zh ?? ''
}

function techTooltip(item: TechProgressItem): string {
  if (isBuilding(item)) {
    const head = item.count > 0 ? `${item.name_zh} ×${item.count}` : item.name_zh
    return item.pending > 0 ? `${head} (${t('tech.tBuilding')} ${item.pending})` : head
  }
  const chrono = item.chrono ? ` (${t('tech.tChrono')})` : ''
  if (isLeveled(item)) {
    if (item.status === 'researching') {
      return `${item.name_zh} Lv${item.level}→${item.researching_level} ${t('tech.tStudying')} ${item.progress}%${chrono}`
    }
    return `${item.name_zh} Lv${item.level}${chrono}`
  }
  const s = item as TechProgressItemSingle
  if (s.status === 'researching') {
    return `${s.name_zh} (${t('tech.tStudying')} ${s.progress}%)${chrono}`
  }
  return `${s.name_zh} (${t('tech.tDone')})${chrono}`
}

function progressWidth(item: TechProgressItem): string {
  return `${Math.max(2, item.progress)}%`
}

function onImgError(e: Event) {
  const img = e.target as HTMLImageElement
  img.style.display = 'none'
  const fb = img.nextElementSibling as HTMLElement | null
  if (fb) fb.style.display = 'flex'
}
</script>

<template>
  <div>
    <!-- 科技行 -->
    <div
      v-if="techItems.length > 0"
      class="flex flex-wrap items-start"
      :class="[S.gap, big ? 'rounded-lg bg-surface-3/30 px-3 py-2.5 mb-2.5' : S.rowMb]"
      data-testid="tech-row"
    >
      <span :class="labelCls">{{ t('panel.tech') }}</span>

      <template v-for="item in techItems" :key="itemKey(item)">
        <!-- ===== leveled 分级升级 ===== -->
        <div
          v-if="isLeveled(item)"
          class="relative flex-shrink-0 flex flex-col items-center"
          :title="techTooltip(item)"
          :data-testid="`tech-item-${item.track_en}`"
        >
          <!-- 光环只包正方形图标盒(不含下方中文名),否则 big 模式被名字撑成长方形 -->
          <div class="relative" :class="{ 'chrono-glow-wrapper': item.chrono }" :style="boxStyle">
            <div v-if="item.chrono" class="chrono-glow" :data-testid="`tech-chrono-${item.track_en}`" />
            <div
              class="relative rounded overflow-hidden bg-surface-3 border border-border/60 flex items-center justify-center w-full h-full"
            >
              <img
                v-if="upgradeIcon(upgradeIconKey(item))"
                :src="upgradeIcon(upgradeIconKey(item))"
                :alt="item.name_zh"
                class="w-full h-full object-cover"
                :class="item.status === 'researching' ? 'opacity-60' : ''"
                @error="onImgError"
              />
              <span
                class="font-bold text-white/80 leading-none text-center px-0.5"
                :class="S.name"
                style="display: none"
              >{{ item.name_zh.slice(0, 4) }}</span>

              <div
                v-if="item.status === 'researching'"
                class="absolute bottom-0 left-0 h-1 bg-accent/80 rounded-b"
                :style="{ width: progressWidth(item) }"
              />
              <div
                v-if="item.status === 'researching'"
                class="absolute -top-0.5 -right-0.5 bg-accent text-white rounded-full px-0.5"
                :style="{ fontSize: `${S.pctFont}px`, lineHeight: 1.4, minWidth: `${S.pctMinW}px`, textAlign: 'center' }"
              >{{ item.progress }}%</div>
              <div
                v-if="item.level > 0"
                class="absolute -bottom-0.5 -left-0.5 rounded flex items-center justify-center"
                :style="{ width: `${S.levelBox}px`, height: `${S.levelBox}px`, background: 'rgba(0,0,0,0.72)', fontSize: `${S.levelFont}px`, color: 'white', fontWeight: 'bold', lineHeight: 1 }"
                :data-testid="`tech-level-${item.track_en}`"
              >{{ item.level }}</div>
            </div>
          </div>
          <span v-if="big" class="text-white/70 mt-0.5 text-center leading-tight w-11 line-clamp-2 break-all" :class="S.name">{{ techName(item) }}</span>
        </div>

        <!-- ===== building 关键科技建筑 ===== -->
        <div
          v-else-if="isBuilding(item)"
          class="relative flex-shrink-0 flex flex-col items-center"
          :title="techTooltip(item)"
          :data-testid="`tech-building-${item.name_en}`"
        >
          <!-- box-sized 包裹层(无 overflow-hidden):角标挂这里才不被裁。与产能建筑同款结构 -->
          <div class="relative" :style="boxStyle">
            <div
              class="relative rounded overflow-hidden bg-surface-3 border border-border/60 flex items-center justify-center w-full h-full"
            >
              <img
                v-if="buildingIcon(item.icon_en)"
                :src="buildingIcon(item.icon_en)"
                :alt="item.name_zh"
                class="w-full h-full object-cover"
                @error="onImgError"
              />
              <span
                class="font-bold text-white/80 leading-none text-center px-0.5"
                :class="S.name"
                :style="buildingIcon(item.icon_en) ? 'display: none' : ''"
              >{{ item.name_zh }}</span>

              <!-- 建造中：底部黄色进度条（最接近完工那个的进度） -->
              <div
                v-if="item.status === 'building'"
                class="absolute bottom-0 left-0 h-1 rounded-b"
                :style="{ width: progressWidth(item), backgroundColor: '#facc15' }"
              />
            </div>
            <!-- 已建成数：蓝色右上角标（与产能建筑同款，>0 才显示） -->
            <div
              v-if="item.count > 0"
              class="absolute -top-0.5 -right-0.5 px-0.5 rounded flex items-center justify-center"
              :style="{ minWidth: `${S.badgeMinW}px`, height: `${S.badgeH}px`, backgroundColor: '#38bdf8', color: '#fff', fontSize: `${S.badgeFont}px`, fontWeight: 'bold', lineHeight: 1 }"
              :data-testid="`tech-building-count-${item.name_en}`"
            >{{ item.count }}</div>
            <!-- 建造中数：黄色右下角标（>0 才显示） -->
            <div
              v-if="item.pending > 0"
              class="absolute -bottom-0.5 -right-0.5 px-0.5 rounded flex items-center justify-center"
              :style="{ minWidth: `${S.badgeMinW}px`, height: `${S.badgeH}px`, backgroundColor: '#facc15', color: '#000', fontSize: `${S.badgeFont}px`, fontWeight: 'bold', lineHeight: 1 }"
              :data-testid="`tech-building-pending-${item.name_en}`"
            >{{ item.pending }}</div>
          </div>
          <span v-if="big" class="text-white/70 mt-0.5 text-center leading-tight w-11 line-clamp-2 break-all" :class="S.name">{{ techName(item) }}</span>
        </div>

        <!-- ===== single 非分级升级 ===== -->
        <div
          v-else-if="isSingle(item)"
          class="relative flex-shrink-0 flex flex-col items-center"
          :title="techTooltip(item)"
          :data-testid="`tech-item-${(item as any).name_en}`"
        >
          <!-- 光环只包正方形图标盒(不含下方中文名),否则 big 模式被名字撑成长方形 -->
          <div class="relative" :class="{ 'chrono-glow-wrapper': item.chrono }" :style="boxStyle">
            <div v-if="item.chrono" class="chrono-glow" :data-testid="`tech-chrono-${(item as any).name_en}`" />
            <div
              class="relative rounded overflow-hidden bg-surface-3 border border-border/60 flex items-center justify-center w-full h-full"
            >
              <img
                v-if="upgradeIcon(upgradeIconKey(item))"
                :src="upgradeIcon(upgradeIconKey(item))"
                :alt="(item as any).name_zh"
                class="w-full h-full object-cover"
                :class="item.status === 'researching' ? 'opacity-60' : ''"
                @error="onImgError"
              />
              <span
                class="font-bold text-white/80 leading-none text-center px-0.5"
                :class="S.name"
                style="display: none"
              >{{ (item as any).name_zh.slice(0, 4) }}</span>

              <div
                v-if="item.status === 'researching'"
                class="absolute bottom-0 left-0 h-1 bg-accent/80 rounded-b"
                :style="{ width: progressWidth(item) }"
              />
              <div
                v-if="item.status === 'done'"
                class="absolute -top-0.5 -right-0.5 rounded-full bg-success flex items-center justify-center"
                :style="{ width: `${S.levelBox}px`, height: `${S.levelBox}px`, fontSize: `${S.levelFont}px`, color: 'white', fontWeight: 'bold', lineHeight: 1 }"
              >v</div>
              <div
                v-if="item.status === 'researching'"
                class="absolute -top-0.5 -right-0.5 bg-accent text-white rounded-full px-0.5"
                :style="{ fontSize: `${S.pctFont}px`, lineHeight: 1.4, minWidth: `${S.pctMinW}px`, textAlign: 'center' }"
              >{{ item.progress }}%</div>
            </div>
          </div>
          <span v-if="big" class="text-white/70 mt-0.5 text-center leading-tight w-11 line-clamp-2 break-all" :class="S.name">{{ techName(item) }}</span>
        </div>
      </template>
    </div>

    <!-- 产能行 -->
    <div
      v-if="productionItems.length > 0"
      class="flex flex-wrap items-start"
      :class="[S.gap, big ? 'rounded-lg bg-surface-3/30 px-3 py-2.5 mb-2.5' : { [S.rowMb]: unitItems.length > 0 }]"
      data-testid="production-row"
    >
      <span :class="labelCls">{{ t('panel.production') }}</span>
      <div
        v-for="item in productionItems"
        :key="item.building_id"
        class="relative flex-shrink-0 flex flex-col items-center"
        :title="`${item.name_zh} ×${item.count}${item.pending > 0 ? ` (${t('tech.tBuilding')} ${item.pending})` : ''}${buildingAddonTooltip(item)}`"
        :data-testid="`building-item-${item.name_en}`"
      >
        <div class="relative" :style="boxStyle">
          <div
            class="relative rounded overflow-hidden bg-surface-3 border border-border/60 flex items-center justify-center w-full h-full"
          >
            <img
              v-if="buildingIcon(item.name_en)"
              :src="buildingIcon(item.name_en)"
              :alt="item.name_zh"
              class="w-full h-full object-cover"
              :class="item.count === 0 && item.pending === 0 ? 'opacity-50' : ''"
              @error="onImgError"
            />
            <span
              class="font-bold text-white/80 leading-none text-center px-0.5"
              :class="S.name"
              style="display: none"
            >{{ item.name_zh.slice(0, 3) }}</span>
          </div>
          <div
            v-if="item.count > 0"
            class="absolute -top-0.5 -right-0.5 px-0.5 rounded flex items-center justify-center"
            :style="{ minWidth: `${S.badgeMinW}px`, height: `${S.badgeH}px`, backgroundColor: '#38bdf8', color: '#fff', fontSize: `${S.badgeFont}px`, fontWeight: 'bold', lineHeight: 1 }"
            :data-testid="`building-count-${item.name_en}`"
          >{{ item.count }}</div>
          <div
            v-if="item.pending > 0"
            class="absolute -bottom-0.5 -right-0.5 px-0.5 rounded flex items-center justify-center"
            :style="{ minWidth: `${S.badgeMinW}px`, height: `${S.badgeH}px`, backgroundColor: '#facc15', color: '#000', fontSize: `${S.badgeFont}px`, fontWeight: 'bold', lineHeight: 1 }"
            :data-testid="`building-pending-${item.name_en}`"
          >{{ item.pending }}</div>
          <!-- 挂件标签（左下角）：科N=科技 / 双N=双倍。绿底，只在有挂件时显示。 -->
          <div
            v-if="addonSummary(item)"
            class="absolute -bottom-0.5 -left-0.5 px-0.5 rounded flex items-center justify-center"
            :style="{ height: `${S.badgeH}px`, backgroundColor: '#22c55e', color: '#fff', fontSize: `${S.badgeFont}px`, fontWeight: 'bold', lineHeight: 1 }"
            :data-testid="`building-addon-${item.name_en}`"
          >{{ addonSummary(item) }}</div>
        </div>
        <span v-if="big" class="text-white/70 mt-0.5 text-center leading-tight w-11 line-clamp-2 break-all" :class="S.name">{{ item.name_zh }}</span>
      </div>
    </div>

    <!-- 兵种行 -->
    <div
      v-if="unitItems.length > 0"
      class="flex flex-wrap items-start"
      :class="[S.gap, big ? 'rounded-lg bg-surface-3/30 px-3 py-2.5' : '']"
      data-testid="units-row"
    >
      <span :class="labelCls">{{ t('panel.units') }}</span>
      <div
        v-for="item in unitItems"
        :key="item.name_en"
        class="relative flex-shrink-0 flex flex-col items-center"
        :title="`${item.name_zh} ×${item.count}${item.pending > 0 ? ` (${t('tech.tProducing')} ${item.pending})` : ''}`"
        :data-testid="`unit-item-${item.name_en}`"
      >
        <div class="relative" :style="boxStyle">
          <div
            class="relative rounded overflow-hidden bg-surface-3 border border-border/60 flex items-center justify-center w-full h-full"
          >
            <img
              v-if="unitIcon(item.name_en)"
              :src="unitIcon(item.name_en)"
              :alt="item.name_zh"
              class="w-full h-full object-cover"
              @error="onImgError"
            />
            <span
              class="font-bold text-white/80 leading-none text-center px-0.5"
              :class="S.name"
              :style="unitIcon(item.name_en) ? 'display: none' : ''"
            >{{ item.name_zh.slice(0, 3) }}</span>
          </div>
          <div
            v-if="item.count > 0"
            class="absolute -top-0.5 -right-0.5 px-0.5 rounded flex items-center justify-center"
            :style="{ minWidth: `${S.badgeMinW}px`, height: `${S.badgeH}px`, backgroundColor: '#38bdf8', color: '#fff', fontSize: `${S.badgeFont}px`, fontWeight: 'bold', lineHeight: 1 }"
            :data-testid="`unit-count-${item.name_en}`"
          >{{ item.count }}</div>
          <div
            v-if="item.pending > 0"
            class="absolute -bottom-0.5 -right-0.5 px-0.5 rounded flex items-center justify-center"
            :style="{ minWidth: `${S.badgeMinW}px`, height: `${S.badgeH}px`, backgroundColor: '#facc15', color: '#000', fontSize: `${S.badgeFont}px`, fontWeight: 'bold', lineHeight: 1 }"
            :data-testid="`unit-pending-${item.name_en}`"
          >{{ item.pending }}</div>
        </div>
        <span v-if="big" class="text-white/70 mt-0.5 text-center leading-tight w-11 line-clamp-2 break-all" :class="S.name">{{ item.name_zh }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 星空加速光环（与 TechProgressPanel 同款，big/small 共用） */
.chrono-glow-wrapper {
  position: relative;
}
.chrono-glow {
  position: absolute;
  inset: -3px;
  border-radius: 6px;
  z-index: 10;
  pointer-events: none;
  animation: chrono-spin 1.4s linear infinite;
  background: conic-gradient(
    from 0deg,
    transparent 0%,
    #7dd3fc 15%,
    #fbbf24 35%,
    #f0abfc 50%,
    #7dd3fc 65%,
    #fbbf24 80%,
    transparent 100%
  );
  mask: radial-gradient(farthest-side, transparent calc(100% - 3px), black calc(100% - 2px));
  -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 3px), black calc(100% - 2px));
  opacity: 0.9;
}
@keyframes chrono-spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}
</style>
