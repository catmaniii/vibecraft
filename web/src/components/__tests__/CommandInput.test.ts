// CommandInput.vue 组件单测（Task 7：语音/文字微信式切换）
//
// 覆盖场景：
//   1. 默认语音模式：渲染 VoiceInput，不渲染 text input
//   2. toggle → 文字模式：渲染 text input + 写 localStorage
//   3. toggle 两次：回到语音模式 + localStorage 更新
//   4. localStorage 记忆：上次存了 'text'，挂载直接用文字模式
//   5. VoiceInput recognized → emit send，command 帧含正确 text / client_id / issued_at
//   6. canSend=false 时 recognized 不 emit
//   7. voiceSupported=false → 强制文字模式，不渲染 VoiceInput，显示 HTTPS 提示
//   8. 关闭 HTTPS 提示后提示消失

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ref, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import CommandInput from '@/components/CommandInput.vue'
import type { CommandEchoFrame, CommandReceivedFrame, RecentCommandView, TranscriptFrame } from '@/types'

// ── VoiceInput 桩组件（不实际录音，方便控制 emit，避免引入 AudioContext 依赖） ──

const VoiceInputStub = {
  name: 'VoiceInput',
  props: ['sendAudioChunk', 'sendAudioEnd', 'sendAudioCancel', 'lastTranscript'],
  emits: ['recognized'],
  template: '<div data-testid="voice-input-stub"></div>',
}

// ── 工具函数 ──────────────────────────────────────────────────────────────────

function makeProps(overrides?: Partial<{
  canSend: boolean
  lastEcho: CommandEchoFrame | null
  lastReceived: CommandReceivedFrame | null
  recentCommands: readonly RecentCommandView[]
  sendAudioChunk: (seq: number, pcm: string) => void
  sendAudioEnd: () => void
  sendAudioCancel: () => void
  lastTranscript: TranscriptFrame | null
}>) {
  return {
    canSend: true,
    lastEcho: null as CommandEchoFrame | null,
    lastReceived: null as CommandReceivedFrame | null,
    recentCommands: [] as readonly RecentCommandView[],
    sendAudioChunk: vi.fn() as (seq: number, pcm: string) => void,
    sendAudioEnd: vi.fn() as () => void,
    sendAudioCancel: vi.fn() as () => void,
    lastTranscript: null as TranscriptFrame | null,
    ...overrides,
  }
}

// 构造一个 command_received 帧（模拟 server ack）
function received(text: string, ts: number): CommandReceivedFrame {
  return { type: 'command_received', text, ts }
}

// 构造一个 command_echo 帧（模拟 server 解析完成）
function echo(user_text: string, interpretation: string, ts = 999): CommandEchoFrame {
  return { type: 'command_echo', user_text, interpretation, ts }
}

// 设置 window.isSecureContext + navigator.mediaDevices，控制 voiceSupported 判定
function setupVoiceSupported(supported: boolean) {
  Object.defineProperty(window, 'isSecureContext', {
    value: supported,
    writable: true,
    configurable: true,
  })
  if (supported) {
    Object.defineProperty(navigator, 'mediaDevices', {
      value: { getUserMedia: vi.fn() },
      writable: true,
      configurable: true,
    })
  } else {
    Object.defineProperty(navigator, 'mediaDevices', {
      value: undefined,
      writable: true,
      configurable: true,
    })
  }
}

function mountCommandInput(props = makeProps()) {
  return mount(CommandInput, {
    props,
    global: {
      stubs: { VoiceInput: VoiceInputStub },
    },
  })
}

// ── 测试套件 ──────────────────────────────────────────────────────────────────

