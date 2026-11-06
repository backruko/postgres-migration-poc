#!/bin/sh
# API の起動: データベースを最新の形にしてから uvicorn を起動する（マイグレーションに失敗したら起動しない）
set -e

mkdir -p "$(dirname "${LOG_FILE:-/app/logs/app.log}")"

echo "Running database migrations (alembic upgrade head)..."
alembic upgrade head

echo "Starting the API on port 8000..."
# 1 プロセス: ログファイルのローテーションを 1 か所にするため（社内の少人数なら十分）
exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips="*"
