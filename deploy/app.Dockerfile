# API（FastAPI + uvicorn）と定期処理のワーカーのイメージ（docker-compose.yml の app / worker が使う）
# ビルドの起点はリポジトリのルート（docker-compose.yml の context: ..）
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Tokyo

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
