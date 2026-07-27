// TechProgressPanel 组件单测
// 覆盖:
//   - tech 和 production 均为空时 panel 隐藏
//   - tech 有数据时显示科技行
//   - production 有数据时显示产能行
//   - 研究中升级显示进度 %
//   - 已完成升级显示完成角标（single kind）
//   - 产能建筑显示 x数量 + 在产数角标
//   - 两行数据都有时都显示
//   ---- 新增：leveled 分级升级 ----
//   - leveled done level=1 → 左下角 "1"、无打钩
//   - leveled researching level=1 researching_level=2 → 左下角"1" + % 角标 + lv2 icon_en
//   - leveled level=0（研究中尚未完成任何级）→ 不显示级数角标
//   - chrono=true → 光环元素存在
//   - single done → 仍有打钩（保持现有行为）
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import TechProgressPanel from '@/components/TechProgressPanel.vue'
import type { TechProgressItem, TechProgressItemLeveled, TechProgressItemSingle, TechProgressItemBuilding, ProductionBuildingItem } from '@/types'

// ---- fixture helpers ----

function mkSingleDone(overrides: Partial<TechProgressItemSingle> = {}): TechProgressItemSingle {
  return {
    kind: 'single',
    upgrade_id: 84,
    name_en: 'WARPGATERESEARCH',
    name_zh: '折跃',
    status: 'done',
    progress: 100,
    chrono: false,
    ...overrides,
  }
}

function mkSingleResearching(overrides: Partial<TechProgressItemSingle> = {}): TechProgressItemSingle {
  return {
    kind: 'single',
    upgrade_id: 3,
    name_en: 'BLINKTECH',
    name_zh: '闪现',
    status: 'researching',
    progress: 60,
    chrono: false,
    ...overrides,
  }
}

function mkLeveledDone(overrides: Partial<TechProgressItemLeveled> = {}): TechProgressItemLeveled {
  return {
    kind: 'leveled',
    track_en: 'PROTOSSGROUNDWEAPONS',
    name_zh: '+攻',
    level: 1,
    status: 'done',
    progress: 100,
    researching_level: null,
    icon_en: 'PROTOSSGROUNDWEAPONSLEVEL1',
    chrono: false,
    ...overrides,
  }
}

function mkLeveledResearching(overrides: Partial<TechProgressItemLeveled> = {}): TechProgressItemLeveled {
  return {
    kind: 'leveled',
    track_en: 'PROTOSSGROUNDWEAPONS',
    name_zh: '+攻',
    level: 1,
    status: 'researching',
    progress: 45,
    researching_level: 2,
    icon_en: 'PROTOSSGROUNDWEAPONSLEVEL2',  // 研究中显示 lv2 图标
    chrono: false,
    ...overrides,
  }
}

function mkBuilding(overrides: Partial<ProductionBuildingItem> = {}): ProductionBuildingItem {
  return {
    building_id: 62,
    name_en: 'GATEWAY',
    name_zh: 'BG',
    count: 3,
    pending: 0,
    in_production: 1,
    queue: [{ unit: 'TRAIN_STALKER', progress: 50 }],
    ...overrides,
  }
}

function mkTechBuilding(overrides: Partial<TechProgressItemBuilding> = {}): TechProgressItemBuilding {
  return {
    kind: 'building',
    name_en: 'TWILIGHTCOUNCIL',
    name_zh: 'VC',
    status: 'done',
    progress: 100,
    icon_en: 'TWILIGHTCOUNCIL',
    count: 1,
    pending: 0,
    ...overrides,
  }
}

// ---- 旧 single 行为（兼容性保证） ----

