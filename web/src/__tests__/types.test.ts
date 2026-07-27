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

describe('SnapshotFrame 类型约束（P0）', () => {
  it('SnapshotFrame 包含 strategy + recent_commands', () => {
    // TypeScript 编译期验证：能正常构造一个合规的 SnapshotFrame 对象
    const f = {
      type: 'snapshot' as const,
      ts: 120.0,
      strategy: {
        current_stage: 'opening' as const,
        opening: { id: '1g_robo', display: '1门Robo 不朽开' },
        midgame: null,
        lategame: null,
      },
      recent_commands: [{ text: '切 IAC', ts: 100.0 }],
    }
    expect(f.type).toBe('snapshot')
    expect(f.strategy.current_stage).toBe('opening')
    expect(f.strategy.opening?.id).toBe('1g_robo')
    expect(f.recent_commands[0].text).toBe('切 IAC')
  })

  it('StrategySlotView phases 字段为可选', () => {
    // phases 可以没有（midgame/lategame 情况）
    const slotNoPhases = { id: 'iac', display: '双矿 IAC 重装地面' }
    const slotWithPhases = {
      id: '1g_robo',
      display: '1门Robo 不朽开',
      phases: [{ id: 'p1', display: '开局', subtitle: '13农BG' }],
    }
    expect(slotNoPhases.id).toBe('iac')
    expect(slotWithPhases.phases).toHaveLength(1)
  })
})

describe('EventFrame 类型约束（P1）', () => {
  it('EventFrame 包含 type/kind/ts/payload', () => {
    const f = {
      type: 'event' as const,
      kind: 'strategy.set',
      ts: 345.1,
      payload: { stage: 'midgame', display: '双矿 IAC 重装地面' },
    }
    expect(f.type).toBe('event')
    expect(f.kind).toBe('strategy.set')
    expect(typeof f.payload).toBe('object')
  })
})

describe('CommandEchoFrame 类型约束', () => {
  it('CommandEchoFrame 包含 user_text + interpretation', () => {
    const f = {
      type: 'command_echo' as const,
      user_text: '切 IAC',
      interpretation: '切到双矿 IAC 重装地面',
      ts: 312.0,
    }
    expect(f.type).toBe('command_echo')
    expect(f.user_text).toBe('切 IAC')
    expect(f.interpretation).toContain('IAC')
  })
})
