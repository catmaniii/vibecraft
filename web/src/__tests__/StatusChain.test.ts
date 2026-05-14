// StatusChain 组件渲染单测
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import StatusChain from '@/components/StatusChain.vue'
import type { SystemStatus } from '@/types'

function mkStatus(overrides: Partial<SystemStatus> = {}): SystemStatus {
  return {
    link: 'connected',
    sc2: 'playing',
    bot: 'running',
    detail: '',
    ...overrides,
  }
}

describe('StatusChain', () => {
  it('全绿时渲染「系统正常」', () => {
    const wrapper = mount(StatusChain, {
      props: { status: mkStatus() },
    })
    expect(wrapper.text()).toContain('系统正常')
  })

  it('link 断线时展开四节链', () => {
    const wrapper = mount(StatusChain, {
      props: {
        status: mkStatus({ link: 'disconnected', sc2: 'idle', bot: 'idle' }),
      },
    })
    // 展开时应出现「服务端」标签
    expect(wrapper.text()).toContain('服务端')
    expect(wrapper.text()).toContain('手机')
  })

  it('sc2 crashed 时展开并出现 SC2 标签', () => {
    const wrapper = mount(StatusChain, {
      props: {
        status: mkStatus({ sc2: 'crashed', bot: 'error', detail: '进程崩溃' }),
      },
    })
    expect(wrapper.text()).toContain('SC2')
    expect(wrapper.text()).toContain('进程崩溃')
  })

  it('sc2 idle + link connected 时展开（非全绿）', () => {
    const wrapper = mount(StatusChain, {
      props: {
        status: mkStatus({ sc2: 'idle', bot: 'idle' }),
      },
    })
    // sc2 idle = 灰色，不是全绿，应展开
    expect(wrapper.text()).toContain('SC2')
  })
})
