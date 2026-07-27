/** @type {import('tailwindcss').Config} */
export default {
  // 扫 src/ 下所有 Vue / TS / HTML 文件
  content: ['./index.html', './src/**/*.{vue,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // SC2 暗色系主题调色板
        surface: '#0d1117',
        'surface-2': '#161b22',
        'surface-3': '#21262d',
        border: '#30363d',
        muted: '#8b949e',
        accent: '#58a6ff',
        success: '#3fb950',
        warn: '#d29922',
        danger: '#f85149',
      },
    },
  },
  plugins: [
    // portrait / landscape variants（CSS @media (orientation: ...)）
    // 用于 LiveView 响应式布局
    ({ addVariant }) => {
      addVariant('portrait', '@media (orientation: portrait)')
      addVariant('landscape', '@media (orientation: landscape)')
    },
  ],
}
