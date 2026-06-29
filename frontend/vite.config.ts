import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: '/app/',
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8097',
      '/login': 'http://localhost:8097',
      '/logout': 'http://localhost:8097',
    },
  },
})
