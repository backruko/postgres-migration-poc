# 社内サーバーへのデプロイ（Docker Compose）

社内ネットワークだけで使う構成です（http、ドメインなし、HTTPS なし）。Linux のサーバーに Docker で立てます。
サーバーは GitHub に届かないため、**新しい版は zip にして USB で持っていきます**。インターネットへは会社のプロキシを通して出ます。

| サービス | 役割 |
|---|---|
| `db` | PostgreSQL 16。データは Docker の名前付きボリューム `ops_pgdata`（版を入れ替えても残る） |
| `app` | API（FastAPI）。起動のたびに `alembic upgrade head` でデータベースを最新にしてから動く |
| `worker` | 定期処理。自動リリースを毎分、予定の生成を毎日 00:05（日本時間）。Windows のタスク スケジューラーの代わり |
| `web` | 画面（nginx）。`http://<サーバー>/` で開き、`/api/` を `app` へ転送する |
| `backup` | 毎日 01:00 に `pg_dump` を `/srv/ops/backups/` へ。14 日より古いものは消す |

## フォルダーの形

```
/srv/ops/
  .env                         設定（1 つだけ。見本は deploy/.env.example）
  certs/                       会社の証明書 *.crt（プロキシが HTTPS を検査するときだけ。deploy/certs/README.md）
  backups/                     毎日のバックアップ（NAS へ同期する）
  logs/                        API のログ app.log
  releases/ops-20261107-3771751/   版ごとのフォルダー（zip を展開したもの。毎回新しいフォルダー）
  current -> releases/ops-…/   今動いている版（deploy/install.sh が切り替える）
```

版のフォルダーは毎回新しく作ります（上書きしない）。前の版で消したファイル（たとえば名前を変えたマイグレーション）が
残って起動できなくなるのを防ぐためです。設定・バックアップ・ログは版の外にあるので、版を入れ替えても残ります。
**`docker compose …` のコマンドはすべて `/srv/ops/current/deploy` で実行します。**

## 0. サーバーの準備（1 回だけ）

前提: Linux（例: Ubuntu Server 24.04）、`sudo` が使えること、社内からサーバーの 80 番ポートに届くこと。
以下の `http://proxy.example.co.jp:8080` は会社のプロキシに読み替えてください。

1. OS のパッケージ取得にプロキシを使う（`/etc/apt/apt.conf.d/95proxy`）:

   ```
   Acquire::http::Proxy "http://proxy.example.co.jp:8080/";
   Acquire::https::Proxy "http://proxy.example.co.jp:8080/";
   ```

2. Docker Engine と Compose プラグイン、`unzip` を入れる（Docker の公式の手順 <https://docs.docker.com/engine/install/ubuntu/>。
   `curl` には `-x http://proxy.example.co.jp:8080` を付ける）。`docker compose version` が動けば OK。
3. **`docker pull` にプロキシを使う**（元になるイメージ postgres / python / node / nginx の取得。Docker Engine 23 以降）。
   `/etc/docker/daemon.json`:

   ```json
   {
     "proxies": {
       "http-proxy": "http://proxy.example.co.jp:8080",
       "https-proxy": "http://proxy.example.co.jp:8080",
       "no-proxy": "localhost,127.0.0.1"
     }
   }
   ```

   `sudo systemctl restart docker` のあと `sudo docker pull hello-world` が成功すれば OK。
4. **ビルドにプロキシを使う**（イメージを作るときの apt / pip / npm）: 下の 1. で `/srv/ops/.env` の `HTTP_PROXY` /
   `HTTPS_PROXY` に同じプロキシを書きます。
5. プロキシが HTTPS を検査している（会社の証明書で署名し直す）ときだけ: 会社のルート証明書（PEM、拡張子 `.crt`）を
   - `/srv/ops/certs/` に置く（ビルドの apt / pip / npm 用。`deploy/install.sh` が使う）
   - OS にも入れる（`docker pull` 用）: `sudo cp company.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates && sudo systemctl restart docker`

## 1. 最初の起動（1 回だけ）

開発 PC で zip を作ります（コミット済みの内容だけが入ります。`.env` は入りません）:

```sh
python scripts/make_package.py      # → packages/ops-20261107-3771751.zip
```

USB でサーバーへ持っていき:

```sh
sudo mkdir -p /srv/ops/releases /srv/ops/certs
sudo unzip ops-20261107-3771751.zip -d /srv/ops/releases
sudo cp /srv/ops/releases/ops-20261107-3771751/deploy/.env.example /srv/ops/.env
sudo vi /srv/ops/.env     # POSTGRES_PASSWORD を長い英数字に。プロキシ（HTTP_PROXY / HTTPS_PROXY）もここ
sudo chmod 600 /srv/ops/.env
sudo sh /srv/ops/releases/ops-20261107-3771751/deploy/install.sh
```

