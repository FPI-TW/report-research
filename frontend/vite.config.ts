import { defineConfig } from 'vite'
import { fileURLToPath, URL } from 'node:url'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  base: '/app/',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // @embedpdf/fonts-tc 的 exports 只宣告了 "."，深層匯入 fonts/*.otf 會被 exports
      // 解析擋掉，故以別名直指套件內的字型目錄。用途見 features/report/pdf/fontFallback.ts。
      '@fonts-tc': fileURLToPath(
        new URL('./node_modules/@embedpdf/fonts-tc/fonts', import.meta.url),
      ),
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8097',
      '/login': 'http://localhost:8097',
      '/logout': 'http://localhost:8097',
    },
  },
})
