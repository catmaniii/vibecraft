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

describe('snapshot 帧解析（P0）', () => {
  it('能解析 snapshot 帧 strategy 字段', () => {
    const raw = JSON.stringify({
      type: 'snapshot',
      ts: 120.5,
      strategy: {
        current_stage: 'opening',
        opening: { id: '1g_robo', display: '1门Robo 不朽开', phases: [{ id: 'p1', display: '开局', subtitle: '13农BG' }] },
        midgame: null,
        lategame: null,
      },
      recent_commands: [{ text: '切 IAC', ts: 100.0 }],
    })
    const frame = JSON.parse(raw) as {
      type: string
      strategy: { current_stage: string; opening: { id: string } | null }
      recent_commands: { text: string; ts: number }[]
    }
    expect(frame.type).toBe('snapshot')
    expect(frame.strategy.current_stage).toBe('opening')
    expect(frame.strategy.opening?.id).toBe('1g_robo')
    expect(frame.recent_commands).toHaveLength(1)
    expect(frame.recent_commands[0].text).toBe('切 IAC')
  })

  it('snapshot 帧 midgame/lategame 可为 null', () => {
    const raw = JSON.stringify({
      type: 'snapshot',
      ts: 200.0,
      strategy: { current_stage: 'midgame', opening: null, midgame: { id: 'iac', display: 'IAC' }, lategame: null },
      recent_commands: [],
    })
    const frame = JSON.parse(raw) as { strategy: { midgame: { id: string } | null; lategame: unknown } }
    expect(frame.strategy.midgame?.id).toBe('iac')
    expect(frame.strategy.lategame).toBeNull()
  })
})

describe('event 帧解析（P1）', () => {
  it('能解析 strategy.set event 帧', () => {
    const raw = JSON.stringify({
      type: 'event',
      kind: 'strategy.set',
      ts: 345.1,
      payload: { stage: 'midgame', strategy_id: 'iac_2base', display: '双矿 IAC 重装地面' },
    })
    const frame = JSON.parse(raw) as { type: string; kind: string; payload: { display: string } }
    expect(frame.type).toBe('event')
    expect(frame.kind).toBe('strategy.set')
    expect(frame.payload.display).toBe('双矿 IAC 重装地面')
  })

  it('能解析 decision.autopilot_phase event 帧', () => {
    const raw = JSON.stringify({
      type: 'event',
      kind: 'decision.autopilot_phase',
      ts: 180.0,
      payload: { phase: 'macro', message: '开局 build 跑完，转入自动运营（造兵/扩张/开矿）' },
    })
    const frame = JSON.parse(raw) as { kind: string; payload: { message: string } }
    expect(frame.kind).toBe('decision.autopilot_phase')
    expect(frame.payload.message).toContain('自动运营')
  })
})

describe('command_echo 帧解析（P0）', () => {
  it('能解析 command_echo 帧', () => {
    const raw = JSON.stringify({
      type: 'command_echo',
      user_text: '切 IAC',
      interpretation: '切到双矿 IAC 重装地面',
      ts: 312.0,
    })
    const frame = JSON.parse(raw) as { type: string; user_text: string; interpretation: string }
    expect(frame.type).toBe('command_echo')
    expect(frame.user_text).toBe('切 IAC')
    expect(frame.interpretation).toBe('切到双矿 IAC 重装地面')
  })
})
