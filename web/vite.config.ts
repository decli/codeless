import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 开发时界面跑在 5173，接口转发到后端；去掉 Origin 头，否则后端的同源检查会拒绝写请求
const backend = process.env.CODELESS_BACKEND || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: backend,
        configure: (proxy) => {
          proxy.on('proxyReq', (req) => req.removeHeader('origin'))
        },
      },
    },
  },
  build: { outDir: 'dist', chunkSizeWarningLimit: 900 },
})
