# Gitea の手順書（構築・確認・障害からの復旧）

アプリと同じ Ubuntu サーバーで、社内の Git サーバー **Gitea** と、そのパイプライン（自動チェックとデプロイ）を動かす
**ランナー**が Docker で動いています。この手順書は、その 2 つの立て方・確認・障害からの復旧を、**この仕組みを
知らない人が、コマンドを写して貼るだけで**行えるように書いています。

- アプリ（画面・API・データベース）の手順は **[README.md](README.md)**（アプリの手順書）です。ここには書きません。
- 読み方の決まり（ブロックは枠ごと貼る、「確認」を必ず見る、⚠ の意味、時刻の読み方）は、アプリの手順書の
  [0.2](README.md#02-読み方の決まり) と同じです。
- ターミナルを開いたら、毎回最初にアプリの手順書の [0.3](README.md#03-ターミナルを開いたら毎回最初にserver_ip-を決める)
  （`SERVER_IP` を決める）を行います。以下のコマンドは `$SERVER_IP` を使います。

Gitea が止まっても、**動いているアプリ（画面・データベース）には影響しません**。新しい版を入れられなくなるだけです。

---

## 0. 場面から探す

| いまの状況 | 行うこと |
|---|---|
| Gitea とランナーが動いているかを確かめたい（再起動の後など） | [2. 動いているかの確認](#2-動いているかの確認) |
| 新しいサーバーに一から立てる（初めて、またはサーバーの作り直し） | [3. 構築](#3-構築) |
| 人を足す・デプロイしてよい人を変える・パスワードを忘れた | [4. 人と権限](#4-人と権限) |
| Gitea が壊れた・データが読めない | [5. Gitea を作り直す](#5-gitea-を作り直す) |
| ランナーのログに「unregistered runner」・ランナーがオフライン・ジョブが「待機中」のまま | [7. トラブルシューティング](#7-トラブルシューティング) の [G-1](#g-1-unregistered-runnerランナーが-gitea-に知られていない)・[G-2](#g-2-ランナーがオフラインジョブが待機中のまま) |
| Gitea の版を上げる | [6. Gitea の版を上げる](#6-gitea-の版を上げる) |
| 会社のプロキシを通さないとパイプラインがインターネットに出られない | [付録](#付録-プロキシが必要なときランナー) |

---

## 1. 全体の形

### 1.1 動いているもの

Compose の名前は `gitea`（アプリの `ops` とは別）。設定は `/srv/gitea/compose/docker-compose.yml`。

| 名前（コンテナ） | 役割 | 止まると |
|---|---|---|
| `gitea` | Git サーバー。画面と git の push は `http://<サーバー>:3000/`（SSH は使わない）。自分で登録はできず、管理者がアカウントを作る | push もデプロイもできない |
| `gitea-runner` | パイプラインのジョブを、使い捨てのコンテナで動かす（このリポジトリ専用） | パイプラインが「待機中」のまま |

### 1.2 ファイルの置き場所

| パス | 中身 | 消えたら |
|---|---|---|
| `/srv/gitea/compose/docker-compose.yml` | Gitea とランナーの Compose（リポジトリの `deploy/gitea/docker-compose.yml` の写し） | 写し直す（3.2） |
| `/srv/gitea/compose/.env` | サーバーのアドレス（`SERVER_ADDRESS`）・版（`GITEA_VERSION`・`ACT_RUNNER_VERSION`）・ランナーの登録トークン（登録の間だけ）。権限 600 | 作り直す（3.2） |
| `/srv/gitea/data/` | Gitea のデータ（リポジトリ・アカウント・設定・データベース） | 作り直して、コードを push し直す（5.） |
| `/srv/gitea/data/backups/gitea-YYYYMMDD.zip` | 毎晩 01:30 のバックアップ（14 日分）。今はサーバーの中だけ（3.6） | 困らない（5.） |
| `/srv/gitea/runner/config.yaml` | ランナーの設定（リポジトリの `deploy/gitea/runner-config.example.yaml` の写し） | 写し直す（3.2） |
| `/srv/gitea/runner/.runner` | ランナーの登録情報（Gitea に「このランナーは自分」と名乗るための鍵） | 登録し直す（3.4） |
| `/etc/cron.d/gitea-dump` | 毎晩のバックアップの予定（記録は `/var/log/gitea-dump.log`） | 入れ直す（3.5） |

**コードの元は GitHub と開発 PC にあります。** Gitea を失って困るのは、アカウント・`DEPLOY_USERS`・ランナーの登録・
パイプラインの実行の記録だけで、どれも作り直せます。

---

## 2. 動いているかの確認

サーバーの再起動のあとは、Gitea もランナーも自動で起動します。それを確かめます（**サーバーで**。先に
[0.3](README.md#03-ターミナルを開いたら毎回最初にserver_ip-を決める)）:

```sh
echo "--- containers"; cd /srv/gitea/compose && sudo docker compose ps --format '{{.Service}} {{.State}}'
echo "--- gitea health"; curl -s --max-time 10 "http://$SERVER_IP:3000/api/healthz" | grep -o '"status": *"[a-z]*"' | head -n 1
echo "--- runner"; sudo docker compose logs --tail 50 runner | grep -E "declare successfully|unregistered|error" | tail -n 3
echo "--- runner screen: http://$SERVER_IP:3000/ops/postgres-migration-poc/settings/actions/runners"
```

**確認**:

```
--- containers
gitea running
runner running
--- gitea health
"status": "pass"
--- runner
… declare successfully …          ← 起動したとき・登録し直したときの行。無くても、下の画面がアイドルならよい
--- runner screen: http://…/settings/actions/runners
```

最後の URL をブラウザで開き、`ops-server` が **アイドル**（ジョブを動かしている間は **動作中**）であることを見ます。

違ったとき:

| 出たもの | 行うこと |
|---|---|
| `gitea` / `runner` が無い・`exited` | `cd /srv/gitea/compose && sudo docker compose up -d` → 1 分待ってもう一度 |
| gitea health が何も出ない（1 分待っても） | [G-3](#g-3-gitea-が起動しない) |
| `unregistered` の行が出る | [G-1](#g-1-unregistered-runnerランナーが-gitea-に知られていない) |
| ランナーの画面で **オフライン** | [G-2](#g-2-ランナーがオフラインジョブが待機中のまま) |

---

## 3. 構築

新しいサーバーに一から立てるときの手順です。**アプリの手順書の [3.3](README.md#33-gitea-とランナーを用意する) から
ここへ来て、終わったらアプリの手順書の [3.4](README.md#34-コードを-gitea-へ送るpc-で) へ戻ります。**

### 3.1 前提（アプリの手順書で済ませておくこと）

- [0.3](README.md#03-ターミナルを開いたら毎回最初にserver_ip-を決める): `SERVER_IP` が決まっている
- [3.1](README.md#31-os-と-dockerサーバーで): 時計（chrony）と Docker が入っている
- [3.2](README.md#32-最初に要るファイルをサーバーへ送る): `/srv/ops/bootstrap/deploy/gitea/` にファイルがある

確かめる（サーバーで）:

```sh
echo "SERVER_IP=$SERVER_IP"; sudo docker compose version; ls -A /srv/ops/bootstrap/deploy/gitea
```

**確認**: アドレス、`Docker Compose version v2.…`、`.env.example  docker-compose.yml  gitea-dump.cron  runner-config.example.yaml`。

### 3.2 Gitea を起動して、管理者を作る（サーバーで）

```sh
[ -n "$SERVER_IP" ] || echo "!!! SERVER_IP が空です。アプリの手順書の 0.3 を先に行い、このブロックをもう一度"
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
`GITEA OK` が出ない（2 分たっても）→ [G-3](#g-3-gitea-が起動しない)。

Gitea の管理者を作ります。2 つのブロックで、それぞれ最後の行が値をたずねます:

```sh
read -r -p "Gitea の管理者のログイン名（半角英数字。例 admin1）: " GITEA_ADMIN
```

```sh
read -r -p "その人のメールアドレス: " GITEA_EMAIL && sudo docker exec -u git gitea gitea admin user create --admin --username "$GITEA_ADMIN" --email "$GITEA_EMAIL" --random-password --must-change-password
```

**確認**: `generated random password is '…'` と `New user '…' has been successfully created!` が出る。
**この仮のパスワードを控えます**（次の 3.3 で最初のログインに使い、すぐ変えます）。
`user already exists` なら、もうある（作り直しで `/srv/gitea/data` が残っている）ので、そのアカウントを使います。

### 3.3 画面での準備（ブラウザで）

使う URL を出します（サーバーで）:

```sh
echo "1 ログイン:            http://$SERVER_IP:3000/user/login"
echo "2 組織を作る:          http://$SERVER_IP:3000/org/create"
echo "3 リポジトリを作る:    http://$SERVER_IP:3000/repo/create"
echo "4 共同作業者:          http://$SERVER_IP:3000/ops/postgres-migration-poc/settings/collaboration"
echo "5 Actions の変数:      http://$SERVER_IP:3000/ops/postgres-migration-poc/settings/actions/variables"
echo "6 Actions の実行:      http://$SERVER_IP:3000/ops/postgres-migration-poc/actions"
echo "7 push 用のトークン:   http://$SERVER_IP:3000/user/settings/applications"
```

社内の PC のブラウザで、上から順に開きます:

1. **ログイン**: 3.2 の管理者の名前と仮のパスワード。新しいパスワードを決めて変える。ほかに push する人・
   デプロイする人がいれば、[4.1](#41-アカウントを作る) で作る。
2. **組織**: 組織名に `ops`（この綴りのまま）、公開範囲は **プライベート** → 作成。
3. **リポジトリ**: オーナー `ops`、リポジトリ名 `postgres-migration-poc`（この綴りのまま）、**プライベート** にチェック。
   README・.gitignore・ライセンスは**何も選ばない**（空のまま）→ 作成。
4. **共同作業者**: push する人・デプロイする人を足し、権限を **書き込み** にする（「Run workflow」のボタンには書き込みの
   権限が要る）。管理者 1 人で運用するなら、この手順は飛ばしてよい。
5. **Actions の変数**: **変数を追加** → 名前 `DEPLOY_USERS`、値に「デプロイしてよい人のログイン名」をカンマで区切って
   書く（例 `admin1,lead1`）→ 追加。**ここに無い人がデプロイを押しても、パイプラインは何もせずに止まります。**
6. **Actions の実行**: 開ける（「ワークフローはまだありません」でよい）。404 なら、リポジトリの **設定** →
   **高度な設定** の **Actions** にチェックを入れて **設定を更新**。
7. **push 用のトークン**: **新しいトークンを生成** → トークン名 `push`、権限の **repository** を **読み取りと書き込み** →
   生成。出たトークンを控える（**この画面を閉じると二度と出ません**）。PC から push するときのパスワードに使います。
   push する人それぞれが、自分のアカウントで作ります。

### 3.4 ランナーを登録する（サーバーで）

ランナーは、Gitea のパイプラインを動かす係です。このリポジトリ専用に登録します。**ランナーを登録し直すとき
（[G-1](#g-1-unregistered-runnerランナーが-gitea-に知られていない)）も、このブロックそのままです**
（古い登録情報 `.runner` を `.runner.old-<日時>` に退けてから登録します）。

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
- `token: 0 文字` のとき（コマンドでトークンを作れなかった）: 3.3 の「6 Actions の実行」と同じリポジトリの
  **設定 → Actions → ランナー → 新しいランナーを作成** で出るトークンをコピーして、サーバーで次を貼り、たずねられたら
  トークンを貼る → その後、上のブロックの `sudo docker compose up -d runner` から下を貼る:

  ```sh
  read -r -p "ランナーのトークン: " TOKEN && sudo sed -i "s/^RUNNER_TOKEN=.*/RUNNER_TOKEN=$TOKEN/" /srv/gitea/compose/.env
  ```

- `unregistered runner` が出る → [G-1](#g-1-unregistered-runnerランナーが-gitea-に知られていない)

登録できたらトークンは要りません。消しておきます:

```sh
sudo sed -i 's/^RUNNER_TOKEN=.*/RUNNER_TOKEN=/' /srv/gitea/compose/.env
echo "http://$SERVER_IP:3000/ops/postgres-migration-poc/settings/actions/runners"
```

ブラウザで出た URL を開き、`ops-server` が **アイドル** で 1 つあることを確かめます。同じ名前で **オフライン** の
行（前の登録）があれば、その行の **編集** → **削除**（**ID の番号で見分ける**。アイドルの方は消さない）。
`/srv/gitea/runner/.runner.old-…` は、アイドルを確かめたら消してかまいません（`sudo rm /srv/gitea/runner/.runner.old-*`）。

### 3.5 毎晩のバックアップを入れる（サーバーで）

```sh
sudo cp /srv/ops/bootstrap/deploy/gitea/gitea-dump.cron /etc/cron.d/gitea-dump
sudo chmod 644 /etc/cron.d/gitea-dump
sudo docker exec -u git -w /data/backups gitea sh -c 'gitea dump -c /data/gitea/conf/app.ini --type zip --file "gitea-$(date +%Y%m%d).zip"' > /dev/null 2>&1
sudo ls -l /srv/gitea/data/backups/
```

**確認**: `gitea-YYYYMMDD.zip`（今日の日付）が 1 つある。以後は毎晩 01:30 に自動でできます（14 日より古いものは
消える。記録は `/var/log/gitea-dump.log`）。

### 3.6 バックアップの保管場所

今（MVP の間）は、`/srv/gitea/data/backups/` もサーバーの中だけに置きます。この節ですることはありません。理由と、
あとで NAS へ同期するときのことは、アプリの手順書の [3.8](README.md#38-バックアップの保管場所)。

> ⚠ 未決（あとでやること）: NAS への同期。専用のサービスアカウントができてから、アプリのバックアップと一緒に。

### 3.7 仕上げ

[2. 動いているかの確認](#2-動いているかの確認) を行い、すべて確認どおりなら完了です。**アプリの手順書の
[3.4](README.md#34-コードを-gitea-へ送るpc-で)（コードを Gitea へ送る）へ戻ります。**

---

## 4. 人と権限

### 4.1 アカウントを作る

自分で登録する画面はありません。管理者が作ります: 右上の自分のアイコン → **サイト管理** → **ユーザーアカウント** →
**ユーザーアカウントを作成**。ログイン名・メールアドレス・仮のパスワードを入れ、「最初のログインでパスワードを
変える」に印を付けて作成。push する人・デプロイする人なら、続けて 4.2。

### 4.2 push とデプロイの権限

| したいこと | 要るもの | どこで |
|---|---|---|
| push する | リポジトリの **書き込み** の権限 | リポジトリの **設定 → 共同作業者**（3.3 の 4. の URL） |
| デプロイ（Run workflow）を押す | 書き込みの権限と、`DEPLOY_USERS` にログイン名がある | 同上と、**設定 → Actions → 変数**（3.3 の 5. の URL）で `DEPLOY_USERS` を **編集** |

`DEPLOY_USERS` はカンマか空白で区切ったログイン名です（大文字・小文字は区別しない）。人を外すときは、
両方から外します。

### 4.3 Gitea のパスワードを忘れた

サーバーで。最後の行がログイン名をたずね、仮のパスワードが出ます（次のログインで変えさせられる）:

```sh
read -r -p "Gitea のログイン名: " U && P=$(openssl rand -hex 8) && sudo docker exec -u git gitea gitea admin user change-password --username "$U" --password "$P" --must-change-password && echo "仮のパスワード: $P"
```

push 用のトークンを失くしたときは、本人が 3.3 の 7. で新しいトークンを作り、古いトークンは同じ画面で **削除** します。
PC が古いトークンを覚えているときは、アプリの手順書の [T-11](README.md#t-11-pc-から-push-できない)。

---

## 5. Gitea を作り直す

Gitea にはコードの写しと、アカウント・`DEPLOY_USERS`・ランナーの登録・パイプラインの実行の記録があります。
**コードの元は GitHub と開発 PC にあるので、Gitea は作り直します**（毎晩の zip から戻す手順は、この手順書では
扱いません。zip は調べものの控えです）。ランナーだけがおかしいときは、作り直さずに [G-1](#g-1-unregistered-runnerランナーが-gitea-に知られていない)。

⚠ 次は Gitea のデータを脇へ移します（消さない）。動いているアプリ（画面・データベース）には影響しません。

```sh
cd /srv/gitea/compose && sudo docker compose down
sudo mv /srv/gitea/data "/srv/gitea/data.broken-$(date +%Y%m%d%H%M%S)"
sudo test -f /srv/gitea/runner/.runner && sudo mv /srv/gitea/runner/.runner "/srv/gitea/runner/.runner.old-$(date +%Y%m%d%H%M%S)"
sudo mkdir -p /srv/gitea/data/backups && sudo chown -R 1000:1000 /srv/gitea/data
ls -d /srv/gitea/data.broken-*
```

**確認**: `/srv/gitea/data.broken-<日時>` が出る。

このあと:
1. `/srv/ops/bootstrap/deploy/gitea/` が無ければ、アプリの手順書の [3.2](README.md#32-最初に要るファイルをサーバーへ送る) で送り直す
2. [3.2](#32-gitea-を起動して管理者を作るサーバーで)（起動と管理者）→ [3.3](#33-画面での準備ブラウザで) →
   [3.4](#34-ランナーを登録するサーバーで) → [3.5](#35-毎晩のバックアップを入れるサーバーで)
3. アプリの手順書の [3.4](README.md#34-コードを-gitea-へ送るpc-で)（コードを push し直す）。アプリ（その 3.5〜3.7）は
   **行わない**（動いたまま）

push の後で `backend` と `frontend` が緑になったら、`/srv/gitea/data.broken-…` は消してかまいません
（`sudo rm -rf /srv/gitea/data.broken-<日時>`）。

---

## 6. Gitea の版を上げる

版は `/srv/gitea/compose/.env` の `GITEA_VERSION`（と `ACT_RUNNER_VERSION`）です。⚠ **Gitea は版を下げられません**
（データの形が変わるため）。上げる前に開発の担当と決め、必ず先にバックアップを取ります。

1. いまの版と、上げる前のバックアップ:

   ```sh
   sudo grep -E '^(GITEA_VERSION|ACT_RUNNER_VERSION)=' /srv/gitea/compose/.env
   sudo docker exec -u git -w /data/backups gitea sh -c 'gitea dump -c /data/gitea/conf/app.ini --type zip --file "gitea-before-upgrade-$(date +%Y%m%d%H%M).zip"' > /dev/null 2>&1; sudo ls -l /srv/gitea/data/backups/ | tail -n 2
   ```

   **確認**: 版の 2 行と、`gitea-before-upgrade-…zip` の行。

2. 新しい版にして起動し直す（最後の行が版をたずねる。例 `1.24`）:

   ```sh
   read -r -p "新しい GITEA_VERSION: " V && sudo sed -i "s/^GITEA_VERSION=.*/GITEA_VERSION=$V/" /srv/gitea/compose/.env && cd /srv/gitea/compose && sudo docker compose pull gitea && sudo docker compose up -d gitea
   ```

3. [2. 動いているかの確認](#2-動いているかの確認)。`"status": "pass"` が出ない → [G-3](#g-3-gitea-が起動しない)。

ランナーの版（`ACT_RUNNER_VERSION`）も同じ形で、`GITEA_VERSION` を `ACT_RUNNER_VERSION` に、`gitea` を `runner` に
置き換えて行います（ランナーにはデータが無いので、バックアップは要りません）。

---

## 7. トラブルシューティング

エラーの文や症状で探します。各項目の「直し方」を上から順に行い、最後の「確認」で直ったかを見ます。
アプリのエラー（app が healthy にならない、デプロイの `install.sh` が失敗した、backend / frontend の赤）は、
アプリの手順書の [8.](README.md#8-トラブルシューティング) です。

### G-1. 「unregistered runner」（ランナーが Gitea に知られていない）

**症状**: `cd /srv/gitea/compose && sudo docker compose logs runner` に
`fail to invoke Declare … Unauthenticated desc = unregistered runner` が繰り返し出る。画面のランナーが **オフライン**。

**原因**: ランナーは `/srv/gitea/runner/.runner` に登録情報を持っていて、起動のたびにそれで名乗る。Gitea 側に
その登録がもう無い（画面でランナーを消した・リポジトリを作り直した・`/srv/gitea/data` を作り直した）。
`.runner` があると、ランナーは登録し直さない（`.env` に新しいトークンを入れても使わない）。

**直し方**: [3.4](#34-ランナーを登録するサーバーで) の最初のブロックをそのまま貼る（古い `.runner` を
`.runner.old-…` に退けて、新しいトークンで登録し直す）。続いて 3.4 の残り（トークンを消す・画面でオフラインの古い行を消す）。

**確認**: ログに `declare successfully`、画面で `ops-server` が **アイドル** で 1 つ。

### G-2. ランナーがオフライン・ジョブが「待機中」のまま

```sh
cd /srv/gitea/compose && sudo docker compose ps --format '{{.Service}} {{.State}}' && sudo docker compose logs --tail 30 runner
```

- `runner` が無い・`exited` → `sudo docker compose up -d runner` → 30 秒後にもう一度
- ログに `unregistered runner` → [G-1](#g-1-unregistered-runnerランナーが-gitea-に知られていない)
- ログに `connection refused` / `no such host`（Gitea に届かない）→ [2.](#2-動いているかの確認) で Gitea を確かめ、
  `sudo grep ^SERVER_ADDRESS= /srv/gitea/compose/.env` が今のサーバーのアドレスか見る。違えば [G-6](#g-6-サーバーの-ip-アドレスが変わった)
- ログに `Cannot connect to the Docker daemon` → `sudo systemctl is-active docker`（`active` でなければ
  `sudo systemctl start docker`）→ `sudo docker compose up -d runner`

**確認**: 画面で **アイドル**。待機中だったジョブが動き出す。

### G-3. Gitea が起動しない

```sh
cd /srv/gitea/compose && sudo docker compose ps -a --format '{{.Service}} {{.State}}' && sudo docker compose logs --tail 40 gitea
```

| ログ・エラー | 直し方 |
|---|---|
| `permission denied`（`/data/...`） | `sudo chown -R 1000:1000 /srv/gitea/data && cd /srv/gitea/compose && sudo docker compose up -d gitea` |
| `SERVER_ADDRESS を .env に` / `GITEA_VERSION を .env に` | `/srv/gitea/compose/.env` が無いか、その行が無い → [3.2](#32-gitea-を起動して管理者を作るサーバーで) の最初のブロック |
| `address already in use`（3000） | [G-4](#g-4-address-already-in-use3000-番が使われている) |
| 版を上げた直後で、データベースのエラー | 版を下げることはできない。ログ（下のブロック）を開発の担当へ。急ぎなら [5.](#5-gitea-を作り直す) で作り直す |
| そのほか | ログをファイルにして開発の担当へ: `cd /srv/gitea/compose && sudo docker compose logs --no-color gitea > /tmp/gitea-log.txt` |

**確認**: [2.](#2-動いているかの確認) で `"status": "pass"`。

### G-4. 「address already in use」（3000 番が使われている）

```sh
sudo ss -ltnp | grep -E ':3000 '
```

出た行の `users:((" … ",pid=…` が、3000 番を使っているプログラム。Gitea 以外なら、その担当と相談して止めてもらう
（Gitea のポートは、PC のリモートの URL・ランナー・パイプラインのすべてが使うので、変えない）。止まったら
`cd /srv/gitea/compose && sudo docker compose up -d gitea`。

### G-5. Run workflow のボタンが無い・Actions の画面が 404

- Actions の画面の左の一覧で **checks and deploy** を選んでいない → 選ぶ（ボタンは一覧で選んだ後に右に出る）
- 自分に書き込みの権限が無い → [4.2](#42-push-とデプロイの権限)
- Actions の画面が 404 → [3.3](#33-画面での準備ブラウザで) の 6.（Actions を有効にする）
- Actions の一覧が空 → まだ push していない → アプリの手順書の [3.4](README.md#34-コードを-gitea-へ送るpc-で)

### G-6. サーバーの IP アドレスが変わった

Gitea・ランナー・PC のリモートは、サーバーのアドレスを持っています。新しいアドレスにそろえます。

1. **サーバーで**、`SERVER_IP` を新しいアドレスにしてから（アプリの手順書の 0.3。`/srv/gitea/compose/.env` はまだ古いので、
   `export SERVER_IP=` に新しいアドレスを続けて打つ）:

   ```sh
   [ -n "$SERVER_IP" ] && sudo sed -i "s/^SERVER_ADDRESS=.*/SERVER_ADDRESS=$SERVER_IP/" /srv/gitea/compose/.env && cd /srv/gitea/compose && sudo docker compose up -d && sudo grep ^SERVER_ADDRESS= /srv/gitea/compose/.env
   ```

2. ランナーのジョブの設定で、プロキシの例外（`NO_PROXY`）にアドレスを書いていれば（[付録](#付録-プロキシが必要なときランナー)）、
   それも直して `sudo docker compose restart runner`
3. **PC で**、アプリの手順書の 0.3（新しいアドレスを打つ）→ その [3.4](README.md#34-コードを-gitea-へ送るpc-で) の最初のブロック
   （リモートを付け直す）
4. アプリの `/srv/ops/.env` の `APP_BASE_URL`（通知のメールの中のリンク）も、新しいアドレスにする（アプリの手順書の
   [7.2](README.md#72-通知のメールsmtp) と同じ形で `APP_BASE_URL` を直し、`cd /srv/ops/current/deploy && sudo docker compose up -d worker`）

**確認**: [2.](#2-動いているかの確認) がすべて確認どおり。PC から `git push gitea main` ができる。

---

## 8. メモ（仕組みの前提）

- **main に push できる人は、パイプラインを書き換えてサーバーの Docker を操作できます**（同じサーバーへデプロイする
  パイプラインでは避けられない。パイロットでは受け入れる判断）。書き込みの権限（4.2）は信頼できる人だけに。
- ランナーは、ジョブのコンテナにサーバーの Docker を自動ではつなぎません（`/srv/gitea/runner/config.yaml` の
  `docker_host: "-"`）。つないでよいのは `valid_volumes` の 2 つ（Docker のソケットと `/srv/ops`）だけで、頼むのは
  デプロイのジョブだけです。
- Gitea は自分で登録できず（`DISABLE_REGISTRATION`）、ログインしないと何も見えません（`REQUIRE_SIGNIN_VIEW`）。
  SSH は使わず、push は http だけです。メールは送りません（失敗は Actions の赤い印で見る）。
- パイプラインが確かめるもの（`.vbs`・`.ttl`・`.md` などは確かめない、`[skip ci]`）は、アプリの手順書の
  [0.4](README.md#04-パイプライン自動チェックが確かめるもの)。
- ジョブは、ソースを github.com からではなく、この Gitea の API から取ります（ジョブごとに Gitea が出すトークンで読む）。
  Gitea へは、プロキシの設定があっても通しません。

---

## 付録. プロキシが必要なとき（ランナー）

ふつうは要りません（サーバーは直接インターネットに出られる）。パイプラインのジョブ（pip / npm / pip-audit / npm audit）が
インターネットに届かず、会社のプロキシを通す決まりのときだけ行います。サーバーの OS・`docker pull`・アプリのイメージの
プロキシは、アプリの手順書の [付録](README.md#付録-プロキシが必要なとき) です。
以下の `http://proxy.example.co.jp:8080` は、ネットワークの担当から聞いた会社のプロキシのアドレスに置き換えます。

1. `/srv/gitea/runner/config.yaml` を開く: `sudo vi /srv/gitea/runner/config.yaml`
2. `envs: {}` の行を消し、その下の `# envs:` から始まるブロックの `#` を外す。プロキシのアドレスを直し、`NO_PROXY` の
   `10.0.0.5` をサーバーのアドレス（`echo $SERVER_IP` で出るもの）に直す
3. **プロキシが HTTPS を検査している（会社の証明書で署名し直す）ときだけ**: 会社の証明書を `/srv/ops/certs/` に置き
   （アプリの手順書の付録の 2.）、`config.yaml` の `PIP_CERT` / `NODE_EXTRA_CA_CERTS` / `REQUESTS_CA_BUNDLE` の行と、
   `container.options` の `-v /srv/ops/certs:/company-certs:ro` の行の `#` を外し、`valid_volumes` に `/srv/ops/certs` を足す
   （pip-audit は `PIP_CERT` を読まないので、`REQUESTS_CA_BUNDLE` が要る）
4. 読み直させる:

   ```sh
   cd /srv/gitea/compose && sudo docker compose restart runner && sleep 10 && sudo docker compose logs --tail 10 runner
   ```

**確認**: ログにエラーが無い。Actions で、赤だった実行の **Re-run all jobs** が緑になる。
