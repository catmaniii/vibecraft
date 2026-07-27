// QrShareButton 组件单测
// 覆盖: 初态弹窗折叠 / 点击展开 / QR img src 指向 /api/qr 且 data 为当前首页 URL / 关闭
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import QrShareButton from '@/components/QrShareButton.vue'

describe('QrShareButton', () => {
  it('初始状态弹窗折叠（不显示 modal）', () => {
    const wrapper = mount(QrShareButton)
    expect(wrapper.find('[data-testid="qr-share-modal"]').exists()).toBe(false)
  })

  it('点击触发按钮后弹出 modal', async () => {
    const wrapper = mount(QrShareButton)
    await wrapper.find('[data-testid="qr-share-open"]').trigger('click')
    expect(wrapper.find('[data-testid="qr-share-modal"]').exists()).toBe(true)
  })

  it('QR 图 src 指向 /api/qr，data 为当前页面 URL（含 origin）', async () => {
    const wrapper = mount(QrShareButton)
    await wrapper.find('[data-testid="qr-share-open"]').trigger('click')
    const img = wrapper.find('[data-testid="qr-share-img"]')
    const src = img.attributes('src') || ''
    expect(src.startsWith('/api/qr?data=')).toBe(true)
    // data 应是 encode 过的当前 URL（jsdom 默认 http://localhost/）
    const encoded = src.slice('/api/qr?data='.length)
    expect(decodeURIComponent(encoded)).toContain(window.location.origin)
  })

  it('显示的 URL 文本与编码进 QR 的一致', async () => {
    const wrapper = mount(QrShareButton)
    await wrapper.find('[data-testid="qr-share-open"]').trigger('click')
    const urlText = wrapper.find('[data-testid="qr-share-url"]').text()
    expect(urlText).toContain(window.location.origin)
  })

  it('size=sm 渲染小药丸触发按钮（文案「二维码」）', () => {
    const wrapper = mount(QrShareButton, { props: { size: 'sm' } })
    expect(wrapper.find('[data-testid="qr-share-open"]').text()).toContain('二维码')
  })
})
