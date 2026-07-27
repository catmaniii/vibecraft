// MacroButton 组件单测（2026-07-06 三维度重构后）
// 覆盖: 无旧 1-5 chips / 「多开一个矿」按钮 emit / 采矿策略 emit / miningPriority 高亮 / 农民策略保留
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import MacroButton from '@/components/MacroButton.vue'

describe('MacroButton', () => {
  // ─── 维度1：开矿 ───────────────────────────────────────────────

  it('不存在旧的 1-5 矿 chips', () => {
    const wrapper = mount(MacroButton)
    for (const n of [1, 2, 3, 4, 5]) {
      expect(wrapper.find(`[data-testid="expand-chip-${n}"]`).exists()).toBe(false)
    }
    expect(wrapper.find('[data-testid="expand-chip-max"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="expand-chip-clear"]').exists()).toBe(false)
  })

  it('存在「多开一个矿」按钮', () => {
    const wrapper = mount(MacroButton)
    expect(wrapper.find('[data-testid="expand-one-more-btn"]').exists()).toBe(true)
  })

  it('点击「多开一个矿」emit macroAction("expand", "one_more")', async () => {
    const wrapper = mount(MacroButton)
    await wrapper.find('[data-testid="expand-one-more-btn"]').trigger('click')
    const emitted = wrapper.emitted('macroAction')
    expect(emitted).toBeTruthy()
    expect(emitted![0]).toEqual(['expand', 'one_more'])
  })

  // ─── 维度2：采矿策略 ────────────────────────────────────────────

  it('存在三个采矿策略 chip（mineral / gas / default）', () => {
    const wrapper = mount(MacroButton)
    expect(wrapper.find('[data-testid="mining-chip-mineral"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="mining-chip-gas"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="mining-chip-default"]').exists()).toBe(true)
  })

  it('点击 mineral chip emit macroAction("mining", "mineral")', async () => {
    const wrapper = mount(MacroButton)
    await wrapper.find('[data-testid="mining-chip-mineral"]').trigger('click')
    const emitted = wrapper.emitted('macroAction')
    expect(emitted).toBeTruthy()
    expect(emitted![0]).toEqual(['mining', 'mineral'])
  })

  it('点击 gas chip emit macroAction("mining", "gas")', async () => {
    const wrapper = mount(MacroButton)
    await wrapper.find('[data-testid="mining-chip-gas"]').trigger('click')
    const emitted = wrapper.emitted('macroAction')
    expect(emitted).toBeTruthy()
    expect(emitted![0]).toEqual(['mining', 'gas'])
  })

  it('点击 default chip emit macroAction("mining", "default")', async () => {
    const wrapper = mount(MacroButton)
    await wrapper.find('[data-testid="mining-chip-default"]').trigger('click')
    const emitted = wrapper.emitted('macroAction')
    expect(emitted).toBeTruthy()
    expect(emitted![0]).toEqual(['mining', 'default'])
  })

  it('miningPriority="mineral" 时 mineral chip 高亮，其余不高亮', () => {
    const wrapper = mount(MacroButton, { props: { miningPriority: 'mineral' } })
    const mineralBtn = wrapper.find('[data-testid="mining-chip-mineral"]')
    const gasBtn = wrapper.find('[data-testid="mining-chip-gas"]')
    const defaultBtn = wrapper.find('[data-testid="mining-chip-default"]')
    expect(mineralBtn.classes()).toContain('text-accent')
    expect(gasBtn.classes()).not.toContain('text-accent')
    expect(defaultBtn.classes()).not.toContain('text-accent')
  })

  it('miningPriority="gas" 时 gas chip 高亮，其余不高亮', () => {
    const wrapper = mount(MacroButton, { props: { miningPriority: 'gas' } })
    const mineralBtn = wrapper.find('[data-testid="mining-chip-mineral"]')
    const gasBtn = wrapper.find('[data-testid="mining-chip-gas"]')
    const defaultBtn = wrapper.find('[data-testid="mining-chip-default"]')
    expect(gasBtn.classes()).toContain('text-accent')
    expect(mineralBtn.classes()).not.toContain('text-accent')
    expect(defaultBtn.classes()).not.toContain('text-accent')
  })

  it('miningPriority=null 时 default chip 高亮', () => {
    const wrapper = mount(MacroButton, { props: { miningPriority: null } })
    const defaultBtn = wrapper.find('[data-testid="mining-chip-default"]')
    const mineralBtn = wrapper.find('[data-testid="mining-chip-mineral"]')
    expect(defaultBtn.classes()).toContain('text-accent')
    expect(mineralBtn.classes()).not.toContain('text-accent')
  })

  it('miningPriority 未传（undefined）时 default chip 高亮', () => {
    const wrapper = mount(MacroButton)
    const defaultBtn = wrapper.find('[data-testid="mining-chip-default"]')
    expect(defaultBtn.classes()).toContain('text-accent')
  })

  // ─── 维度3：农民生产（保留不动） ──────────────────────────────────

  it('存在农民策略三个 chip（stop / max / default）', () => {
    const wrapper = mount(MacroButton)
    expect(wrapper.find('[data-testid="worker-chip-stop"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="worker-chip-max"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="worker-chip-default"]').exists()).toBe(true)
  })

  it('点击 worker-chip-stop emit macroAction("workers", "stop")', async () => {
    const wrapper = mount(MacroButton)
    await wrapper.find('[data-testid="worker-chip-stop"]').trigger('click')
    const emitted = wrapper.emitted('macroAction')
    expect(emitted).toBeTruthy()
    expect(emitted![0]).toEqual(['workers', 'stop'])
  })

  it('workerMode=null 时 worker default chip 高亮', () => {
    const wrapper = mount(MacroButton, { props: { workerMode: null } })
    const defaultBtn = wrapper.find('[data-testid="worker-chip-default"]')
    expect(defaultBtn.classes()).toContain('text-accent')
  })

  it('workerMode="stop" 时 worker stop chip 高亮', () => {
    const wrapper = mount(MacroButton, { props: { workerMode: 'stop' } })
    const stopBtn = wrapper.find('[data-testid="worker-chip-stop"]')
    const defaultBtn = wrapper.find('[data-testid="worker-chip-default"]')
    expect(stopBtn.classes()).toContain('text-accent')
    expect(defaultBtn.classes()).not.toContain('text-accent')
  })
})
