#!/bin/sh
# データベースの毎日のバックアップ（docker-compose.yml の backup サービス）。
# 毎日 BACKUP_TIME（日本時間、既定 01:00）に pg_dump（カスタム形式・圧縮）を /backups に書き、
# BACKUP_KEEP_DAYS 日（既定 14。0 なら消さない）より古いものを消す。NAS への同期は OS 側で行う。
#
#   sh /backup.sh        … 毎日の繰り返し（サービスとして）
#   sh /backup.sh once   … 今すぐ 1 回だけ（docker compose exec backup sh /backup.sh once）
#
# 接続先は PGHOST / PGUSER / PGPASSWORD / PGDATABASE（docker-compose.yml が渡す）。
set -eu

BACKUP_TIME="${BACKUP_TIME:-01:00}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
DIR="${BACKUP_DEST:-/backups}"  # コンテナでは /backups（docker-compose.yml がフォルダーを渡す）

dump() {
    name="$DIR/${PGDATABASE}_$(date +%Y%m%d_%H%M%S).dump"
    echo "$(date '+%Y-%m-%d %H:%M:%S') backup: writing $name"
    # 書き終わってから名前を付ける（途中で止まった不完全なファイルを、正しいバックアップと見間違えないように）
    if pg_dump --format=custom --compress=6 --file="$name.partial"; then
        mv "$name.partial" "$name"
        echo "$(date '+%Y-%m-%d %H:%M:%S') backup: done ($(du -h "$name" | cut -f1))"
    else
        rm -f "$name.partial"
        echo "$(date '+%Y-%m-%d %H:%M:%S') backup: FAILED" >&2
        return 1
    fi
    if [ "$BACKUP_KEEP_DAYS" -gt 0 ]; then
        find "$DIR" -maxdepth 1 -name "${PGDATABASE}_*.dump" -mtime +"$((BACKUP_KEEP_DAYS - 1))" -print -delete \
            | sed "s/^/$(date '+%Y-%m-%d %H:%M:%S') backup: removed old /"
    fi
}

if [ "${1:-}" = "once" ]; then
    dump
    exit $?
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') backup: daily at $BACKUP_TIME, keeping $BACKUP_KEEP_DAYS days in $DIR"
while true; do
    now=$(date +%s)
    next=$(date -d "today $BACKUP_TIME" +%s)
    [ "$next" -le "$now" ] && next=$(date -d "tomorrow $BACKUP_TIME" +%s)
    sleep $((next - now))
    dump || true  # 失敗してもサービスは止めず、次の日にまた試す（ログに FAILED が残る）
done
