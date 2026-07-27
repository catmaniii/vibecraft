// UpgradeTargetPanel 组件单测：攻防升级目标等级控件（2026-07-07）
// 覆盖：渲染 5 条攻防升级线 / 点 chip emit 正确 payload / target 高亮 / 非 leveled 条目不渲染控件
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import UpgradeTargetPanel from '@/components/UpgradeTargetPanel.vue'
import type { TechProgressItem } from '@/types'

const FIVE_LINES: TechProgressItem[] = [
  { kind: 'leveled', track_en: 'PROTOSSGROUNDWEAPONS', name_zh: '+攻', level: 2, status: 'researching', progress: 60, researching_level: 3, icon_en: 'PROTOSSGROUNDWEAPONSLEVEL3', chrono: false, target: null },
  { kind: 'leveled', track_en: 'PROTOSSGROUNDARMORS', name_zh: '+防', level: 1, status: 'done', progress: 100, researching_level: null, icon_en: 'PROTOSSGROUNDARMORSLEVEL1', chrono: false, target: 1 },
  { kind: 'leveled', track_en: 'PROTOSSSHIELDS', name_zh: '+盾', level: 0, status: 'researching', progress: 30, researching_level: 1, icon_en: 'PROTOSSSHIELDSLEVEL1', chrono: false, target: 3 },
  { kind: 'leveled', track_en: 'PROTOSSAIRWEAPONS', name_zh: '+空攻', level: 1, status: 'done', progress: 100, researching_level: null, icon_en: 'PROTOSSAIRWEAPONSLEVEL1', chrono: false, target: 0 },
  { kind: 'leveled', track_en: 'PROTOSSAIRARMORS', name_zh: '+空防', level: 0, status: 'researching', progress: 10, researching_level: 1, icon_en: 'PROTOSSAIRARMORSLEVEL1', chrono: false, target: null },
  // 非 leveled（无 target 字段）：不应生成目标控件行
  { kind: 'single', upgrade_id: 86, name_en: 'BLINKTECH', name_zh: '闪烁', status: 'done', progress: 100 },
]

