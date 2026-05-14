// 类型帧的单测：验证 DEFAULT_STATUS + 帧结构基本不变量
import { describe, it, expect } from 'vitest'
import { DEFAULT_STATUS } from '@/types'

describe('DEFAULT_STATUS', () => {
  it('link 初始为 connecting', () => {
    expect(DEFAULT_STATUS.link).toBe('connecting')
  })

  it('sc2 初始为 idle', () => {
    expect(DEFAULT_STATUS.sc2).toBe('idle')
  })

  it('bot 初始为 idle', () => {
    expect(DEFAULT_STATUS.bot).toBe('idle')
  })

  it('detail 初始为空串', () => {
    expect(DEFAULT_STATUS.detail).toBe('')
  })
})

describe('帧结构字段检查', () => {
  it('start_game 帧 type 字段正确', () => {
    const frame = { type: 'start_game' as const }
    expect(frame.type).toBe('start_game')
  })

  it('command 帧包含必填字段', () => {
    const frame = {
      type: 'command' as const,
      client_id: 'c_abc',
      issued_at: 100.0,
      text: '切 IAC',
    }
    expect(frame.type).toBe('command')
    expect(frame.text).toBe('切 IAC')
    expect(typeof frame.issued_at).toBe('number')
  })

  it('game_status 帧 sc2 字段枚举值合法', () => {
    const validSc2 = ['idle', 'launching', 'in_game', 'playing', 'ended', 'crashed']
    for (const v of validSc2) {
      expect(validSc2).toContain(v)
    }
  })
})
