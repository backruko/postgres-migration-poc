# 画面（React のビルド結果）を nginx で配信し、/api/ を API（app）へ転送するイメージ
# ビルドの起点はリポジトリのルート（docker-compose.yml の context: ..）
FROM node:24-alpine AS build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# API は同じオリジンの /api（既定値）。タイムゾーンは日本時間（既定値）
RUN npm run build

FROM nginx:1.27-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html
EXPOSE 80
