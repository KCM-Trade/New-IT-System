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
    allowedHosts: [
      "localhost",
      "127.0.0.1",
      "analysis.kohleservices.com",
      "10.6.20.138"
    ],
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
      // 代理 Login IP 监测服务到内网 8000 端口
      "/ipmonitor": {
        target: "http://10.6.20.138:8000",
        changeOrigin: true,
        // 将 /ipmonitor 前缀去掉，转发到根路径 /
        rewrite: (path) => path.replace(/^\/ipmonitor/, ""),
        // 若后端支持 WebSocket，可开启
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
          // 重写后端 3xx 跳转的 Location 头，保持在 /ipmonitor 前缀下
          proxy.on('proxyRes', (proxyRes) => {
            try {
              const locHeader = proxyRes.headers?.['location']
              if (!locHeader) return
              const loc = Array.isArray(locHeader) ? locHeader[0] : locHeader
              if (!loc || typeof loc !== 'string') return
              if (loc.startsWith('/ipmonitor')) return
              if (loc.startsWith('/')) {
                proxyRes.headers['location'] = `/ipmonitor${loc}`
                return
              }
              try {
                const u = new URL(loc)
                if (u.host === '10.6.20.138:8000') {
                  proxyRes.headers['location'] = `/ipmonitor${u.pathname}${u.search}${u.hash}`
                }
              } catch {}
            } catch {}
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
      // Login IP 服务的静态资源（如 /static/images/Logo-01.svg）转发到 8000
      "/static": {
        target: "http://10.6.20.138:8000",
        changeOrigin: true,
        timeout: 30000,
      },
    },
  },
})