# 業務カレンダー（フロントエンド）

顧客に属する全業務の予定を月カレンダーで表示し、予定の **変更・取消・臨時追加・元に戻す** を行う React アプリです。
UI は Ant Design、API の型は FastAPI の OpenAPI 仕様から自動生成しています。

## 必要なもの

- Node.js 18 以上（動作確認: Node 24 / npm 11）
- FastAPI バックエンド（このリポジトリのルート）が起動していること

## 起動（開発）

```bash
# 1. バックエンド（リポジトリのルートで）
uvicorn main:app --reload            # http://127.0.0.1:8000

# 2. フロントエンド
cd frontend
npm install
cp .env.example .env                 # 任意: 既定の顧客コード・休日カレンダーなど
npm run dev                          # http://localhost:5173
```

`http://localhost:5173/?client=MOCK&month=2026-11&holidays=JP_BANK` のように、
顧客・月・休日カレンダーは URL で指定・共有できます。

## ディレクトリ構成

```
src/
  shared/                      どのドメインにも属さない土台
    api/schema.gen.ts          ← OpenAPI から自動生成（手で編集しない）
    api/http.ts                型付き HTTP クライアント（openapi-fetch）と ApiError
    hooks/useApiQuery.ts       GET 系の読み込み状態（中断・再読み込み）
    lib/datetime.ts            dayjs の設定、タイムゾーン付き日時の変換
    config.ts                  VITE_* の既定値
  domain/                      バックエンドのドメインと対応（UI を含まない）
    plan/                      ← src/domain_plan
      types/                   生成された型の別名（ClientCalendar, ClientOccurrence など）
      api/calendarApi.ts       カレンダー・営業日カレンダーの取得
      api/instanceApi.ts       取消・変更・元に戻す・臨時追加
      hooks/                   useClientCalendar, useBusinessCalendars
      lib/describe.ts          ルール・休日ポリシーの日本語表示
    client/                    ← src/domain_client
      types/ api/ hooks/
  features/                    画面（Ant Design のコンポーネント）
    calendar/                  月カレンダー（CalendarPage, MonthGrid, DayDetail, Legend, Toolbar …）
    schedule-actions/          変更・取消・臨時追加のフォーム（Modal + Form）
  app/AppProviders.tsx         Ant Design のテーマ・日本語ロケール
```

依存の向きは `features → domain → shared` の一方向です。画面のコンポーネントは HTTP を直接呼ばず、
必ず `domain/*/api` または `domain/*/hooks` を経由します。

## API の型を更新する（バックエンドを変更したとき）

```bash
# リポジトリのルートで: FastAPI から OpenAPI 仕様を書き出す（サーバーの起動は不要）
python scripts/export_openapi.py     # -> frontend/openapi.json

# frontend で: 型を再生成
npm run gen:api                      # -> src/shared/api/schema.gen.ts
npm run typecheck                    # 型の不一致があればここで分かる
```

起動中のサーバーから直接生成する場合は `npm run gen:api:live` です。
`openapi.json` と `schema.gen.ts` はリポジトリに含めます（バックエンドが無くてもビルドできるように）。

## バックエンドとのつながり

```
ブラウザ ──POST /api/plans/instances/123/override──▶ Vite 開発サーバー (5173)
                                                       │ /api を外して転送（vite.config.ts）
                                                       ▼
                                 FastAPI (8000)  POST /plans/instances/123/override
```

| 画面の操作 | API |
|---|---|
| カレンダー表示 | `GET /plans/clients/{client_code}/calendar?month=&holiday_calendar=` |
| 予定の変更（1件だけ日時を変える） | `POST /plans/instances/{id}/override` |
| 予定の取消 | `POST /plans/instances/{id}/cancel` |
| 元に戻す（変更を取り消す） | `POST /plans/instances/{id}/revert` |
| 臨時の予定を追加 | `POST /plans/templates/{id}/instances` |
| 顧客・休日カレンダーの選択肢 | `GET /clients/`, `GET /plans/calendars` |

- 開発時はブラウザから見て同一オリジンになるため、CORS の設定は不要です。
- バックエンドの業務エラー（`error_code` 付き）と入力検証エラー（422）は `ApiError` にそろえ、画面にメッセージを表示します。
- フォームで入力した日時は、ブラウザのタイムゾーンに関係なく `VITE_TIMEZONE`（既定 Asia/Tokyo）の時刻として送信します。

## 本番

```bash
npm run build                        # dist/ に静的ファイルを出力
```

1. **同一オリジン（推奨）**: nginx などで `dist/` を配信し、`/api/` を FastAPI へ転送する（`/api` を外して転送）。
2. **別オリジン**: ビルド時に `VITE_API_BASE_URL=https://api.example.com` を指定し、
   バックエンドの `.env` に `CORS_ORIGINS=["https://<フロントのオリジン>"]` を設定する。

## スクリプト

| コマンド | 内容 |
|---|---|
| `npm run dev` | 開発サーバー（プロキシ付き） |
| `npm run build` | 型チェック + 本番ビルド |
| `npm run typecheck` | 型チェックのみ |
| `npm run gen:api` | `openapi.json` から型を生成 |
| `npm run gen:api:live` | 起動中の FastAPI から型を生成 |
| `npm run preview` | ビルド結果の確認 |
