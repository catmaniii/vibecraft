<script setup lang="ts">
// 入口页：用户名输入 + 服务器列表（点选/删除）+ 添加服务器折叠表单 + [连接] 按钮
// 设计 docs/plans/2026-06-12-multiplayer-implementation-plan.md Task 8
import { ref, computed } from 'vue'
import { useProfile } from '@/composables/useProfile'
import QrShareButton from '@/components/QrShareButton.vue'
import LanguageSwitcher from '@/components/LanguageSwitcher.vue'
import { t, i18n } from '@/i18n'

const { profile, setUsername, addServer, removeServer, selectServer, selectedServer, isComplete } =
  useProfile()

const emit = defineEmits<{
  connect: []
}>()

// 用户名输入框（双向绑定，失焦时持久化）
const usernameInput = ref(profile.value.username)

function onUsernameBlur() {
  setUsername(usernameInput.value)
}

// 添加服务器折叠表单
const addFormExpanded = ref(false)
const newName = ref('')
const newUrl = ref('')
const newToken = ref('')
const addError = ref('')

function toggleAddForm() {
  addFormExpanded.value = !addFormExpanded.value
  addError.value = ''
}

function submitAddServer() {
  const url = newUrl.value.trim()
  const token = newToken.value.trim()
  const name = newName.value.trim() || url

  if (!url) {
    addError.value = t('entry.errNoUrl')
    return
  }
  if (!token) {
    addError.value = t('entry.errNoToken')
    return
  }
  // 简单校验 url 格式
  try {
    new URL(url)
  } catch {
    addError.value = t('entry.errBadUrl')
    return
  }

  addServer({ name, url, token })
  newName.value = ''
  newUrl.value = ''
  newToken.value = ''
  addError.value = ''
  addFormExpanded.value = false
}

// [连接] 按钮可用条件。用 usernameInput（实时值）而非 profile.username（失焦才更新）：
// 否则用户输了名字没失焦，按钮还是灰的，更让人懵。
const canConnect = computed(
  () => !!usernameInput.value.trim() && !!selectedServer(),
)

// 连接被禁用时，明确告诉用户还缺什么（避免"按钮点不下去又不知道为啥"）。
const connectHint = computed(() => {
  const hasName = !!usernameInput.value.trim()
  const hasServer = !!selectedServer()
  if (!hasName && !hasServer) return t('entry.hintNameAndServer')
  if (!hasName) return t('entry.hintName')
  if (!hasServer) return t('entry.hintServer')
  return ''
})

function onConnect() {
  if (!canConnect.value) return
  // 确保最新用户名已写入
  setUsername(usernameInput.value)
  emit('connect')
}

function onSelectServer(i: number) {
  selectServer(i)
}

function onRemoveServer(i: number) {
  removeServer(i)
}

/** 从 url 提取 host:port，供服务器列表副标题展示；解析失败返回空字符串。 */
function serverHostLabel(url: string): string {
  try { return new URL(url).host } catch { return '' }
}

// ── 信息反馈表单 ──────────────────────────────────────────────────────────────
// 提交走 GET /api/feedback（server 追加到本地 logs/feedback.csv，记昵称/分类/内容/IP/时间）
const fbOpen = ref(false)
const fbName = ref('')
const fbCategory = ref('建议')
const fbContent = ref('')
const fbStatus = ref<'idle' | 'sending' | 'done' | 'error'>('idle')

function openFeedback() {
  fbName.value = usernameInput.value || ''
  fbCategory.value = t('entry.fbCatSuggestion')
  fbContent.value = ''
  fbStatus.value = 'idle'
  fbOpen.value = true
}
function closeFeedback() {
  fbOpen.value = false
}
async function submitFeedback() {
  if (!fbContent.value.trim() || fbStatus.value === 'sending') return
  fbStatus.value = 'sending'
  try {
    const q = new URLSearchParams({
      name: fbName.value.trim() || t('entry.fbAnon'),
      category: fbCategory.value,
      content: fbContent.value.trim(),
    })
    const r = await fetch('/api/feedback?' + q.toString(), { signal: AbortSignal.timeout(8000) })
    const j = await r.json()
    if (j?.ok) {
      fbStatus.value = 'done'
      setTimeout(() => { fbOpen.value = false }, 1500)
    } else {
      fbStatus.value = 'error'
    }
  } catch {
    fbStatus.value = 'error'
  }
}
</script>