describe('TechProgressPanel - single 非分级升级', () => {
  it('tech 和 production 均为空时整个 panel 隐藏', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [], production: [] },
    })
    expect(wrapper.find('[data-testid="tech-progress-panel"]').exists()).toBe(false)
  })

  it('tech 为 null / production 为 null 时隐藏', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: null, production: null },
    })
    expect(wrapper.find('[data-testid="tech-progress-panel"]').exists()).toBe(false)
  })

  it('有 tech 数据时显示 panel + 科技行', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkSingleDone()], production: [] },
    })
    expect(wrapper.find('[data-testid="tech-progress-panel"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="tech-row"]').exists()).toBe(true)
  })

  it('有 production 数据时显示 panel + 产能行', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [], production: [mkBuilding()] },
    })
    expect(wrapper.find('[data-testid="tech-progress-panel"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="production-row"]').exists()).toBe(true)
  })

  it('两行数据都有时都显示', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkSingleDone()], production: [mkBuilding()] },
    })
    expect(wrapper.find('[data-testid="tech-row"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="production-row"]').exists()).toBe(true)
  })

  it('已完成 single 科技显示 testid 节点', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkSingleDone()], production: [] },
    })
    expect(wrapper.find('[data-testid="tech-item-WARPGATERESEARCH"]').exists()).toBe(true)
  })

  it('single 研究中科技显示 testid 节点', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkSingleResearching()], production: [] },
    })
    expect(wrapper.find('[data-testid="tech-item-BLINKTECH"]').exists()).toBe(true)
  })

  it('single 研究中 tooltip 含进度百分比', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkSingleResearching({ progress: 60 })], production: [] },
    })
    const el = wrapper.find('[data-testid="tech-item-BLINKTECH"]')
    expect(el.attributes('title')).toContain('60%')
  })

  it('single 已完成 tooltip 含"已完成"', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkSingleDone()], production: [] },
    })
    const el = wrapper.find('[data-testid="tech-item-WARPGATERESEARCH"]')
    expect(el.attributes('title')).toContain('已完成')
  })

  it('single done → 仍有打钩"v"（保持现有行为）', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkSingleDone()], production: [] },
    })
    // 打钩 div 存在且文本为 "v"
    const item = wrapper.find('[data-testid="tech-item-WARPGATERESEARCH"]')
    expect(item.text()).toContain('v')
  })

  it('多条 single 科技都渲染', () => {
    const tech: TechProgressItem[] = [mkSingleDone(), mkSingleResearching()]
    const wrapper = mount(TechProgressPanel, {
      props: { tech, production: [] },
    })
    expect(wrapper.find('[data-testid="tech-item-WARPGATERESEARCH"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="tech-item-BLINKTECH"]').exists()).toBe(true)
  })

  it('产能行不渲染 tech 节点', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [], production: [mkBuilding()] },
    })
    expect(wrapper.find('[data-testid="tech-row"]').exists()).toBe(false)
  })

  it('科技行不渲染 production 节点', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkSingleDone()], production: [] },
    })
    expect(wrapper.find('[data-testid="production-row"]').exists()).toBe(false)
  })

  it('产能建筑显示 testid 节点 + 右上角总数角标', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [], production: [mkBuilding({ count: 4 })] },
    })
    const el = wrapper.find('[data-testid="building-item-GATEWAY"]')
    expect(el.exists()).toBe(true)
    expect(wrapper.find('[data-testid="building-count-GATEWAY"]').text()).toBe('4')
  })

  it('产能建筑 tooltip 含建筑中文名和建造中数', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [], production: [mkBuilding({ pending: 2 })] },
    })
    const el = wrapper.find('[data-testid="building-item-GATEWAY"]')
    expect(el.attributes('title')).toContain('BG')
    expect(el.attributes('title')).toContain('建造中 2')
  })

  it('纯建造中（count=0）不显示总数角标，只显示右下角建造中角标', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [], production: [mkBuilding({ count: 0, pending: 2 })] },
    })
    const el = wrapper.find('[data-testid="building-item-GATEWAY"]')
    expect(el.exists()).toBe(true)
    expect(wrapper.find('[data-testid="building-count-GATEWAY"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="building-pending-GATEWAY"]').text()).toBe('2')
  })
})

// ---- 新增：leveled 分级升级行为 ----

