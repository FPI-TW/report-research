import { defineConfig } from 'vitest/config'
import { fileURLToPath, URL } from 'node:url'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // SSE 事件契約 fixture 住在 repo 根的 tests/fixtures：後端測試也吃同一份，
      // 複製一份到 frontend/ 底下就等於讓兩側各有一份真相（正是這份檔案要消滅的問題）。
      // 只有測試用得到，故只設在 vitest 這邊（vite.config.ts 的 build 不需要）。
      '@fixtures': fileURLToPath(new URL('../tests/fixtures', import.meta.url)),
      // 與 vite.config.ts 同一條別名。測試裡 PdfViewer 一律被 mock 掉、不會真的載字型，
      // 但少了它，任何一支 import 到 fontFallback.ts 的測試會在解析階段就炸。
      '@fonts-tc': fileURLToPath(
        new URL('./node_modules/@embedpdf/fonts-tc/fonts', import.meta.url),
      ),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: true,
  },
})
