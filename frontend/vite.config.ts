/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// dev 서버는 /api 를 로컬 API(8000)로 넘긴다. VITE_USE_MOCK=true 면 프록시를 쓰지 않는다.
export default defineConfig({
  plugins: [react()],
  build: { chunkSizeWarningLimit: 900 }, // mock JSON 포함
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    css: false,
  },
})
