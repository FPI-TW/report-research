import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
    // 只收 src 下的單元測試；e2e/ 為 Playwright，勿讓 Vitest 收（其 test() 不能在 Vitest 跑）
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
})