describe('TechProgressPanel - leveled 分级升级', () => {
  it('leveled done level=1 → 渲染左下角 "1"', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkLeveledDone({ level: 1 })], production: [] },
    })
    const badge = wrapper.find('[data-testid="tech-level-PROTOSSGROUNDWEAPONS"]')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe('1')
  })

  it('leveled done level=1 → 无打钩 "v"，无 % 角标', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkLeveledDone({ level: 1 })], production: [] },
    })
    const item = wrapper.find('[data-testid="tech-item-PROTOSSGROUNDWEAPONS"]')
    // 不能有打钩文本（single 才有）
    expect(item.text()).not.toContain('v')
    // 没有 % 角标（done 不显示 %）
    expect(item.text()).not.toContain('%')
    // 级数角标存在且文本为 "1"
    const badge = wrapper.find('[data-testid="tech-level-PROTOSSGROUNDWEAPONS"]')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe('1')
  })

  it('leveled researching level=1 researching_level=2 → 左下角"1" + % 角标', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkLeveledResearching()], production: [] },
    })
    const item = wrapper.find('[data-testid="tech-item-PROTOSSGROUNDWEAPONS"]')
    expect(item.exists()).toBe(true)
    // 左下角级数
    const badge = wrapper.find('[data-testid="tech-level-PROTOSSGROUNDWEAPONS"]')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe('1')
    // 研究中 % 角标
    expect(item.text()).toContain('%')
  })

  it('leveled researching tooltip 含 Lv→ 格式', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkLeveledResearching({ progress: 45 })], production: [] },
    })
    const item = wrapper.find('[data-testid="tech-item-PROTOSSGROUNDWEAPONS"]')
    const title = item.attributes('title') ?? ''
    expect(title).toContain('Lv1→2')
    expect(title).toContain('45%')
    expect(title).toContain('研究中')
  })

  it('leveled done tooltip 含 Lv 级数', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkLeveledDone({ level: 2 })], production: [] },
    })
    const item = wrapper.find('[data-testid="tech-item-PROTOSSGROUNDWEAPONS"]')
    expect(item.attributes('title')).toContain('Lv2')
  })

  it('leveled level=0（lv1 研究中尚未完成任何级）→ 不显示级数角标', () => {
    const wrapper = mount(TechProgressPanel, {
      props: {
        tech: [mkLeveledResearching({ level: 0, researching_level: 1, icon_en: 'PROTOSSGROUNDWEAPONSLEVEL1' })],
        production: [],
      },
    })
    // level=0 → 不渲染级数角标
    expect(wrapper.find('[data-testid="tech-level-PROTOSSGROUNDWEAPONS"]').exists()).toBe(false)
  })

  it('leveled done level=3（max）→ 显示 "3"', () => {
    const wrapper = mount(TechProgressPanel, {
      props: {
        tech: [mkLeveledDone({ level: 3, icon_en: 'PROTOSSGROUNDWEAPONSLEVEL3' })],
        production: [],
      },
    })
    const badge = wrapper.find('[data-testid="tech-level-PROTOSSGROUNDWEAPONS"]')
    expect(badge.text()).toBe('3')
  })
})

// ---- 新增：chrono 星空加速光环 ----

describe('TechProgressPanel - chrono 星空加速', () => {
  it('leveled chrono=true → 光环元素存在', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkLeveledDone({ chrono: true })], production: [] },
    })
    expect(wrapper.find('[data-testid="tech-chrono-PROTOSSGROUNDWEAPONS"]').exists()).toBe(true)
  })

  it('leveled chrono=false → 光环元素不存在', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkLeveledDone({ chrono: false })], production: [] },
    })
    expect(wrapper.find('[data-testid="tech-chrono-PROTOSSGROUNDWEAPONS"]').exists()).toBe(false)
  })

  it('single chrono=true → 光环元素存在', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkSingleDone({ chrono: true })], production: [] },
    })
    expect(wrapper.find('[data-testid="tech-chrono-WARPGATERESEARCH"]').exists()).toBe(true)
  })

  it('single chrono=false → 光环元素不存在', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkSingleDone({ chrono: false })], production: [] },
    })
    expect(wrapper.find('[data-testid="tech-chrono-WARPGATERESEARCH"]').exists()).toBe(false)
  })

  it('chrono tooltip 含"星空加速"', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkLeveledDone({ chrono: true })], production: [] },
    })
    const item = wrapper.find('[data-testid="tech-item-PROTOSSGROUNDWEAPONS"]')
    expect(item.attributes('title')).toContain('星空加速')
  })
})

// ---- 新增：variant 变体（直播 overlay vs 原位卡片）----

describe('TechProgressPanel - 容器样式', () => {
  it('固定圆角卡片样式（去掉 variant prop 后永远是卡片）', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkLeveledDone({})], production: [] },
    })
    const root = wrapper.find('[data-testid="tech-progress-panel"]')
    expect(root.classes()).toContain('rounded-xl')
    expect(root.classes()).toContain('bg-surface-2')
    expect(root.classes()).not.toContain('border-t')
  })
})

