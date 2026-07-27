<script setup lang="ts">
// 偷矿基地被攻击/发现自动取消时的通知 toast（需求2）
// 监听 lastStealthRelease 事件，弹 5s toast 提醒玩家
import { ref, watch } from 'vue'
import { t } from '@/i18n'

const props = defineProps<{
  // 来自 useWs 的 stealth.cell_released 事件，null = 无事件
  releaseEvent: {
    ts: number
    payload: {
      cell_id: number
      reason: string
      reason_zh: string
      location: [number, number]
    }
  } | null
}>()

const visible = ref(false)
let dismissTimer: ReturnType<typeof setTimeout> | null = null

watch(
  () => props.releaseEvent?.ts,
  (newTs) => {
    if (newTs == null) return
    visible.value = true
    if (dismissTimer != null) clearTimeout(dismissTimer)
    dismissTimer = setTimeout(() => {
      visible.value = false
    }, 5000)
  },
)
</script>

<template>
  <Transition
    enter-active-class="transition ease-out duration-200"
    enter-from-class="opacity-0 translate-y-2"
    enter-to-class="opacity-100 translate-y-0"
    leave-active-class="transition ease-in duration-300"
    leave-from-class="opacity-100"
    leave-to-class="opacity-0"
  >
    <div
      v-if="visible && releaseEvent != null"
      data-testid="stealth-release-toast"
      class="fixed top-16 left-1/2 -translate-x-1/2 z-[60] w-[calc(100%-2rem)] max-w-sm
             px-4 py-3 rounded-xl shadow-lg
             bg-danger/20 border border-danger/60 backdrop-blur"
    >
      <p class="text-xs font-semibold text-danger uppercase tracking-wider">{{ t('stealth.title') }}</p>
      <p class="text-sm font-bold text-white mt-1">
        {{ t('stealth.cellInfo', { id: releaseEvent.payload.cell_id, x: Math.round(releaseEvent.payload.location[0]), y: Math.round(releaseEvent.payload.location[1]) }) }}
      </p>
      <p class="text-xs text-white/80 mt-0.5">
        {{ releaseEvent.payload.reason_zh }}{{ t('stealth.releasedSuffix') }}
      </p>
    </div>
  </Transition>
</template>
