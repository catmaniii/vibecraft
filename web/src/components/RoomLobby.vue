<script setup lang="ts">
// 多人联网 lobby 视图：SC2 经典大厅风格
// 帧契约（下行 room_state / 上行 lobby_* 系列）见 src/vibecraft/server/room.py + web/src/types.ts
import { ref, computed } from 'vue'
import StatusChain from '@/components/StatusChain.vue'
import QrShareButton from '@/components/QrShareButton.vue'
import type { RoomStateFrame, RoomSlot, SystemStatus } from '@/types'
import { t } from '@/i18n'

const props = defineProps<{
  roomState: RoomStateFrame
  myPlayerId: string
  roomError: string | null
  /** 系统状态链（手机/服务器/SC2/bot，复用自原 LaunchView，2026-06-12 用户）。可选保测试兼容。 */
  status?: SystemStatus
}>()

const emit = defineEmits<{
  /** 上行 lobby 帧（lobby_set_race / lobby_ready / lobby_start / ... 等全部走此通道）。 */
  lobby: [frame: object]
  /** 玩家主动退出房间：App.vue 负责发 lobby_leave + 断 WS + 回入口页。 */
  leave: []
}>()

// ---- 种族 / 难度选项（computed 以便切换 locale 时即时更新标签） ----

const RACE_OPTIONS = computed(() => [
  { value: 'Random', label: t('lobby.raceRandom') },
  { value: 'Protoss', label: t('lobby.raceProtoss') },
  { value: 'Terran', label: t('lobby.raceTerran') },
  { value: 'Zerg', label: t('lobby.raceZerg') },
])

const DIFFICULTY_OPTIONS = computed(() => [
  { value: 'VeryEasy', label: t('lobby.diffVeryEasy') },
  { value: 'Easy', label: t('lobby.diffEasy') },
  { value: 'Medium', label: t('lobby.diffMedium') },
  { value: 'MediumHard', label: t('lobby.diffMediumHard') },
  { value: 'Hard', label: t('lobby.diffHard') },
  { value: 'Harder', label: t('lobby.diffHarder') },
  { value: 'VeryHard', label: t('lobby.diffVeryHard') },
  { value: 'CheatVision', label: t('lobby.diffCheatVision') },
  { value: 'CheatMoney', label: t('lobby.diffCheatMoney') },
  { value: 'CheatInsane', label: t('lobby.diffCheatInsane') },
])

// ---- 派生状态 ----

const isHost = computed(() => props.roomState.host_player_id === props.myPlayerId)
const mySlot = computed(() =>
  props.roomState.slots.find(s => s.kind === 'bot' && s.player_id === props.myPlayerId) ?? null
)
const botSlots = computed(() => props.roomState.slots.filter(s => s.kind === 'bot'))
const filledSlots = computed(() =>
  props.roomState.slots.filter(s => s.kind === 'bot' || s.kind === 'computer')
)
// #3: allHumansReady 只看非房主真人，房主本身不参与准备逻辑
const nonHostBots = computed(() =>
  botSlots.value.filter(s => s.player_id !== props.roomState.host_player_id)
)
const allHumansReady = computed(() => nonHostBots.value.every(s => s.ready))
const canStart = computed(
  () => isHost.value && allHumansReady.value && filledSlots.value.length >= 2
)
// 引擎限制（spike 实测）：双真人局不支持电脑
const canAddComputer = computed(() => botSlots.value.length < 2)

const startingCount = computed(() => botSlots.value.length)

// ---- 空位行内 [+ 电脑] 展开状态 ----

// 一次只展开一个空位行；null = 全收起
const expandedOpenSlotIndex = ref<number | null>(null)
const newComputerRace = ref('Random')
const newComputerDifficulty = ref('VeryHard')

// ---- 上行帧发送 ----

function setRace(race: string) {
  emit('lobby', { type: 'lobby_set_race', race })
}

function toggleReady() {
  const slot = mySlot.value
  if (!slot) return
  emit('lobby', { type: 'lobby_ready', ready: !slot.ready })
}

function toggleOpenSlotExpand(slotIndex: number) {
  if (expandedOpenSlotIndex.value === slotIndex) {
    expandedOpenSlotIndex.value = null
  } else {
    expandedOpenSlotIndex.value = slotIndex
    newComputerRace.value = 'Random'
    newComputerDifficulty.value = 'VeryHard'
  }
}

