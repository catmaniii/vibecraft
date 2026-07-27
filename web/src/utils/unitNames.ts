// 兵种英文 id → 各语言显示名（zh 用社区黑话，en 用官方英文名）。
// 与后端 bot/localization.py UNIT_NAMES 概念对应；前端单独维护一份（专有名词不进 strings.json）。
import { i18n } from '@/i18n'

export const UNIT_ZH: Record<string, string> = {
  PROBE: '农民',
  ZEALOT: '叉子',
  STALKER: '追猎',
  SENTRY: '哨兵',
  ADEPT: '闪追',
  IMMORTAL: '不朽',
  COLOSSUS: '巨像',
  DISRUPTOR: '电球',
  OBSERVER: '天眼',
  WARPPRISM: '运输机',
  PHOENIX: '凤凰',
  VOIDRAY: '虚空',
  ORACLE: '先知',
  CARRIER: '航母',
  MOTHERSHIP: '母舰',
  TEMPEST: '暴风',
  HIGHTEMPLAR: '电兵',
  DARKTEMPLAR: 'DT',
  ARCHON: '白球',
  // 虫族常见
  DRONE: '农民',
  ZERGLING: '小狗',
  BANELING: '妖虫',
  ROACH: '蟑螂',
  RAVAGER: '破坏者',
  HYDRALISK: '刺蛇',
  MUTALISK: '飞龙',
  CORRUPTOR: '腐化者',
  BROODLORD: 'BL',
  LURKERMP: '潜伏者',
  INFESTOR: '感染者',
  ULTRALISK: '雷兽',
  QUEEN: '女王',
  // 人族常见
  SCV: '农民',
  MARINE: '枪兵',
  MARAUDER: '流氓',
  MEDIVAC: '医疗船',
  SIEGETANK: '坦克',
  SIEGETANKSIEGED: '坦克',
  VIKINGFIGHTER: '维京',
  VIKINGASSAULT: '维京',
  GHOST: '幽灵',
  BATTLECRUISER: '船长',
  LIBERATOR: '自由',
  BANSHEE: '女妖',
  RAVEN: '渡鸦',
  THOR: '索尔',
}

// 官方英文名（en）。查不到回退原 id。
export const UNIT_EN: Record<string, string> = {
  PROBE: 'Probe',
  ZEALOT: 'Zealot',
  STALKER: 'Stalker',
  SENTRY: 'Sentry',
  ADEPT: 'Adept',
  IMMORTAL: 'Immortal',
  COLOSSUS: 'Colossus',
  DISRUPTOR: 'Disruptor',
  OBSERVER: 'Observer',
  WARPPRISM: 'Warp Prism',
  PHOENIX: 'Phoenix',
  VOIDRAY: 'Void Ray',
  ORACLE: 'Oracle',
  CARRIER: 'Carrier',
  MOTHERSHIP: 'Mothership',
  TEMPEST: 'Tempest',
  HIGHTEMPLAR: 'High Templar',
  DARKTEMPLAR: 'Dark Templar',
  ARCHON: 'Archon',
  // 虫族常见
  DRONE: 'Drone',
  ZERGLING: 'Zergling',
  BANELING: 'Baneling',
  ROACH: 'Roach',
  RAVAGER: 'Ravager',
  HYDRALISK: 'Hydralisk',
  MUTALISK: 'Mutalisk',
  CORRUPTOR: 'Corruptor',
  BROODLORD: 'Brood Lord',
  LURKERMP: 'Lurker',
  INFESTOR: 'Infestor',
  ULTRALISK: 'Ultralisk',
  QUEEN: 'Queen',
  // 人族常见
  SCV: 'SCV',
  MARINE: 'Marine',
  MARAUDER: 'Marauder',
  MEDIVAC: 'Medivac',
  SIEGETANK: 'Siege Tank',
  SIEGETANKSIEGED: 'Siege Tank',
  VIKINGFIGHTER: 'Viking',
  VIKINGASSAULT: 'Viking',
  GHOST: 'Ghost',
  BATTLECRUISER: 'Battlecruiser',
  LIBERATOR: 'Liberator',
  BANSHEE: 'Banshee',
  RAVEN: 'Raven',
  THOR: 'Thor',
}

/** 兵种英文 id → 当前语言显示名；查不到时回退显示原英文 id。读 i18n.locale → 切语言即时重渲。 */
export function unitName(name: string): string {
  const table = i18n.locale === 'en' ? UNIT_EN : UNIT_ZH
  return table[name] ?? name
}

/** @deprecated 用 unitName（现已 locale-aware）；保留旧名向后兼容。 */
export function unitZh(name: string): string {
  return unitName(name)
}

/**
 * 把 units dict（兵种英文→数量）转成 "显示名×数量" 列表，
 * 过滤数量 <= 0 的条目。读 i18n.locale，切语言即时重渲。
 */
export function unitEntries(units: Record<string, number>): string[] {
  return Object.entries(units)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => `${unitName(k)}×${n}`)
}

/**
 * 同 unitEntries，但把名字与数量**拆开**返回 `{key, name, count}`，
 * 便于窄容器（编队条 5 格）里把名字 truncate、数量固定不被裁掉
 * （英文名长，"Void Ray×6" 整串 truncate 会把 ×6 也吃掉）。
 */
export function unitEntryParts(
  units: Record<string, number>,
): Array<{ key: string; name: string; count: number }> {
  return Object.entries(units)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => ({ key: k, name: unitName(k), count: n }))
}