// ---- 新增：building 关键科技建筑 ----

describe('TechProgressPanel - building 科技建筑', () => {
  it('已建成 2 个 → 蓝色总数角标显示 2,无建造中角标', () => {
    const wrapper = mount(TechProgressPanel, {
      props: { tech: [mkTechBuilding({ status: 'done', count: 2, pending: 0 })], production: [] },
    })
    expect(wrapper.find('[data-testid="tech-building-TWILIGHTCOUNCIL"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="tech-building-count-TWILIGHTCOUNCIL"]').text()).toBe('2')
    expect(wrapper.find('[data-testid="tech-building-pending-TWILIGHTCOUNCIL"]').exists()).toBe(false)
  })

  it('已有 1 个 + 在建 2 个 → 蓝色总数 1 + 黄色建造中 2', () => {
    const wrapper = mount(TechProgressPanel, {
      props: {
        tech: [mkTechBuilding({ status: 'done', count: 1, pending: 2, progress: 100 })],
        production: [],
      },
    })
    expect(wrapper.find('[data-testid="tech-building-count-TWILIGHTCOUNCIL"]').text()).toBe('1')
    expect(wrapper.find('[data-testid="tech-building-pending-TWILIGHTCOUNCIL"]').text()).toBe('2')
  })

  it('纯建造中(count=0,pending=1) → 无总数角标,只黄色建造中角标', () => {
    const wrapper = mount(TechProgressPanel, {
      props: {
        tech: [mkTechBuilding({ status: 'building', count: 0, pending: 1, progress: 60 })],
        production: [],
      },
    })
    expect(wrapper.find('[data-testid="tech-building-count-TWILIGHTCOUNCIL"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="tech-building-pending-TWILIGHTCOUNCIL"]').text()).toBe('1')
  })

  it('building 项进入 techItems 过滤(status=building 不被丢)', () => {
    const wrapper = mount(TechProgressPanel, {
      props: {
        tech: [mkTechBuilding({ status: 'building', count: 0, pending: 1, progress: 30 })],
        production: [],
      },
    })
    // panel 显示(building 也算 techItems → hasAny true)
    expect(wrapper.find('[data-testid="tech-progress-panel"]').exists()).toBe(true)
  })

  it('tooltip 含建成数/建造中数', () => {
    const done = mount(TechProgressPanel, {
      props: { tech: [mkTechBuilding({ status: 'done', count: 2, pending: 0 })], production: [] },
    })
    expect(done.find('[data-testid="tech-building-TWILIGHTCOUNCIL"]').attributes('title')).toContain('×2')
    const building = mount(TechProgressPanel, {
      props: {
        tech: [mkTechBuilding({ status: 'done', count: 1, pending: 2, progress: 45 })],
        production: [],
      },
    })
    expect(building.find('[data-testid="tech-building-TWILIGHTCOUNCIL"]').attributes('title')).toContain('建造中 2')
  })
})

describe('TechProgressPanel - 放大 modal', () => {
  const units = [{ name_en: 'ZEALOT', name_zh: '叉子', count: 3, pending: 1 }]

  it('有数据时显示放大按钮', () => {
    const wrapper = mount(TechProgressPanel, { props: { units } })
    expect(wrapper.find('[data-testid="tech-zoom-btn"]').exists()).toBe(true)
  })

  it('点放大按钮弹出 modal，再点关闭收起', async () => {
    const wrapper = mount(TechProgressPanel, {
      props: { units },
      global: { stubs: { teleport: true } },
    })
    expect(wrapper.find('[data-testid="tech-zoom-modal"]').exists()).toBe(false)
    await wrapper.find('[data-testid="tech-zoom-btn"]').trigger('click')
    const modal = wrapper.find('[data-testid="tech-zoom-modal"]')
    expect(modal.exists()).toBe(true)
    // modal 里也有兵种行（大尺寸 TechRows）
    expect(modal.find('[data-testid="unit-item-ZEALOT"]').exists()).toBe(true)
    await wrapper.find('[data-testid="tech-zoom-close"]').trigger('click')
    expect(wrapper.find('[data-testid="tech-zoom-modal"]').exists()).toBe(false)
  })
})
