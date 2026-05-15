// Minimap.vue 坐标变换函数 + useWs minimap 帧解析单测

import { describe, it, expect } from 'vitest'
import type { MinimapFrame } from '@/types'

// ---------------------------------------------------------------------------
// 坐标变换（直接测逻辑，不依赖 DOM）
// ---------------------------------------------------------------------------

const CANVAS_W = 280
const CANVAS_H = 280

function worldToCanvas(wx: number, wy: number, playable: number[]): [number, number] {
  const [px, py, pw, ph] = playable
  const cx = ((wx - px) / pw) * CANVAS_W
  const cy = CANVAS_H - ((wy - py) / ph) * CANVAS_H
  return [cx, cy]
}

function canvasToWorld(cx: number, cy: number, playable: number[]): [number, number] {
  const [px, py, pw, ph] = playable
  const wx = (cx / CANVAS_W) * pw + px
  const wy = ((CANVAS_H - cy) / CANVAS_H) * ph + py
  return [wx, wy]
}

// sendIfInFrame 的核心:CSS 坐标 → 归一化 → 物理画布像素 → 世界坐标
// (修复"越靠右下角误差越大"bug 的回归测试:不能直接拿 cssCoord clamp 到 CANVAS_W)
function cssToWorld(
  cssX: number,
  cssY: number,
  rectW: number,
  rectH: number,
  playable: number[],
): [number, number] {
  const cx = (cssX / rectW) * CANVAS_W
  const cy = (cssY / rectH) * CANVAS_H
  const ccx = Math.max(0, Math.min(CANVAS_W, cx))
  const ccy = Math.max(0, Math.min(CANVAS_H, cy))
  return canvasToWorld(ccx, ccy, playable)
}

describe('Minimap sendIfInFrame: CSS → canvas → world 归一化(防越靠右下角偏差越大 bug)', () => {
  const playable = [16, 12, 152, 116]
  // 模拟 CSS w-full 把 280×280 画布拉伸到 380×380 显示
  const rectW = 380
  const rectH = 380

  it('CSS 右下角 → playable 右下(世界右下,y 翻转后 canvas 底)', () => {
    const [wx, wy] = cssToWorld(rectW, rectH, rectW, rectH, playable)
    expect(wx).toBeCloseTo(16 + 152, 0)
    expect(wy).toBeCloseTo(12, 0) // 翻转:cssY=rectH → canvas 底 → world y 小
  })

  it('CSS 左上角 → playable 左上(世界左上,canvas 顶)', () => {
    const [wx, wy] = cssToWorld(0, 0, rectW, rectH, playable)
    expect(wx).toBeCloseTo(16, 0)
    expect(wy).toBeCloseTo(12 + 116, 0)
  })

  it('CSS 中心 → playable 中心', () => {
    const [wx, wy] = cssToWorld(rectW / 2, rectH / 2, rectW, rectH, playable)
    expect(wx).toBeCloseTo(16 + 152 / 2, 0)
    expect(wy).toBeCloseTo(12 + 116 / 2, 0)
  })

  it('CSS 坐标超过 rect(理论不该发生)也被 clamp 到画布范围内', () => {
    const [wx, wy] = cssToWorld(rectW + 50, rectH + 50, rectW, rectH, playable)
    expect(wx).toBeCloseTo(16 + 152, 0)
    expect(wy).toBeCloseTo(12, 0)
  })
})

describe('Minimap 坐标变换：worldToCanvas', () => {
  const playable = [16, 12, 152, 116]  // 典型 playable_area

  it('playable 左下角 → canvas 左下角（y 翻转：世界 y 小 = canvas y 大）', () => {
    const [cx, cy] = worldToCanvas(16, 12, playable)
    expect(cx).toBeCloseTo(0, 1)
    expect(cy).toBeCloseTo(CANVAS_H, 1)  // y 翻转：世界底边 → canvas 底边
  })

  it('playable 右上角 → canvas 右上角', () => {
    const [cx, cy] = worldToCanvas(16 + 152, 12 + 116, playable)
    expect(cx).toBeCloseTo(CANVAS_W, 1)
    expect(cy).toBeCloseTo(0, 1)  // y 翻转：世界顶边 → canvas 顶
  })

  it('playable 中心 → canvas 中心', () => {
    const cx_world = 16 + 152 / 2
    const cy_world = 12 + 116 / 2
    const [cx, cy] = worldToCanvas(cx_world, cy_world, playable)
    expect(cx).toBeCloseTo(CANVAS_W / 2, 1)
    expect(cy).toBeCloseTo(CANVAS_H / 2, 1)
  })
})

describe('Minimap 坐标变换：canvasToWorld', () => {
  const playable = [16, 12, 152, 116]

  it('canvas 原点 → playable 左下角', () => {
    const [wx, wy] = canvasToWorld(0, CANVAS_H, playable)
    expect(wx).toBeCloseTo(16, 1)
    expect(wy).toBeCloseTo(12, 1)
  })

  it('canvas 右上角 → playable 右上角', () => {
    const [wx, wy] = canvasToWorld(CANVAS_W, 0, playable)
    expect(wx).toBeCloseTo(16 + 152, 1)
    expect(wy).toBeCloseTo(12 + 116, 1)
  })
})