describe('CommandInput', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    // 默认：voiceSupported = true
    setupVoiceSupported(true)
  })

  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
    // 还原 mediaDevices（避免影响下一条测试）
    Object.defineProperty(navigator, 'mediaDevices', {
      value: undefined,
      writable: true,
      configurable: true,
    })
  })

  // ── 1. 默认文字模式（2026-06-10 用户：默认改文字）──────────────────────────

  it('默认渲染文字模式：text input 存在，VoiceInput 不渲染', () => {
    const wrapper = mountCommandInput()

    expect(wrapper.find('input[type="text"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="voice-input-stub"]').exists()).toBe(false)
  })

  // ── 2. toggle → 语音模式 ──────────────────────────────────────────────────

  it('点 toggle 切到语音模式：VoiceInput 出现，text input 消失，localStorage 写 voice', async () => {
    const wrapper = mountCommandInput()

    await wrapper.find('[data-testid="input-mode-toggle"]').trigger('click')
    await nextTick()

    expect(wrapper.find('[data-testid="voice-input-stub"]').exists()).toBe(true)
    expect(wrapper.find('input[type="text"]').exists()).toBe(false)
    expect(localStorage.getItem('vibecraft.input_mode')).toBe('voice')
  })

  // ── 3. toggle 两次回到文字模式 ───────────────────────────────────────────

  it('toggle 两次回到文字模式，localStorage 更新为 text', async () => {
    const wrapper = mountCommandInput()

    const toggleBtn = wrapper.find('[data-testid="input-mode-toggle"]')
    await toggleBtn.trigger('click')
    await nextTick()
    await toggleBtn.trigger('click')
    await nextTick()

    expect(wrapper.find('input[type="text"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="voice-input-stub"]').exists()).toBe(false)
    expect(localStorage.getItem('vibecraft.input_mode')).toBe('text')
  })

  // ── 4. localStorage 记忆文字模式 ─────────────────────────────────────────

  it('localStorage 存了 text → 挂载直接用文字模式', () => {
    localStorage.setItem('vibecraft.input_mode', 'text')
    const wrapper = mountCommandInput()

    expect(wrapper.find('input[type="text"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="voice-input-stub"]').exists()).toBe(false)
  })

  // ── 5. VoiceInput recognized → emit send command 帧 ─────────────────────

  it('VoiceInput recognized → emit send，command 帧包含正确 text / client_id / issued_at', async () => {
    const wrapper = mountCommandInput()
    // 默认文字模式 → 先切到语音模式才渲染 VoiceInput
    await wrapper.find('[data-testid="input-mode-toggle"]').trigger('click')
    await nextTick()

    // 从 VoiceInput 桩组件触发 recognized 事件（模拟语音识别出结果）
    const voiceInputStub = wrapper.findComponent({ name: 'VoiceInput' })
    await voiceInputStub.vm.$emit('recognized', '切4BG')
    await nextTick()

    const emitted = wrapper.emitted('send')
    expect(emitted).toBeTruthy()
    expect(emitted![0][0]).toMatchObject({
      type: 'command',
      text: '切4BG',
    })
    // 含 client_id 和有效 issued_at
    const frame = emitted![0][0] as { client_id: string; issued_at: number }
    expect(frame.client_id).toBeTruthy()
    expect(frame.issued_at).toBeGreaterThan(0)
  })

  // ── 6. canSend=false 时 recognized 不 emit ───────────────────────────────

  it('canSend=false 时语音 recognized 不 emit send', async () => {
    const wrapper = mountCommandInput(makeProps({ canSend: false }))
    // 默认文字模式 → 先切到语音模式
    await wrapper.find('[data-testid="input-mode-toggle"]').trigger('click')
    await nextTick()

    const voiceInputStub = wrapper.findComponent({ name: 'VoiceInput' })
    await voiceInputStub.vm.$emit('recognized', '切4BG')
    await nextTick()

    expect(wrapper.emitted('send')).toBeFalsy()
  })

  // ── 7. voiceSupported=false → 强制文字模式 + HTTPS 提示 ──────────────────

  it('voiceSupported=false → 强制文字模式，不渲染 VoiceInput，显示 HTTPS 提示', () => {
    setupVoiceSupported(false)
    const wrapper = mountCommandInput()

    expect(wrapper.find('input[type="text"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="voice-input-stub"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="https-hint"]').exists()).toBe(true)
  })

  // ── 8. 关闭 HTTPS 提示 ───────────────────────────────────────────────────

  it('关闭 HTTPS 提示后提示消失', async () => {
    setupVoiceSupported(false)
    const wrapper = mountCommandInput()

    expect(wrapper.find('[data-testid="https-hint"]').exists()).toBe(true)

    await wrapper.find('[data-testid="https-hint-close"]').trigger('click')
    await nextTick()

    expect(wrapper.find('[data-testid="https-hint"]').exists()).toBe(false)
  })

  // ── 9. 文字模式发送按钮微信式：空隐藏、有内容才显示 ──────────────────────

  it('文字模式下发送按钮默认隐藏，输入框有内容才出现（微信式）', async () => {
    const wrapper = mountCommandInput()  // 默认就是文字模式

    // 空输入 → 无发送按钮
    expect(wrapper.find('[data-testid="cmd-send-btn"]').exists()).toBe(false)

    // 输入内容 → 发送按钮出现
    await wrapper.find('input[type="text"]').setValue('切4BG')
    await nextTick()
    expect(wrapper.find('[data-testid="cmd-send-btn"]').exists()).toBe(true)

    // 清空 → 又隐藏
    await wrapper.find('input[type="text"]').setValue('   ')
    await nextTick()
    expect(wrapper.find('[data-testid="cmd-send-btn"]').exists()).toBe(false)
  })

  // ── 10-15. 命令气泡队列（2026-07-08：非阻塞多条命令并存）─────────────────

  describe('命令气泡队列', () => {
    beforeEach(() => {
      vi.useFakeTimers()
    })
    afterEach(() => {
      vi.useRealTimers()
    })

    it('command_received 到达 → 出现一个 pending(识别中) 气泡', async () => {
      const wrapper = mountCommandInput()
      await wrapper.setProps({ lastReceived: received('切4bg', 100) })

      const bubble = wrapper.find('[data-testid="cmd-bubble"]')
      expect(bubble.exists()).toBe(true)
      expect(bubble.attributes('data-status')).toBe('pending')
      expect(wrapper.find('[data-testid="cmd-bubble-text"]').text()).toBe('切4bg')
    })

    it('两条 command_received 相继到达 → 两个气泡并存，互不覆盖', async () => {
      const wrapper = mountCommandInput()
      await wrapper.setProps({ lastReceived: received('切4bg', 100) })
      await wrapper.setProps({ lastReceived: received('全军撤退', 101) })

      const bubbles = wrapper.findAll('[data-testid="cmd-bubble"]')
      expect(bubbles.length).toBe(2)
      expect(bubbles[0].attributes('data-status')).toBe('pending')
      expect(bubbles[1].attributes('data-status')).toBe('pending')
      const texts = wrapper.findAll('[data-testid="cmd-bubble-text"]').map((n) => n.text())
      expect(texts).toEqual(['切4bg', '全军撤退'])
    })

    it('command_echo 匹配文本 → 只更新对应气泡为 done，另一条 pending 不受影响', async () => {
      const wrapper = mountCommandInput()
      await wrapper.setProps({ lastReceived: received('切4bg', 100) })
      await wrapper.setProps({ lastReceived: received('全军撤退', 101) })

      // 第二条先解析完（LLM 完成顺序不保证跟发送顺序一致）
      await wrapper.setProps({ lastEcho: echo('全军撤退', '全军撤退回家') })

      const bubbles = wrapper.findAll('[data-testid="cmd-bubble"]')
      expect(bubbles[0].attributes('data-status')).toBe('pending')  // 切4bg 仍在等
      expect(bubbles[1].attributes('data-status')).toBe('done')
      expect(wrapper.find('[data-testid="cmd-bubble-detail"]').text()).toContain('全军撤退回家')
    })

    it('失败 echo([解析失败] 前缀) → 对应气泡变 failed', async () => {
      const wrapper = mountCommandInput()
      await wrapper.setProps({ lastReceived: received('乱说一通', 100) })
      await wrapper.setProps({ lastEcho: echo('乱说一通', '[解析失败] 听不懂') })

      const bubble = wrapper.find('[data-testid="cmd-bubble"]')
      expect(bubble.attributes('data-status')).toBe('failed')
    })

    it('done/failed 气泡停留一段时间后自动淡出移除', async () => {
      const wrapper = mountCommandInput()
      await wrapper.setProps({ lastReceived: received('切4bg', 100) })
      await wrapper.setProps({ lastEcho: echo('切4bg', '切到 4bg 开局') })
      expect(wrapper.find('[data-testid="cmd-bubble"]').exists()).toBe(true)

      vi.advanceTimersByTime(2000)
      await nextTick()
      expect(wrapper.find('[data-testid="cmd-bubble"]').exists()).toBe(false)
    })

    it('pending 气泡长时间没等到 echo → 兜底标 failed', async () => {
      const wrapper = mountCommandInput()
      await wrapper.setProps({ lastReceived: received('切4bg', 100) })

      vi.advanceTimersByTime(18000)
      await nextTick()

      const bubble = wrapper.find('[data-testid="cmd-bubble"]')
      expect(bubble.attributes('data-status')).toBe('failed')
    })

    it('超过上限(4条)时先移最旧的一条 pending 气泡', async () => {
      const wrapper = mountCommandInput()
      for (let i = 0; i < 5; i++) {
        await wrapper.setProps({ lastReceived: received(`指令${i}`, 100 + i) })
      }
      const texts = wrapper.findAll('[data-testid="cmd-bubble-text"]').map((n) => n.text())
      expect(texts.length).toBe(4)
      // 最旧的「指令0」被挤掉，剩下 1..4
      expect(texts).toEqual(['指令1', '指令2', '指令3', '指令4'])
    })

    it('语音模式：第一条还在 pending 时（超过冷却窗口后）允许发出第二条，不再被上一条状态阻塞', async () => {
      const wrapper = mountCommandInput()
      await wrapper.find('[data-testid="input-mode-toggle"]').trigger('click')
      await nextTick()
      const voiceInputStub = wrapper.findComponent({ name: 'VoiceInput' })

      await voiceInputStub.vm.$emit('recognized', '第一条')
      // 冷却 5s 过去，但第一条尚未收到 echo（第一条依然停留在"识别中"，若采用旧的
      // status==='sending' 门槛第二条会被拦下）
      vi.advanceTimersByTime(5000)
      await voiceInputStub.vm.$emit('recognized', '第二条')

      const emitted = wrapper.emitted('send')
      expect(emitted).toBeTruthy()
      expect(emitted!.length).toBe(2)
      expect((emitted![0][0] as { text: string }).text).toBe('第一条')
      expect((emitted![1][0] as { text: string }).text).toBe('第二条')
    })
  })
})
