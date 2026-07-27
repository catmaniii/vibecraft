// 截图单个预览组件（可选先点击展开），检查横向溢出。
// 用法: node scripts/preview_shot.mjs <ComponentName> <locale> [clickTestId]
import { chromium } from 'playwright'
import { join } from 'path'

const [name, locale = 'en', clickId] = process.argv.slice(2)
const out = join(process.env.TEMP || '/tmp', 'vibecraft-i18n-shots')
const url = `http://127.0.0.1:8080/preview.html?c=${name}&locale=${locale}`

const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })
const p = await ctx.newPage()
await p.goto(url, { waitUntil: 'networkidle', timeout: 15000 })
await p.waitForTimeout(300)
if (clickId) {
  const el = await p.$(`[data-testid="${clickId}"]`)
  if (el) { await el.click(); await p.waitForTimeout(250) }
}
const w = await p.evaluate(() => document.documentElement.scrollWidth)
const file = join(out, `prev-${name}.${locale}.png`)
await p.screenshot({ path: file, fullPage: true })
console.log(`${name}.${locale} scrollWidth=${w} (overflow if >390)  -> ${file}`)
await b.close()
