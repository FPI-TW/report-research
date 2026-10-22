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
      // repo 根的 docs/：後台「API 用戶端」頁直接打包 docs/EXTERNAL_API.md（交給 API 用戶端的說明文件），
      // 不在 frontend/ 底下另存一份。
      '@docs': fileURLToPath(new URL('../docs', import.meta.url)),
      // @embedpdf/fonts-tc 的 exports 只宣告了 "."，深層匯入 fonts/*.otf 會被 exports
      // 解析擋掉，故以別名直指套件內的字型目錄。用途見 features/report/pdf/fontFallback.ts。
      '@fonts-tc': fileURLToPath(
        new URL('./node_modules/@embedpdf/fonts-tc/fonts', import.meta.url),
      ),
    },
  },
  server: {
    // dev server 預設只供應 frontend/ 底下的檔案；@docs 指到 repo 根的 docs/，要另外放行。
    fs: {
      allow: [fileURLToPath(new URL('.', import.meta.url)), fileURLToPath(new URL('../docs', import.meta.url))],
    },
    proxy: {
      '/api': 'http://localhost:8097',
      '/external': 'http://localhost:8097',
      '/login': 'http://localhost:8097',
      '/logout': 'http://localhost:8097',
    },
  },
})