function addComputerAtSlot(slotIndex: number) {
  emit('lobby', {
    type: 'lobby_add_computer',
    race: newComputerRace.value,
    difficulty: newComputerDifficulty.value,
    index: slotIndex,
  })
  expandedOpenSlotIndex.value = null
}

function removeSlot(index: number) {
  emit('lobby', { type: 'lobby_remove_slot', index })
}

function startGame() {
  emit('lobby', { type: 'lobby_start' })
}

// setRealtime 保留供 selftest 脚本使用，不出 UI
function setRealtime(realtime: boolean) {
  emit('lobby', { type: 'lobby_set_realtime', realtime })
}

// 点击空位换到该位（自己已在房间时有效）
function takeSlot(index: number) {
  emit('lobby', { type: 'lobby_take_slot', index })
}

// ---- 工具函数 ----

function isMyRow(slot: RoomSlot): boolean {
  return slot.kind === 'bot' && slot.player_id === props.myPlayerId
}

// 电脑难度标签本地化：复用难度选项表的 label（找不到则回退原始值）
function diffLabel(value: string): string {
  return DIFFICULTY_OPTIONS.value.find(o => o.value === value)?.label ?? value
}

// 行填充色 = 准备状态（2026-06-12 用户）：
// 青色填充 = 房主(点开始即就绪)/已准备玩家/电脑(天然就绪)；灰 = 未准备。
// 自己那行额外加粗青边框（填充仍按准备状态走）。
function rowFillClass(slot: RoomSlot): string {
  const isReadyLike =
    slot.kind === 'computer' ||
    (slot.kind === 'bot' &&
      (slot.ready || slot.player_id === props.roomState.host_player_id))
  const fill = isReadyLike ? 'bg-cyan-400/15' : 'bg-surface-2'
  const border = isMyRow(slot)
    ? 'border-2 border-cyan-400/70'
    : isReadyLike
      ? 'border border-cyan-500/40'
      : 'border border-border'
  return `${fill} ${border}`
}

// 暴露给测试调用（vitest expose）
defineExpose({ setRealtime })
</script>

