// VoiceInput.vue 组件单测
//
// 策略：vi.mock 掉 @/composables/useVoiceInput，注入可控的
//   isRecording / partial / final / supported + start / stop / cancel spy。
//   手势用 pointer 事件（jsdom 下比 TouchEvent 更易构造），逻辑路径相同。
//
// 覆盖场景：
//   1. supported=false → 不渲染麦克风按钮
//   2. supported=true  → 渲染麦克风按钮
//   3. pointerdown     → start() 被调，浮层出现
//   4. pointermove 上滑 >60px → voice-cancel-zone 出现
//   5. pointermove 上滑 <60px → 不进取消区
//   6. pointerup 正常区 → stop() 被调，cancel() 不调
//   7. pointerup 取消区 → cancel() 被调，stop() 不调
//   8. partial 变化 → 浮层文字实时更新
//   9. final 写入（非取消路径）→ emit recognized 带 text
//  10. final 写入（取消路径）→ 不 emit recognized

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref, computed, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import VoiceInput from '@/components/VoiceInput.vue'
import { useVoiceInput } from '@/composables/useVoiceInput'
import type { TranscriptFrame } from '@/types'

// ── mock useVoiceInput ────────────────────────────────────────────────────────
vi.mock('@/composables/useVoiceInput', () => ({
  useVoiceInput: vi.fn(),
}))

// ── 工具：生成一套可控 mock，注入 vi.mocked(useVoiceInput) ───────────────────

function setupMock(supported = true) {
  const isRecording = ref(false)
  const partialText = ref('')
  const final = ref('')

  // start 同步设置 isRecording=true（免 nextTick 等微任务）
  const start = vi.fn().mockImplementation(() => {
    isRecording.value = true
  })
  const stop = vi.fn().mockImplementation(() => {
    isRecording.value = false
  })
  const cancel = vi.fn().mockImplementation(() => {
    isRecording.value = false
  })
  // arm/disarm：组件 onMounted/onUnmounted 调；预热麦克风(常驻采集)。
  const arm = vi.fn().mockResolvedValue(undefined)
  const disarm = vi.fn()
  // getLevels：浮层波形 canvas 每帧调；jsdom 无 canvas ctx 时其实不会被调，返空。
  const getLevels = vi.fn(() => [] as number[])

  vi.mocked(useVoiceInput).mockReturnValue({
    isRecording,
    partial: computed(() => partialText.value) as any,
    final,
    supported,
    arm,
    disarm,
    start,
    stop,
    cancel,
    getLevels,
  } as any)

  return {
    isRecording,
    final,
    setPartial: (t: string) => { partialText.value = t },
    setFinal:   (t: string) => { final.value = t },
    spies: { arm, disarm, start, stop, cancel, getLevels },
  }
}

// 默认 props（mock 内部不使用这些值，传假 stub 即可）
function makeProps() {
  return {
    sendAudioChunk:  vi.fn() as (seq: number, pcm: string) => void,
    sendAudioEnd:    vi.fn() as () => void,
    sendAudioCancel: vi.fn() as () => void,
    lastTranscript:  null as TranscriptFrame | null,
  }
}

// ── 测试套件 ──────────────────────────────────────────────────────────────────

