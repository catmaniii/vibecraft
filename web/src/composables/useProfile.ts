// 玩家本地档案：用户名 + 服务器列表（localStorage 持久化，无账号系统）。
// 设计 docs/plans/2026-06-12-multiplayer-design.md §3.1。
import { ref } from 'vue'

export interface ServerEntry {
  name: string      // 显示名（"我家 PC"）
  url: string       // http(s)://host:port origin
  token: string     // 房间码
}

const LS_KEY = 'vibecraft_profile_v1'

interface Profile {
  username: string
  deviceId: string          // 首次生成，同名玩家靠它区分
  servers: ServerEntry[]
  selectedIndex: number
}

// deviceId 生成：crypto.randomUUID() 仅在 secure context（https / localhost）可用，
// 非安全上下文（如 http://192.168.x.x 局域网直连）下它是 undefined，裸调用会抛异常
// 导致模块加载失败 → 整个 app 白屏。这里降级到 Math.random() 兜底。
function genDeviceId(): string {
  try {
    if (globalThis.crypto?.randomUUID) return crypto.randomUUID().slice(0, 8)
  } catch { /* 非 secure context：fall through */ }
  return Math.random().toString(36).slice(2, 10).padEnd(8, '0')
}

function load(): Profile {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) return JSON.parse(raw) as Profile
  } catch { /* 损坏则重置 */ }
  return {
    username: '',
    deviceId: genDeviceId(),
    servers: [],
    selectedIndex: -1,
  }
}

const profile = ref<Profile>(load())

function persist(): void {
  localStorage.setItem(LS_KEY, JSON.stringify(profile.value))
}

export function useProfile() {
  function setUsername(name: string): void {
    profile.value.username = name.trim()
    persist()
  }
  function addServer(entry: ServerEntry): void {
    // 同 url+token 去重：已存在则选中它
    const i = profile.value.servers.findIndex(
      (s) => s.url === entry.url && s.token === entry.token,
    )
    if (i >= 0) { profile.value.selectedIndex = i } else {
      profile.value.servers.push(entry)
      profile.value.selectedIndex = profile.value.servers.length - 1
    }
    persist()
  }
  function removeServer(index: number): void {
    profile.value.servers.splice(index, 1)
    if (profile.value.selectedIndex >= profile.value.servers.length) {
      profile.value.selectedIndex = profile.value.servers.length - 1
    }
    persist()
  }
  function selectServer(index: number): void {
    profile.value.selectedIndex = index
    persist()
  }
  function selectedServer(): ServerEntry | null {
    return profile.value.servers[profile.value.selectedIndex] ?? null
  }
  /** 扫码/带 ?room= 打开 → 把当前 origin 自动注册成一条服务器并选中。
   *  注册后 fire-and-forget 拉 /api/server-info 取真实服务器名；
   *  失败 / 404 / 用户已手动改名 → 保持 location.host 兜底，不阻塞 mount。 */
  function adoptUrlRoom(): void {
    const token = new URLSearchParams(location.search).get('room')
    if (!token) return
    const fallbackName = location.host
    const entryUrl = location.origin
    const entryToken = token
    addServer({ name: fallbackName, url: entryUrl, token: entryToken })

    // 异步拉真实名称，不阻塞主流程
    fetch('/api/server-info', { signal: AbortSignal.timeout(5000) })
      .then((r) => {
        if (!r.ok) return null
        return r.json() as Promise<{ name?: string | null }>
      })
      .then((data) => {
        const realName = data?.name?.trim()
        if (!realName) return
        // 重新找条目（可能已被用户删除）
        const idx = profile.value.servers.findIndex(
          (s) => s.url === entryUrl && s.token === entryToken,
        )
        if (idx < 0) return                                       // 已被删除
        if (profile.value.servers[idx].name !== fallbackName) return // 用户已手动改名
        profile.value.servers[idx].name = realName
        persist()
      })
      .catch(() => { /* 网络错误 / 404 → 保持兜底，静默忽略 */ })
  }
  /** 入口页是否已可跳过（有用户名 + 有选中的服务器）。 */
  function isComplete(): boolean {
    return !!profile.value.username && !!selectedServer()
  }
  return {
    profile, setUsername, addServer, removeServer, selectServer,
    selectedServer, adoptUrlRoom, isComplete,
  }
}
