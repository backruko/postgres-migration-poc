# サーバー手順書（構築・再起動・更新・障害からの復旧）

社内の Ubuntu サーバー 1 台で、業務のアプリ（画面・API・定期処理・データベース）と、新しい版を入れるための
Gitea（社内の Git サーバー）を Docker で動かしています。この手順書は、**この仕組みを知らない人が、コマンドを
写して貼るだけで**、サーバーを確かめ、作り直し、元に戻せるように書いています。

本番を使い始めるときの業務の準備（顧客・職員・並行稼働・切り替え）は [GO-LIVE.md](GO-LIVE.md) です。

---

## 0. 最初に読む

### 0.1 場面から探す

| いまの状況 | 行うこと |
|---|---|
| サーバーが再起動した・停電から戻った・「動いているか」を確かめたい | [2. 再起動のあとの確認](#2-再起動のあとの確認) |
| 新しいサーバーを一から立てる（初めて、またはサーバーが壊れて作り直す） | [3. サーバーを一から立てる](#3-サーバーを一から立てる) |
| 開発者から「新しい版を入れて」と言われた | [4. 新しい版を入れる](#4-新しい版を入れる) |
| 新しい版を入れたら動かなくなった | [5. 前の版に戻す](#5-前の版に戻す) |
| データが壊れた・消えた（バックアップの時点に戻したい） | [6.3 データベースをバックアップから戻す](#63-データベースをバックアップから戻す) |
| 画面やコマンドにエラーが出た | [8. トラブルシューティング](#8-トラブルシューティング)（エラーの文で探す） |
| ログを見たい・メールの設定を変えたい | [7. ログと設定](#7-ログと設定) |

### 0.2 読み方の決まり

- 灰色の枠（コードのブロック）は、**枠ごと写して、そのまま貼って Enter** します。書き換える場所はありません。
  人によって違う値（名前・メールアドレスなど）は、ブロックの最後の行が画面でたずねます。
- **1 つのブロックが終わってから次へ**。各ブロックの後の「**確認**」が、書いてあるとおりに出ることを必ず見ます。
  違ったら先へ進まず、そこに書いてある [8. トラブルシューティング](#8-トラブルシューティング) の項目へ行きます。
- 「**サーバーで**」は Ubuntu サーバーのターミナル（SSH）、「**PC で**」は開発 PC（Windows）の **Git Bash**
  （Git for Windows に付いてくるターミナル）で行います。PowerShell やコマンド プロンプトでは動きません。
- サーバーのコマンドには `sudo` が付いています。最初の 1 回だけ、自分のパスワードをたずねます（0.3）。
- 何かを消すコマンドの前には ⚠ を付けています。⚠ の説明を読んでから実行します。

### 0.3 ターミナルを開いたら、毎回最初に（SERVER_IP を決める）

サーバーの IP アドレスは、この手順書には書いていません（リポジトリは GitHub にもあるため）。代わりに、ターミナルを
開くたびに最初に下のブロックを貼り、`SERVER_IP` という変数に入れます。以後のコマンドは `$SERVER_IP` を使うので、
書き換えずに貼れます。**ターミナルを閉じると消えるので、開くたびに行います。**

**サーバーで**（2 つのブロック。1 つ目は sudo のパスワードをたずねます）:

```sh
sudo -v
```

```sh
if sudo test -f /srv/gitea/compose/.env; then export SERVER_IP=$(sudo sed -n 's/^SERVER_ADDRESS=//p' /srv/gitea/compose/.env); else export SERVER_IP=$(hostname -I | awk '{print $1}'); fi
echo "SERVER_IP=$SERVER_IP"
```

**確認**: `SERVER_IP=` の後に、社内の PC からこのサーバーを開くときの IP アドレスが出る（例の形 `10.x.x.x`）。
- 何も出ない・違うアドレスが出る（ネットワークの口が複数ある）とき: `ip -4 -br addr` で一覧を出し、社内から届く
  アドレスを見つけて、`export SERVER_IP=` の後にそのアドレスを続けて打って Enter します（この 1 回だけ手で打つ）。

**PC で**（Git Bash。リポジトリのフォルダーは `C:\p\postgres-migration-poc` = `/c/p/postgres-migration-poc` とします。
別の場所に置いたときは 1 行目のパスだけ変えます）:

```sh
cd /c/p/postgres-migration-poc
export SERVER_IP=$(git remote get-url gitea 2>/dev/null | sed -E 's#^https?://([^:/]+).*#\1#')
echo "SERVER_IP=$SERVER_IP"
```

**確認**: サーバーで出たものと同じアドレスが出る。
- 何も出ない（この PC に Gitea のリモートがまだ無い。初めての構築）とき: `export SERVER_IP=` の後に、サーバーで
  出たアドレスを続けて打って Enter します。

---

## 1. 全体の形

### 1.1 動いているもの

すべて Docker のコンテナです。サーバーには Docker のほかに何も入れません。

| 名前 | 役割 | 止まると |
|---|---|---|
| `db` | PostgreSQL 16。業務のデータ | すべて使えない |
| `app` | API。起動のたびにデータベースを最新の形にしてから動く | 画面にログインできない |
| `worker` | 定期処理（毎分: 自動リリース・締切の監視・通知のメール。毎日 00:05: 予定の生成） | 画面に赤い帯「ワーカーが止まっている」 |
| `web` | 画面（`http://<サーバー>/`）。`/api/` を `app` へ渡す | 画面が開かない |
| `backup` | 毎日 01:00 にデータベースをファイルに書き出す（14 日分） | バックアップが増えない |
| `gitea` | 社内の Git サーバー（`http://<サーバー>:3000/`）。新しい版はここから入る | 新しい版を入れられない（動いているアプリには影響なし） |
| `gitea-runner` | Gitea のパイプライン（自動チェックとデプロイ）を動かす | パイプラインが「待機中」のまま |

アプリの 5 つ（db・app・worker・web・backup）は Compose の名前 `ops`、Gitea の 2 つは名前 `gitea` で、別々に動きます。

### 1.2 ファイルの置き場所

| パス | 中身 | 消えたら |
|---|---|---|
| `/srv/ops/.env` | アプリの設定（データベースのパスワード・メールの設定など）。権限 600 | 作り直す（3.7）。メールの設定は控え（6.4）から入れ直す |
| `/srv/ops/current` | 今動いている版のフォルダーへのリンク。**アプリの `docker compose` はすべて `/srv/ops/current/deploy` で実行する** | 版を入れ直す（4.） |
| `/srv/ops/releases/ops-<日付>-<コミット>/` | 版ごとのフォルダー（入れるたびに新しく作る。上書きしない） | 版を入れ直す |
| `/srv/ops/backups/ops_YYYYMMDD_HHMMSS.dump` | 毎日のデータベースのバックアップ（14 日分）。**NAS へ同期する** | NAS から戻す |
| `/srv/ops/logs/app.log` | API のログ（10 MB × 5 で回る） | 困らない |
| `/srv/ops/deploys.log` | パイプラインのデプロイの記録（1 回 1 行: 日時・押した人・コミット・版・ok / failed） | 困らない |
| `/srv/ops/certs/` | 会社の証明書（会社のプロキシが HTTPS を検査するときだけ。付録） | 置き直す |
| Docker のボリューム `ops_pgdata` | **データベースの本体** | バックアップから戻す（6.3） |
| `/srv/gitea/compose/docker-compose.yml`、`/srv/gitea/compose/.env` | Gitea とランナーの設定（`.env` にサーバーのアドレス） | 作り直す（3.3） |
| `/srv/gitea/data/` | Gitea のデータ（リポジトリ・アカウント・設定） | 作り直して、コードを push し直す（6.5） |
| `/srv/gitea/data/backups/gitea-YYYYMMDD.zip` | Gitea の毎晩のバックアップ（01:30、14 日分）。**NAS へ同期する** | 困らない（6.5） |
| `/srv/gitea/runner/config.yaml` | ランナーの設定 | 作り直す（3.3） |
| `/srv/gitea/runner/.runner` | ランナーの登録情報（Gitea に「このランナーは自分」と名乗るための鍵） | 登録し直す（3.5） |

**コードの元は GitHub と開発 PC にあります。** サーバーが丸ごと壊れても、失って困るのはデータベースだけで、
それは毎日のバックアップ（NAS）から戻せます。

---

## 2. 再起動のあとの確認

サーバーの再起動・停電のあとは、Docker が自動で起動し、コンテナも自動で起動します。**何もしなくても戻る**はずで、
ここではそれを確かめるだけです。毎朝の点検にも使えます。

**サーバーで**（先に [0.3](#03-ターミナルを開いたら毎回最初にserver_ip-を決める)）:

```sh
echo "--- docker";  sudo systemctl is-active docker
echo "--- gitea";   cd /srv/gitea/compose && sudo docker compose ps --format '{{.Service}} {{.State}}'
echo "--- ops";     cd /srv/ops/current/deploy && sudo docker compose ps --format '{{.Service}} {{.State}} {{.Health}}'
echo "--- gitea health";  curl -s --max-time 10 "http://$SERVER_IP:3000/api/healthz" | grep -o '"status": *"[a-z]*"' | head -n 1
echo "--- app health";    curl -s --max-time 10 "http://$SERVER_IP/api/health/app"; echo
echo "--- worker health"; curl -s --max-time 10 "http://$SERVER_IP/api/health/worker"; echo
```

**確認**（行の順番は違ってかまいません）:

```
--- docker
active
--- gitea
gitea running
runner running
--- ops
app running healthy
backup running
db running healthy
web running
worker running
--- gitea health
"status": "pass"
--- app health
{"status":"ok"}
--- worker health
{"state":"ok", …}        ← 起動から 2 分ほどは ok にならないことがある。2 分待ってもう一度
```

違ったとき:

| 出たもの | 行うこと |
|---|---|
| `docker` が `active` でない | `sudo systemctl start docker` → 1 分待ってこのブロックをもう一度 |
| `gitea` / `runner` が無い・`exited` | `cd /srv/gitea/compose && sudo docker compose up -d` → もう一度 |
| `ops` のどれかが無い・`exited`・`restarting` | `cd /srv/ops/current/deploy && sudo docker compose up -d` → 2 分待ってもう一度。それでも → [T-5](#t-5-app-が-healthy-にならない画面が出ない502) |
| `app` が `starting` | 1〜2 分待ってもう一度（起動のたびにデータベースを確かめるため） |
| `app` が `unhealthy`・app health が何も出ない | [T-5](#t-5-app-が-healthy-にならない画面が出ない502) |
| worker health が `stale` / `stopped` / `never`（2 分待っても） | [T-10](#t-10-ワーカーが止まっている画面に赤い帯) |
| `cd: /srv/ops/current/deploy: No such file or directory` | アプリがまだ入っていない → [3.8](#38-最初のデプロイ) |

最後に、ブラウザで Gitea のランナーの画面を開き、`ops-server` が **アイドル** であることを見ます。開く URL は次で出ます:

```sh
echo "http://$SERVER_IP:3000/ops/postgres-migration-poc/settings/actions/runners"
```

**オフライン**なら [T-2](#t-2-ランナーがオフラインジョブが待機中のまま)。

---

## 3. サーバーを一から立てる

初めて立てるときも、サーバーが壊れて別のサーバーに作り直すとき（災害復旧）も、同じ手順です。違うのは
[3.9](#39-データを入れる) だけです（初めてなら管理者を作る、作り直しならバックアップから戻す）。

**作り直しのときに先に用意するもの**:
- NAS にある最新のデータベースのバックアップ `ops_YYYYMMDD_HHMMSS.dump`（`/srv/ops/backups` を同期したもの）
- メールの設定の控え（[6.4](#64-設定の控え)）
- 開発 PC。無ければ、Git for Windows を入れた PC で GitHub からリポジトリを取る:
  `git clone https://github.com/<GitHub のアカウント>/postgres-migration-poc.git /c/p/postgres-migration-poc`

**前提**: Ubuntu Server 24.04、自分のアカウントで `sudo` が使える、社内の PC からサーバーの 80 番（画面）と
3000 番（Gitea）に届く、サーバーから直接インターネットに出られる（3.1 で確かめる）。かかる時間は 1〜2 時間
（最初のパイプラインでイメージを取るのに 20〜30 分）。

### 3.1 OS と Docker（サーバーで）

[0.3](#03-ターミナルを開いたら毎回最初にserver_ip-を決める) の後で。まずインターネットに出られるかを確かめます:

```sh
for url in https://download.docker.com/linux/ubuntu/gpg https://registry-1.docker.io/v2/ https://pypi.org/simple/ https://registry.npmjs.org/; do
  printf '%-50s ' "$url"; curl -s -o /dev/null -w '%{http_code}\n' --max-time 15 "$url" || echo "届かない"
done
```

**確認**: 4 行とも 3 桁の数字が出る（`200`。Docker Hub は `401` でよい = 届いている）。
`000`・`届かない`・証明書のエラーが出たら、会社のプロキシが要ります → [付録](#付録-プロキシが必要なとき) の 1. と 2. を
行ってから、このブロックをもう一度。

Docker を入れます（Docker の公式の apt リポジトリから。Ubuntu の `docker.io` パッケージは使わない）:

```sh
sudo apt-get update
sudo apt-get install -y ca-certificates curl unzip openssl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

```sh
sudo systemctl is-active docker
sudo docker compose version
sudo docker run --rm hello-world | grep -m 1 "Hello from Docker"
```

**確認**: `active`、`Docker Compose version v2.…`、`Hello from Docker!` の 3 行。
最後の行が出ないで `pull` のエラーなら → [T-9](#t-9-インターネットに出られないdocker-pull-が失敗する)。

### 3.2 最初に要るファイルをサーバーへ送る

**PC で**（Git Bash。先に 0.3 の PC の手順）。コミット済みの `deploy` フォルダーを 1 つのファイルにします:

```sh
cd /c/p/postgres-migration-poc
git fetch origin && git status -sb | head -n 1
git -c core.autocrlf=false archive --format=tar -o ops-bootstrap.tar origin/main deploy
ls -l ops-bootstrap.tar
```

**確認**: 2 行目が `## main...origin/main`（`[behind …]` が付いていたら `git pull` してからもう一度）、最後に
`ops-bootstrap.tar` の行が出る。

サーバーへ送ります（最後の行がサーバーのログイン名をたずね、続いて scp がサーバーのパスワードをたずねます）:

```sh
read -r -p "サーバーにログインする名前: " SERVER_USER && scp ops-bootstrap.tar "$SERVER_USER@$SERVER_IP:/tmp/ops-bootstrap.tar"
```

**確認**: `ops-bootstrap.tar  100%` の行が出る。

**サーバーで**、置き場所に展開します:

```sh
sudo mkdir -p /srv/ops/bootstrap
sudo tar -xf /tmp/ops-bootstrap.tar -C /srv/ops/bootstrap
ls -A /srv/ops/bootstrap/deploy/gitea
```

**確認**: `.env.example  docker-compose.yml  gitea-dump.cron  runner-config.example.yaml` が出る。

### 3.3 Gitea を起動する（サーバーで）

```sh
[ -n "$SERVER_IP" ] || echo "!!! SERVER_IP が空です。0.3 を先に行い、このブロックをもう一度"
sudo mkdir -p /srv/gitea/compose /srv/gitea/data/backups /srv/gitea/runner
sudo cp /srv/ops/bootstrap/deploy/gitea/docker-compose.yml /srv/gitea/compose/docker-compose.yml
sudo test -f /srv/gitea/compose/.env || sudo cp /srv/ops/bootstrap/deploy/gitea/.env.example /srv/gitea/compose/.env
[ -n "$SERVER_IP" ] && sudo sed -i "s/^SERVER_ADDRESS=.*/SERVER_ADDRESS=$SERVER_IP/" /srv/gitea/compose/.env
sudo chmod 600 /srv/gitea/compose/.env
sudo test -f /srv/gitea/runner/config.yaml || sudo cp /srv/ops/bootstrap/deploy/gitea/runner-config.example.yaml /srv/gitea/runner/config.yaml
sudo chown -R 1000:1000 /srv/gitea/data
cd /srv/gitea/compose && sudo docker compose up -d gitea
for i in $(seq 1 40); do curl -s --max-time 5 "http://$SERVER_IP:3000/api/healthz" | grep -q '"pass"' && { echo "GITEA OK"; break; }; sleep 3; done
sudo grep -E '^(SERVER_ADDRESS|GITEA_VERSION|ACT_RUNNER_VERSION)=' /srv/gitea/compose/.env
```

**確認**: `GITEA OK`、続いて `SERVER_ADDRESS=` に SERVER_IP と同じアドレス、`GITEA_VERSION=`・`ACT_RUNNER_VERSION=` の行。
`GITEA OK` が出ない（2 分たっても）→ [T-3](#t-3-gitea-が起動しない)。

Gitea の管理者を作ります。2 つのブロックで、それぞれ最後の行が値をたずねます:

```sh
read -r -p "Gitea の管理者のログイン名（半角英数字。例 admin1）: " GITEA_ADMIN
```

```sh
read -r -p "その人のメールアドレス: " GITEA_EMAIL && sudo docker exec -u git gitea gitea admin user create --admin --username "$GITEA_ADMIN" --email "$GITEA_EMAIL" --random-password --must-change-password
```

**確認**: `generated random password is '…'` と `New user '…' has been successfully created!` が出る。
**この仮のパスワードを控えます**（次の 3.4 で最初のログインに使い、すぐ変えます）。
`user already exists` なら、もうある（作り直しで `/srv/gitea/data` が残っている）ので、そのアカウントを使います。

### 3.4 Gitea の画面での準備（ブラウザで）

使う URL を出します（サーバーで）:

```sh
echo "1 ログイン:            http://$SERVER_IP:3000/user/login"
echo "2 組織を作る:          http://$SERVER_IP:3000/org/create"
echo "3 リポジトリを作る:    http://$SERVER_IP:3000/repo/create"
echo "4 共同作業者:          http://$SERVER_IP:3000/ops/postgres-migration-poc/settings/collaboration"
echo "5 Actions の変数:      http://$SERVER_IP:3000/ops/postgres-migration-poc/settings/actions/variables"
echo "6 Actions の実行:      http://$SERVER_IP:3000/ops/postgres-migration-poc/actions"
```

社内の PC のブラウザで、上から順に開きます:

1. **ログイン**: 3.3 の管理者の名前と仮のパスワード。新しいパスワードを決めて変える。
   - ほかに push する人・デプロイする人がいれば、右上の自分のアイコン → **サイト管理** → **ユーザーアカウント** →
     **ユーザーアカウントを作成** で作る（自分で登録する画面はありません）。
2. **組織**: 組織名に `ops`（この綴りのまま）、公開範囲は **プライベート** → 作成。
3. **リポジトリ**: オーナー `ops`、リポジトリ名 `postgres-migration-poc`（この綴りのまま）、**プライベート** にチェック。
   README・.gitignore・ライセンスは**何も選ばない**（空のまま）→ 作成。
4. **共同作業者**: push する人・デプロイする人を足し、権限を **書き込み** にする（「Run workflow」のボタンには書き込みの
   権限が要る）。管理者 1 人で運用するなら、この手順は飛ばしてよい。
5. **Actions の変数**: **変数を追加** → 名前 `DEPLOY_USERS`、値に「デプロイしてよい人のログイン名」をカンマで区切って
   書く（例 `admin1,lead1`）→ 追加。**ここに無い人がデプロイを押しても、パイプラインは何もせずに止まります。**
6. **Actions の実行**: 開ける（「ワークフローはまだありません」でよい）。404 なら、リポジトリの **設定** →
   **高度な設定** の **Actions** にチェックを入れて **設定を更新**。

### 3.5 ランナーを登録する（サーバーで）

ランナーは、Gitea のパイプラインを動かす係です。このリポジトリ専用に登録します。**ランナーを作り直すとき
（[T-1](#t-1-unregistered-runnerランナーが-gitea-に知られていない)）も、このブロックそのままです。**

```sh
cd /srv/gitea/compose
sudo docker compose stop runner 2>/dev/null
sudo test -f /srv/gitea/runner/.runner && sudo mv /srv/gitea/runner/.runner "/srv/gitea/runner/.runner.old-$(date +%Y%m%d%H%M%S)"
TOKEN=$(sudo docker exec -u git gitea gitea actions generate-runner-token --scope ops/postgres-migration-poc 2>/dev/null | tail -n 1)
echo "token: ${#TOKEN} 文字"
[ -n "$TOKEN" ] && sudo sed -i "s/^RUNNER_TOKEN=.*/RUNNER_TOKEN=$TOKEN/" /srv/gitea/compose/.env
sudo docker compose up -d runner
sleep 15
sudo docker compose logs --tail 30 runner | grep -E "registered successfully|declare successfully|error"
```

**確認**: `token: 40 文字` くらいの数、`Runner registered successfully`、`declare successfully` が出る。
- `token: 0 文字` のとき（コマンドでトークンを作れなかった）: ブラウザで
  `http://<SERVER_IP>:3000/ops/postgres-migration-poc/settings/actions/runners` を開き、**新しいランナーを作成** で
  出るトークンをコピーして、サーバーで次を貼り、たずねられたらトークンを貼る → その後、上のブロックの
  `sudo docker compose up -d runner` から下を貼る:

  ```sh
  read -r -p "ランナーのトークン: " TOKEN && sudo sed -i "s/^RUNNER_TOKEN=.*/RUNNER_TOKEN=$TOKEN/" /srv/gitea/compose/.env
  ```

- `unregistered runner` が出る → [T-1](#t-1-unregistered-runnerランナーが-gitea-に知られていない)

登録できたらトークンは要りません。消しておきます:

```sh
sudo sed -i 's/^RUNNER_TOKEN=.*/RUNNER_TOKEN=/' /srv/gitea/compose/.env
echo "http://$SERVER_IP:3000/ops/postgres-migration-poc/settings/actions/runners"
```

ブラウザで出た URL を開き、`ops-server` が **アイドル** で 1 つあることを確かめます。同じ名前で **オフライン** の
行（前の登録）があれば、その行の **編集** → **削除**（ID の番号で見分ける。アイドルの方は消さない）。

### 3.6 コードを Gitea へ送る（PC で）

Gitea の画面で、push に使うトークンを作ります: 右上の自分のアイコン → **設定** → **アプリケーション** →
**新しいトークンを生成**。トークン名は `push`、権限の **repository** を **読み取りと書き込み** にして生成。
出たトークンをコピーしておきます（この画面を閉じると二度と出ません）。

Git Bash で（先に 0.3 の PC の手順）:

```sh
cd /c/p/postgres-migration-poc
git checkout main && git pull origin main
git remote remove gitea 2>/dev/null; git remote add gitea "http://$SERVER_IP:3000/ops/postgres-migration-poc.git"
printf 'protocol=http\nhost=%s:3000\n\n' "$SERVER_IP" | git credential reject
git remote -v | grep gitea
```

**確認**: `gitea  http://<SERVER_IP>:3000/ops/postgres-migration-poc.git (fetch)` と `(push)` の 2 行。

送ります（ログインの窓が出たら、ユーザー名に **Gitea のログイン名**、パスワードに **さっきのトークン**）:

```sh
git push gitea main
```

**確認**: 最後に `* [new branch]      main -> main`。失敗したら → [T-14](#t-14-pc-から-push-できない)。

ブラウザで 3.4 の「6 Actions の実行」の URL を開くと、**checks and deploy** の実行ができています。
`backend` と `frontend` が **緑** になるまで待ちます（初回は 20〜30 分）。`gate` と `deploy` は「スキップ」で正しい
（push ではデプロイしない）。**赤**なら → [T-8](#t-8-backend--frontend-が赤)。

### 3.7 アプリの設定を作る（サーバーで）

```sh
[ -n "$SERVER_IP" ] || echo "!!! SERVER_IP が空です。0.3 を先に行い、このブロックをもう一度"
sudo mkdir -p /srv/ops/releases /srv/ops/certs /srv/ops/backups /srv/ops/logs
if sudo test -f /srv/ops/.env; then
  echo "/srv/ops/.env は既にあります。作り直しません（パスワードを変えると、データベースに入れなくなる: T-6）"
else
  sudo cp /srv/ops/bootstrap/deploy/.env.example /srv/ops/.env
  sudo sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" /srv/ops/.env
  [ -n "$SERVER_IP" ] && sudo sed -i "s|^APP_BASE_URL=.*|APP_BASE_URL=http://$SERVER_IP|" /srv/ops/.env
  echo "/srv/ops/.env を作りました"
fi
sudo chmod 600 /srv/ops/.env
sudo grep -E '^(POSTGRES_DB|POSTGRES_USER|HTTP_PORT|BACKUP_DIR|LOG_DIR|APP_BASE_URL)=' /srv/ops/.env
sudo grep -c '^POSTGRES_PASSWORD=change-me' /srv/ops/.env
```

**確認**: `POSTGRES_DB=ops`、`POSTGRES_USER=ops`、`HTTP_PORT=80`、`BACKUP_DIR=/srv/ops/backups`、
`LOG_DIR=/srv/ops/logs`、`APP_BASE_URL=http://<SERVER_IP>`、最後の行が `0`（パスワードが見本のままでない）。

通知のメールを送るなら、ここで [7.2](#72-通知のメールsmtp) も行います（後からでもよい）。

### 3.8 最初のデプロイ

ブラウザで 3.4 の「6 Actions の実行」の URL を開き、左の一覧で **checks and deploy**（`ci.yml`）を選ぶ →
右の **Run workflow** → 枝 **main** のまま → **Run workflow**。押す人は `DEPLOY_USERS` に入っていること。
ボタンが無いとき → [T-16](#t-16-run-workflow-のボタンが無い)。

`gate`・`backend`・`frontend` が緑になると `deploy` が動きます（10〜20 分）。`deploy` が **緑** になったら、**サーバーで**:

```sh
sudo tail -n 1 /srv/ops/deploys.log
readlink /srv/ops/current
cd /srv/ops/current/deploy && sudo docker compose ps --format '{{.Service}} {{.State}} {{.Health}}'
curl -s --max-time 10 "http://$SERVER_IP/api/health/app"; echo
```

**確認**: 1 行目の終わりの方に `ok`、2 行目が `/srv/ops/releases/ops-…`、`app running healthy`・`db running healthy`・
`backup`・`web`・`worker` が `running`、最後が `{"status":"ok"}`。
`deploy` が **赤**・`failed` → [T-7](#t-7-デプロイdeploy--gateが赤)。

### 3.9 データを入れる

**初めて立てたとき**（データが空）: 最初のシステム管理者を作ります（パスワードを 2 回たずねます。最後の行なので
そのまま貼ってよい）:

```sh
cd /srv/ops/current/deploy && sudo docker compose exec app python scripts/create_staff.py admin "システム管理者" --roles system_admin admin
```

**確認**: ブラウザで `http://<SERVER_IP>/` を開き、ログイン名 `admin` と今のパスワードでログインできる。
続きは [GO-LIVE.md](GO-LIVE.md)（使い始めのチェックリスト）。

**作り直し（災害復旧）のとき**: 管理者は作らず、NAS のバックアップから戻します。

1. PC から NAS のバックアップの `.dump` ファイルを 1 つ、サーバーの `/tmp/` へ送る（Git Bash で、`.dump` のある
   フォルダーで。最後の行がファイル名とログイン名をたずねる）:

   ```sh
   read -r -p "送る .dump のファイル名（例 ops_20261118_010000.dump）: " DUMPFILE && read -r -p "サーバーにログインする名前: " SERVER_USER && scp "$DUMPFILE" "$SERVER_USER@$SERVER_IP:/tmp/$DUMPFILE"
   ```

2. サーバーで、バックアップの置き場所へ移す:

   ```sh
   sudo mv /tmp/ops_*.dump /srv/ops/backups/ && sudo ls -l /srv/ops/backups/
   ```

3. [6.3](#63-データベースをバックアップから戻す) の手順で戻す（「今の状態も残す」の手順は飛ばしてよい。中身は空なので）。

**確認**: 6.3 の確認のとおり。ブラウザで `http://<SERVER_IP>/` を開き、**前のサーバーのログイン名とパスワード**で
ログインでき、データが見える。

### 3.10 Gitea の毎晩のバックアップを入れる（サーバーで）

```sh
sudo cp /srv/ops/bootstrap/deploy/gitea/gitea-dump.cron /etc/cron.d/gitea-dump
sudo chmod 644 /etc/cron.d/gitea-dump
sudo docker exec -u git -w /data/backups gitea sh -c 'gitea dump -c /data/gitea/conf/app.ini --type zip --file "gitea-$(date +%Y%m%d).zip"' > /dev/null 2>&1
sudo ls -l /srv/gitea/data/backups/
```

**確認**: `gitea-YYYYMMDD.zip`（今日の日付）が 1 つある。以後は毎晩 01:30 に自動でできます（記録は
`/var/log/gitea-dump.log`）。

### 3.11 NAS への同期

`/srv/ops/backups/`（データベース。**必須**）と `/srv/gitea/data/backups/`（Gitea）を、社内の NAS へ毎日同期します。
同期のやり方は社内の NAS の手順に従います（この手順書では決めていません）。書き込み中のファイルは名前の最後が
`.partial` なので、同期の対象から外します。

> ⚠ 未決: NAS への同期の具体的な手順（コマンド・時刻・保管期間）は、運用の担当が決めてここに書き足します。
> これが無いと、サーバーが壊れたときにデータベースを戻せません。

### 3.12 仕上げ

[2. 再起動のあとの確認](#2-再起動のあとの確認) を行い、すべて確認どおりなら完了です。最初に送ったファイルを片づけます:

```sh
sudo rm -rf /srv/ops/bootstrap /tmp/ops-bootstrap.tar
sudo ls -A /srv/gitea/runner/
```

`/srv/gitea/runner/` に `.runner.old-…` が残っていれば消してかまいません（`sudo rm /srv/gitea/runner/.runner.old-*`）。
PC の `C:\p\postgres-migration-poc\ops-bootstrap.tar` も消してかまいません（git には入りません）。

---

## 4. 新しい版を入れる

1. **PC で**（Git Bash。先に 0.3）、開発者が main に入れた変更を Gitea へ送る:

   ```sh
   cd /c/p/postgres-migration-poc
   git checkout main && git pull origin main
   git push gitea main
   ```

   Actions の画面で `backend` と `frontend` が **緑** になるのを待つ。**赤**なら入れない（[T-8](#t-8-backend--frontend-が赤)）。

2. **ブラウザで**、`DEPLOY_USERS` に入っている人が **Actions → checks and deploy → Run workflow**（枝 **main**）。
   チェックをもう一度すべて通し、緑のときだけ `deploy` が動きます（**赤なら何も変わらない**。今の版のまま動き続ける）。

3. `deploy` が **緑** になったら、**サーバーで** [3.8](#38-最初のデプロイ) の確認のブロックを貼り、同じ確認をする。

`deploy` がしていること（知っておくと、失敗したときに慌てない）:
- 入れ替える**前に**、今のデータベースのバックアップを 1 つ取る（`/srv/ops/backups/` に、その時刻のファイル）
- 新しい版を `/srv/ops/releases/ops-<日付>-<コミット>/` に作り、起動する（`app` がデータベースを最新の形にする）
- **うまく起動したときだけ** `/srv/ops/current` を新しい版に向ける。失敗したら `current` は前の版のまま、
  `deploys.log` は `failed`
- デプロイは同時に 1 つだけ（2 つ目は「ほかのデプロイが動いている」で止まる）

並行稼働の 14 日間（GO-LIVE.md の I.）は、なるべく入れません。

---

## 5. 前の版に戻す

新しい版で画面がおかしい・動かないときに、1 つ前の版に戻します。**サーバーで**:

```sh
ls -1 /srv/ops/releases/
echo "今の版: $(readlink /srv/ops/current)"
sudo tail -n 5 /srv/ops/deploys.log
```

一覧から、戻したい版（ふつうは今の版の 1 つ前。`deploys.log` の前の行の `ops-…`）を選び、次の最後の行で名前を貼ります:

```sh
read -r -p "戻す版の名前（例 ops-20261117-abc1234）: " PREV && sudo sh "/srv/ops/releases/$PREV/deploy/install.sh"
```

**確認**: 最後に `install: done.` と `docker compose ps` の表が出る。2 分後に [2.](#2-再起動のあとの確認) の確認で
`app running healthy`。

**`app` が healthy にならず、ログに `Can't locate revision` が出るとき**（新しい版がデータベースの形を変えていて、
前の版ではその形を読めない）:

```sh
cd /srv/ops/current/deploy && sudo docker compose logs --tail 50 app | grep -m 1 "Can't locate revision"
```

この行が出たら、**新しい版を入れる直前のバックアップ**に戻してから、前の版をもう一度入れます。
⚠ 新しい版を入れた後に入力したデータは失われます。

1. 新しい版を入れた時刻と、その直前のバックアップを見る:

   ```sh
   sudo tail -n 3 /srv/ops/deploys.log
   sudo ls -lt /srv/ops/backups/ | head -n 6
   ```

   `deploys.log` の新しい版の行の時刻より**前で、いちばん新しい** `.dump` が、入れる直前のバックアップです。
2. [6.3](#63-データベースをバックアップから戻す) の 1.〜4. を、そのファイルで行う（5. の「動かす」は行わない）。
3. 前の版をもう一度入れる（上の `read -r -p "戻す版の名前…` のブロックをもう一度）。

---

## 6. バックアップと復元

### 6.1 自動のバックアップ

| もの | いつ | どこに | 何日分 |
|---|---|---|---|
| データベース | 毎日 01:00 と、版を入れる直前 | `/srv/ops/backups/ops_YYYYMMDD_HHMMSS.dump` | 14 日（`/srv/ops/.env` の `BACKUP_KEEP_DAYS`） |
| Gitea | 毎日 01:30 | `/srv/gitea/data/backups/gitea-YYYYMMDD.zip` | 14 日 |

昨日のバックアップがあるかを見る:

```sh
sudo ls -lt /srv/ops/backups/ | head -n 4
cd /srv/ops/current/deploy && sudo docker compose logs --tail 5 backup
```

**確認**: 今日か昨日の日付の `.dump` があり、ログに `backup: done (…)`。無い・`FAILED` → [T-11](#t-11-バックアップが無いfailed)。

### 6.2 今すぐバックアップを取る

```sh
cd /srv/ops/current/deploy && sudo docker compose exec backup sh /backup.sh once
```

**確認**: `backup: writing /backups/ops_….dump` と `backup: done (…)` が出る。

### 6.3 データベースをバックアップから戻す

⚠ データベースを**バックアップの時点に戻します**。それ以降に入力したデータは失われます。

1. 戻すファイルを決める。一覧を出して:

   ```sh
   sudo ls -lt /srv/ops/backups/*.dump | head -n 10
   ```

   使うファイルのパス（`/srv/ops/backups/ops_….dump`）を、次の最後の行で貼る:

   ```sh
   read -r -p "戻すファイルのパス: " DUMP && sudo test -s "$DUMP" && echo "OK: $DUMP" || echo "!!! ファイルが無い・空: もう一度"
   ```

   **確認**: `OK: /srv/ops/backups/ops_….dump`。

2. 今の状態も残す（戻した後で「やはり戻す前がよかった」となったときのため。作り直しのときは飛ばす）:

   ```sh
   cd /srv/ops/current/deploy && sudo docker compose exec backup sh /backup.sh once
   ```

3. API と定期処理を止める:

   ```sh
   cd /srv/ops/current/deploy && sudo docker compose stop app worker
   ```

4. データベースを作り直して、バックアップを入れる:

   ```sh
   cd /srv/ops/current/deploy
   sudo docker compose exec -T db dropdb --force -U ops ops
   sudo docker compose exec -T db createdb -U ops ops
   sudo cat "$DUMP" | sudo docker compose exec -T db pg_restore --no-owner -U ops -d ops
   echo "pg_restore の終了コード: $?"
   ```

   **確認**: `pg_restore の終了コード: 0`。ほかに何も出ない（エラーの行が無い）。
   エラーの行が出た・`0` でない → [T-15](#t-15-復元pg_restoreでエラーが出る)。

5. 動かして、中身を確かめる:

   ```sh
   cd /srv/ops/current/deploy && sudo docker compose start app worker
   sleep 30
   sudo docker compose ps --format '{{.Service}} {{.State}} {{.Health}}'
   sudo docker compose exec -T db psql -X -A -t -U ops -d ops < /srv/ops/current/deploy/restore-check.sql
   ```

   **確認**: `app running healthy`（まだ `starting` なら 1 分待って最後の 2 行をもう一度）、続いて
   `alembic …` と、`staff_user`・`client_master` などの件数（0 でない数）が出る。ブラウザでログインでき、データが見える。

### 6.4 設定の控え

サーバーが壊れたとき、バックアップから戻せないのは**設定の値**だけです。次の値を、社内の決められた保管場所
（パスワード管理の仕組みなど）に控えておきます。値を変えたら控えも直します。

```sh
sudo grep -E '^(SMTP_|APP_BASE_URL|HTTP_PORT|BACKUP_)' /srv/ops/.env
```

- データベースのパスワード（`POSTGRES_PASSWORD`）は**控えなくてよい**: 作り直しでは新しいパスワードで新しい
  データベースを作り、そこへバックアップを入れるため（3.7・3.9）。
- Gitea の設定（`/srv/gitea/compose/.env`）は控えなくてよい: サーバーのアドレスと版だけで、3.3 で作り直せる。

### 6.5 Gitea が壊れたとき

Gitea にはコードの写しと、アカウント・`DEPLOY_USERS`・ランナーの登録・パイプラインの実行の記録があります。
**コードの元は GitHub と開発 PC にあるので、Gitea は作り直します**（毎晩の zip から戻す手順は、この手順書では
扱いません。zip は調べものの控えです）。

⚠ 次は Gitea のデータを消します。動いているアプリ（画面・データベース）には影響しません。

```sh
cd /srv/gitea/compose && sudo docker compose down
sudo mv /srv/gitea/data "/srv/gitea/data.broken-$(date +%Y%m%d%H%M%S)"
sudo test -f /srv/gitea/runner/.runner && sudo mv /srv/gitea/runner/.runner "/srv/gitea/runner/.runner.old-$(date +%Y%m%d%H%M%S)"
sudo mkdir -p /srv/gitea/data/backups && sudo chown -R 1000:1000 /srv/gitea/data
echo "次: 3.3 の最初のブロックから"
```

このあと [3.3](#33-gitea-を起動するサーバーで)（管理者を作る）→ [3.4](#34-gitea-の画面での準備ブラウザで) →
[3.5](#35-ランナーを登録するサーバーで) → [3.6](#36-コードを-gitea-へ送るpc-で) → [3.10](#310-gitea-の毎晩のバックアップを入れるサーバーで)。
アプリ（3.7〜3.9）は行いません（動いたまま）。すべて緑になったら、`/srv/gitea/data.broken-…` は消してかまいません。

---

## 7. ログと設定

### 7.1 ログを見る

```sh
cd /srv/ops/current/deploy && sudo docker compose logs --tail 100 app
```

`app` の代わりに `worker`（定期処理・メール）・`backup`・`web`（アクセス）・`db`。流れ続けるログを見るときは
`--tail 100` を `-f` に（`Ctrl+C` で止める）。API のログはファイルにも残ります: `sudo tail -n 100 /srv/ops/logs/app.log`。

Gitea とランナー:

```sh
cd /srv/gitea/compose && sudo docker compose logs --tail 100 runner
```

開発者にログを渡すとき（ファイルにして送る）:

```sh
cd /srv/ops/current/deploy && sudo docker compose logs --no-color --since 24h > "/tmp/ops-logs-$(date +%Y%m%d%H%M).txt" && ls -l /tmp/ops-logs-*.txt
```

### 7.2 通知のメール（SMTP）

`worker` がメールを送ります。4 つのブロックで、それぞれ最後の行が値をたずねます（分からない値は空のまま Enter）:

```sh
read -r -p "SMTP_HOST（メールサーバーの名前。例 smtp.example.local）: " V && sudo sed -i "s|^SMTP_HOST=.*|SMTP_HOST=$V|" /srv/ops/.env
```

```sh
read -r -p "SMTP_PORT（社内の中継なら 25 が多い。空なら 587）: " V && sudo sed -i "s|^SMTP_PORT=.*|SMTP_PORT=$V|" /srv/ops/.env
```

```sh
read -r -p "SMTP_FROM_ADDRESS（差出人のアドレス）: " V && sudo sed -i "s|^SMTP_FROM_ADDRESS=.*|SMTP_FROM_ADDRESS=$V|" /srv/ops/.env
```

```sh
read -r -p "SMTP_STARTTLS（空 = 使えれば使う、1 = 必ず、0 = 使わない）: " V && sudo sed -i "s|^SMTP_STARTTLS=.*|SMTP_STARTTLS=$V|" /srv/ops/.env
```

ログインが要るメールサーバーのときだけ、`SMTP_USER` と `SMTP_PASSWORD` も同じ形で入れます（`SMTP_HOST` の行の
`SMTP_HOST` を 2 か所とも置き換えたブロック）。読み直させて確かめる:

```sh
sudo grep -E '^SMTP_' /srv/ops/.env | grep -v PASSWORD
cd /srv/ops/current/deploy && sudo docker compose up -d worker && sleep 10 && sudo docker compose logs worker | grep "Notification channels" | tail -n 1
```

**確認**: `Notification channels: email: …` に、入れたメールサーバーが出る（パスワードは出ない）。`email: none` なら
`SMTP_HOST` が空です。届かない理由は、画面の「出来事・通知 → 通知の記録」に相手のサーバーの返事のまま残ります。
変えたら [6.4](#64-設定の控え) の控えも直します。

### 7.3 よく使う操作

すべて `cd /srv/ops/current/deploy &&` の後に続けます。

| したいこと | コマンド |
|---|---|
| 状態を見る | `sudo docker compose ps` |
| 全部を止める / 動かす | `sudo docker compose stop` / `sudo docker compose up -d` |
| 1 つだけ動かし直す | `sudo docker compose restart worker`（`worker` を `app` などに） |
| アプリの職員のパスワードを変える | `sudo docker compose exec app python scripts/set_password.py <ログイン名>` |
| データベースに入る（調べもの） | `sudo docker compose exec db psql -U ops -d ops`（`\q` で出る） |

⚠ `sudo docker compose down -v` と `sudo docker volume rm ops_pgdata` と `sudo docker system prune --volumes` は
**データベースを消します**。この手順書のどこでも使いません。

---

## 8. トラブルシューティング

エラーの文や症状で探します。各項目の「直し方」を上から順に行い、最後の「確認」で直ったかを見ます。直らなければ、
[7.1](#71-ログを見る) の最後のブロックでログをファイルにして、開発の担当に渡します。

### T-1. 「unregistered runner」（ランナーが Gitea に知られていない）

**症状**: `sudo docker compose logs runner`（`/srv/gitea/compose` で）に
`fail to invoke Declare … Unauthenticated desc = unregistered runner` が繰り返し出る。画面のランナーが **オフライン**。

**原因**: ランナーは `/srv/gitea/runner/.runner` に登録情報を持っていて、起動のたびにそれで名乗る。Gitea 側に
その登録がもう無い（画面でランナーを消した・リポジトリを作り直した・`/srv/gitea/data` を作り直した）。
`.runner` があると、ランナーは登録し直さない（`.env` に新しいトークンを入れても使わない）。

**直し方**: [3.5](#35-ランナーを登録するサーバーで) の最初のブロックをそのまま貼る（古い `.runner` を
`.runner.old-…` に退けて、新しいトークンで登録し直す）。続いて 3.5 の残り（トークンを消す・画面でオフラインの古い行を消す）。

**確認**: ログに `declare successfully`、画面で `ops-server` が **アイドル** で 1 つ。

### T-2. ランナーがオフライン・ジョブが「待機中」のまま

**直し方**:

```sh
cd /srv/gitea/compose && sudo docker compose ps --format '{{.Service}} {{.State}}' && sudo docker compose logs --tail 30 runner
```

- `runner` が無い・`exited` → `sudo docker compose up -d runner` → 30 秒後にもう一度
- ログに `unregistered runner` → [T-1](#t-1-unregistered-runnerランナーが-gitea-に知られていない)
- ログに `connection refused` / `no such host`（Gitea に届かない）→ [2.](#2-再起動のあとの確認) で Gitea を確かめ、
  `sudo grep ^SERVER_ADDRESS= /srv/gitea/compose/.env` が今のサーバーのアドレスか見る。違えば
  `sudo sed -i "s/^SERVER_ADDRESS=.*/SERVER_ADDRESS=$SERVER_IP/" /srv/gitea/compose/.env && cd /srv/gitea/compose && sudo docker compose up -d`
  （サーバーの IP アドレスが変わったとき。PC の `gitea` のリモートも [3.6](#36-コードを-gitea-へ送るpc-で) で付け直す）

**確認**: 画面で **アイドル**。待機中だったジョブが動き出す。

### T-3. Gitea が起動しない

```sh
cd /srv/gitea/compose && sudo docker compose ps -a --format '{{.Service}} {{.State}}' && sudo docker compose logs --tail 40 gitea
```

| ログ・エラー | 直し方 |
|---|---|
| `permission denied`（`/data/...`） | `sudo chown -R 1000:1000 /srv/gitea/data && cd /srv/gitea/compose && sudo docker compose up -d gitea` |
| `SERVER_ADDRESS を .env に` / `GITEA_VERSION を .env に` | `/srv/gitea/compose/.env` が無いか、その行が無い → [3.3](#33-gitea-を起動するサーバーで) の最初のブロック |
| `address already in use`（3000） | [T-4](#t-4-address-already-in-useポートが使われている) |

**確認**: [2.](#2-再起動のあとの確認) で `"status": "pass"`。

### T-4. 「address already in use」（ポートが使われている）

80 番（画面）か 3000 番（Gitea）を、ほかのプログラムが使っている。

```sh
sudo ss -ltnp | grep -E ':(80|3000) '
```

出た行の `users:((" … ",pid=…` がそのプログラム。Ubuntu に最初から入っていた `apache2` や `nginx` なら止めて
自動起動を外す: `sudo systemctl disable --now apache2`（`nginx` なら `apache2` を `nginx` に）。その後、止まっていた
方を起動し直す（[2.](#2-再起動のあとの確認) の表のとおり）。どうしても 80 番を空けられないときは、`/srv/ops/.env` の
`HTTP_PORT` を `8080` などにして `cd /srv/ops/current/deploy && sudo docker compose up -d`（画面は `http://<サーバー>:8080/`）。

### T-5. app が healthy にならない・画面が出ない（502）

```sh
cd /srv/ops/current/deploy && sudo docker compose ps --format '{{.Service}} {{.State}} {{.Health}}' && sudo docker compose logs --tail 60 app
```

| ログに出る文 | 原因 | 直し方 |
|---|---|---|
| `password authentication failed for user "ops"` | `.env` のパスワードとデータベースが合わない | [T-6](#t-6-password-authentication-failed-for-user-ops) |
| `Can't locate revision` | 今の版が、データベースより古い | [5.](#5-前の版に戻す) の後半（`Can't locate revision` のとき） |
| `could not translate host name "db"` / `Connection refused` | `db` が動いていない | `sudo docker compose up -d db` → 1 分後に `sudo docker compose up -d` |
| `Running database migrations` の後でエラー | 新しい版のデータベースの変更が失敗 | 今の版はそのまま止まっている。[5.](#5-前の版に戻す) で前の版に戻し、ログ（7.1）を開発の担当へ |
| ログが `Starting the API on port 8000...` で終わっていて `starting` のまま | 起動に時間がかかっている | 2 分待って、もう一度このブロック |

画面だけ出ない（`app` は `healthy`）なら `sudo docker compose up -d web` → `sudo docker compose logs --tail 30 web`。

**確認**: [2.](#2-再起動のあとの確認) で `app running healthy` と `{"status":"ok"}`。

### T-6. 「password authentication failed for user "ops"」

**原因**: データベースのパスワードは、**最初にデータベースを作ったときの** `/srv/ops/.env` の `POSTGRES_PASSWORD` で
決まり、後で `.env` を書き換えてもデータベースは変わらない。`.env` を作り直した・書き換えたときに起きる。

**直し方**: データベースのパスワードを、今の `.env` に合わせる（データは消えない）:

```sh
cd /srv/ops/current/deploy
PW=$(sudo sed -n 's/^POSTGRES_PASSWORD=//p' /srv/ops/.env)
sudo docker compose exec -T db psql -U ops -d ops -c "ALTER USER ops WITH PASSWORD '$PW';"
sudo docker compose up -d
```

**確認**: `ALTER ROLE` が出る。2 分後に [2.](#2-再起動のあとの確認) で `app running healthy`。

### T-7. デプロイ（deploy / gate）が赤

Actions の画面で赤いジョブを開き、赤い手順のログの最後の方を見ます。

| ログの文 | 直し方 |
|---|---|
| `リポジトリの変数 DEPLOY_USERS が空` | [3.4](#34-gitea-の画面での準備ブラウザで) の 5. で `DEPLOY_USERS` を作る |
| `<名前> はデプロイできない（DEPLOY_USERS: …）` | 押した人を `DEPLOY_USERS` に足す（3.4 の 5. の画面で値を編集）か、入っている人が押す |
| `デプロイできるのは main だけ` | Run workflow で枝 **main** を選んで押し直す |
| `/srv/ops/.env が無い` | [3.7](#37-アプリの設定を作るサーバーで) |
| `ほかのデプロイが動いている` | 前のデプロイが終わるまで待つ（Actions の画面で動いている実行を見る）。動いていないのに出るなら 5 分待って押し直す |
| `install:` の後に `docker compose` のエラー | 新しい版が起動できなかった。今の版のまま動いている。サーバーで [T-5](#t-5-app-が-healthy-にならない画面が出ない502) の表で原因を見る |
| `backend` / `frontend` が赤（`deploy` は「スキップ」） | [T-8](#t-8-backend--frontend-が赤) |

**確認**: 押し直して `deploy` が緑、`sudo tail -n 1 /srv/ops/deploys.log` が `ok`。

### T-8. backend / frontend が赤

コードのチェックが通らなかった。**サーバーの今の版はそのまま動いていて、何も入っていません。**
赤い手順の名前で見分けます:

| 赤い手順 | 意味 | 行うこと |
|---|---|---|
| 「依存関係の既知の脆弱性（pip-audit）」「npm audit」でネットワークのエラー（`ConnectionError`・`ETIMEDOUT`・証明書） | サーバーから pypi.org / registry.npmjs.org に届かない | [T-9](#t-9-インターネットに出られないdocker-pull-が失敗する)。直ったら Actions で **Re-run all jobs** |
| 同じ手順で、パッケージの名前と `GHSA-…` / `PYSEC-…` が出る | 使っている部品に、新しく脆弱性が見つかった | 開発の担当へ（部品を上げる必要がある）。急ぎで入れる判断は開発の担当がする |
| 「テスト」「型チェック」「Ruff」「openapi.json が古い」 | コードの誤り | 開発の担当へ |
| 「イメージの取得」・`pull access denied`・`toomanyrequests` | Docker Hub に届かない・回数の上限 | [T-9](#t-9-インターネットに出られないdocker-pull-が失敗する)。`toomanyrequests` なら 6 時間後に **Re-run all jobs** |

### T-9. インターネットに出られない・docker pull が失敗する

```sh
for url in https://registry-1.docker.io/v2/ https://pypi.org/simple/ https://registry.npmjs.org/; do printf '%-40s ' "$url"; curl -s -o /dev/null -w '%{http_code}\n' --max-time 15 "$url" || echo "届かない"; done
```

- `000` / `届かない`: ネットワークの担当に、サーバーから上の 3 つ（と `download.docker.com`）への HTTPS（443 番）を
  開けてもらう。会社のプロキシを通す決まりなら [付録](#付録-プロキシが必要なとき)
- `SSL certificate problem`: 会社のプロキシが HTTPS を検査している → [付録](#付録-プロキシが必要なとき) の 2.

**確認**: 3 行とも 3 桁の数字（`200` か `401`）。`sudo docker pull hello-world` が成功する。

### T-10. ワーカーが止まっている（画面に赤い帯）

```sh
cd /srv/ops/current/deploy && sudo docker compose ps --format '{{.Service}} {{.State}}' | grep worker; sudo docker compose logs --tail 40 worker
```

- `worker` が無い・`exited` → `sudo docker compose up -d worker`
- `worker` は `running` なのにログが止まっている → `sudo docker compose restart worker`
- `app` が `healthy` でない（`worker` は `app` を待つ）→ [T-5](#t-5-app-が-healthy-にならない画面が出ない502)

**確認**: 2 分後に `curl -s "http://$SERVER_IP/api/health/worker"` が `"state":"ok"`。画面の赤い帯は 1 分以内に自然に消える。
止まっていた間の自動リリース・締切の監視・メールは、ワーカーが戻ると続きから行う。

### T-11. バックアップが無い・FAILED

```sh
cd /srv/ops/current/deploy && sudo docker compose logs --tail 20 backup; df -h /srv
```

- `backup` が動いていない → `sudo docker compose up -d backup`
- `FAILED` と `No space left on device`、または `df` の `Use%` が 90% 以上 → [T-12](#t-12-ディスクがいっぱい)
- `FAILED` とほかのエラー → 今すぐ [6.2](#62-今すぐバックアップを取る) を試し、出たエラーを開発の担当へ

**確認**: [6.2](#62-今すぐバックアップを取る) で `backup: done`。

### T-12. ディスクがいっぱい

```sh
df -h / /srv /var/lib/docker
sudo docker system df
sudo du -sh /srv/ops/releases /srv/ops/backups /srv/ops/logs /srv/gitea/data
```

安全に空けられるもの（上から順に。データベースとバックアップには触らない）:

```sh
sudo docker image prune -f
sudo docker builder prune -f
```

```sh
echo "今の版: $(readlink /srv/ops/current)"; ls -1t /srv/ops/releases/ | tail -n +4
```

最後のブロックで出た版（新しい 3 つより古いもの。今の版は出ない）は消してかまいません:
`sudo rm -rf /srv/ops/releases/<版の名前>`（1 つずつ。⚠ 今の版を消さない）。

⚠ `sudo docker system prune --volumes`・`sudo docker volume prune` は**データベースを消す**ので使わない。

**確認**: `df -h /srv` の `Use%` が 80% 以下。

### T-13. パスワードを忘れた

- **アプリの職員**: [7.3](#73-よく使う操作) の「職員のパスワードを変える」（新しいパスワードを 2 回たずねる）
- **Gitea**（サーバーで。最後の行がログイン名をたずね、仮のパスワードが出る。次のログインで変えさせられる）:

  ```sh
  read -r -p "Gitea のログイン名: " U && P=$(openssl rand -hex 8) && sudo docker exec -u git gitea gitea admin user change-password --username "$U" --password "$P" --must-change-password && echo "仮のパスワード: $P"
  ```

### T-14. PC から push できない

| エラー | 直し方 |
|---|---|
| `Authentication failed` / `401` | PC が古いパスワードを覚えている。下のブロックで忘れさせて、もう一度 `git push gitea main`（ユーザー名 = Gitea のログイン名、パスワード = [3.6](#36-コードを-gitea-へ送るpc-で) のトークン） |
| `403` / `permission denied` | その人に書き込みの権限が無い → [3.4](#34-gitea-の画面での準備ブラウザで) の 4. |
| `repository not found` | リポジトリが無い・名前の綴りが違う → 3.4 の 2.〜3.（`ops` / `postgres-migration-poc`） |
| `Could not resolve host` / `Connection refused` / `timed out` | Gitea に届かない → サーバーで [2.](#2-再起動のあとの確認)。サーバーのアドレスが変わったなら PC で 0.3 → 3.6 の最初のブロック |
| `rejected … (fetch first)` / `non-fast-forward` | Gitea の方が新しい（ほかの人が push した）。`git pull gitea main` の後で push。分からなければ開発の担当へ |

PC が覚えているログインを忘れさせる（Git Bash。先に 0.3）:

```sh
printf 'protocol=http\nhost=%s:3000\n\n' "$SERVER_IP" | git credential reject
```

### T-15. 復元（pg_restore）でエラーが出る

- `No such file or directory`: `DUMP` が空 → [6.3](#63-データベースをバックアップから戻す) の 1. からやり直す（ターミナルを
  開き直したら 0.3 と 6.3 の 1. から）
- `database "ops" already exists`（createdb）: 前の dropdb が失敗している。`sudo docker compose stop app worker` を
  確かめてから 6.3 の 4. をもう一度
- `input file appears to be a text format dump` / `does not appear to be a valid archive`: ファイルが壊れている・別の
  ファイル。別の日の `.dump` で 6.3 の 1. から
- そのほか: 戻すのをやめて、6.3 の 2. で取ったファイル（直前の状態）で 6.3 をやり直し、エラーの全文を開発の担当へ

### T-16. Run workflow のボタンが無い

- Actions の画面の左の一覧で **checks and deploy** を選んでいない → 選ぶ（ボタンは一覧で選んだ後に右に出る）
- 自分に書き込みの権限が無い → [3.4](#34-gitea-の画面での準備ブラウザで) の 4.
- Actions の一覧が空（ワークフローが無い）→ まだ push していない → [3.6](#36-コードを-gitea-へ送るpc-で)

---

## 9. メモ（仕組みの前提）

- 社内のネットワークだけで使う構成です（http、ドメインなし、HTTPS なし）。ログインの Cookie に `Secure` は付けていません
  （`docker-compose.yml` の `COOKIE_SECURE: "false"`。HTTPS にしたら `"true"`）。
- データベースのポートは外に出していません（`db` には Compose の中からだけ届く）。
- インターネットに出るのは、イメージを作る・取るとき（apt / pip / npm / Docker Hub）と、パイプラインのチェックだけです。
  動いているコンテナはインターネットに出ません（メールは社内の中継へ）。
- **main に push できる人は、パイプラインを書き換えてサーバーの Docker を操作できます**（同じサーバーへデプロイする
  パイプラインでは避けられない。パイロットでは受け入れる判断）。push の権限（3.4 の 4.）は信頼できる人だけに。
- ランナーは、ジョブのコンテナにサーバーの Docker を自動ではつなぎません（`/srv/gitea/runner/config.yaml` の
  `docker_host: "-"`）。つないでよいのは `valid_volumes` の 2 つ（Docker のソケットと `/srv/ops`）だけで、頼むのは
  `deploy` のジョブだけです。
- Gitea が使えないときの予備: PC で `python scripts/make_package.py`（→ `packages/ops-<日付>-<コミット>.zip`）を作り、
  USB でサーバーへ持っていき、`sudo unzip <zip> -d /srv/ops/releases && sudo sh /srv/ops/releases/<zip の名前から .zip を除いたもの>/deploy/install.sh`。
  パイプラインと同じ置き場所・同じ `install.sh` です（`deploys.log` には残りません）。
- 開発 PC の Windows のタスク スケジューラー（PlanExtendJob / OrderReleaseJob）は開発用のデータベース向けです。
  サーバーでは `worker` が同じことをします。
- API の説明（OpenAPI）は `http://<サーバー>/api/openapi.json`。外の監視には、ログインなしの
  `http://<サーバー>/api/health/app`（API）と `http://<サーバー>/api/health/worker`（定期処理。止まっていると 503）。

---

## 付録. プロキシが必要なとき

ふつうは要りません（サーバーは直接インターネットに出られる）。[3.1](#31-os-と-dockerサーバーで) の確かめで届かなかった
ときだけ行います。プロキシの値が空なら仕組みは何もしないので、ファイルはそのまま残しています。
以下の `http://proxy.example.co.jp:8080` は、ネットワークの担当から聞いた会社のプロキシのアドレスに置き換えます。

1. **OS のパッケージ取得**: `/etc/apt/apt.conf.d/95proxy` に次の 2 行を書く:

   ```
   Acquire::http::Proxy "http://proxy.example.co.jp:8080/";
   Acquire::https::Proxy "http://proxy.example.co.jp:8080/";
   ```

   3.1 の Docker の署名の鍵は `curl` で取り、`curl` は apt の設定を使わないので、その行に `-x http://proxy.example.co.jp:8080`
   を付ける。
2. **プロキシが HTTPS を検査している（会社の証明書で署名し直す）ときだけ**: 会社のルート証明書（PEM、拡張子 `.crt`）を
   - OS に入れる（apt・curl・`docker pull` 用。3.1 の Docker より前に）:
     `sudo cp company.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates`
     （Docker を入れた後なら `&& sudo systemctl restart docker` も）
   - `/srv/ops/certs/` に置く（イメージを作るときの apt / pip / npm 用。`deploy/install.sh` が使う。
     [certs/README.md](certs/README.md)）
3. **`docker pull`**（元になるイメージの取得）。`/etc/docker/daemon.json`:

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
4. **パイプラインのジョブ**（[3.5](#35-ランナーを登録するサーバーで)）: `/srv/gitea/runner/config.yaml` の `envs: {}` の行を消し、
   その下のブロックの `#` を外して、プロキシのアドレスと `NO_PROXY` のサーバーのアドレス（例の `10.0.0.5` を SERVER_IP に）を
   直す。HTTPS を検査するときは、会社の証明書の行（`PIP_CERT` / `NODE_EXTRA_CA_CERTS` と `container.options`・
   `valid_volumes` の `/srv/ops/certs`）の `#` も外す。pip-audit は `PIP_CERT` を読まないので、同じ場所に
   `REQUESTS_CA_BUNDLE: /company-certs/company.crt` の行も足す。最後に
   `cd /srv/gitea/compose && sudo docker compose restart runner`。
5. **イメージを作るとき**（apt / pip / npm）: `/srv/ops/.env` の `HTTP_PROXY` / `HTTPS_PROXY` に同じプロキシを書く
   （`NO_PROXY` はそのまま）。動いているコンテナには渡りません。
6. **開発 PC から**（シードデータの道具・`scp`）: 開発 PC がプロキシを通っているなら、`NO_PROXY` にサーバーのアドレスを
   入れて、サーバーへは直接つなぐ（GO-LIVE.md の C-0）。