describe('Minimap 坐标变换：来回互反（identity）', () => {
  const playable = [16, 12, 152, 116]

  it('世界坐标 → canvas → 世界 ≈ 原值', () => {
    const test_points: [number, number][] = [
      [88.5, 134.2],
      [16, 12],
      [16 + 152, 12 + 116],
      [60.0, 80.0],
    ]
    for (const [wx, wy] of test_points) {
      const [cx, cy] = worldToCanvas(wx, wy, playable)
      const [wx2, wy2] = canvasToWorld(cx, cy, playable)
      expect(wx2).toBeCloseTo(wx, 3)
      expect(wy2).toBeCloseTo(wy, 3)
    }
  })

  it('canvas 坐标 → 世界 → canvas ≈ 原值', () => {
    const test_canvas: [number, number][] = [
      [0, 0],
      [CANVAS_W / 2, CANVAS_H / 2],
      [CANVAS_W, CANVAS_H],
      [100, 150],
    ]
    for (const [cx, cy] of test_canvas) {
      const [wx, wy] = canvasToWorld(cx, cy, playable)
      const [cx2, cy2] = worldToCanvas(wx, wy, playable)
      expect(cx2).toBeCloseTo(cx, 3)
      expect(cy2).toBeCloseTo(cy, 3)
    }
  })
})

// ---------------------------------------------------------------------------
// MinimapFrame 类型约束（前端 types.ts）
// ---------------------------------------------------------------------------

describe('MinimapFrame 类型约束', () => {
  it('MinimapFrame 包含所有必填字段', () => {
    const f: MinimapFrame = {
      type: 'minimap',
      ts: 42.5,
      map: {
        playable: [16, 12, 152, 116],
        size: [168, 168],
      },
      viewport: {
        center: [88.5, 134.2],
        size: [24, 18],
      },
      units_own: [
        [88.0, 130.5, 'N'],
        [85.0, 132.0, 'P'],
      ],
      units_enemy_visible: [
        [42.5, 96.0, '?'],
      ],
    }
    expect(f.type).toBe('minimap')
    expect(f.ts).toBeCloseTo(42.5, 3)
    expect(f.map.playable).toHaveLength(4)
    expect(f.map.size).toHaveLength(2)
    expect(f.viewport.center).toHaveLength(2)
    expect(f.viewport.size).toEqual([24, 18])
    expect(f.units_own).toHaveLength(2)
    expect(f.units_enemy_visible).toHaveLength(1)
  })

  it('units_own kind 字段是字符串', () => {
    const f: MinimapFrame = {
      type: 'minimap',
      ts: 0,
      map: { playable: [0, 0, 100, 100], size: [100, 100] },
      viewport: { center: [50, 50], size: [24, 18] },
      units_own: [
        [10, 20, 'N'],
        [15, 25, 'P'],
        [20, 30, 'B'],
        [30, 40, 'A'],
      ],
      units_enemy_visible: [[50, 60, '?'], [55, 65, 'W']],
    }
    for (const [, , k] of f.units_own) {
      expect(typeof k).toBe('string')
    }
    for (const [, , k] of f.units_enemy_visible) {
      expect(typeof k).toBe('string')
    }
  })
})

describe('ViewMoveFrame 类型约束', () => {
  it('ViewMoveFrame 包含 type + target_point', () => {
    const f = {
      type: 'view_move' as const,
      target_point: [88.5, 134.2] as [number, number],
    }
    expect(f.type).toBe('view_move')
    expect(f.target_point).toHaveLength(2)
    expect(typeof f.target_point[0]).toBe('number')
  })
})

// ---------------------------------------------------------------------------
// minimap 帧 JSON 解析（模拟 useWs onmessage）
// ---------------------------------------------------------------------------

describe('minimap 帧 JSON 解析（useWs onmessage 路径）', () => {
  it('能解析完整 minimap 帧', () => {
    const raw = JSON.stringify({
      type: 'minimap',
      ts: 332.4,
      map: { playable: [16, 12, 152, 116], size: [168, 168] },
      viewport: { center: [88.5, 134.2], size: [24, 18] },
      units_own: [[88.0, 130.5, 'N'], [85.0, 132.0, 'P']],
      units_enemy_visible: [[42.5, 96.0, '?']],
    })
    const frame = JSON.parse(raw) as MinimapFrame
    expect(frame.type).toBe('minimap')
    expect(frame.ts).toBeCloseTo(332.4, 3)
    expect(frame.map.playable[0]).toBe(16)
    expect(frame.viewport.center[0]).toBeCloseTo(88.5, 2)
    expect(frame.units_own[0][2]).toBe('N')
    expect(frame.units_enemy_visible[0][2]).toBe('?')
  })

  it('空单位列表合法', () => {
    const raw = JSON.stringify({
      type: 'minimap',
      ts: 0.0,
      map: { playable: [0, 0, 100, 100], size: [100, 100] },
      viewport: { center: [50, 50], size: [24, 18] },
      units_own: [],
      units_enemy_visible: [],
    })
    const frame = JSON.parse(raw) as MinimapFrame
    expect(frame.units_own).toHaveLength(0)
    expect(frame.units_enemy_visible).toHaveLength(0)
  })

  it('minimap 帧 type 字段为 minimap', () => {
    const frame = JSON.parse(
      JSON.stringify({ type: 'minimap', ts: 0, map: { playable: [0,0,100,100], size: [100,100] },
        viewport: { center: [50,50], size: [24,18] }, units_own: [], units_enemy_visible: [] })
    ) as { type: string }
    expect(frame.type).toBe('minimap')
  })
})
