// i18n 视觉回归：headless Chromium 手机分辨率，对各页面/态在 zh + en 各截一张图。
// 用法: node scripts/visual_shots.mjs [baseURL] [outDir]
//   baseURL 默认 http://127.0.0.1:8080/?room=vibecraft-dev（指向运行中的 server，serve 最新 bundle）
// 截图供人/Claude Read 判读：英文更长，按钮/标签/状态链是否溢出/截断/错位。
import { chromium } from 'playwright'
import { mkdirSync } from 'fs'
import { join } from 'path'

const BASE = process.argv[2] || 'http://127.0.0.1:8080/?room=vibecraft-dev'
const OUT = process.argv[3] || join(process.env.TEMP || '/tmp', 'vibecraft-i18n-shots')
mkdirSync(OUT, { recursive: true })

const VIEWPORT = { width: 390, height: 844 } // iPhone 12/13/14 逻辑分辨率

// 每个 case：name + 一段在页面里跑的交互（打开弹窗/展开表单等），null 表示基础态。
const CASES = [
  { name: 'entry-base', setup: null },
  { name: 'entry-addform', setup: async (p) => { await p.click('[data-testid="add-server-toggle"]'); await p.waitForTimeout(150) } },
  { name: 'entry-feedback', setup: async (p) => { await p.click('[data-testid="feedback-open"]'); await p.waitForTimeout(150) } },
]

const browser = await chromium.launch()
const results = []
for (const locale of ['zh', 'en']) {
  const ctx = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 2 })
  // 首帧前注入 locale，保证初次渲染即对的语言
  await ctx.addInitScript((loc) => { try { localStorage.setItem('vibecraft.locale', loc) } catch {} }, locale)
  for (const c of CASES) {
    const page = await ctx.newPage()
    try {
      await page.goto(BASE, { waitUntil: 'networkidle', timeout: 15000 })
      await page.waitForTimeout(300)
      if (c.setup) await c.setup(page)
      const file = join(OUT, `${c.name}.${locale}.png`)
      await page.screenshot({ path: file })
      results.push(`OK  ${c.name}.${locale}`)
    } catch (e) {
      results.push(`ERR ${c.name}.${locale}: ${e.message.split('\n')[0]}`)
    }
    await page.close()
  }
  await ctx.close()
}
await browser.close()
console.log(results.join('\n'))
console.log('OUT=' + OUT)
