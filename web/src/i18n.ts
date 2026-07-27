// 前端 i18n：从仓库根 locales/strings.json（中英唯一真理源）读取 UI 字符串。
//
// 复用并扩展原有自研 t()（不引 vue-i18n）：
//   - locale 改为 reactive：切语言即时整页重渲（无需 reload）。
//   - 支持 {name} 模板插值：t('starting.count', { n: 3 })。
//   - 查不到 key → 回退当前 locale 的 zh → 再回退 key 本身（不崩、能看出缺哪条）。
//   - 默认 locale：localStorage 持久化 > 浏览器语言（zh* → zh，其余 → en）。
//
// 与后端 localization.py（Localizer）分工：Localizer 管"id→单位/建筑/科技专有名词"，
// 本层 + strings.json 管"key→UI 句子/标签（含模板）"。句子里嵌专有名词时由后端先渲染好。
import { reactive } from 'vue'
import strings from '@locales/strings.json'

export type Locale = 'zh' | 'en'

interface Entry {
  zh: string
  en?: string
  context?: string
}

const DB = strings as Record<string, Entry>
const STORAGE_KEY = 'vibecraft.locale'

function detectDefault(): Locale {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'zh' || saved === 'en') return saved
  } catch {
    /* localStorage 不可用（隐私模式/SSR）→ 落到浏览器语言 */
  }
  try {
    const nav = (navigator.language || '').toLowerCase()
    return nav.startsWith('zh') ? 'zh' : 'en'
  } catch {
    return 'zh'
  }
}

// reactive：模板里 t() 读到 i18n.locale，切换时 Vue 自动重渲依赖组件。
export const i18n = reactive<{ locale: Locale }>({ locale: detectDefault() })

export function setLocale(loc: Locale): void {
  i18n.locale = loc
  try {
    localStorage.setItem(STORAGE_KEY, loc)
  } catch {
    /* ignore */
  }
}

/** 翻译 key；可选模板参数替换 {name}。缺 key/缺译有多级回退。 */
export function t(key: string, params?: Record<string, string | number>): string {
  const e = DB[key]
  let s: string
  if (!e) {
    s = key
  } else {
    s = e[i18n.locale] ?? e.zh ?? key
  }
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      s = s.replace(new RegExp('\\{' + k + '\\}', 'g'), String(v))
    }
  }
  return s
}
