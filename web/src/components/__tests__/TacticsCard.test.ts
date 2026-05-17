// TacticsCard 组件单测（P3.6 TDD）
// 覆盖: 空态 / 列表渲染 / revoke emit
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import TacticsCard from '@/components/TacticsCard.vue'
import type { TacticalObjectiveView } from '@/types'

function mkTactic(overrides: Partial<TacticalObjectiveView> = {}): TacticalObjectiveView {
  return {
    id: 'tac-1',
    display: '进攻 enemy_natural',
    verb: 'attack',
    target_area: 'enemy_natural',
    issued_at: 180.0,
    ...overrides,
  }
}

describe('TacticsCard', () => {
  it('空态时展示「暂无战术指令」提示', () => {
    const wrapper = mount(TacticsCard, {
      props: { tactics: [] },
    })
    expect(wrapper.text()).toContain('暂无战术指令')
  })

  it('有战术指令时渲染 verb 中文 + target_area', () => {
    const tactics = [
      mkTactic({ id: 't1', verb: 'attack', target_area: 'enemy_natural' }),
      mkTactic({ id: 't2', verb: 'scout', target_area: 'enemy_main' }),
      mkTactic({ id: 't3', verb: 'defend', target_area: null }),
    ]
    const wrapper = mount(TacticsCard, {
      props: { tactics },
    })
    expect(wrapper.text()).toContain('进攻 enemy_natural')
    expect(wrapper.text()).toContain('探 enemy_main')
    expect(wrapper.text()).toContain('守')
    // 空态提示不应出现
    expect(wrapper.text()).not.toContain('暂无战术指令')
  })

  it('点 × 按钮时 emit revoke 并携带 tactic.id', async () => {
    const tac = mkTactic({ id: 'xyz-456' })
    const wrapper = mount(TacticsCard, {
      props: { tactics: [tac] },
    })
    const btn = wrapper.find('[data-testid="revoke-btn"]')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    const emitted = wrapper.emitted('revoke')
    expect(emitted).toBeTruthy()
    expect(emitted![0]).toEqual(['xyz-456'])
  })
})
