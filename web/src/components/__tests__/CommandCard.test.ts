// CommandCard 单测 (2026-05-28 用户:units_lost 视觉表现)
// 当后端发 status='done' + status_reason='units_lost' 时:
//   - statusLabel = '单位全失'(代替默认"已完成")
//   - 框样式暗红(代替默认半透明灰)
//   - status 行不再露 reason token 'units_lost'
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import CommandCard from '@/components/CommandCard.vue'
import type { CommandCardView } from '@/types'

function mkCard(overrides: Partial<CommandCardView> = {}): CommandCardView {
  return {
    id: 'c1',
    layer: 'L2',
    type: 'tactical_objective',
    display: 'attack enemy_natural',
    issued_at: 100.0,
    status: 'active',
    status_reason: '',
    revokable: true,
    conditions: [],
    ...overrides,
  }
}

describe('CommandCard - units_lost 状态', () => {
  it('status=done + status_reason=units_lost 显示"单位全失"', () => {
    const wrapper = mount(CommandCard, {
      props: { card: mkCard({ status: 'done', status_reason: 'units_lost' }) },
    })
    expect(wrapper.text()).toContain('单位全失')
    expect(wrapper.text()).not.toContain('已完成')
  })

  it('units_lost 框样式暗红(rose-900),非默认 done 灰', () => {
    const wrapper = mount(CommandCard, {
      props: { card: mkCard({ status: 'done', status_reason: 'units_lost' }) },
    })
    const cls = wrapper.find('div').classes().join(' ')
    expect(cls).toMatch(/rose-/)
    expect(cls).not.toMatch(/surface-3\/40/)
  })

  it('units_lost status label 颜色 text-rose(暗红),非 muted', () => {
    const wrapper = mount(CommandCard, {
      props: { card: mkCard({ status: 'done', status_reason: 'units_lost' }) },
    })
    // 找 statusLabel span(font-semibold)
    const labelSpan = wrapper.findAll('span').find((s) => s.text().includes('单位全失'))
    expect(labelSpan).toBeTruthy()
    expect(labelSpan!.classes().join(' ')).toMatch(/text-rose/)
  })

  it('units_lost 时副文本不暴露 "units_lost" token', () => {
    const wrapper = mount(CommandCard, {
      props: { card: mkCard({ status: 'done', status_reason: 'units_lost' }) },
    })
    expect(wrapper.text()).not.toContain('units_lost')
    expect(wrapper.text()).not.toContain('— units_lost')
  })

  it('普通 done(无 units_lost reason)仍显示"已完成"灰', () => {
    const wrapper = mount(CommandCard, {
      props: { card: mkCard({ status: 'done', status_reason: '' }) },
    })
    expect(wrapper.text()).toContain('已完成')
    const cls = wrapper.find('div').classes().join(' ')
    expect(cls).not.toMatch(/rose-/)
  })

  it('done 状态隐藏 × 按钮(grace 期不可再撤)', () => {
    const wrapper = mount(CommandCard, {
      props: { card: mkCard({ status: 'done', status_reason: 'units_lost', revokable: true }) },
    })
    expect(wrapper.find('[data-testid="revoke-btn"]').exists()).toBe(false)
  })

  it('active 状态正常显示 × 按钮', () => {
    const wrapper = mount(CommandCard, {
      props: { card: mkCard({ status: 'active', revokable: true }) },
    })
    expect(wrapper.find('[data-testid="revoke-btn"]').exists()).toBe(true)
  })
})

describe('CommandCard - 前置条件 + 完成条件', () => {
  it('有 prerequisites 时显示"前置"区 + 条件文字', () => {
    const wrapper = mount(CommandCard, {
      props: {
        card: mkCard({
          prerequisites: [
            { text: '已有 VC >=1', met: false },
            { text: '农民到达 (42, 60)', met: true },
          ],
        }),
      },
    })
    const pre = wrapper.find('[data-testid="card-prerequisites"]')
    expect(pre.exists()).toBe(true)
    expect(pre.text()).toContain('激活条件')
    expect(pre.text()).toContain('已有 VC >=1')
    expect(pre.text()).toContain('农民到达 (42, 60)')
  })

  it('有 conditions(done_when)时显示"完成"区 + 单独"进展"行', () => {
    const wrapper = mount(CommandCard, {
      props: {
        card: mkCard({
          conditions: [{ text: '造 4 个 叉子', met: false, progress: { current: 1, target: 4, unit: '个' } }],
        }),
      },
    })
    const done = wrapper.find('[data-testid="card-conditions"]')
    expect(done.exists()).toBe(true)
    expect(done.text()).toContain('完成条件')
    expect(done.text()).toContain('造 4 个 叉子')
    // 2026-06-07:进展数字搬到独立"进展"行(card-progress),完成条件行只剩文字
    const prog = wrapper.find('[data-testid="card-progress"]')
    expect(prog.exists()).toBe(true)
    expect(prog.text()).toContain('进展')
    expect(prog.text()).toContain('1/4 个')
  })

  it('多条 prerequisites 合并到一行(、分隔)', () => {
    const wrapper = mount(CommandCard, {
      props: {
        card: mkCard({
          prerequisites: [
            { text: '已有 VC >=1', met: false },
            { text: '农民到达 (42, 60)', met: true },
          ],
        }),
      },
    })
    const pre = wrapper.find('[data-testid="card-prerequisites"]')
    expect(pre.text()).toContain('已有 VC >=1、农民到达 (42, 60)')
  })

  it('无 progress 的 conditions 不渲染进展行', () => {
    const wrapper = mount(CommandCard, {
      props: { card: mkCard({ conditions: [{ text: '侦察到 enemy_main', met: false }] }) },
    })
    expect(wrapper.find('[data-testid="card-progress"]').exists()).toBe(false)
  })

  it('无 prerequisites 时不渲染前置区', () => {
    const wrapper = mount(CommandCard, { props: { card: mkCard({}) } })
    expect(wrapper.find('[data-testid="card-prerequisites"]').exists()).toBe(false)
  })
})

describe('CommandCard - stealth_mine 农民数（需求1）', () => {
  it('无 stealth_workers 时不渲染农民数区', () => {
    const wrapper = mount(CommandCard, { props: { card: mkCard({ type: 'stealth_mine' }) } })
    expect(wrapper.find('[data-testid="stealth-workers"]').exists()).toBe(false)
  })

  it('有 stealth_workers 时显示采矿 N', () => {
    const wrapper = mount(CommandCard, {
      props: {
        card: mkCard({
          type: 'stealth_mine',
          stealth_workers: { mineral: 3, gas: 0 },
        }),
      },
    })
    const el = wrapper.find('[data-testid="stealth-workers"]')
    expect(el.exists()).toBe(true)
    expect(el.text()).toContain('采矿 3')
  })

  it('gas > 0 时同时显示采气 N', () => {
    const wrapper = mount(CommandCard, {
      props: {
        card: mkCard({
          type: 'stealth_mine',
          stealth_workers: { mineral: 3, gas: 2 },
        }),
      },
    })
    const el = wrapper.find('[data-testid="stealth-workers"]')
    expect(el.text()).toContain('采矿 3')
    expect(el.text()).toContain('采气 2')
  })

  it('gas = 0 时不渲染采气 span', () => {
    const wrapper = mount(CommandCard, {
      props: {
        card: mkCard({
          type: 'stealth_mine',
          stealth_workers: { mineral: 5, gas: 0 },
        }),
      },
    })
    expect(wrapper.text()).not.toContain('采气')
  })
})
