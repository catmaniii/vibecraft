import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '@/i18n'
import { unitEntries, unitName } from '@/utils/unitNames'

afterEach(() => {
  setLocale('zh') // 复位，避免污染其它用例（全局 setup 默认 zh）
})

describe('unitName / unitEntries locale-aware', () => {
  it('zh 返回社区黑话', () => {
    setLocale('zh')
    expect(unitName('ZEALOT')).toBe('叉子')
    expect(unitName('IMMORTAL')).toBe('不朽')
    expect(unitEntries({ ZEALOT: 2, STALKER: 3 })).toEqual(['叉子×2', '追猎×3'])
  })

  it('en 返回官方英文名', () => {
    setLocale('en')
    expect(unitName('ZEALOT')).toBe('Zealot')
    expect(unitName('IMMORTAL')).toBe('Immortal')
    expect(unitName('VOIDRAY')).toBe('Void Ray')
    expect(unitName('HIGHTEMPLAR')).toBe('High Templar')
    expect(unitEntries({ ZEALOT: 2, STALKER: 3 })).toEqual(['Zealot×2', 'Stalker×3'])
  })

  it('未知 id 回退原串', () => {
    setLocale('en')
    expect(unitName('FOOBAR')).toBe('FOOBAR')
  })

  it('zh/en 单位表 key 集一致（无遗漏翻译）', async () => {
    const mod = await import('@/utils/unitNames')
    const zhKeys = Object.keys(mod.UNIT_ZH).sort()
    const enKeys = Object.keys(mod.UNIT_EN).sort()
    expect(enKeys).toEqual(zhKeys)
  })
})
