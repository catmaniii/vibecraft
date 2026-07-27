// CommandBubbleQueue.vue 组件单测（2026-07-08：命令气泡队列纯展示层）
//
// 覆盖场景：
//   1. 空数组 → 不渲染队列容器
//   2. 单条 pending 气泡：正确的状态标签 + 文本 + 无 detail
//   3. done 气泡：绿色语义 class + 显示 detail
//   4. failed 气泡：红色语义 class + 显示 detail
//   5. 多条气泡按传入顺序渲染（新的排最后，靠近输入框）

import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import CommandBubbleQueue from '@/components/CommandBubbleQueue.vue'
import type { CommandBubble } from '@/components/CommandBubbleQueue.vue'

function mountQueue(bubbles: CommandBubble[]) {
  return mount(CommandBubbleQueue, { props: { bubbles } })
}

describe('CommandBubbleQueue', () => {
  it('空数组 → 不渲染队列容器', () => {
    const wrapper = mountQueue([])
    expect(wrapper.find('[data-testid="cmd-bubble-queue"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="cmd-bubble"]').exists()).toBe(false)
  })

  it('单条 pending 气泡：显示文本，无 detail', () => {
    const wrapper = mountQueue([{ id: '1', text: '切4bg', ts: 1, status: 'pending' }])
    const bubble = wrapper.find('[data-testid="cmd-bubble"]')
    expect(bubble.exists()).toBe(true)
    expect(bubble.attributes('data-status')).toBe('pending')
    expect(wrapper.find('[data-testid="cmd-bubble-text"]').text()).toBe('切4bg')
    expect(wrapper.find('[data-testid="cmd-bubble-detail"]').exists()).toBe(false)
    // 琥珀色语义
    expect(bubble.classes().join(' ')).toContain('amber')
  })

  it('done 气泡：成功语义 class + 显示 detail', () => {
    const wrapper = mountQueue([
      { id: '1', text: '切4bg', ts: 1, status: 'done', detail: '切到 4bg 开局' },
    ])
    const bubble = wrapper.find('[data-testid="cmd-bubble"]')
    expect(bubble.attributes('data-status')).toBe('done')
    expect(bubble.classes().join(' ')).toContain('success')
    expect(wrapper.find('[data-testid="cmd-bubble-detail"]').text()).toContain('切到 4bg 开局')
  })

  it('failed 气泡：失败语义 class + 显示 detail', () => {
    const wrapper = mountQueue([
      { id: '1', text: '乱说', ts: 1, status: 'failed', detail: '[解析失败] 听不懂' },
    ])
    const bubble = wrapper.find('[data-testid="cmd-bubble"]')
    expect(bubble.attributes('data-status')).toBe('failed')
    expect(bubble.classes().join(' ')).toContain('danger')
    expect(wrapper.find('[data-testid="cmd-bubble-detail"]').text()).toContain('[解析失败] 听不懂')
  })

  it('多条气泡按传入顺序渲染（新的排最后）', () => {
    const wrapper = mountQueue([
      { id: '1', text: '第一条', ts: 1, status: 'pending' },
      { id: '2', text: '第二条', ts: 2, status: 'done', detail: 'ok' },
      { id: '3', text: '第三条', ts: 3, status: 'failed', detail: 'bad' },
    ])
    const texts = wrapper.findAll('[data-testid="cmd-bubble-text"]').map((n) => n.text())
    expect(texts).toEqual(['第一条', '第二条', '第三条'])
  })
})