describe('VoiceInput', () => {
  // 可控的 Date.now —— MIN_HOLD_MS(300ms) 判定需要：按住时长 = Date.now()差。
  // 默认让"按住"足够长(stop 路径);测"太短误触"时把时长压到 <300。
  let nowVal = 0
  beforeEach(() => {
    vi.clearAllMocks()
    nowVal = 100000
    vi.spyOn(Date, 'now').mockImplementation(() => nowVal)
    // stub canvas 2d context：jsdom 未实现 getContext，波形 rAF 调它会往 virtual
    // console 喷 "Not implemented" 噪音（即便代码 try/catch 也拦不住已 emit 的日志）。
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      setTransform: vi.fn(),
      clearRect: vi.fn(),
      fillRect: vi.fn(),
      fillStyle: '',
    } as unknown as CanvasRenderingContext2D)
  })

  // ── 1 & 2. supported 渲染判断 ─────────────────────────────────────────────

  it('supported=false 时不渲染麦克风按钮，只渲染 voice-not-supported 占位', () => {
    setupMock(false)
    const wrapper = mount(VoiceInput, { props: makeProps() })
    expect(wrapper.find('[data-testid="voice-mic-btn"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="voice-not-supported"]').exists()).toBe(true)
  })

  it('supported=true 时渲染麦克风按钮，不渲染 voice-not-supported', () => {
    setupMock(true)
    const wrapper = mount(VoiceInput, { props: makeProps() })
    expect(wrapper.find('[data-testid="voice-mic-btn"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="voice-not-supported"]').exists()).toBe(false)
  })

  // ── 1b/1c. 预热生命周期：挂载 arm、卸载 disarm ────────────────────────────

  it('挂载(进语音模式)时 arm 预热麦克风，卸载时 disarm 释放', () => {
    const { spies } = setupMock(true)
    const wrapper = mount(VoiceInput, { props: makeProps() })
    expect(spies.arm).toHaveBeenCalledOnce()  // 进语音模式即预热
    expect(spies.disarm).not.toHaveBeenCalled()

    wrapper.unmount()
    expect(spies.disarm).toHaveBeenCalledOnce()  // 离开语音模式释放麦克风
  })

  it('supported=false 时挂载不 arm（无麦克风可预热）', () => {
    const { spies } = setupMock(false)
    mount(VoiceInput, { props: makeProps() })
    expect(spies.arm).not.toHaveBeenCalled()
  })

  // ── 3. pointerdown → start() + 浮层 ──────────────────────────────────────

  it('pointerdown 调用 start()，浮层（voice-overlay）出现', async () => {
    const { spies } = setupMock()
    const wrapper = mount(VoiceInput, { props: makeProps() })

    await wrapper.find('[data-testid="voice-mic-btn"]').trigger('pointerdown', { clientY: 500 })
    await nextTick()

    expect(spies.start).toHaveBeenCalledOnce()
    expect(wrapper.find('[data-testid="voice-overlay"]').exists()).toBe(true)
  })

  // ── 3b. 录音浮层含实时波形 canvas ────────────────────────────────────────

  it('录音中浮层包含实时波形 canvas', async () => {
    setupMock()
    const wrapper = mount(VoiceInput, { props: makeProps() })

    await wrapper.find('[data-testid="voice-mic-btn"]').trigger('pointerdown', { clientY: 500 })
    await nextTick()

    expect(wrapper.find('[data-testid="voice-waveform"]').exists()).toBe(true)
  })

  // ── 4. pointermove 上滑越 60px 阈值 → 取消区 ────────────────────────────

  it('录音中 pointermove 上滑 >60px → voice-cancel-zone 出现（红态）', async () => {
    setupMock()
    const wrapper = mount(VoiceInput, { props: makeProps() })
    const btn = wrapper.find('[data-testid="voice-mic-btn"]')

    await btn.trigger('pointerdown', { clientY: 500 })
    await nextTick()
    // 上滑 70px（500 - 430 = 70 > 60）
    await btn.trigger('pointermove', { clientY: 430 })
    await nextTick()

    expect(wrapper.find('[data-testid="voice-cancel-zone"]').exists()).toBe(true)
  })

  // ── 5. pointermove 上滑未越阈值 → 不进取消区 ────────────────────────────

  it('pointermove 上滑未越 60px 阈值 → voice-cancel-zone 不出现', async () => {
    setupMock()
    const wrapper = mount(VoiceInput, { props: makeProps() })
    const btn = wrapper.find('[data-testid="voice-mic-btn"]')

    await btn.trigger('pointerdown', { clientY: 500 })
    await nextTick()
    // 上滑 30px（500 - 470 = 30 < 60）
    await btn.trigger('pointermove', { clientY: 470 })
    await nextTick()

    expect(wrapper.find('[data-testid="voice-cancel-zone"]').exists()).toBe(false)
  })

  // ── 6. pointerup 正常区 → stop() ─────────────────────────────────────────

  it('pointerup 在正常区调用 stop()，不调 cancel()', async () => {
    const { spies } = setupMock()
    const wrapper = mount(VoiceInput, { props: makeProps() })
    const btn = wrapper.find('[data-testid="voice-mic-btn"]')

    await btn.trigger('pointerdown', { clientY: 500 })
    await nextTick()
    nowVal += 400  // 按住 400ms ≥ MIN_HOLD → 正常发送
    // 松手（无上滑，inCancelZone 保持 false）
    await btn.trigger('pointerup', { clientY: 495 })
    await nextTick()

    expect(spies.stop).toHaveBeenCalledOnce()
    expect(spies.cancel).not.toHaveBeenCalled()
  })

  // ── 6b. 按住太短(<300ms,误触/轻点) → cancel,不 stop（必须按住才发） ───────
  it('按住太短(<300ms)松手 → 当误触取消,不发送', async () => {
    const { spies } = setupMock()
    const wrapper = mount(VoiceInput, { props: makeProps() })
    const btn = wrapper.find('[data-testid="voice-mic-btn"]')

    await btn.trigger('pointerdown', { clientY: 500 })
    await nextTick()
    nowVal += 100  // 只按住 100ms < MIN_HOLD(300)
    await btn.trigger('pointerup', { clientY: 498 })
    await nextTick()

    expect(spies.cancel).toHaveBeenCalledOnce()
    expect(spies.stop).not.toHaveBeenCalled()
  })

  // ── 7. pointerup 取消区 → cancel() ───────────────────────────────────────

  it('pointerup 在取消区调用 cancel()，不调 stop()', async () => {
    const { spies } = setupMock()
    const wrapper = mount(VoiceInput, { props: makeProps() })
    const btn = wrapper.find('[data-testid="voice-mic-btn"]')

    await btn.trigger('pointerdown', { clientY: 500 })
    await nextTick()
    await btn.trigger('pointermove', { clientY: 430 })  // 上滑 70px → 进取消区
    await nextTick()
    await btn.trigger('pointerup', { clientY: 430 })
    await nextTick()

    expect(spies.cancel).toHaveBeenCalledOnce()
    expect(spies.stop).not.toHaveBeenCalled()
  })

  // ── 8. partial 变化 → 浮层文字更新 ──────────────────────────────────────

  it('录音中 partial 变化后浮层文字实时更新', async () => {
    const { setPartial } = setupMock()
    const wrapper = mount(VoiceInput, { props: makeProps() })

    await wrapper.find('[data-testid="voice-mic-btn"]').trigger('pointerdown', { clientY: 500 })
    await nextTick()

    setPartial('切4BG')
    await nextTick()

    expect(wrapper.find('[data-testid="voice-partial-text"]').text()).toContain('切4BG')
  })

  // 工具：构造一个 is_final 定稿帧（组件 watch props.lastTranscript 判 success/failed）
  const finalFrame = (text: string): TranscriptFrame =>
    ({ type: 'transcript', text, is_final: true } as TranscriptFrame)

  // ── 9. 定稿帧（非取消路径）→ emit recognized ─────────────────────────────

  it('stop 路径后 定稿帧到达 emit recognized 带 text', async () => {
    setupMock()
    const wrapper = mount(VoiceInput, { props: makeProps() })
    const btn = wrapper.find('[data-testid="voice-mic-btn"]')

    // 正常录音 → 松手（正常区，按住足够长）→ finalizing
    await btn.trigger('pointerdown', { clientY: 500 })
    await nextTick()
    nowVal += 400
    await btn.trigger('pointerup', { clientY: 495 })
    await nextTick()

    // server 推定稿帧
    await wrapper.setProps({ lastTranscript: finalFrame('切4BG') })
    await nextTick()

    const emitted = wrapper.emitted('recognized')
    expect(emitted).toBeTruthy()
    expect(emitted![0]).toEqual(['切4BG'])
  })

  // ── 10. 取消路径下定稿帧 → 不 emit ───────────────────────────────────────

  it('cancel 路径后 定稿帧不 emit recognized', async () => {
    setupMock()
    const wrapper = mount(VoiceInput, { props: makeProps() })
    const btn = wrapper.find('[data-testid="voice-mic-btn"]')

    // 上滑取消
    await btn.trigger('pointerdown', { clientY: 500 })
    await nextTick()
    await btn.trigger('pointermove', { clientY: 430 })
    await nextTick()
    await btn.trigger('pointerup', { clientY: 430 })
    await nextTick()

    // 模拟意外定稿帧（server 竞态；正常 audio_cancel 后不发）→ 已 cancelled，不接受
    await wrapper.setProps({ lastTranscript: finalFrame('意外文字') })
    await nextTick()

    expect(wrapper.emitted('recognized')).toBeFalsy()
  })

  // ── 11. 松手后浮层保留(识别中)，不立刻消失 ──────────────────────────────

  it('松手后浮层保留进入 finalizing(识别中…)，不立刻消失', async () => {
    setupMock()
    const wrapper = mount(VoiceInput, { props: makeProps() })
    const btn = wrapper.find('[data-testid="voice-mic-btn"]')

    await btn.trigger('pointerdown', { clientY: 500 })
    await nextTick()
    nowVal += 400
    await btn.trigger('pointerup', { clientY: 495 })
    await nextTick()

    // 松手后浮层仍在（finalizing），显"识别中…"脉动指示，波形已撤
    expect(wrapper.find('[data-testid="voice-overlay"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="voice-finalizing"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="voice-waveform"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="voice-overlay-hint"]').text()).toContain('识别中')
  })

  // ── 12. finalizing → 定稿到达变 success(已识别) ─────────────────────────

  it('finalizing 下定稿到达 → 浮层显定稿 + 已识别，浮层仍在(变绿停留)', async () => {
    setupMock()
    const wrapper = mount(VoiceInput, { props: makeProps() })
    const btn = wrapper.find('[data-testid="voice-mic-btn"]')

    await btn.trigger('pointerdown', { clientY: 500 })
    await nextTick()
    nowVal += 400
    await btn.trigger('pointerup', { clientY: 495 })
    await nextTick()

    await wrapper.setProps({ lastTranscript: finalFrame('切4BG') })
    await nextTick()

    // success：浮层仍在、显定稿文字 + "已识别"提示（停留后才消失，这里不等定时器）
    expect(wrapper.find('[data-testid="voice-overlay"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="voice-partial-text"]').text()).toContain('切4BG')
    expect(wrapper.find('[data-testid="voice-overlay-hint"]').text()).toContain('已识别')
  })

  // ── 12b. finalizing 下空定稿 → failed(识别失败，变红) ────────────────────

  it('finalizing 下空定稿 → 浮层显识别失败(变红)，仍在停留', async () => {
    setupMock()
    const wrapper = mount(VoiceInput, { props: makeProps() })
    const btn = wrapper.find('[data-testid="voice-mic-btn"]')

    await btn.trigger('pointerdown', { clientY: 500 })
    await nextTick()
    nowVal += 400
    await btn.trigger('pointerup', { clientY: 495 })
    await nextTick()

    // 空定稿（识别没出内容）→ failed
    await wrapper.setProps({ lastTranscript: finalFrame('') })
    await nextTick()

    expect(wrapper.find('[data-testid="voice-overlay"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="voice-overlay-hint"]').text()).toContain('识别失败')
    expect(wrapper.emitted('recognized')).toBeFalsy()  // 失败不下发指令
  })

  // ── 13. 取消后浮层短暂保留显"已取消" ────────────────────────────────────

  it('取消后浮层短暂保留显"已取消"(红)', async () => {
    setupMock()
    const wrapper = mount(VoiceInput, { props: makeProps() })
    const btn = wrapper.find('[data-testid="voice-mic-btn"]')

    await btn.trigger('pointerdown', { clientY: 500 })
    await nextTick()
    await btn.trigger('pointermove', { clientY: 430 })  // 上滑进取消区
    await nextTick()
    await btn.trigger('pointerup', { clientY: 430 })
    await nextTick()

    expect(wrapper.find('[data-testid="voice-overlay"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="voice-partial-text"]').text()).toContain('已取消')
  })
})
