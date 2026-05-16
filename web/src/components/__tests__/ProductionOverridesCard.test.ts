// ProductionOverridesCard 组件单测（P2 TDD）
// 覆盖: 空态 / 列表渲染 / revoke emit
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ProductionOverridesCard from '@/components/ProductionOverridesCard.vue'
import type { ProductionOverrideView } from '@/types'

function mkOverride(overrides: Partial<ProductionOverrideView> = {}): ProductionOverrideView {
  return {
    id: 'override-1',
    display: '出 2 哨兵',
    issued_at: 180.0,
    directive_type: 'production_override',
    ...overrides,
  }
}

describe('ProductionOverridesCard', () => {
  it('空态时展示「暂无产能调整」提示', () => {
    const wrapper = mount(ProductionOverridesCard, {
      props: { orders: [] },
    })
    expect(wrapper.text()).toContain('暂无产能调整')
  })

  it('有指令时渲染每条 display 文本', () => {
    const orders = [
      mkOverride({ id: 'o1', display: '出 2 哨兵', directive_type: 'production_override' }),
      mkOverride({ id: 'o2', display: '研 Blink', directive_type: 'tech_override' }),
      mkOverride({ id: 'o3', display: '开 3 矿', directive_type: 'expansion_override' }),
    ]
    const wrapper = mount(ProductionOverridesCard, {
      props: { orders },
    })
    expect(wrapper.text()).toContain('出 2 哨兵')
    expect(wrapper.text()).toContain('研 Blink')
    expect(wrapper.text()).toContain('开 3 矿')
    // 空态提示不应出现
    expect(wrapper.text()).not.toContain('暂无产能调整')
  })

  it('点 × 按钮时 emit revoke 并携带 order.id', async () => {
    const order = mkOverride({ id: 'abc-456' })
    const wrapper = mount(ProductionOverridesCard, {
      props: { orders: [order] },
    })
    // 找 × 撤销按钮并点击
    const btn = wrapper.find('[data-testid="revoke-btn"]')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    // 验证 emit
    const emitted = wrapper.emitted('revoke')
    expect(emitted).toBeTruthy()
    expect(emitted![0]).toEqual(['abc-456'])
  })
})
