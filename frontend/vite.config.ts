import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    origin: process.env.VITE_PUBLIC_ORIGIN || "https://analysis.kohleservices.com",
    allowedHosts: [
      "localhost",
      "127.0.0.1",
      "analysis.kohleservices.com",
      "10.6.20.138"
    ],
    hmr: {
      protocol: (process.env.VITE_HMR_PROTOCOL as "ws" | "wss") || "wss",
      host: process.env.VITE_HMR_HOST || "analysis.kohleservices.com",
      clientPort: Number(process.env.VITE_HMR_CLIENT_PORT || 443),
    },
    headers: {
      "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
      Pragma: "no-cache",
      Expires: "0",
    },
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET || "http://localhost:8001",
        changeOrigin: true,
        // 如后端没有 /api 前缀，可启用以下行：
        // rewrite: (p) => p.replace(/^\/api/, ""),
      },
      // 代理 dash 服务到内网 8050 端口
      "/dash": {
        target: "http://10.6.20.138:8050",
        changeOrigin: true,
        // 将 /dash 前缀去掉，转发到 Dash 根路径 /
        rewrite: (path) => path.replace(/^\/dash/, ""),
        // 支持 WebSocket（如果 dash 使用）
        ws: true,
        // 配置超时
        timeout: 30000,
        // 处理代理错误
        configure: (proxy) => {
          proxy.on('error', (err) => {
            console.log('proxy error', err);
          });
          proxy.on('proxyReq', (_proxyReq, req) => {
            console.log('Sending Request to the Target:', req.method, req.url);
          });
        },
      },
      // Dash 在页面内会请求一系列以根路径开头的资源，这里一并转发到 8050
      "/_dash-component-suites": {
        target: "http://10.6.20.138:8050",
        changeOrigin: true,
        ws: true,
        timeout: 30000,
      },
      "/_dash-dependencies": {
        target: "http://10.6.20.138:8050",
        changeOrigin: true,
        timeout: 30000,
      },
      "/_dash-layout": {
        target: "http://10.6.20.138:8050",
        changeOrigin: true,
        timeout: 30000,
      },
      "/_dash-update-component": {
        target: "http://10.6.20.138:8050",
        changeOrigin: true,
        timeout: 30000,
      },
      "/assets": {
        target: "http://10.6.20.138:8050",
        changeOrigin: true,
        timeout: 30000,
      },
      "/_favicon.ico": {
        target: "http://10.6.20.138:8050",
        changeOrigin: true,
        timeout: 30000,
      },
      "/favicon.ico": {
        target: "http://10.6.20.138:8050",
        changeOrigin: true,
        timeout: 30000,
      },
    },
  },
})
