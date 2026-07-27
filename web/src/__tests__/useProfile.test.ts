// useProfile composable 单测（localStorage + crypto mock）
import { describe, it, expect, vi, beforeEach } from 'vitest'

// ---------- mock crypto.randomUUID ----------
const FAKE_UUID = '12345678-abcd-0000-0000-000000000000'

// mock localStorage
const localStorageStore: Record<string, string> = {}
const localStorageMock = {
  getItem: vi.fn((key: string) => localStorageStore[key] ?? null),
  setItem: vi.fn((key: string, value: string) => { localStorageStore[key] = value }),
  removeItem: vi.fn((key: string) => { delete localStorageStore[key] }),
  clear: vi.fn(() => { Object.keys(localStorageStore).forEach((k) => delete localStorageStore[k]) }),
}

beforeEach(() => {
  // 清空 localStorage 状态
  localStorageMock.clear()
  vi.clearAllMocks()

  // 注入 mock
  Object.defineProperty(globalThis, 'localStorage', {
    value: localStorageMock,
    writable: true,
  })

  Object.defineProperty(globalThis, 'crypto', {
    value: { randomUUID: vi.fn(() => FAKE_UUID) },
    writable: true,
  })

  Object.defineProperty(globalThis, 'location', {
    value: {
      search: '',
      host: 'localhost:8080',
      origin: 'http://localhost:8080',
    },
    writable: true,
  })

  // 每次重置模块缓存，使 profile 单例从空 localStorage 重新初始化
  vi.resetModules()
})

describe('useProfile 基础存取', () => {
  it('初次加载：username 空、deviceId 取 UUID 前 8 位、服务器列表空', async () => {
    const { useProfile } = await import('@/composables/useProfile')
    const { profile } = useProfile()
    expect(profile.value.username).toBe('')
    expect(profile.value.deviceId).toBe(FAKE_UUID.slice(0, 8))
    expect(profile.value.servers).toHaveLength(0)
    expect(profile.value.selectedIndex).toBe(-1)
  })

  it('setUsername 写入并持久化', async () => {
    const { useProfile } = await import('@/composables/useProfile')
    const { setUsername, profile } = useProfile()
    setUsername('  alice  ')
    expect(profile.value.username).toBe('alice')   // trim
    expect(localStorageMock.setItem).toHaveBeenCalled()
  })
})

describe('addServer / removeServer / selectServer', () => {
  it('addServer 追加并选中', async () => {
    const { useProfile } = await import('@/composables/useProfile')
    const { addServer, profile, selectedServer } = useProfile()
    addServer({ name: '我家 PC', url: 'http://192.168.1.100:8080', token: 'abc' })
    expect(profile.value.servers).toHaveLength(1)
    expect(profile.value.selectedIndex).toBe(0)
    expect(selectedServer()?.token).toBe('abc')
  })

  it('addServer 同 url+token 去重，仅选中已存条目', async () => {
    const { useProfile } = await import('@/composables/useProfile')
    const { addServer, profile } = useProfile()
    addServer({ name: '我家 PC', url: 'http://192.168.1.100:8080', token: 'abc' })
    addServer({ name: '另一名字', url: 'http://192.168.1.100:8080', token: 'abc' })
    expect(profile.value.servers).toHaveLength(1)   // 没有新增
    expect(profile.value.selectedIndex).toBe(0)
  })

  it('removeServer 删除条目后 selectedIndex 收紧', async () => {
    const { useProfile } = await import('@/composables/useProfile')
    const { addServer, removeServer, profile, selectedServer } = useProfile()
    addServer({ name: 'A', url: 'http://a:8080', token: 'tok-a' })
    addServer({ name: 'B', url: 'http://b:8080', token: 'tok-b' })
    // selectedIndex 此时 = 1（最后添加的）
    removeServer(1)
    expect(profile.value.servers).toHaveLength(1)
    // selectedIndex 收紧到 0
    expect(profile.value.selectedIndex).toBe(0)
    expect(selectedServer()?.token).toBe('tok-a')
  })
})

