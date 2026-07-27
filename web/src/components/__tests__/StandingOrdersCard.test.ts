// StandingOrdersCard 组件单测（P1.5 TDD）
// 覆盖: 空态 / 列表渲染 / revoke emit
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import StandingOrdersCard from '@/components/StandingOrdersCard.vue'
import type { StandingOrderView } from '@/types'

function mkOrder(overrides: Partial<StandingOrderView> = {}): StandingOrderView {
  return {
    id: 'order-1',
    display: 'Phoenix patrol natural',
    issued_at: 120.5,
    selector: { unit_type: 'Phoenix' },
    task_summary: '凤凰 patrol 二矿',
    ...overrides,
  }
}

describe('StandingOrdersCard', () => {
  it('空态时展示「暂无持久指令」提示', () => {
    const wrapper = mount(StandingOrdersCard, {
      props: { orders: [] },
    })
    expect(wrapper.text()).toContain('暂无持久指令')
  })

  it('有指令时渲染每条 display 文本', () => {
    const orders = [
      mkOrder({ id: 'o1', display: 'Phoenix patrol natural' }),
      mkOrder({ id: 'o2', display: 'Zealot push green' }),
    ]
    const wrapper = mount(StandingOrdersCard, {
      props: { orders },
    })
    expect(wrapper.text()).toContain('Phoenix patrol natural')
    expect(wrapper.text()).toContain('Zealot push green')
    // 空态提示不应出现
    expect(wrapper.text()).not.toContain('暂无持久指令')
  })

  it('点 × 按钮时 emit revokeOrder 并携带 order.id', async () => {
    const order = mkOrder({ id: 'abc-123' })
    const wrapper = mount(StandingOrdersCard, {
      props: { orders: [order] },
    })
    // 找 × 撤销按钮并点击
    const btn = wrapper.find('[data-testid="revoke-btn"]')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    // 验证 emit
    const emitted = wrapper.emitted('revokeOrder')
    expect(emitted).toBeTruthy()
    expect(emitted![0]).toEqual(['abc-123'])
  })
})
