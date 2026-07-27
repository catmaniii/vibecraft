<script setup lang="ts">
// 语言切换：中 / EN 段控。切换即时整页重渲（i18n.locale 是 reactive）+ localStorage 持久化。
import { i18n, setLocale, type Locale } from '@/i18n'

const OPTIONS: { code: Locale; label: string }[] = [
  { code: 'zh', label: '中' },
  { code: 'en', label: 'EN' },
]
</script>

<template>
  <div
    class="inline-flex items-center rounded-md border border-border overflow-hidden text-xs"
    role="group"
    :aria-label="$options.__name"
    data-testid="lang-switcher"
  >
    <button
      v-for="opt in OPTIONS"
      :key="opt.code"
      type="button"
      class="px-2.5 py-1 transition-colors"
      :class="i18n.locale === opt.code
        ? 'bg-accent text-surface font-semibold'
        : 'bg-surface-2 text-muted hover:text-white'"
      @click="setLocale(opt.code)"
      :data-testid="`lang-${opt.code}`"
      :aria-pressed="i18n.locale === opt.code"
    >
      {{ opt.label }}
    </button>
  </div>
</template>
