// WS 连接 composable：
// - 从 URL query 取 room token
// - 连接 ws(s)://host/ws?room=<token>
// - 断线指数退避重连（1→2→4→8→8s，§9.6）
// - 暴露 status / send / 关闭能力

import { ref, readonly, onUnmounted } from 'vue'
import type {
  SystemStatus,
  UpFrame,
  GameStatusFrame,
  SnapshotFrame,
  EventFrame,
  CommandEchoFrame,
} from '@/types'
import { DEFAULT_STATUS } from '@/types'

// 退避序列（秒）：1 2 4 8 8 8...
const BACKOFF_SEQ = [1000, 2000, 4000, 8000]

function getBackoffMs(attempt: number): number {
  return BACKOFF_SEQ[Math.min(attempt, BACKOFF_SEQ.length - 1)]
}

// 从当前页面 URL 推断 WS 地址
function buildWsUrl(token: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws?room=${encodeURIComponent(token)}`
}

// 从 URL 取 room token（可为空串——调用方决定如何处理）
export function getRoomToken(): string {
  return new URLSearchParams(location.search).get('room') ?? ''
}

export function useWs() {
  // 系统状态链（响应式）
  const status = ref<SystemStatus>({ ...DEFAULT_STATUS })

  // P0：snapshot strategy + recent_commands（响应式）
  const snapshotStrategy = ref<SnapshotFrame['strategy'] | null>(null)
  const recentCommands = ref<{ text: string; ts: number }[]>([])

  // P1：event ring buffer（最近 30 条，响应式）
  const events = ref<EventFrame[]>([])

  // command_echo（最新一条）
  const lastEcho = ref<CommandEchoFrame | null>(null)

  // 内部 WS 实例 + 重连计数
  let ws: WebSocket | null = null
  let retryCount = 0
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let closed = false   // 手动关闭标志，不触发重连

  const token = getRoomToken()
  const wsUrl = buildWsUrl(token)

  function connect() {
    if (closed) return

    status.value = {
      ...status.value,
      link: retryCount === 0 ? 'connecting' : 'reconnecting',
    }

    ws = new WebSocket(wsUrl)

    ws.onopen = () => {
      retryCount = 0
      status.value = { ...status.value, link: 'connected' }
    }

    ws.onmessage = (evt: MessageEvent) => {
      try {
        const frame = JSON.parse(evt.data as string) as { type: string }
        switch (frame.type) {
          case 'game_status': {
            const f = frame as GameStatusFrame
            status.value = {
              link: status.value.link,   // link 由 WS 事件自己管理
              sc2: f.sc2,
              bot: f.bot,
              detail: f.detail ?? '',
            }
            break
          }
          case 'snapshot': {
            // P0：更新剧本状态 + 最近指令
            const f = frame as SnapshotFrame
            snapshotStrategy.value = f.strategy
            recentCommands.value = f.recent_commands
            break
          }
          case 'event': {
            // P1：push 进 ring buffer（最多 30 条）
            const f = frame as EventFrame
            events.value = [f, ...events.value].slice(0, 30)
            break
          }
          case 'command_echo': {
            // 更新最近 echo
            lastEcho.value = frame as CommandEchoFrame
            break
          }
          case 'ping':
            // 静默忽略（保活用）
            break
          default:
            // 未知帧类型，静默忽略
            break
        }
      } catch {
        console.warn('[voicecraft] WS 帧解析失败', evt.data)
      }
    }

    ws.onclose = () => {
      ws = null
      if (closed) {
        status.value = { ...status.value, link: 'disconnected' }
        return
      }
      // 断线重连
      status.value = { ...status.value, link: 'reconnecting' }
      const delay = getBackoffMs(retryCount)
      retryCount++
      retryTimer = setTimeout(connect, delay)
    }

    ws.onerror = () => {
      // onerror 之后 onclose 一定会触发，重连逻辑交给 onclose
      console.warn('[voicecraft] WebSocket error，等待 onclose 触发重连')
    }
  }

  function send(frame: UpFrame) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(frame))
    } else {
      console.warn('[voicecraft] WS 未连接，帧丢弃', frame.type)
    }
  }

  function close() {
    closed = true
    if (retryTimer !== null) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
    ws?.close()
    ws = null
  }

  // 组件卸载时自动关
  onUnmounted(close)

  // 启动连接（token 为空时暂不连，UI 会提示）
  if (token) {
    connect()
  } else {
    status.value = { ...status.value, link: 'disconnected' }
  }

  return {
    status: readonly(status),
    snapshotStrategy: readonly(snapshotStrategy),
    recentCommands: readonly(recentCommands),
    events: readonly(events),
    lastEcho: readonly(lastEcho),
    send,
    close,
    token,
  }
}
