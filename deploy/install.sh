#!/bin/sh
# 版を入れる（最初の 1 回も、更新も同じ）。USB で持ってきた zip を /srv/ops/releases に展開して、その版の
# deploy/install.sh を動かす:
#
#   sudo unzip ops-20261107-3771751.zip -d /srv/ops/releases
#   sudo sh /srv/ops/releases/ops-20261107-3771751/deploy/install.sh
#
# すること:
#   1. 版の外にある設定 /srv/ops/.env を、この版の deploy/.env としてつなぐ（設定は 1 か所だけ）
#   2. 会社の証明書 /srv/ops/certs/*.crt があれば、この版の deploy/certs/ へ写す（ビルドで使う。deploy/certs/README.md）
#   3. すでに動いていれば、入れ替える前に今すぐバックアップを取る
#   4. docker compose up -d --build（app が起動のときにマイグレーションを流す）
#   5. 成功したら /srv/ops/current をこの版に向ける（以後のコマンドは /srv/ops/current/deploy で）
# 置き場所を変えるときは OPS_HOME（既定 /srv/ops）。
set -eu

OPS_HOME="${OPS_HOME:-/srv/ops}"
DEPLOY_DIR=$(cd "$(dirname "$0")" && pwd)
RELEASE_DIR=$(dirname "$DEPLOY_DIR")

say() { echo "install: $*"; }

if [ ! -f "$OPS_HOME/.env" ]; then
    say "$OPS_HOME/.env がありません。最初に作って値を決めてください:" >&2
    say "  cp $DEPLOY_DIR/.env.example $OPS_HOME/.env && vi $OPS_HOME/.env" >&2
    exit 1
fi

say "release $RELEASE_DIR"
ln -sfn "$OPS_HOME/.env" "$DEPLOY_DIR/.env"

if ls "$OPS_HOME"/certs/*.crt >/dev/null 2>&1; then
    cp "$OPS_HOME"/certs/*.crt "$DEPLOY_DIR/certs/"
    say "company certificates copied: $(ls "$OPS_HOME"/certs/*.crt | wc -l)"
fi

cd "$DEPLOY_DIR"

# 動いている backup サービスがあれば、入れ替える前のデータを残す（compose の名前は ops に固定なので、前の版のコンテナも見える）
if [ -n "$(docker compose ps -q backup 2>/dev/null)" ]; then
    say "backup before the update"
    docker compose exec -T backup sh /backup.sh once
fi

docker compose up -d --build

ln -sfn "$RELEASE_DIR" "$OPS_HOME/current"
say "$OPS_HOME/current -> $RELEASE_DIR"
docker compose ps
say "done. app が healthy になるまで少し待ってください（docker compose ps / docker compose logs app）"
