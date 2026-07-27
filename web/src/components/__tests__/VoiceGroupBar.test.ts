// VoiceGroupBar 组件单测
// 覆盖：
//   - 整条常显（空时也显示，提醒可以编队）
//   - 有编队时显示编队条
//   - 已编队的格显示队号 + 兵种中文×数量
//   - 未编队的格灰显占位（"—"）
//   - 兵种英文 fallback 显示原名
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import VoiceGroupBar from '@/components/VoiceGroupBar.vue'
import type { VoiceGroupView } from '@/types'

describe('VoiceGroupBar', () => {
  it('voice_groups 为空时仍常显（空槽位提醒可以编队）', () => {
    const wrapper = mount(VoiceGroupBar, { props: { voiceGroups: [] } })
    expect(wrapper.find('[data-testid="voice-group-bar"]').exists()).toBe(true)
    // 5 个空槽位都在，且显示占位 "—"
    expect(wrapper.find('[data-testid="voice-group-1"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('—')
  })

  it('有编队时显示编队条', () => {
    const groups: VoiceGroupView[] = [
      { group_id: 1, units: { ZEALOT: 8, IMMORTAL: 2 }, count: 10 },
    ]
    const wrapper = mount(VoiceGroupBar, { props: { voiceGroups: groups } })
    expect(wrapper.find('[data-testid="voice-group-bar"]').exists()).toBe(true)
  })

  it('已编队格显示正确中文兵种名', () => {
    const groups: VoiceGroupView[] = [
      { group_id: 2, units: { ZEALOT: 8, IMMORTAL: 2 }, count: 10 },
    ]
    const wrapper = mount(VoiceGroupBar, { props: { voiceGroups: groups } })
    const cell = wrapper.find('[data-testid="voice-group-2"]')
    expect(cell.text()).toContain('叉子×8')
    expect(cell.text()).toContain('不朽×2')
  })

  it('运输机兵种名正确映射', () => {
    const groups: VoiceGroupView[] = [
      { group_id: 1, units: { WARPPRISM: 1 }, count: 1 },
    ]
    const wrapper = mount(VoiceGroupBar, { props: { voiceGroups: groups } })
    expect(wrapper.find('[data-testid="voice-group-1"]').text()).toContain('运输机×1')
  })

  it('未编队的格显示占位符', () => {
    const groups: VoiceGroupView[] = [
      { group_id: 1, units: { ZEALOT: 4 }, count: 4 },
    ]
    const wrapper = mount(VoiceGroupBar, { props: { voiceGroups: groups } })
    // 组 3 未编队
    const cell3 = wrapper.find('[data-testid="voice-group-3"]')
    expect(cell3.text()).toContain('—')
  })

  it('未知兵种 fallback 显示原英文名', () => {
    const groups: VoiceGroupView[] = [
      { group_id: 5, units: { SOMEUNKNOWNUNIT: 3 }, count: 3 },
    ]
    const wrapper = mount(VoiceGroupBar, { props: { voiceGroups: groups } })
    expect(wrapper.find('[data-testid="voice-group-5"]').text()).toContain('SOMEUNKNOWNUNIT×3')
  })

  it('默认渲染 5 格', () => {
    const groups: VoiceGroupView[] = [
      { group_id: 1, units: { ZEALOT: 2 }, count: 2 },
    ]
    const wrapper = mount(VoiceGroupBar, { props: { voiceGroups: groups } })
    for (let i = 1; i <= 5; i++) {
      expect(wrapper.find(`[data-testid="voice-group-${i}"]`).exists()).toBe(true)
    }
    expect(wrapper.find('[data-testid="voice-group-6"]').exists()).toBe(false)
  })

  it('maxVoiceGroups 可配置槽位数', () => {
    const groups: VoiceGroupView[] = [
      { group_id: 1, units: { ZEALOT: 2 }, count: 2 },
    ]
    // 上限 3：只渲染 3 格
    const w3 = mount(VoiceGroupBar, { props: { voiceGroups: groups, maxVoiceGroups: 3 } })
    expect(w3.find('[data-testid="voice-group-3"]').exists()).toBe(true)
    expect(w3.find('[data-testid="voice-group-4"]').exists()).toBe(false)
    // 上限 7：渲染到第 7 格
    const w7 = mount(VoiceGroupBar, { props: { voiceGroups: groups, maxVoiceGroups: 7 } })
    expect(w7.find('[data-testid="voice-group-7"]').exists()).toBe(true)
    expect(w7.find('[data-testid="voice-group-8"]').exists()).toBe(false)
  })

  it('已编队槽边框用 groupColors 队色（= 游戏内圆环色）', () => {
    const groups: VoiceGroupView[] = [
      { group_id: 1, units: { ZEALOT: 2 }, count: 2 },
    ]
    const w = mount(VoiceGroupBar, {
      props: { voiceGroups: groups, groupColors: { '1': [255, 230, 0] } },
    })
    const cell1 = w.find('[data-testid="voice-group-1"]')
    expect(cell1.attributes('style') || '').toContain('rgb(255, 230, 0)')
    // 未编队的 2 格不带队色
    const cell2 = w.find('[data-testid="voice-group-2"]')
    expect(cell2.attributes('style') || '').not.toContain('rgb(255, 230, 0)')
  })
})
