# 画面（React のビルド結果）を nginx で配信し、/api/ を API（app）へ転送するイメージ
# ビルドの起点はリポジトリのルート（docker-compose.yml の context: ..）
# 会社のプロキシ（HTTP_PROXY / HTTPS_PROXY / NO_PROXY）は docker-compose.yml がビルドの引数として渡す（npm が使う）。
FROM node:24-alpine AS build
WORKDIR /build
# 会社のプロキシが HTTPS を検査するときのため、deploy/certs/ の *.crt を npm（Node）に信頼させる（無ければ何もしない）
COPY deploy/certs/ /certs/
COPY frontend/package.json frontend/package-lock.json ./
RUN if ls /certs/*.crt >/dev/null 2>&1; then \
        cat /certs/*.crt > /certs/company.pem && export NODE_EXTRA_CA_CERTS=/certs/company.pem; \
    fi; \
    npm ci
COPY frontend/ ./
# API は同じオリジンの /api（既定値）。タイムゾーンは日本時間（既定値）
RUN npm run build

FROM nginx:1.27-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html
EXPOSE 80
