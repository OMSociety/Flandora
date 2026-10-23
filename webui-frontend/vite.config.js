import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  base: './',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    // 产物落进包内：webui/app.py 按 __file__ 旁的 dist/ 找它。根目录的 dist/ 归
    // PyInstaller 与发布 zip 用，前端产物不能写进去。
    outDir: '../flandora/webui/dist',
    emptyOutDir: true,
  },
})
