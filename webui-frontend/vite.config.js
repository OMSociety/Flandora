import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import { readFileSync } from 'node:fs'

// 品牌图 `assets/icon.svg` 留在仓库根当源文件：构建时原样放进产物，开发服务器也照发。
// 这样 index.html 里只写 <link rel="icon" href="/icon.svg">，不用再内联一份同款图形。
const brandIcon = fileURLToPath(new URL('../assets/icon.svg', import.meta.url))

function brandIconPlugin() {
  return {
    name: 'flandora-brand-icon',
    configureServer(server) {
      server.middlewares.use('/icon.svg', (_req, res) => {
        res.setHeader('Content-Type', 'image/svg+xml')
        res.end(readFileSync(brandIcon))
      })
    },
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'icon.svg', source: readFileSync(brandIcon) })
    },
  }
}

export default defineConfig({
  base: './',
  plugins: [vue(), brandIconPlugin()],
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
