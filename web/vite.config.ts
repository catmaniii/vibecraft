// Vite 配置：Vue 3 + PWA manifest + 产物输出到后端 static 目录
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'
import path from 'path'

export default defineConfig({
  plugins: [
    vue(),
    VitePWA({
      // MVP 阶段不需要离线缓存。autoUpdate 会在新版本就绪后**自动 reload 整页**，
      // 对局中刷掉 WS 状态导致 SC2/Bot 段显示成 idle。改 'prompt' 让用户主动触发
      // （目前 UI 还没接 prompt → 实际上等于不会自动 reload，开局后无中断）。
      registerType: 'prompt',
      manifest: {
        name: 'VibeCraft',
        short_name: 'VibeCraft',
        description: '语音指挥 SC2 神族 bot',
        theme_color: '#0d1117',
        background_color: '#0d1117',
        display: 'standalone',
        orientation: 'portrait',
        icons: [
          {
            src: 'icons/icon-192.png',
            sizes: '192x192',
            type: 'image/png',
          },
          {
            src: 'icons/icon-512.png',
            sizes: '512x512',
            type: 'image/png',
          },
        ],
      },
      // MVP：不做离线 SW 预缓存，只注册 manifest
      workbox: {
        // 开发阶段不生成大 SW
        globPatterns: [],
      },
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  // build 产物输出到后端 static 目录，供 http.py serve
  build: {
    outDir: path.resolve(__dirname, '../src/vibecraft/server/static'),
    emptyOutDir: true,
  },
})
