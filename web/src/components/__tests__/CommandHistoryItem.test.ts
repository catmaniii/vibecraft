// #4 历史三层展开单测。
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import CommandHistoryItem from '@/components/CommandHistoryItem.vue'
import type { RecentCommandView } from '@/types'

function mkCmd(overrides: Partial<RecentCommandView> = {}): RecentCommandView {
  return {
    text: '派农民去11点修水晶',
    ts: 125,
    interpretation_zh: '代理建造：农民到 (42,60) 修 BE',
    status: 'active',
    directives: [
      { id: 'd1', display: '探机 移动到 (42,60)', status: 'completed', progress: null },
      {
        id: 'd2',
        display: '建 BE 在 (42,60)',
        status: 'active',
        progress: { current: 1, target: 4, unit: '个' },
      },
    ],
    ...overrides,
  }
}

describe('CommandHistoryItem - 折叠/展开', () => {
  it('折叠态显示文本，详情隐藏', () => {
    const wrapper = mount(CommandHistoryItem, { props: { cmd: mkCmd() } })
    expect(wrapper.text()).toContain('派农民去11点修水晶')
    expect(wrapper.find('[data-testid="history-detail"]').exists()).toBe(false)
  })

  it('点击展开三层：输入文本 + 识别解读 + directive 状态', async () => {
    const wrapper = mount(CommandHistoryItem, { props: { cmd: mkCmd() } })
    await wrapper.find('[data-testid="history-toggle"]').trigger('click')
    const detail = wrapper.find('[data-testid="history-detail"]')
    expect(detail.exists()).toBe(true)
    // ② 识别解读
    expect(detail.text()).toContain('代理建造：农民到 (42,60) 修 BE')
    // ③ directive 列表 + 中文状态
    const dirs = wrapper.findAll('[data-testid="history-directive"]')
    expect(dirs).toHaveLength(2)
    expect(detail.text()).toContain('探机 移动到 (42,60)')
    expect(detail.text()).toContain('已完成')
    expect(detail.text()).toContain('进行中')
    // active 带进度
    expect(detail.text()).toContain('1/4 个')
  })

  it('再次点击收起', async () => {
    const wrapper = mount(CommandHistoryItem, { props: { cmd: mkCmd() } })
    const toggle = wrapper.find('[data-testid="history-toggle"]')
    await toggle.trigger('click')
    expect(wrapper.find('[data-testid="history-detail"]').exists()).toBe(true)
    await toggle.trigger('click')
    expect(wrapper.find('[data-testid="history-detail"]').exists()).toBe(false)
  })

  it('状态中文映射：cancelled→已手动取消 / terminated→已终止', async () => {
    const wrapper = mount(CommandHistoryItem, {
      props: {
        cmd: mkCmd({
          directives: [
            { id: 'a', display: '进攻 敌方二矿', status: 'cancelled', progress: null },
            { id: 'b', display: '凤凰 骚扰 敌方三矿', status: 'terminated', progress: null },
            { id: 'c', display: '建 VR', status: 'waiting', progress: null },
          ],
        }),
      },
    })
    await wrapper.find('[data-testid="history-toggle"]').trigger('click')
    const text = wrapper.find('[data-testid="history-detail"]').text()
    expect(text).toContain('已手动取消')
    expect(text).toContain('已终止')
    expect(text).toContain('等待激活')
  })

  it('无 directive 时显示占位', async () => {
    const wrapper = mount(CommandHistoryItem, {
      props: { cmd: mkCmd({ directives: [], interpretation_zh: '没听懂' }) },
    })
    await wrapper.find('[data-testid="history-toggle"]').trigger('click')
    expect(wrapper.find('[data-testid="history-detail"]').text()).toContain('无可执行指令')
  })
})

describe('CommandHistoryItem - 状态徽章上色', () => {
  it('识别失败 → 红色徽章 + 左边框标红', () => {
    const wrapper = mount(CommandHistoryItem, { props: { cmd: mkCmd({ status: 'failed' }) } })
    const chip = wrapper.find('[data-testid="history-status-chip"]')
    expect(chip.text()).toBe('识别失败')
    expect(chip.classes().join(' ')).toContain('text-danger')
    expect(wrapper.find('[data-testid="history-item"]').classes().join(' ')).toContain('border-l-danger')
  })

  it('各状态映射对应中文标签', () => {
    const cases: Array<[RecentCommandView['status'], string]> = [
      ['active', '执行中'],
      ['pending', '等待生效'],
      ['completed', '已完成'],
      ['terminated', '已终止'],
      ['cancelled', '已手动取消'],
    ]
    for (const [status, label] of cases) {
      const wrapper = mount(CommandHistoryItem, { props: { cmd: mkCmd({ status }) } })
      expect(wrapper.find('[data-testid="history-status-chip"]').text()).toBe(label)
    }
  })
})
