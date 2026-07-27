// Vitest 配置：jsdom 环境，对 composable + 类型逻辑做单测
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import path from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      '@locales': path.resolve(__dirname, '../locales'),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    // i18n locale 固定 zh，让断言中文文案的组件测试确定性通过
    setupFiles: ['./src/__tests__/setup.ts'],
    // 允许导入 web/ 之外的 locales/（i18n 字符串真理源）
    server: { deps: { inline: [/locales/] } },
  },
})
