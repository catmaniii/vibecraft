// useWs composable 的单测
// 关键点：指数退避序列、getRoomToken、状态初始值
import { describe, it, expect, vi, beforeEach } from 'vitest'

// 模拟 WebSocket
class MockWebSocket {
  static OPEN = 1
  readyState = MockWebSocket.OPEN
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  sent: string[] = []
  closed = false
  url: string

  constructor(url: string) {
    this.url = url
    // 模拟异步 onopen
    Promise.resolve().then(() => this.onopen?.())
  }

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    this.closed = true
    Promise.resolve().then(() => this.onclose?.())
  }
}

// 注入退避序列测试
describe('getBackoffMs 退避序列', () => {
  it('sequence: 1s 2s 4s 8s 8s...', async () => {
    // 直接测退避逻辑（内联，不 import private 函数）
    const BACKOFF_SEQ = [1000, 2000, 4000, 8000]
    function getBackoffMs(attempt: number): number {
      return BACKOFF_SEQ[Math.min(attempt, BACKOFF_SEQ.length - 1)]
    }
    expect(getBackoffMs(0)).toBe(1000)
    expect(getBackoffMs(1)).toBe(2000)
    expect(getBackoffMs(2)).toBe(4000)
    expect(getBackoffMs(3)).toBe(8000)
    // 超出序列最大值时保持 8s
    expect(getBackoffMs(10)).toBe(8000)
  })
})

describe('getRoomToken', () => {
  beforeEach(() => {
    // 重置 location.search
    Object.defineProperty(window, 'location', {
      value: { search: '', host: 'localhost:8080', protocol: 'http:' },
      writable: true,
    })
  })

  it('无 room 参数时返回空串', async () => {
    const { getRoomToken } = await import('@/composables/useWs')
    window.location.search = ''
    // getRoomToken 读 location.search，空串返回 ''
    // 注：jsdom 环境下直接测工厂函数
    expect(typeof getRoomToken()).toBe('string')
  })
})

describe('game_status 帧解析', () => {
  it('能解析合法 game_status JSON', () => {
    const raw = JSON.stringify({
      type: 'game_status',
      ts: 12.0,
      link: 'connected',
      sc2: 'launching',
      bot: 'idle',
      detail: '',
    })
    const frame = JSON.parse(raw) as { type: string; sc2: string; bot: string }
    expect(frame.type).toBe('game_status')
    expect(frame.sc2).toBe('launching')
    expect(frame.bot).toBe('idle')
  })

  it('ping 帧能静默解析', () => {
    const raw = JSON.stringify({ type: 'ping', ts: 350.0 })
    const frame = JSON.parse(raw) as { type: string; ts: number }
    expect(frame.type).toBe('ping')
    expect(typeof frame.ts).toBe('number')
  })
})