`install.sh` が最後に `docker compose ps` を出します。db / app / worker / web / backup が running（app は healthy）になれば OK。

最初のシステム管理者を作ります（パスワードを 2 回たずねます）:

```sh
cd /srv/ops/current/deploy
sudo docker compose exec app python scripts/create_staff.py admin "管理者の氏名" --roles system_admin admin
```

ブラウザで `http://<サーバーの IP アドレス>/` を開き、`admin` とそのパスワードでログインします。
ほかの職員は「設定 → 職員」で登録し、「有効化」と「仮パスワード」を設定します（本人は最初のログインで自分のパスワードに変えます）。

本番を使い始める前の作業（復元の練習、営業日カレンダー `scripts/setup_calendar.py`、休日、最初の顧客、職員、
ブラウザでの確認）は [GO-LIVE.md](GO-LIVE.md) のチェックリストに沿って進めます。

## 2. 更新（新しい版を入れる）

開発 PC で `python scripts/make_package.py`、USB でサーバーへ持っていき:

```sh
sudo unzip ops-20261120-abcdef1.zip -d /srv/ops/releases
sudo sh /srv/ops/releases/ops-20261120-abcdef1/deploy/install.sh
```

`install.sh` は、動いているなら入れ替える前に**バックアップを 1 つ取り**、新しい版を作って起動し、`/srv/ops/current` を
新しい版に向けます。`app` は起動するときにマイグレーションを流します。失敗したら `app` は起動せず
（`install.sh` もエラーで止まり、`current` は前の版のまま）、`docker compose logs app` に理由が出ます。

- **前の版に戻す**: `sudo sh /srv/ops/releases/<前の版>/deploy/install.sh`。ただし新しい版でマイグレーションが
  流れていたら、前の版はデータベースの形が合わず動かないことがあります。そのときは開発者に連絡するか、
  更新の前のバックアップから戻します（4.）。
- 古い版のフォルダーは、`current` が指していなければ消してかまいません（`sudo rm -r /srv/ops/releases/<版>`）。

## 3. ログ

```sh
cd /srv/ops/current/deploy
sudo docker compose logs -f app       # API（/srv/ops/logs/app.log にも残る。10 MB × 5 で回る）
sudo docker compose logs -f worker    # 自動リリース・予定の生成
sudo docker compose logs -f backup    # バックアップ
sudo docker compose logs -f web       # アクセス
```

## 4. バックアップと復元

- 毎日 `BACKUP_TIME`（既定 01:00）に `/srv/ops/backups/ops_YYYYMMDD_HHMMSS.dump` ができます（`pg_dump` のカスタム形式・圧縮）。
- `BACKUP_KEEP_DAYS`（既定 14）日より古いファイルは消えます。NAS への同期は OS 側で `/srv/ops/backups/` を同期してください
  （書き込み中のファイルは `.partial` という名前なので、同期の対象から外すと確実です）。
- 保存先を変えるときは `/srv/ops/.env` の `BACKUP_DIR`。

今すぐバックアップ（`/srv/ops/current/deploy` で）:

```sh
sudo docker compose exec backup sh /backup.sh once
```

復元（バックアップの時点に戻す。それ以降の入力は失われます）:

```sh
cd /srv/ops/current/deploy
sudo docker compose stop app worker
sudo docker compose exec -T db pg_restore --clean --if-exists --no-owner -U ops -d ops < /srv/ops/backups/ops_20261107_010000.dump
sudo docker compose start app worker
```

（`-U ops -d ops` は `.env` の `POSTGRES_USER` / `POSTGRES_DB`。）

## 5. よく使う操作（`/srv/ops/current/deploy` で）

| したいこと | コマンド |
|---|---|
| 止める / 動かす | `sudo docker compose stop` / `sudo docker compose start` |
| 状態を見る | `sudo docker compose ps` |
| パスワードを忘れた（画面に入れない） | `sudo docker compose exec app python scripts/set_password.py <ログインID>` |
| データベースに入る | `sudo docker compose exec db psql -U ops -d ops` |
| すべて消して作り直す（データも消える） | `sudo docker compose down -v` ※ 先にバックアップ |

## 6. メモ

- http で配信するため、ログインの Cookie に `Secure` は付けていません（`docker-compose.yml` の `COOKIE_SECURE: "false"`）。
  あとで HTTPS にしたら `"true"` にします。
- データベースのポートは外に出していません（`db` は Compose の中からだけ届く）。
- プロキシはイメージを作るときだけ使います。動いているコンテナはインターネットに出ません。
- API の説明（OpenAPI）は `http://<サーバー>/api/openapi.json` で見られます（`/api/docs` の画面は使えません）。
- 開発 PC の Windows のタスク スケジューラー（PlanExtendJob / OrderReleaseJob）は開発用のデータベース向けです。
  サーバーでは `worker` が同じことをするので、サーバーには登録しません。