<template>
  <div class="min-h-screen bg-surface flex flex-col items-center justify-center px-5 py-8">
    <div class="w-full max-w-sm flex flex-col gap-6">

      <!-- 标题 -->
      <div class="text-center relative">
        <!-- 语言切换：右上角 -->
        <div class="absolute right-0 top-0">
          <LanguageSwitcher />
        </div>
        <p class="text-2xl font-bold text-accent tracking-wide">VibeCraft</p>
        <p class="text-sm text-muted mt-1">{{ t('entry.tagline') }}</p>
        <!-- 玩家操作指南：开新页（静态 /guide.html，内容源自 README）。传当前 lang → 指南页同步语言 -->
        <a
          :href="`/guide.html?lang=${i18n.locale}`"
          target="_blank"
          rel="noopener"
          class="inline-block mt-2 text-xs text-accent hover:text-accent/80 underline underline-offset-2 transition-colors"
        >
          {{ t('entry.guide') }}
        </a>
      </div>

      <!-- 用户名 -->
      <div class="flex flex-col gap-1.5">
        <label class="text-xs font-semibold text-muted uppercase tracking-wider">
          {{ t('entry.username') }} <span class="text-danger normal-case">{{ t('entry.required') }}</span>
        </label>
        <input
          v-model="usernameInput"
          type="text"
          :placeholder="t('entry.usernamePlaceholder')"
          maxlength="20"
          class="w-full bg-surface-2 border rounded-lg px-3 py-2.5 text-sm text-white placeholder-muted focus:outline-none focus:border-accent transition-colors"
          :class="usernameInput.trim() ? 'border-border' : 'border-danger/70'"
          @blur="onUsernameBlur"
          @keydown.enter="onUsernameBlur"
          data-testid="username-input"
        />
      </div>

      <!-- 服务器列表 -->
      <div class="flex flex-col gap-1.5">
        <div class="flex items-center justify-between">
          <label class="text-xs font-semibold text-muted uppercase tracking-wider">{{ t('entry.servers') }}</label>
          <button
            type="button"
            class="text-xs text-accent hover:text-accent/80 transition-colors"
            @click="toggleAddForm"
            data-testid="add-server-toggle"
          >
            {{ addFormExpanded ? t('entry.collapse') : t('entry.add') }}
          </button>
        </div>

        <!-- 无服务器时的空状态 -->
        <div
          v-if="profile.servers.length === 0 && !addFormExpanded"
          class="text-xs text-muted text-center py-4 border border-dashed border-border rounded-lg"
        >
          {{ t('entry.noServers') }}
        </div>

        <!-- 服务器卡片列表 -->
        <div
          v-for="(srv, i) in profile.servers"
          :key="i"
          class="flex items-center gap-2.5 px-3 py-2.5 rounded-lg border-2 cursor-pointer transition-colors"
          :class="
            profile.selectedIndex === i
              ? 'border-accent bg-accent/20 text-white ring-1 ring-accent/40'
              : 'border-border bg-surface-2 text-muted hover:border-accent/50 hover:text-white'
          "
          @click="onSelectServer(i)"
          :data-testid="`server-card-${i}`"
        >
          <!-- 选中指示：选中=实心对勾，未选=空心圈 -->
          <span
            class="w-5 h-5 rounded-full shrink-0 flex items-center justify-center text-[12px] font-bold transition-colors"
            :class="profile.selectedIndex === i
              ? 'bg-accent text-surface'
              : 'border-2 border-muted text-transparent'"
          >✓</span>

          <!-- 名称 + host:port 副标题（token 不展示；与名称重复时省略） -->
          <div class="flex-1 min-w-0">
            <p class="text-sm font-medium truncate">{{ srv.name }}</p>
            <p
              v-if="serverHostLabel(srv.url) && serverHostLabel(srv.url) !== srv.name"
              class="text-[11px] text-muted truncate"
            >{{ serverHostLabel(srv.url) }}</p>
          </div>

          <!-- 已选中 / 点击选择 提示 -->
          <span
            class="text-[11px] shrink-0 whitespace-nowrap"
            :class="profile.selectedIndex === i ? 'text-accent font-semibold' : 'text-muted'"
          >{{ profile.selectedIndex === i ? t('entry.selected') : t('entry.tapSelect') }}</span>

          <!-- 删除按钮 -->
          <button
            type="button"
            class="shrink-0 w-6 h-6 flex items-center justify-center rounded text-muted hover:text-danger hover:bg-danger/10 transition-colors text-sm"
            @click.stop="onRemoveServer(i)"
            :aria-label="t('entry.removeServer', { name: srv.name })"
            :data-testid="`server-remove-${i}`"
          >
            x
          </button>
        </div>

        <!-- 添加服务器折叠表单 -->
        <div
          v-if="addFormExpanded"
          class="border border-border rounded-lg p-3 bg-surface-2 flex flex-col gap-2.5"
          data-testid="add-server-form"
        >
          <input
            v-model="newName"
            type="text"
            :placeholder="t('entry.namePlaceholder')"
            class="w-full bg-surface border border-border rounded px-2.5 py-2 text-xs text-white placeholder-muted focus:outline-none focus:border-accent transition-colors"
            data-testid="add-server-name"
          />
          <input
            v-model="newUrl"
            type="text"
            :placeholder="t('entry.urlPlaceholder')"
            class="w-full bg-surface border border-border rounded px-2.5 py-2 text-xs text-white placeholder-muted focus:outline-none focus:border-accent transition-colors"
            data-testid="add-server-url"
          />
          <input
            v-model="newToken"
            type="text"
            :placeholder="t('entry.tokenPlaceholder')"
            class="w-full bg-surface border border-border rounded px-2.5 py-2 text-xs text-white placeholder-muted focus:outline-none focus:border-accent transition-colors"
            data-testid="add-server-token"
          />
          <p v-if="addError" class="text-xs text-danger">{{ addError }}</p>
          <button
            type="button"
            class="w-full py-2 rounded bg-accent/20 border border-accent/40 text-accent text-xs font-semibold hover:bg-accent/30 transition-colors"
            @click="submitAddServer"
            data-testid="add-server-submit"
          >
            {{ t('entry.submitAdd') }}
          </button>
        </div>
      </div>

      <!-- 连接按钮 + 缺啥提示 -->
      <div class="flex flex-col gap-2">
        <button
          type="button"
          class="w-full py-3 rounded-xl text-base font-bold transition-colors"
          :class="
            canConnect
              ? 'bg-accent text-surface hover:bg-accent/90 active:scale-[0.98]'
              : 'bg-surface-2 text-muted border border-border cursor-not-allowed'
          "
          :disabled="!canConnect"
          @click="onConnect"
          data-testid="connect-btn"
        >
          {{ t('entry.connect') }}
        </button>
        <!-- 按钮点不下去时，明确说还缺什么（红字居中） -->
        <p
          v-if="!canConnect"
          class="text-xs text-center text-danger"
          data-testid="connect-hint"
        >
          {{ connectHint }}
        </p>
      </div>

      <!-- 信息反馈入口（次级按钮：边框 + 图标，明显可点；不抢"连接"主按钮） -->
      <button
        type="button"
        class="w-full py-2.5 rounded-lg border border-accent/50 bg-accent/10 text-accent text-sm font-medium
               hover:bg-accent/20 active:scale-[0.99] transition-colors flex items-center justify-center gap-2"
        @click="openFeedback"
        data-testid="feedback-open"
      >
        <svg viewBox="0 0 24 24" class="h-4 w-4 shrink-0" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <path
            d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
        {{ t('entry.feedback') }}
      </button>

      <!-- 分享二维码：弹出当前首页 URL 的二维码，另一台手机扫码即可访问 -->
      <QrShareButton size="full" />

    </div>

    <!-- 信息反馈表单（模态） -->
    <div
      v-if="fbOpen"
      class="fixed inset-0 z-50 bg-black/60 flex items-center justify-center px-5"
      @click.self="closeFeedback"
      data-testid="feedback-modal"
    >
      <div class="w-full max-w-sm bg-surface-2 border border-border rounded-xl p-4 flex flex-col gap-3">
        <div class="flex items-center justify-between">
          <p class="text-base font-bold text-white">{{ t('entry.fbTitle') }}</p>
          <button type="button" class="text-muted hover:text-white text-xl leading-none" @click="closeFeedback">×</button>
        </div>
        <input
          v-model="fbName"
          type="text"
          :placeholder="t('entry.fbNamePlaceholder')"
          maxlength="60"
          class="w-full bg-surface border border-border rounded px-2.5 py-2 text-sm text-white placeholder-muted focus:outline-none focus:border-accent"
          data-testid="feedback-name"
        />
        <select
          v-model="fbCategory"
          class="w-full bg-surface border border-border rounded px-2.5 py-2 text-sm text-white focus:outline-none focus:border-accent"
          data-testid="feedback-category"
        >
          <option>{{ t('entry.fbCatSuggestion') }}</option>
          <option>{{ t('entry.fbCatBug') }}</option>
          <option>{{ t('entry.fbCatOther') }}</option>
        </select>
        <textarea
          v-model="fbContent"
          rows="4"
          :placeholder="t('entry.fbContentPlaceholder')"
          maxlength="2000"
          class="w-full bg-surface border border-border rounded px-2.5 py-2 text-sm text-white placeholder-muted focus:outline-none focus:border-accent resize-none"
          data-testid="feedback-content"
        ></textarea>
        <button
          v-if="fbStatus !== 'done'"
          type="button"
          class="w-full py-2.5 rounded-lg font-semibold transition-colors"
          :class="fbContent.trim() && fbStatus !== 'sending'
            ? 'bg-accent text-surface hover:bg-accent/90'
            : 'bg-surface-3 text-muted border border-border cursor-not-allowed'"
          :disabled="!fbContent.trim() || fbStatus === 'sending'"
          @click="submitFeedback"
          data-testid="feedback-submit"
        >
          {{ fbStatus === 'sending' ? t('entry.fbSubmitting') : t('entry.fbSubmit') }}
        </button>
        <p v-if="fbStatus === 'done'" class="text-center text-green-400 text-sm py-1" data-testid="feedback-done">
          {{ t('entry.fbDone') }}
        </p>
        <p v-if="fbStatus === 'error'" class="text-center text-danger text-xs">{{ t('entry.fbError') }}</p>
      </div>
    </div>
  </div>
</template>
