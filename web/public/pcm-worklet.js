/**
 * pcm-worklet.js — AudioWorkletProcessor
 *
 * 麦克风 Float32（通常 44.1kHz 或 48kHz）→ 16kHz mono Int16 PCM
 * 降采样算法：线性插值（ASR 够用，延迟极低）
 * 每攒满 1600 samples（100ms @ 16kHz）→ port.postMessage(int16.buffer) 发给主线程
 *
 * 使用方：AudioWorkletNode('pcm-processor')
 *   node.port.onmessage = (e) => { /* e.data 是 ArrayBuffer (Int16) * / }
 */

'use strict'

const TARGET_RATE = 16000   // 目标采样率 Hz
const FRAME_SIZE  = 1600    // 每帧 samples = 100ms × 16000

class PcmProcessor extends AudioWorkletProcessor {
  constructor () {
    super()
    // 降采样输出缓冲（Float32 暂存）
    this._outBuf = new Float32Array(FRAME_SIZE)
    this._outPos = 0
    // 当前块内的浮点读取位置（余量在块切换时保留）
    // e.g. 48kHz→16kHz ratio=3.0，每前进 1 个输出采样，在输入里走 3.0 格
    this._phase = 0
  }

  process (inputs) {
    // 取第一个输入节点的第一个声道（mono；多声道忽略其余）
    const src = inputs[0]?.[0]
    if (!src || src.length === 0) return true

    // 降采样比例（当前 AudioContext 的实际采样率，由全局 sampleRate 给出）
    // eslint-disable-next-line no-undef
    const ratio = sampleRate / TARGET_RATE   // sampleRate 是 AudioWorkletGlobalScope 全局变量

    let pos = this._phase  // 在本块 src 内的浮点读取位置

    while (pos < src.length) {
      // 线性插值：在 src[i0] 和 src[i1] 之间按 frac 插值
      const i0 = Math.floor(pos)
      const i1 = Math.min(i0 + 1, src.length - 1)
      const frac = pos - i0
      this._outBuf[this._outPos++] = src[i0] + frac * (src[i1] - src[i0])

      // 攒满一帧 → Float32 转 Int16 → postMessage 转移所有权
      if (this._outPos >= FRAME_SIZE) {
        const int16 = new Int16Array(FRAME_SIZE)
        for (let i = 0; i < FRAME_SIZE; i++) {
          // Float32 [-1, 1] → Int16 [-32768, 32767]，clamp 防溢出
          int16[i] = Math.max(-32768, Math.min(32767, Math.round(this._outBuf[i] * 32767)))
        }
        // 转移 ArrayBuffer 所有权（零拷贝）
        this.port.postMessage(int16.buffer, [int16.buffer])
        this._outPos = 0
      }

      pos += ratio
    }

    // 本块走完后的余量带入下一块（pos - src.length 即下一块从哪里开始读）
    this._phase = pos - src.length
    return true  // 返回 true 保持 processor 存活
  }
}

// eslint-disable-next-line no-undef
registerProcessor('pcm-processor', PcmProcessor)
