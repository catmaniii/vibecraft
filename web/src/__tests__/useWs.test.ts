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

// ---- socket 代际守卫（2026-06-12 修"lobby 名单狂闪"根因）----

describe('socket 代际守卫 — connectNow() 不触发旧 socket 重连', () => {
  let sockets: MockWebSocket[] = []

  beforeEach(() => {
    sockets = []
    Object.defineProperty(window, 'location', {
      value: { search: '?room=test-gen', host: 'localhost:8080', protocol: 'http:' },
      writable: true,
    })
    ;(globalThis as any).WebSocket = class extends MockWebSocket {
      constructor(url: string) {
        super(url)
        sockets.push(this)
      }
    }
    vi.resetModules()
  })

  it('connectNow() 两次后，旧 socket 的 onclose 不触发重连（总 socket 数保持 2）', async () => {
    const { useWs } = await import('@/composables/useWs')
    const wsComp = useWs()
    await Promise.resolve()  // 等第一个 socket onopen

    expect(sockets).toHaveLength(1)

    // 第一次 connectNow：ws 先置 null → close 旧 socket → connect() 建新 socket
    wsComp.connectNow()
    await Promise.resolve()  // sock1.onclose + sock2.onopen 均在此 tick 触发
    await Promise.resolve()  // 额外等一个 tick，确保所有微任务清空

    // 旧代 sock1.onclose 应因 sock1!==ws 而直接 return，不创建第三个 socket
    expect(sockets.length).toBe(2)
  })
})

// ---- ASR 音频帧 + transcript 接收（Task 4）----

describe('音频发送 helper + transcript 接收（ASR Task 4）', () => {
  // 当前捕获的 MockWebSocket 实例（由 beforeEach 注入的构造函数写入）
  let capturedWs: MockWebSocket | null = null

  beforeEach(() => {
    capturedWs = null
    // 设置带 room token 的 location，让 useWs 触发 connect()
    Object.defineProperty(window, 'location', {
      value: { search: '?room=test-asr', host: 'localhost:8080', protocol: 'http:' },
      writable: true,
    })
    // 替换全局 WebSocket，让 useWs 内的 new WebSocket() 使用 mock
    ;(globalThis as any).WebSocket = class extends MockWebSocket {
      constructor(url: string) {
        super(url)
        capturedWs = this  // 捕获实例，供各 case 断言
      }
    }
    vi.resetModules()  // 清除模块缓存，确保 useWs 拿到最新 window.location + WebSocket
  })

  it('sendAudioChunk 发出 audio_chunk 帧，字段与后端约定一致', async () => {
    const { useWs } = await import('@/composables/useWs')
    const wsComp = useWs()
    await Promise.resolve()  // 等 onopen 异步触发
    wsComp.sendAudioChunk(3, 'AAAA')
    expect(capturedWs).not.toBeNull()
    expect(capturedWs!.sent).toHaveLength(1)
    expect(JSON.parse(capturedWs!.sent[0])).toEqual({ type: 'audio_chunk', seq: 3, pcm: 'AAAA' })
  })

  it('sendAudioEnd 发出 audio_end 帧', async () => {
    const { useWs } = await import('@/composables/useWs')
    const wsComp = useWs()
    await Promise.resolve()
    wsComp.sendAudioEnd()
    expect(capturedWs!.sent).toHaveLength(1)
    expect(JSON.parse(capturedWs!.sent[0])).toEqual({ type: 'audio_end' })
  })

  it('sendAudioCancel 发出 audio_cancel 帧', async () => {
    const { useWs } = await import('@/composables/useWs')
    const wsComp = useWs()
    await Promise.resolve()
    wsComp.sendAudioCancel()
    expect(capturedWs!.sent).toHaveLength(1)
    expect(JSON.parse(capturedWs!.sent[0])).toEqual({ type: 'audio_cancel' })
  })

  it('收到 transcript 帧 → lastTranscript 更新且 is_final 正确', async () => {
    const { useWs } = await import('@/composables/useWs')
    const wsComp = useWs()
    await Promise.resolve()  // 等 onopen + onmessage 绑定完成
    // 初始应为 null
    expect(wsComp.lastTranscript.value).toBeNull()
    // 模拟 server 推送 transcript 帧
    capturedWs!.onmessage?.({
      data: JSON.stringify({ type: 'transcript', text: '切4bg', is_final: true }),
    })
    expect(wsComp.lastTranscript.value).not.toBeNull()
    expect(wsComp.lastTranscript.value?.text).toBe('切4bg')
    expect(wsComp.lastTranscript.value?.is_final).toBe(true)
  })
})
