# 社内サーバーへのデプロイ（Docker Compose）

社内ネットワークだけで使う構成です（http、ドメインなし、HTTPS なし）。Linux の VM に Docker で立てます。

| サービス | 役割 |
|---|---|
| `db` | PostgreSQL 16。データは Docker の名前付きボリューム `ops_pgdata` |
| `app` | API（FastAPI）。起動のたびに `alembic upgrade head` でデータベースを最新にしてから動く |
| `worker` | 定期処理。自動リリースを毎分、予定の生成を毎日 00:05（日本時間）。Windows のタスク スケジューラーの代わり |
| `web` | 画面（nginx）。`http://<サーバー>/` で開き、`/api/` を `app` へ転送する |
| `backup` | 毎日 01:00 に `pg_dump` を `deploy/backups/` へ。14 日より古いものは消す |

## 1. 最初の準備（1 回だけ）

前提: Docker Engine と Compose プラグイン（`docker compose version` が動く）。社内からサーバーの 80 番ポートに届くこと。

```sh
git clone <このリポジトリ> ops && cd ops/deploy
cp .env.example .env
vi .env          # POSTGRES_PASSWORD を長い英数字にする。ポート・バックアップの設定もここ
docker compose up -d --build
docker compose ps   # db / app / worker / web / backup が running（app は healthy）になる
```

最初のシステム管理者を作ります（パスワードを 2 回たずねます）:

```sh
docker compose exec app python scripts/create_staff.py admin "管理者の氏名" --roles system_admin admin
```

ブラウザで `http://<サーバーの IP アドレス>/` を開き、`admin` とそのパスワードでログインします。
ほかの職員は「設定 → 職員」で登録し、「有効化」と「仮パスワード」を設定します（本人は最初のログインで自分のパスワードに変えます）。

本番を使い始める前の作業（復元の練習、営業日カレンダー `scripts/setup_calendar.py`、休日、最初の顧客、職員、
ブラウザでの確認）は [GO-LIVE.md](GO-LIVE.md) のチェックリストに沿って進めます。

## 2. 更新（新しい版を入れる）

```sh
cd ops && git pull
cd deploy && docker compose up -d --build
```

`app` が起動するときにマイグレーションを流します。失敗したら `app` は起動せず、`docker compose logs app` に理由が出ます
（そのときは前の版に戻すか、開発者に連絡してください）。更新の前に、念のため下の「今すぐバックアップ」を取ってください。

## 3. ログ

```sh
docker compose logs -f app       # API（deploy/logs/app.log にも残る。10 MB × 5 で回る）
docker compose logs -f worker    # 自動リリース・予定の生成
docker compose logs -f backup    # バックアップ
docker compose logs -f web       # アクセス
```

## 4. バックアップと復元

- 毎日 `BACKUP_TIME`（既定 01:00）に `deploy/backups/ops_YYYYMMDD_HHMMSS.dump` ができます（`pg_dump` のカスタム形式・圧縮）。
- `BACKUP_KEEP_DAYS`（既定 14）日より古いファイルは消えます。NAS への同期は OS 側で `deploy/backups/` を同期してください
  （書き込み中のファイルは `.partial` という名前なので、同期の対象から外すと確実です）。
- 保存先を変えるときは `.env` の `BACKUP_DIR`（例: `/srv/ops-backups`）。

今すぐバックアップ:

```sh
docker compose exec backup sh /backup.sh once
```

復元（バックアップの時点に戻す。それ以降の入力は失われます）:

```sh
docker compose stop app worker
docker compose exec -T db pg_restore --clean --if-exists --no-owner -U ops -d ops < backups/ops_20261107_010000.dump
docker compose start app worker
```

（`-U ops -d ops` は `.env` の `POSTGRES_USER` / `POSTGRES_DB`。）

## 5. よく使う操作

| したいこと | コマンド |
|---|---|
| 止める / 動かす | `docker compose stop` / `docker compose start` |
| 状態を見る | `docker compose ps` |
| パスワードを忘れた（画面に入れない） | `docker compose exec app python scripts/set_password.py <ログインID>` |
| データベースに入る | `docker compose exec db psql -U ops -d ops` |
| すべて消して作り直す（データも消える） | `docker compose down -v` ※ 先にバックアップ |

## 6. メモ

- http で配信するため、ログインの Cookie に `Secure` は付けていません（`docker-compose.yml` の `COOKIE_SECURE: "false"`）。
  あとで HTTPS にしたら `"true"` にします。
- データベースのポートは外に出していません（`db` は Compose の中からだけ届く）。
- API の説明（OpenAPI）は `http://<サーバー>/api/openapi.json` で見られます（`/api/docs` の画面は使えません）。
- 開発 PC の Windows のタスク スケジューラー（PlanExtendJob / OrderReleaseJob）は開発用のデータベース向けです。
  サーバーでは `worker` が同じことをするので、サーバーには登録しません。
