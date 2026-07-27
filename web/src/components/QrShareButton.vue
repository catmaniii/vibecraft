<script setup lang="ts">
// 二维码分享按钮 + 弹窗：展示「当前首页 URL」的二维码，手机一扫即可访问游戏主页。
// QR 内容 = window.location（origin + path + search），即用户当前进来的那个地址
// （经公网前门进来就是公网 URL，含 ?room= 房间码 → 扫码后自动填入，与启动二维码一致）。
// SVG 由 server `/api/qr?data=<url>` 渲染（复用 Python qrcode，无前端依赖）。
import { ref, computed } from 'vue'
import { t } from '@/i18n'

// size: 'full' = 入口页全宽次级按钮；'sm' = 大厅顶栏小药丸按钮。
const props = withDefaults(defineProps<{ size?: 'full' | 'sm' }>(), { size: 'full' })

const open = ref(false)
const copied = ref(false)

// 当前 URL（去掉 hash）：origin + pathname + search。SPA 全程停在首页地址，
// 入口页/大厅都拿到同一个「首页 URL」。
const shareUrl = computed(() => {
  if (typeof window === 'undefined') return ''
  const l = window.location
  return l.origin + l.pathname + l.search
})

const qrSrc = computed(() => '/api/qr?data=' + encodeURIComponent(shareUrl.value))

function openModal() {
  copied.value = false
  open.value = true
}
function closeModal() {
  open.value = false
}
async function copyUrl() {
  try {
    await navigator.clipboard.writeText(shareUrl.value)
    copied.value = true
    setTimeout(() => { copied.value = false }, 1800)
  } catch {
    // 复制失败静默（用户仍可手动选中文本）
  }
}
</script>

<template>
  <!-- 触发按钮（两种尺寸） -->
  <button
    v-if="props.size === 'sm'"
    type="button"
    class="text-xs px-3 py-1.5 rounded border border-accent/50 bg-accent/10 text-accent
           hover:bg-accent/20 transition-colors shrink-0 flex items-center gap-1"
    @click="openModal"
    data-testid="qr-share-open"
  >
    <svg viewBox="0 0 24 24" class="h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
      <rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" /><path d="M14 14h3v3M21 14v7h-7" stroke-linecap="round" />
    </svg>
    {{ t('qr.shareSm') }}
  </button>
  <button
    v-else
    type="button"
    class="w-full py-2.5 rounded-lg border border-accent/50 bg-accent/10 text-accent text-sm font-medium
           hover:bg-accent/20 active:scale-[0.99] transition-colors flex items-center justify-center gap-2"
    @click="openModal"
    data-testid="qr-share-open"
  >
    <svg viewBox="0 0 24 24" class="h-4 w-4 shrink-0" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
      <rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" /><path d="M14 14h3v3M21 14v7h-7" stroke-linecap="round" />
    </svg>
    {{ t('qr.shareFull') }}
  </button>

  <!-- 弹窗 -->
  <div
    v-if="open"
    class="fixed inset-0 z-50 bg-black/60 flex items-center justify-center px-5"
    @click.self="closeModal"
    data-testid="qr-share-modal"
  >
    <div class="w-full max-w-xs bg-surface-2 border border-border rounded-xl p-4 flex flex-col gap-3 items-center">
      <div class="w-full flex items-center justify-between">
        <p class="text-base font-bold text-white">{{ t('qr.title') }}</p>
        <button type="button" class="text-muted hover:text-white text-xl leading-none" @click="closeModal">×</button>
      </div>
      <!-- QR 图（白底，方便扫） -->
      <div class="bg-white rounded-lg p-3">
        <img :src="qrSrc" :alt="t('qr.alt')" class="w-48 h-48 block" data-testid="qr-share-img" />
      </div>
      <p class="text-xs text-muted text-center">{{ t('qr.hint') }}</p>
      <!-- 下载高清 PNG（识别工具/打印用） -->
      <a
        :href="qrSrc"
        download="vibecraft-qr.png"
        class="text-xs text-accent hover:text-accent/80 underline underline-offset-2"
        data-testid="qr-share-download"
      >{{ t('qr.download') }}</a>
      <!-- URL 文本 + 复制 -->
      <div class="w-full flex items-center gap-2 bg-surface border border-border rounded px-2.5 py-2">
        <span class="text-[11px] text-muted font-mono truncate flex-1" data-testid="qr-share-url">{{ shareUrl }}</span>
        <button
          type="button"
          class="text-xs text-accent hover:text-accent/80 shrink-0 font-medium"
          @click="copyUrl"
          data-testid="qr-share-copy"
        >
          {{ copied ? t('qr.copied') : t('qr.copy') }}
        </button>
      </div>
    </div>
  </div>
</template>
