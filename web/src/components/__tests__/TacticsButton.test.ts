// TacticsButton 组件单测
// 覆盖: 折叠初态 / 点击展开 / 每个按钮发对应 tacticalAction emit
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import TacticsButton from '@/components/TacticsButton.vue'

// 2026-05-25 attack 拆 all_in / probe;2026-05-28 加 hold
// 跟 TacticsButton.vue OPTIONS 一一对应
const OPTIONS: { verb: string; label: string; mode?: 'all_in' | 'probe' }[] = [
  { verb: 'attack',  label: '强制全体进攻', mode: 'all_in' },
  { verb: 'attack',  label: '试探性进攻',   mode: 'probe' },
  { verb: 'defend',  label: '全军防守' },
  { verb: 'hold',    label: '全军坚守' },
  { verb: 'retreat', label: '全军撤退' },
  { verb: 'recon',   label: '火力侦查' },
  { verb: 'scout',   label: '派单位探路' },
]
const UNIQUE_VERBS = [...new Set(OPTIONS.map((o) => o.verb))]

describe('TacticsButton', () => {
  it('初始状态菜单折叠（不显示选项）', () => {
    const wrapper = mount(TacticsButton)
    expect(wrapper.find('[data-testid="tactics-menu"]').exists()).toBe(false)
  })

  it('点击主按钮后展开,所有 verb 都有 testid 节点', async () => {
    const wrapper = mount(TacticsButton)
    await wrapper.find('[data-testid="tactics-toggle"]').trigger('click')
    expect(wrapper.find('[data-testid="tactics-menu"]').exists()).toBe(true)
    for (const verb of UNIQUE_VERBS) {
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

  it.each(OPTIONS)(
    '点击 "$label" emit tacticalAction($verb, $mode)',
    async ({ verb, label, mode }) => {
      const wrapper = mount(TacticsButton)
      await wrapper.find('[data-testid="tactics-toggle"]').trigger('click')
      // 用 label 文本定位 button(testid 在 verb 重复的 attack 上不唯一)
      const btn = wrapper
        .findAll('[data-testid^="tactics-option-"]')
        .find((b) => b.text().includes(label))
      expect(btn, `未找到 label="${label}" 的按钮`).toBeTruthy()
      await btn!.trigger('click')
      const emitted = wrapper.emitted('tacticalAction')
      expect(emitted).toBeTruthy()
      expect(emitted![0]).toEqual([verb, mode])
    },
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
    const allBtns = wrapper.findAll('[data-testid^="tactics-option-"]')
    for (const { label } of OPTIONS) {
      const found = allBtns.some((b) => b.text().includes(label))
      expect(found, `未找到 label="${label}" 的按钮`).toBe(true)
    }
  })
})