<template>
  <!-- state=starting：全屏进度遮罩 -->
  <div
    v-if="roomState.state === 'starting'"
    class="fixed inset-0 bg-black/80 flex items-center justify-center z-50"
    data-testid="starting-overlay"
  >
    <div class="text-center px-6">
      <p class="text-white text-lg font-bold">{{ t('lobby.startingInstances', { n: startingCount }) }}</p>
      <p class="text-muted text-sm mt-2">{{ t('lobby.startingWait') }}</p>
    </div>
  </div>

  <!-- 主 lobby 界面 -->
  <div class="min-h-screen bg-surface flex flex-col px-4 py-5">

    <!-- 顶部：标题行（标题 | 退出按钮）→ 状态链（标题正下方，2026-06-12 用户）→ 地图 -->
    <div class="mb-5">
      <div class="flex items-center justify-between gap-2">
        <p class="text-xl font-bold text-accent truncate min-w-0">{{ t('lobby.title') }}</p>
        <div class="flex items-center gap-2 shrink-0">
          <!-- 分享二维码：弹出当前首页 URL 的二维码，邀请别人扫码进来 -->
          <QrShareButton size="sm" />
          <!-- 退出房间 -->
          <button
            type="button"
            class="text-xs px-3 py-1.5 rounded border border-border/60 text-muted hover:border-danger/50 hover:text-danger transition-colors shrink-0"
            @click="emit('leave')"
            data-testid="leave-room-btn"
          >
            {{ t('lobby.leave') }}
          </button>
        </div>
      </div>
      <!-- 系统状态链（手机/服务器/SC2/bot，复用自原开局页；等距分布居中） -->
      <div v-if="status" class="mt-2">
        <StatusChain :status="status" stretch expanded />
      </div>
      <p class="text-xs text-muted mt-2">{{ t('lobby.mapLabel') }}：{{ roomState.map }}</p>
      <p v-if="roomState.match_id" class="text-xs font-mono text-muted mt-0.5">
        {{ t('lobby.matchIdLabel') }}：{{ roomState.match_id }}
      </p>
    </div>

    <!-- 错误提示（room_error 帧） -->
    <div
      v-if="roomError"
      class="mb-3 px-3 py-2 rounded border bg-danger/10 border-danger/40 text-xs text-danger"
      data-testid="room-error"
    >
      {{ roomError }}
    </div>

    <!-- slot 列表 -->
    <div class="flex flex-col gap-1.5 mb-5">
      <template v-for="slot in roomState.slots" :key="slot.index">

        <!-- 空位行：可点击换位 + 房主行内 [+ 电脑] -->
        <div
          v-if="slot.kind === 'open'"
          class="flex flex-col rounded-lg border transition-colors"
          :class="[
            'border-border/40 bg-surface-2/50',
            mySlot ? 'cursor-pointer hover:border-accent/40 hover:bg-surface-2/70 hover:opacity-80' : 'opacity-50',
          ]"
          @click="mySlot && takeSlot(slot.index)"
          data-testid="open-slot-row"
        >
          <div class="flex items-center gap-2 px-3 py-2.5">
            <span class="w-2 h-2 rounded-full shrink-0 bg-border" />
            <span class="text-sm text-muted italic flex-1 min-w-0 truncate">
              {{ t('lobby.openSlot') }}
              <span v-if="mySlot" class="text-[10px] text-accent/60 ml-1">{{ t('lobby.takeSeat') }}</span>
            </span>
            <!-- 房主且可加电脑：[+ 电脑] 按钮（行内展开） -->
            <button
              v-if="isHost && canAddComputer"
              type="button"
              class="text-xs px-2 py-0.5 rounded border border-border/60 text-muted hover:border-accent/50 hover:text-white transition-colors shrink-0"
              @click.stop="toggleOpenSlotExpand(slot.index)"
              data-testid="add-computer-btn"
            >
              {{ expandedOpenSlotIndex === slot.index ? t('lobby.collapseComputer') : t('lobby.addComputer') }}
            </button>
          </div>

          <!-- 展开：种族 + 难度 + 确认 -->
          <div
            v-if="isHost && expandedOpenSlotIndex === slot.index"
            class="px-3 pb-3 flex flex-col gap-2"
            @click.stop
            data-testid="add-computer-form"
          >
            <div class="flex gap-2">
              <div class="flex-1">
                <p class="text-[10px] text-muted mb-1">{{ t('lobby.raceLabel') }}</p>
                <select
                  v-model="newComputerRace"
                  class="w-full bg-surface border border-border rounded px-2 py-1.5 text-xs text-white focus:outline-none"
                >
                  <option v-for="r in RACE_OPTIONS" :key="r.value" :value="r.value">{{ r.label }}</option>
                </select>
              </div>
              <div class="flex-1">
                <p class="text-[10px] text-muted mb-1">{{ t('lobby.difficultyLabel') }}</p>
                <select
                  v-model="newComputerDifficulty"
                  class="w-full bg-surface border border-border rounded px-2 py-1.5 text-xs text-white focus:outline-none"
                >
                  <option v-for="d in DIFFICULTY_OPTIONS" :key="d.value" :value="d.value">{{ d.label }}</option>
                </select>
              </div>
            </div>
            <button
              type="button"
              class="w-full py-1.5 rounded bg-surface-3 border border-border text-xs text-white hover:border-accent/50 transition-colors"
              @click.stop="addComputerAtSlot(slot.index)"
              data-testid="add-computer-confirm"
            >
              {{ t('lobby.addComputerConfirm') }}
            </button>
          </div>
        </div>

        <!-- 非空位行（bot / computer / closed）。
             布局（2026-06-12 用户）：[准备按钮(自己,最左)][房主徽标(名前)] 名字 …… [×移除][种族(最右)]
             填充色=准备状态：青色=房主(视为已准备)/已准备/电脑；灰=未准备。
             不再显示"已准备/未准备"文字（按钮状态 + 填充色已表达）。 -->
        <div
          v-else
          class="flex items-center gap-2 px-3 py-2.5 rounded-lg transition-colors"
          :class="rowFillClass(slot)"
        >
          <!-- 准备按钮（最左；2026-06-13 用户）：文字款"已准备/未准备"，与房主徽标
               等宽对齐(w-16)。自己的可点击；**自己未准备 = 红色高亮**（2026-06-13
               用户：灰色看不清，要突出提醒"该点准备了"）；别人的只读（变暗、
               不可点），状态互相可见。 -->
          <button
            v-if="slot.kind === 'bot' && slot.player_id !== roomState.host_player_id"
            type="button"
            class="w-16 py-1 rounded text-xs font-semibold text-center transition-colors shrink-0 whitespace-nowrap"
            :class="[
              slot.ready
                ? (isMyRow(slot)
                    ? 'bg-green-500 text-white'
                    : 'bg-green-500/40 text-white/80')
                : (isMyRow(slot)
                    ? 'bg-danger/25 border border-danger text-danger hover:bg-danger/40 hover:text-white animate-pulse'
                    : 'bg-surface-3/50 border border-border/40 text-muted/60'),
              isMyRow(slot) ? 'cursor-pointer' : 'cursor-default',
            ]"
            :disabled="!isMyRow(slot)"
            :aria-label="isMyRow(slot) ? (slot.ready ? t('lobby.cancelReady') : t('lobby.doReady')) : (slot.ready ? t('lobby.isReady') : t('lobby.notReady'))"
            @click="isMyRow(slot) && toggleReady()"
            :data-testid="isMyRow(slot) ? 'ready-btn' : 'ready-indicator'"
          >{{ slot.ready ? t('lobby.isReady') : t('lobby.notReady') }}</button>

          <!-- 房主金色徽标（名字前；与准备按钮等宽对齐 w-16） -->
          <span
            v-if="slot.kind === 'bot' && slot.player_id === roomState.host_player_id"
            class="w-16 py-1 rounded text-xs font-semibold text-center shrink-0 text-yellow-400 bg-yellow-400/10"
            data-testid="host-badge"
          >{{ t('lobby.hostBadge') }}</span>

          <!-- 电脑行占位（与准备按钮/房主徽标等宽，保证名字列对齐） -->
          <span
            v-if="slot.kind === 'computer'"
            class="w-16 py-1 text-xs text-center shrink-0 text-muted/50"
          >{{ t('lobby.computerReady') }}</span>

          <!-- 名字；自己那行追加 (我) 标签 -->
          <span
            class="text-sm truncate font-medium text-white"
            :style="{ flex: '1', minWidth: 0 }"
          >
            {{ slot.name }}
            <span v-if="isMyRow(slot)" class="text-[10px] text-cyan-300/80 ml-1">{{ t('lobby.myLabel') }}</span>
          </span>

          <!-- 电脑难度标签 -->
          <span v-if="slot.kind === 'computer'" class="text-[10px] text-yellow-400 shrink-0">
            {{ diffLabel(slot.difficulty) }}
          </span>

          <!-- 房主操作：[x] 移除（非自己 + 非 open 行） -->
          <button
            v-if="isHost && slot.kind !== 'open' && !isMyRow(slot)"
            type="button"
            class="w-5 h-5 flex items-center justify-center rounded text-xs text-muted hover:text-danger hover:bg-danger/10 transition-colors shrink-0"
            :aria-label="t('lobby.removeSlot', { name: slot.name })"
            @click="removeSlot(slot.index)"
            data-testid="remove-slot-btn"
          >
            x
          </button>

          <!-- 种族（最右对齐；自己那行可改，其他行只读） -->
          <select
            :value="slot.race"
            :disabled="!isMyRow(slot)"
            class="bg-surface-3 border border-border rounded px-1.5 py-0.5 text-xs text-white shrink-0 focus:outline-none"
            :class="isMyRow(slot) ? 'cursor-pointer' : 'cursor-default opacity-70'"
            @change="isMyRow(slot) && setRace(($event.target as HTMLSelectElement).value)"
            data-testid="race-select"
          >
            <option v-for="r in RACE_OPTIONS" :key="r.value" :value="r.value">{{ r.label }}</option>
          </select>
        </div>

      </template>
    </div>

    <!-- 房主操作区：仅开始对局（添加电脑已移至空位行内） -->
    <div v-if="isHost">
      <button
        type="button"
        :disabled="!canStart"
        class="w-full py-3 rounded-xl text-base font-bold transition-colors"
        :class="canStart
          ? 'bg-accent text-surface hover:bg-accent/90 active:scale-[0.98]'
          : 'bg-surface-2 text-muted border border-border cursor-not-allowed'"
        @click="canStart && startGame()"
        data-testid="start-game-btn"
      >
        {{ t('lobby.startGame') }}
      </button>
    </div>

  </div>
</template>
