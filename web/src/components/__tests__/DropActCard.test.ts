// DropActCard 组件单测（drop_act Task 9 TDD）
// 覆盖: display 渲染 / status badge 颜色 / revoke emit / conditions 渲染
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import DropActCard from '@/components/DropActCard.vue'
import type { CommandCardView } from '@/types'

function mkCard(overrides: Partial<CommandCardView> = {}): CommandCardView {
  return {
    id: 'drop-1',
    layer: 'L4',
    type: 'drop_act',
    display: '空投 4×Zealot → 二矿矿区',
    issued_at: 240.0,
    status: 'active',
    status_reason: '',
    revokable: true,
    conditions: [],
    ...overrides,
  }
}

describe('DropActCard', () => {
  it('渲染 display 文字', () => {
    const wrapper = mount(DropActCard, {
      props: { card: mkCard({ display: '空投 4×Zealot → 二矿矿区' }) },
    })
    expect(wrapper.text()).toContain('空投 4×Zealot → 二矿矿区')
  })

  it('status=active 时显示绿色 badge class', () => {
    const wrapper = mount(DropActCard, {
      props: { card: mkCard({ status: 'active' }) },
    })
    const badge = wrapper.find('[data-testid="status-badge"]')
    expect(badge.exists()).toBe(true)
    expect(badge.classes().join(' ')).toMatch(/text-success|text-green/)
  })

  it('status=pending 时显示浅蓝 badge class', () => {
    const wrapper = mount(DropActCard, {
      props: { card: mkCard({ status: 'pending' }) },
    })
    const badge = wrapper.find('[data-testid="status-badge"]')
    expect(badge.exists()).toBe(true)
    expect(badge.classes().join(' ')).toMatch(/text-sky|text-blue/)
  })

  it('status=on_hold 时显示黄/橙 badge class', () => {
    const wrapper = mount(DropActCard, {
      props: { card: mkCard({ status: 'on_hold' }) },
    })
    const badge = wrapper.find('[data-testid="status-badge"]')
    expect(badge.exists()).toBe(true)
    expect(badge.classes().join(' ')).toMatch(/text-amber|text-warn|text-yellow|text-orange/)
  })

  it('status=done 时显示灰色 badge class', () => {
    const wrapper = mount(DropActCard, {
      props: { card: mkCard({ status: 'done' }) },
    })
    const badge = wrapper.find('[data-testid="status-badge"]')
    expect(badge.exists()).toBe(true)
    expect(badge.classes().join(' ')).toMatch(/text-muted|text-gray/)
  })

  it('status_reason 有内容时副行渲染', () => {
    const wrapper = mount(DropActCard, {
      props: { card: mkCard({ status: 'on_hold', status_reason: '单位不足' }) },
    })
    expect(wrapper.text()).toContain('单位不足')
  })

  it('status_reason 为空时不渲染副行', () => {
    const wrapper = mount(DropActCard, {
      props: { card: mkCard({ status: 'active', status_reason: '' }) },
    })
    const reasonEl = wrapper.find('[data-testid="status-reason"]')
    expect(reasonEl.exists()).toBe(false)
  })

  it('点 × 按钮触发 revoke emit 并携带 card.id', async () => {
    const wrapper = mount(DropActCard, {
      props: { card: mkCard({ id: 'drop-xyz', status: 'active' }) },
    })
    const btn = wrapper.find('[data-testid="revoke-btn"]')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    const emitted = wrapper.emitted('revoke')
    expect(emitted).toBeTruthy()
    expect(emitted![0]).toEqual(['drop-xyz'])
  })

  it('status=done 时不渲染 revoke 按钮', () => {
    const wrapper = mount(DropActCard, {
      props: { card: mkCard({ status: 'done' }) },
    })
    const btn = wrapper.find('[data-testid="revoke-btn"]')
    expect(btn.exists()).toBe(false)
  })

  it('revokable=false 时不渲染 revoke 按钮', () => {
    const wrapper = mount(DropActCard, {
      props: { card: mkCard({ revokable: false, status: 'active' }) },
    })
    const btn = wrapper.find('[data-testid="revoke-btn"]')
    expect(btn.exists()).toBe(false)
  })

  it('有 conditions 时渲染条件文字', () => {
    const card = mkCard({
      status: 'active',
      conditions: [
        { text: '造 4 个叉子', met: false },
        { text: '30 秒后', met: true },
      ],
    })
    const wrapper = mount(DropActCard, { props: { card } })
    expect(wrapper.text()).toContain('造 4 个叉子')
    expect(wrapper.text()).toContain('30 秒后')
  })
})
