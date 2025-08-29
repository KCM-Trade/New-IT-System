FROM node:20-alpine

WORKDIR /app

# 可选：预装依赖以加速（实际运行前还会在容器启动时确保依赖存在）
COPY package*.json ./
RUN npm ci

# Vite 开发端口
EXPOSE 5173

# 开发环境：命令由 docker compose 覆盖（会先确保依赖，再启动 dev server）
CMD ["npm","run","dev","--","--host","0.0.0.0","--port","5173"]


