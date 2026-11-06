# API（FastAPI + uvicorn）と定期処理のワーカーのイメージ（docker-compose.yml の app / worker が使う）
# ビルドの起点はリポジトリのルート（docker-compose.yml の context: ..）
# 会社のプロキシ（HTTP_PROXY / HTTPS_PROXY / NO_PROXY）は docker-compose.yml がビルドの引数として渡す。
# Docker が最初から知っている引数なので ARG は要らず、イメージにも残らない。
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Tokyo

# 会社のプロキシが HTTPS を検査する（会社の証明書で署名し直す）ときのため、deploy/certs/ の *.crt を信頼する。
# *.crt が無ければ何も変わらない。pip は自分の証明書の束を使うので、OS の束（会社の証明書を含む）を使わせる
COPY deploy/certs/ /usr/local/share/ca-certificates/company/
RUN update-ca-certificates
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt

# ログの時刻を日本時間にするためのタイムゾーン情報
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini main.py ./
COPY alembic ./alembic
COPY src ./src
COPY scripts ./scripts
COPY deploy/app-entrypoint.sh /usr/local/bin/app-entrypoint
# Windows で編集・チェックアウトしても動くよう、改行を LF にそろえる
RUN sed -i 's/\r$//' /usr/local/bin/app-entrypoint && chmod +x /usr/local/bin/app-entrypoint

EXPOSE 8000
CMD ["app-entrypoint"]
