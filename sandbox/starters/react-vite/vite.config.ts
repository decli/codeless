import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 预览经 codeless 反向代理访问：必须监听 0.0.0.0，端口用环境变量 PORT
const port = Number(process.env.PORT) || 3000

export default defineConfig({
  plugins: [react()],
  server: { host: '0.0.0.0', port, strictPort: true, allowedHosts: true },
  preview: { host: '0.0.0.0', port, strictPort: true, allowedHosts: true },
})