describe('adoptUrlRoom', () => {
  it('带 ?room= 参数时，把当前 origin 注册为服务器并选中', async () => {
    Object.defineProperty(globalThis, 'location', {
      value: {
        search: '?room=my-room-token',
        host: 'localhost:8080',
        origin: 'http://localhost:8080',
      },
      writable: true,
    })

    const { useProfile } = await import('@/composables/useProfile')
    const { adoptUrlRoom, profile, selectedServer } = useProfile()
    adoptUrlRoom()
    expect(profile.value.servers).toHaveLength(1)
    expect(selectedServer()?.token).toBe('my-room-token')
    expect(selectedServer()?.url).toBe('http://localhost:8080')
  })

  it('adoptUrlRoom 成功拉到 server-info 后更新名称', async () => {
    Object.defineProperty(globalThis, 'location', {
      value: {
        search: '?room=room1',
        host: 'localhost:8080',
        origin: 'http://localhost:8080',
      },
      writable: true,
    })
    // mock fetch 返回真实名称
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ name: '我家 PC' }),
    })
    Object.defineProperty(globalThis, 'fetch', { value: fetchMock, writable: true })
    Object.defineProperty(globalThis, 'AbortSignal', {
      value: { timeout: vi.fn(() => ({})) },
      writable: true,
    })

    const { useProfile } = await import('@/composables/useProfile')
    const { adoptUrlRoom, profile } = useProfile()
    adoptUrlRoom()
    // 等微任务链跑完
    await new Promise<void>((r) => setTimeout(r, 0))
    expect(profile.value.servers[0].name).toBe('我家 PC')
  })

  it('adoptUrlRoom 拉到 404 时保持 location.host 兜底', async () => {
    Object.defineProperty(globalThis, 'location', {
      value: {
        search: '?room=room2',
        host: 'legacy.host:8080',
        origin: 'http://legacy.host:8080',
      },
      writable: true,
    })
    const fetchMock = vi.fn().mockResolvedValue({ ok: false })
    Object.defineProperty(globalThis, 'fetch', { value: fetchMock, writable: true })
    Object.defineProperty(globalThis, 'AbortSignal', {
      value: { timeout: vi.fn(() => ({})) },
      writable: true,
    })

    const { useProfile } = await import('@/composables/useProfile')
    const { adoptUrlRoom, profile } = useProfile()
    adoptUrlRoom()
    await new Promise<void>((r) => setTimeout(r, 0))
    expect(profile.value.servers[0].name).toBe('legacy.host:8080')
  })

  it('adoptUrlRoom 拉到名称但条目已被删除 → 不崩不写', async () => {
    Object.defineProperty(globalThis, 'location', {
      value: {
        search: '?room=room3',
        host: 'localhost:8080',
        origin: 'http://localhost:8080',
      },
      writable: true,
    })
    let resolveFetch!: (v: unknown) => void
    const fetchMock = vi.fn().mockReturnValue(
      new Promise((r) => { resolveFetch = r }),
    )
    Object.defineProperty(globalThis, 'fetch', { value: fetchMock, writable: true })
    Object.defineProperty(globalThis, 'AbortSignal', {
      value: { timeout: vi.fn(() => ({})) },
      writable: true,
    })

    const { useProfile } = await import('@/composables/useProfile')
    const { adoptUrlRoom, removeServer, profile } = useProfile()
    adoptUrlRoom()
    // 删除条目（fetch 还未返回）
    removeServer(0)
    expect(profile.value.servers).toHaveLength(0)

    // 现在 resolve fetch
    resolveFetch({ ok: true, json: () => Promise.resolve({ name: '我家 PC' }) })
    await new Promise<void>((r) => setTimeout(r, 0))
    // 条目不应被重新创建
    expect(profile.value.servers).toHaveLength(0)
  })
})

describe('isComplete', () => {
  it('无用户名 → false', async () => {
    const { useProfile } = await import('@/composables/useProfile')
    const { isComplete, addServer } = useProfile()
    addServer({ name: 'PC', url: 'http://x:8080', token: 'tok' })
    expect(isComplete()).toBe(false)
  })

  it('有用户名 + 有选中服务器 → true', async () => {
    const { useProfile } = await import('@/composables/useProfile')
    const { isComplete, setUsername, addServer } = useProfile()
    setUsername('alice')
    addServer({ name: 'PC', url: 'http://x:8080', token: 'tok' })
    expect(isComplete()).toBe(true)
  })
})
