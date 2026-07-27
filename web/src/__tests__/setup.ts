// vitest 全局 setup：把 i18n locale 固定为 zh，让断言中文文案的既有组件测试确定性通过。
// 需要测 en 的用例自行 setLocale('en')（i18n.test.ts 在 beforeEach 复位）。
import { setLocale } from '@/i18n'

setLocale('zh')
