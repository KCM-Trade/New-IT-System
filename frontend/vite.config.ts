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
      // [REMOVED] Dash proxy to 10.6.20.138:8050 - service disabled
    },
  },
})
