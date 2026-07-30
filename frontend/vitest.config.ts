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
