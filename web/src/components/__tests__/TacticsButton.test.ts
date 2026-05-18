// TacticsButton 组件单测
// 覆盖: 折叠初态 / 点击展开 / 每个按钮发对应 tacticalAction emit
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import TacticsButton from '@/components/TacticsButton.vue'

const VERBS = ['attack', 'defend', 'retreat', 'recon', 'scout']
const LABELS = ['全军进攻', '全军防守', '全军撤退', '火力侦查', '派单位探路']

describe('TacticsButton', () => {
  it('初始状态菜单折叠（不显示 5 选项）', () => {
    const wrapper = mount(TacticsButton)
    expect(wrapper.find('[data-testid="tactics-menu"]').exists()).toBe(false)
  })

  it('点击主按钮后展开 5 选项', async () => {
    const wrapper = mount(TacticsButton)
    await wrapper.find('[data-testid="tactics-toggle"]').trigger('click')
    expect(wrapper.find('[data-testid="tactics-menu"]').exists()).toBe(true)
    // 5 个选项全部存在
    for (const verb of VERBS) {
      expect(wrapper.find(`[data-testid="tactics-option-${verb}"]`).exists()).toBe(true)
    }
  })

  it('再次点击主按钮收回菜单', async () => {
    const wrapper = mount(TacticsButton)
    const toggle = wrapper.find('[data-testid="tactics-toggle"]')
    await toggle.trigger('click')
    expect(wrapper.find('[data-testid="tactics-menu"]').exists()).toBe(true)
    await toggle.trigger('click')
    expect(wrapper.find('[data-testid="tactics-menu"]').exists()).toBe(false)
  })

  it.each(VERBS.map((v, i) => [v, LABELS[i]]))(
    '点击 "%s" 选项 emit tacticalAction("%s")',
    async (verb, _label) => {
      const wrapper = mount(TacticsButton)
      // 先展开
      await wrapper.find('[data-testid="tactics-toggle"]').trigger('click')
      // 点对应选项
      await wrapper.find(`[data-testid="tactics-option-${verb}"]`).trigger('click')
      const emitted = wrapper.emitted('tacticalAction')
      expect(emitted).toBeTruthy()
      expect(emitted![0]).toEqual([verb])
    }
  )

  it('点击选项后菜单收回', async () => {
    const wrapper = mount(TacticsButton)
    await wrapper.find('[data-testid="tactics-toggle"]').trigger('click')
    await wrapper.find('[data-testid="tactics-option-attack"]').trigger('click')
    expect(wrapper.find('[data-testid="tactics-menu"]').exists()).toBe(false)
  })

  it('选项文本与期望 label 对应', async () => {
    const wrapper = mount(TacticsButton)
    await wrapper.find('[data-testid="tactics-toggle"]').trigger('click')
    for (const [verb, label] of VERBS.map((v, i) => [v, LABELS[i]])) {
      const btn = wrapper.find(`[data-testid="tactics-option-${verb}"]`)
      expect(btn.text()).toContain(label as string)
    }
  })
})
