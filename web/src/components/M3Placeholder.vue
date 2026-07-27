<script setup lang="ts">
import { t } from '@/i18n'

defineProps<{
  label: string        // "资源条" / "Standing Orders" / "快捷区"
  hint?: string        // 说明这块未来要填什么
  minHeight?: string   // "60px" / "80px" 之类
}>()
</script>

<template>
  <div
    class="rounded-lg border border-dashed border-border bg-surface-3/40
           flex items-center justify-center relative"
    :style="{ minHeight: minHeight ?? '48px' }"
  >
    <span class="text-xs text-muted/60 italic">{{ label }}</span>
    <span class="absolute top-1 right-2 text-[10px] text-muted/50 font-mono">
      {{ t('m3.badge') }}
    </span>
    <p v-if="hint" class="absolute bottom-1 left-2 text-[10px] text-muted/40">
      {{ hint }}
    </p>
  </div>
</template>
