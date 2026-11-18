# 社内サーバーへのデプロイ（Docker Compose）

社内ネットワークだけで使う構成です（http、ドメインなし、HTTPS なし）。Linux（Ubuntu）のサーバーに Docker で立てます。
**新しい版は、同じサーバーで動く Gitea（社内の Git サーバー）のパイプラインで入れます**: 開発 PC から main を
Gitea へ push すると自動のチェックが走り、決まった人が「Run workflow」を押すと、チェックをもう一度とおしてから
サーバーに入ります（0b.・1.・2.）。Gitea が使えないときの予備として、zip を USB で持っていく方法も残します（2b.）。
サーバーは**インターネットに直接出られる**前提です（イメージ・パッケージの取得）。会社のプロキシは、たぶん要らないと
分かりました（2026-11-17）。0. の最初の確かめで出られなければ、[付録. プロキシが必要なとき](#付録-プロキシが必要なとき) を
行います。

| サービス | 役割 |
|---|---|
| `db` | PostgreSQL 16。データは Docker の名前付きボリューム `ops_pgdata`（版を入れ替えても残る） |
| `app` | API（FastAPI）。起動のたびに `alembic upgrade head` でデータベースを最新にしてから動く |
| `worker` | 定期処理。自動リリース・締切の監視（遅れ・まもなくを出来事として記録）・通知の振り分けと配信（N.3 ではテスト用のチャネルだけ。実際には届かない）を毎分、予定の生成を毎日 00:05（日本時間）。Windows のタスク スケジューラーの代わり |
| `web` | 画面（nginx）。`http://<サーバー>/` で開き、`/api/` を `app` へ転送する |
| `backup` | 毎日 01:00 に `pg_dump` を `/srv/ops/backups/` へ。14 日より古いものは消す |

## フォルダーの形

```
/srv/ops/
  .env                         設定（1 つだけ。見本は deploy/.env.example）
  certs/                       会社の証明書 *.crt（プロキシが要り、HTTPS を検査するときだけ。付録と deploy/certs/README.md）
  backups/                     毎日のバックアップ（NAS へ同期する）
  logs/                        API のログ app.log
  releases/ops-20261107-3771751/   版ごとのフォルダー（ops-<コミットの日付>-<コミット>。パイプラインでも zip でも同じ名前）
  current -> releases/ops-…/   今動いている版（deploy/install.sh が切り替える）
  deploys.log                  パイプラインのデプロイの記録（日時・押した人・コミット・版・ok / failed。1 回 1 行）
/srv/gitea/
  compose/                     Gitea とランナーの docker-compose.yml と .env（deploy/gitea/ から）
  data/                        Gitea のデータ（リポジトリ・データベース・設定）。data/backups/ に毎晩の gitea dump
  runner/                      ランナーの登録情報と config.yaml
```

版のフォルダーは毎回新しく作ります（上書きしない）。前の版で消したファイル（たとえば名前を変えたマイグレーション）が
残って起動できなくなるのを防ぐためです。設定・バックアップ・ログは版の外にあるので、版を入れ替えても残ります。
**`docker compose …` のコマンドはすべて `/srv/ops/current/deploy` で実行します。**

## 0. サーバーの準備（1 回だけ）

前提: Linux（例: Ubuntu Server 24.04）、`sudo` が使えること、社内からサーバーの 80 番ポート（画面）と 3000 番ポート
（Gitea、0b.）に届くこと。

1. **インターネットに直接出られるか確かめる**（イメージ・パッケージの取得先。どれも HTTP の応答の 1 行目が出れば OK。
   Docker Hub は `401` でも OK = 届いている）:

   ```sh
   curl -sI --max-time 15 https://download.docker.com/linux/ubuntu/gpg | head -1
   curl -sI --max-time 15 https://registry-1.docker.io/v2/ | head -1
   curl -sI --max-time 15 https://pypi.org/simple/ | head -1
   curl -sI --max-time 15 https://registry.npmjs.org/ | head -1
   ```

   何も出ない（待ったあとで終わる）・証明書の誤りが出るときは、会社のプロキシが要ります。
   [付録. プロキシが必要なとき](#付録-プロキシが必要なとき) の 1. と 2. を行ってから 2. へ進み、Docker を入れた後で
   付録の 3. を行います（0b. と 1. でも付録の 4. と 5. が要ります）。

2. **Docker Engine と Compose プラグイン、`unzip` を入れる**（Docker の公式の手順
   <https://docs.docker.com/engine/install/ubuntu/> と同じ。Ubuntu の標準の `docker.io` パッケージではなく、Docker の
   apt リポジトリから入れる）。

   ```sh
   # 下準備
   sudo apt-get update
   sudo apt-get install -y ca-certificates curl unzip

   # Docker の署名の鍵
   sudo install -m 0755 -d /etc/apt/keyrings
   sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
   sudo chmod a+r /etc/apt/keyrings/docker.asc

   # Docker の apt リポジトリを足す（Ubuntu の版の名前、たとえば noble は自動で入る）
   echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" \
       | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

   # 入れる
   sudo apt-get update
   sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
   ```

   確かめる: `sudo systemctl is-active docker` が `active`、`sudo docker compose version` が版を出せば OK
   （サーバーを再起動しても Docker は自動で起動します）。この手順書のコマンドはすべて `sudo` 付きなので、
   自分を `docker` グループに入れる必要はありません。
3. **元になるイメージを取れるか確かめる**（postgres / python / node / nginx などは Docker Hub から取ります）:
   `sudo docker pull hello-world` が成功すれば OK。

## 0b. Gitea とランナー（1 回だけ）

> **先に 0.（サーバーの準備）をすべて終えてください。** Docker が動き、`sudo docker pull hello-world` が
> 成功していること（0. の 2. と 3.）。Gitea・ランナー・パイプラインのジョブは、どれも Docker のイメージを取って動きます。

Gitea（画面と git の push は `http://<サーバー>:3000/`、SSH は使わない）と、パイプラインのジョブを動かすランナーを
立てます。どちらも Docker のコンテナで、アプリとは別の Compose（`/srv/gitea/compose`）です。ジョブはそのたびに
使い捨てのコンテナで動くので、サーバーには Docker のほかに何も入れません。以下の `10.0.0.5` はサーバーの IP アドレスに
読み替えてください。

1. **開発 PC で、サーバーに最初に要るファイルをまとめて送る**（コミット済みの内容だけ。改行は LF のまま。
   開発 PC の Git Bash か PowerShell で、リポジトリのフォルダーから）:

   ```sh
   git -c core.autocrlf=false archive --format=tar -o ops-bootstrap.tar HEAD deploy/gitea deploy/.env.example
   scp ops-bootstrap.tar <サーバーのユーザー>@10.0.0.5:/tmp/
   ```

2. **サーバーで置き場所を作る**:

   ```sh
   sudo mkdir -p /srv/ops/bootstrap /srv/gitea/compose /srv/gitea/data/backups /srv/gitea/runner
   sudo tar -xf /tmp/ops-bootstrap.tar -C /srv/ops/bootstrap
   sudo cp /srv/ops/bootstrap/deploy/gitea/docker-compose.yml /srv/gitea/compose/
   sudo cp /srv/ops/bootstrap/deploy/gitea/.env.example /srv/gitea/compose/.env
   sudo cp /srv/ops/bootstrap/deploy/gitea/runner-config.example.yaml /srv/gitea/runner/config.yaml
   sudo chown -R 1000:1000 /srv/gitea/data
   sudo vi /srv/gitea/compose/.env           # SERVER_ADDRESS をサーバーの IP アドレスに（RUNNER_TOKEN は 5. で）
   sudo chmod 600 /srv/gitea/compose/.env
   ```

3. **Gitea を起動して、管理者を作る**（パスワードが画面に出るので控え、最初のログインで変える）:

   ```sh
   cd /srv/gitea/compose
   sudo docker compose up -d gitea
   sudo docker exec -u git gitea gitea admin user create --admin --username <管理者の名前> \
       --email <メールアドレス> --random-password --must-change-password
   ```

   ブラウザで `http://10.0.0.5:3000/` を開き、その名前でログインします。自分で登録はできません。ほかの人
   （push する人、デプロイする人）のアカウントは、管理者が「サイト管理 → ユーザーアカウント」で作ります。

4. **リポジトリを作る**（画面で）:
   - 組織 `ops` を作り、その中にリポジトリ `postgres-migration-poc` を作る（非公開。中身は空のまま。README も足さない）
   - リポジトリの「設定 → 共同作業者」で、push する人とデプロイする人に **書き込み** の権限（デプロイの
     「Run workflow」には書き込みの権限が要る）。設定を変えられるのは管理者だけ
   - 「設定 → 高度な設定」で **Actions** が有効になっていることを確かめる
   - 「設定 → Actions → 変数」に **`DEPLOY_USERS`** を作り、デプロイしてよい人のログイン名を書く（カンマか空白で区切る。
     例 `ryukou, lead1`）。**ここに無い人がボタンを押しても、パイプラインは何もせずに止まります**

5. **ランナーを登録して起動する**:
   - リポジトリの「設定 → Actions → ランナー → 新しいランナーを作成」でトークンを出し、`/srv/gitea/compose/.env` の
     `RUNNER_TOKEN=` に入れる（このリポジトリだけのランナーになる）
   - `/srv/gitea/runner/config.yaml` は、そのままで使えます（プロキシが要るときだけ、付録の 4. のとおり直す）
   - 起動する:

     ```sh
     cd /srv/gitea/compose
     sudo docker compose up -d runner
     sudo docker compose logs runner        # "Runner registered successfully" と "declare successfully"
     ```

   - 画面の「設定 → Actions → ランナー」に `ops-server` が **アイドル** で出れば OK。登録できたら `.env` の
     `RUNNER_TOKEN` は消してかまいません

   ランナーの設定の大事なところ（`config.yaml`）: ジョブのコンテナには、サーバーの Docker を自動ではつなぎません
   （`docker_host: "-"`）。つないでよいのは `valid_volumes` の 2 つ（Docker のソケットと `/srv/ops`）だけで、それを頼むのは
   デプロイのジョブだけです。**main に push できる人は、パイプラインを書き換えてサーバーの Docker を操作できる**
   ことになります（同じサーバーへデプロイするパイプラインでは避けられない。設計書のとおり、パイロットでは受け入れる）。
   push の権限は信頼できる人だけに。

6. **開発 PC から push する**: Gitea の「設定 → アプリケーション」でアクセストークンを作り（権限 `repository` の
   読み書き）、push のパスワードの代わりに使います（Windows の Git が覚えます）。今の `origin` はそのまま、Gitea を
   2 つ目のリモートにします:

   ```sh
   git remote add gitea http://10.0.0.5:3000/ops/postgres-migration-poc.git
   git push gitea main
   ```

   リポジトリの「Actions」に **checks and deploy** の実行ができ、`backend` と `frontend` が緑になれば OK
   （初回は元になるイメージと pip / npm の取得に時間がかかります）。赤なら、その実行を開いて止まった手順のログを見ます。
   `gate` と `deploy` は push では動かず「スキップ」になります。

7. **Gitea の毎晩のバックアップ**（01:30、`/srv/gitea/data/backups/gitea-YYYYMMDD.zip`、14 日分）:

   ```sh
   sudo cp /srv/ops/bootstrap/deploy/gitea/gitea-dump.cron /etc/cron.d/gitea-dump
   ```

   次の日に `ls /srv/gitea/data/backups/` に zip があれば OK（記録は `/var/log/gitea-dump.log`）。NAS への同期は
   `/srv/ops/backups/` と同じように。Gitea を失っても、コードの履歴は開発 PC にすべてあります（失うのは
   アカウント・`DEPLOY_USERS`・ランナーの登録・実行の記録）。

## 1. 最初の起動（1 回だけ）

0b. のあとで。アプリの設定を作ります（0b. で送った見本から）:

```sh
sudo mkdir -p /srv/ops/releases /srv/ops/certs
sudo cp /srv/ops/bootstrap/deploy/.env.example /srv/ops/.env
sudo vi /srv/ops/.env     # POSTGRES_PASSWORD を長い英数字に（プロキシの行は空のまま。要るときだけ付録の 5.）
sudo chmod 600 /srv/ops/.env
```

（会社の証明書を使うときは、0. の 5. のとおり `/srv/ops/certs/` にも置きます。）

Gitea のリポジトリの「Actions → checks and deploy → **Run workflow**」を、枝 **main** で押します（押す人は
`DEPLOY_USERS` に入っていること）。`gate`・`backend`・`frontend` が緑になると `deploy` が動き、このコミットを
`/srv/ops/releases/ops-<日付>-<コミット>/` に展開して、その版の `deploy/install.sh` を動かします。`deploy` の
ログの最後に `docker compose ps` が出ます。db / app / worker / web / backup が running（app は healthy）になれば OK。
`/srv/ops/deploys.log` に `ok` の行が 1 つできます。0b. で使った `/srv/ops/bootstrap` は消してかまいません。

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

1. 開発 PC で main を Gitea へ push する（`git push gitea main`）。自動のチェックが走り、Actions に緑か赤の印がつく
2. 入れてよいと決めたら、`DEPLOY_USERS` の人が「Actions → checks and deploy → **Run workflow**」を枝 **main** で押す。
   チェックをもう一度すべてとおし、緑のときだけ `deploy` が動く（**赤なら何も入らない**）。押した時点の main の
   先頭が入る
3. `deploy` が緑になり、`/srv/ops/deploys.log` の最後の行が `ok` なら終わり

`install.sh` は、動いているなら入れ替える前に**バックアップを 1 つ取り**、新しい版を作って起動し、`/srv/ops/current` を
新しい版に向けます。`app` は起動するときにマイグレーションを流します。失敗したら `app` は起動せず
（`install.sh` もエラーで止まり、`current` は前の版のまま、`deploys.log` は `failed`）、`docker compose logs app` に
理由が出ます。同じコミットをもう一度入れると、版のフォルダーはそのまま使い回します。デプロイは同時に 1 つだけです
（2 つ目は「ほかのデプロイが動いている」で止まる）。並行稼働の 14 日間（GO-LIVE.md の I.）は、なるべく押しません。

- **前の版に戻す**: `sudo sh /srv/ops/releases/<前の版>/deploy/install.sh`。ただし新しい版でマイグレーションが
  流れていたら、前の版はデータベースの形が合わず動かないことがあります。そのときは開発者に連絡するか、
  更新の前のバックアップから戻します（4.）。
  戻すのはボタンではなく、このコマンドです（ボタンは main の先頭しか入れません）。
- 古い版のフォルダーは、`current` が指していなければ消してかまいません（`sudo rm -r /srv/ops/releases/<版>`）。

## 2b. Gitea が使えないとき（予備: zip を USB で）

開発 PC で zip を作ります（コミット済みの内容だけが入ります。`.env` は入りません）:

```sh
python scripts/make_package.py      # → packages/ops-20261120-abcdef1.zip
```

USB でサーバーへ持っていき:

```sh
sudo unzip ops-20261120-abcdef1.zip -d /srv/ops/releases
sudo sh /srv/ops/releases/ops-20261120-abcdef1/deploy/install.sh
```

パイプラインと同じ置き場所・同じ名前・同じ `install.sh` なので、あとでパイプラインに戻ってもそのまま続けられます
（`deploys.log` には残りません）。

## 3. ログ

```sh
cd /srv/ops/current/deploy
sudo docker compose logs -f app       # API（/srv/ops/logs/app.log にも残る。10 MB × 5 で回る）
sudo docker compose logs -f worker    # 自動リリース・予定の生成・締切の監視・通知の配信
sudo docker compose logs -f backup    # バックアップ
sudo docker compose logs -f web       # アクセス
```

### 通知のメール（SMTP）

worker がメールを送ります。`/srv/ops/.env` の `SMTP_HOST`・`SMTP_PORT`（既定 587。社内の中継なら 25 が多い）・
`SMTP_USER` / `SMTP_PASSWORD`（ログインが要るときだけ）・`SMTP_FROM_ADDRESS`（差出人）・`SMTP_STARTTLS`
（空 = サーバーが対応していれば使う、1 = 必ず、0 = 使わない）を入れ、`sudo docker compose up -d worker` で読み直します。
本文に画面へのリンクを入れるなら `APP_BASE_URL`（例: `http://10.0.0.5`）も。

確かめ方: `sudo docker compose logs worker | grep "Notification channels"` に、使う送り先が 1 行出ます
（パスワードは出ません）。`email: none` なら `SMTP_HOST` が空で、メールは送られず、配信は約 80 分の再試行の後に
「失敗」になります。届かない理由は、配信の記録（`notification_delivery.last_error`）に相手のサーバーの返事のまま残ります。
失敗した通知を画面で見られるのは次の段階（日次ダッシュボード）からです。

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
- インターネットに出るのは、イメージを取る・作るとき（apt / pip / npm）と、パイプラインのジョブのパッケージの取得だけです。
  動いているコンテナ（db / app / worker / web / backup）はインターネットに出ません（メールは社内の中継へ）。
- API の説明（OpenAPI）は `http://<サーバー>/api/openapi.json` で見られます（`/api/docs` の画面は使えません）。
- 開発 PC の Windows のタスク スケジューラー（PlanExtendJob / OrderReleaseJob）は開発用のデータベース向けです。
  サーバーでは `worker` が同じことをするので、サーバーには登録しません。

## 付録. プロキシが必要なとき

最初は会社のプロキシを通る前提でしたが、たぶん要らないと分かりました（2026-11-17）。0. の 1. の確かめで
インターネットに直接出られなかったときだけ、ここを行います。仕組み（Compose・Dockerfile・ランナーの設定）は、
プロキシの値が空なら何もしないので、そのまま残しています。以下の `http://proxy.example.co.jp:8080` は会社のプロキシに
読み替えてください。

1. **OS のパッケージ取得**（`/etc/apt/apt.conf.d/95proxy`）:

   ```
   Acquire::http::Proxy "http://proxy.example.co.jp:8080/";
   Acquire::https::Proxy "http://proxy.example.co.jp:8080/";
   ```

   0. の 2. の Docker の署名の鍵は `curl` で取り、`curl` は apt の設定を使わないので、`-x http://proxy.example.co.jp:8080`
   を付けます。
2. **プロキシが HTTPS を検査している（会社の証明書で署名し直す）ときだけ**: 会社のルート証明書（PEM、拡張子 `.crt`）を
   - OS に入れる（apt・curl・`docker pull` 用。0. の 2. より前に）:
     `sudo cp company.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates`
     （Docker を入れた後なら `&& sudo systemctl restart docker` も）
   - `/srv/ops/certs/` に置く（イメージを作るときの apt / pip / npm 用。`deploy/install.sh` が使う。deploy/certs/README.md）
3. **`docker pull`**（元になるイメージの取得。Docker Engine 23 以降）。`/etc/docker/daemon.json`:

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
4. **パイプラインのジョブ**（0b. の 5.）: `/srv/gitea/runner/config.yaml` の `envs` のプロキシの行の `#` を外し、
   プロキシのアドレスと `NO_PROXY` のサーバーのアドレス（`10.0.0.5`）を直す。HTTPS を検査するときは、会社の証明書の行
   （`PIP_CERT` / `NODE_EXTRA_CA_CERTS` と `container.options`・`valid_volumes` の `/srv/ops/certs`）の `#` も外す。
   `sudo docker compose restart runner`（`/srv/gitea/compose` で）
5. **イメージを作るとき**（apt / pip / npm）: `/srv/ops/.env` の `HTTP_PROXY` / `HTTPS_PROXY` に同じプロキシを書く
   （`NO_PROXY` はそのまま）。動いているコンテナには渡りません
6. **開発 PC から**（シードデータの道具・`scp`）: 開発 PC がプロキシを通っているなら、`NO_PROXY` にサーバーのアドレスを
   入れて、サーバーへは直接つなぐ（GO-LIVE.md の C-0）