describe('UpgradeTargetPanel', () => {
  it('无 tech / 无 leveled 条目时不渲染', () => {
    const wrapper = mount(UpgradeTargetPanel, { props: { tech: null } })
    expect(wrapper.find('[data-testid="upgrade-target-panel"]').exists()).toBe(false)

    const wrapper2 = mount(UpgradeTargetPanel, {
      props: { tech: [FIVE_LINES[5]] }, // 只有 single，无 leveled
    })
    expect(wrapper2.find('[data-testid="upgrade-target-panel"]').exists()).toBe(false)
  })

  it('渲染 5 条攻防升级线，每条一行；单一非分级条目不生成行', () => {
    const wrapper = mount(UpgradeTargetPanel, { props: { tech: FIVE_LINES } })
    expect(wrapper.find('[data-testid="upgrade-target-panel"]').exists()).toBe(true)
    for (const track of ['PROTOSSGROUNDWEAPONS', 'PROTOSSGROUNDARMORS', 'PROTOSSSHIELDS', 'PROTOSSAIRWEAPONS', 'PROTOSSAIRARMORS']) {
      expect(wrapper.find(`[data-testid="upgrade-target-row-${track}"]`).exists()).toBe(true)
    }
    // 每行 5 个 chip（0/1/2/3/auto）
    for (const v of [0, 1, 2, 3, 'auto']) {
      expect(wrapper.find(`[data-testid="upgrade-target-chip-PROTOSSGROUNDWEAPONS-${v}"]`).exists()).toBe(true)
    }
  })

  it('当前等级徽标显示 Lv{level}', () => {
    const wrapper = mount(UpgradeTargetPanel, { props: { tech: FIVE_LINES } })
    expect(wrapper.find('[data-testid="upgrade-target-current-PROTOSSGROUNDWEAPONS"]').text()).toBe('Lv2')
    expect(wrapper.find('[data-testid="upgrade-target-current-PROTOSSSHIELDS"]').text()).toBe('Lv0')
  })

  it('target=null → "自动" chip 高亮，其余不高亮', () => {
    const wrapper = mount(UpgradeTargetPanel, { props: { tech: FIVE_LINES } })
    const autoBtn = wrapper.find('[data-testid="upgrade-target-chip-PROTOSSGROUNDWEAPONS-auto"]')
    const zeroBtn = wrapper.find('[data-testid="upgrade-target-chip-PROTOSSGROUNDWEAPONS-0"]')
    expect(autoBtn.classes()).toContain('text-accent')
    expect(zeroBtn.classes()).not.toContain('text-accent')
  })

  it('target=1 → "1" chip 高亮，其余（含自动）不高亮', () => {
    const wrapper = mount(UpgradeTargetPanel, { props: { tech: FIVE_LINES } })
    const oneBtn = wrapper.find('[data-testid="upgrade-target-chip-PROTOSSGROUNDARMORS-1"]')
    const autoBtn = wrapper.find('[data-testid="upgrade-target-chip-PROTOSSGROUNDARMORS-auto"]')
    const zeroBtn = wrapper.find('[data-testid="upgrade-target-chip-PROTOSSGROUNDARMORS-0"]')
    expect(oneBtn.classes()).toContain('text-accent')
    expect(autoBtn.classes()).not.toContain('text-accent')
    expect(zeroBtn.classes()).not.toContain('text-accent')
  })

  it('target=0 → "0" chip 高亮（区分 0 与 null/自动，不能用 falsy 判断）', () => {
    const wrapper = mount(UpgradeTargetPanel, { props: { tech: FIVE_LINES } })
    const zeroBtn = wrapper.find('[data-testid="upgrade-target-chip-PROTOSSAIRWEAPONS-0"]')
    const autoBtn = wrapper.find('[data-testid="upgrade-target-chip-PROTOSSAIRWEAPONS-auto"]')
    expect(zeroBtn.classes()).toContain('text-accent')
    expect(autoBtn.classes()).not.toContain('text-accent')
  })

  it('点 chip "3" emit macroAction("upgrade_target", {family, level:3})', async () => {
    const wrapper = mount(UpgradeTargetPanel, { props: { tech: FIVE_LINES } })
    await wrapper.find('[data-testid="upgrade-target-chip-PROTOSSGROUNDWEAPONS-3"]').trigger('click')
    const emitted = wrapper.emitted('macroAction')
    expect(emitted).toBeTruthy()
    expect(emitted![0]).toEqual(['upgrade_target', { family: 'PROTOSSGROUNDWEAPONS', level: 3 }])
  })

  it('点 chip "自动" emit macroAction("upgrade_target", {family, level:"auto"})', async () => {
    const wrapper = mount(UpgradeTargetPanel, { props: { tech: FIVE_LINES } })
    await wrapper.find('[data-testid="upgrade-target-chip-PROTOSSSHIELDS-auto"]').trigger('click')
    const emitted = wrapper.emitted('macroAction')
    expect(emitted).toBeTruthy()
    expect(emitted![0]).toEqual(['upgrade_target', { family: 'PROTOSSSHIELDS', level: 'auto' }])
  })

  it('点 chip "0" emit macroAction("upgrade_target", {family, level:0})', async () => {
    const wrapper = mount(UpgradeTargetPanel, { props: { tech: FIVE_LINES } })
    await wrapper.find('[data-testid="upgrade-target-chip-PROTOSSAIRARMORS-0"]').trigger('click')
    const emitted = wrapper.emitted('macroAction')
    expect(emitted).toBeTruthy()
    expect(emitted![0]).toEqual(['upgrade_target', { family: 'PROTOSSAIRARMORS', level: 0 }])
  })
})
