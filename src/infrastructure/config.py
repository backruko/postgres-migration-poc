# src/infrastructure/config.py
import os
from pydantic_settings import BaseSettings, SettingsConfigDict

env_state = os.getenv("ENVIRONMENT", "dev")

env_file_to_load = ".env.test" if env_state == "test" else ".env"

class Settings(BaseSettings):
    # 環境を指定。デフォルトは "dev"
    environment: str = "dev"

    database_url: str

    # フロントエンドを別オリジンで配信する場合に許可するオリジン（例: ["https://plan.example.com"]）
    # 開発時は Vite のプロキシ経由で同一オリジンになるため不要
    cors_origins: list[str] = []

    # 実行する SQL をすべてログに出すか（SQL_ECHO=1 / true。既定はオフ: 1 リクエストで数百行になり、コンソールとログを埋めるため。
    # 調査のときだけオンにする）。.env にも書ける
    sql_echo: bool = False

    # ログインの Cookie に Secure を付けるか（COOKIE_SECURE=1。HTTPS で配信するときだけ。社内の http なら付けない: 付けると送られない）
    cookie_secure: bool = False

    # 通知の文面に入れる画面へのリンクの先頭（APP_BASE_URL。例: http://10.0.0.5）。空なら通知にリンクを付けない
    # （サーバーのアドレスが決まるまで。通知の仕組み）
    app_base_url: str | None = None

    # 通知のメール（SMTP。通知の仕組み）。SMTP_HOST が空なら、開発・テストでは偽物のチャネル（送らない）、
    # 本番（ENVIRONMENT=production）ではメールのチャネルなし（配信は再試行の後に失敗として見える。黙って捨てない）
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None          # あればログインする
    smtp_password: str | None = None
    smtp_from_address: str = "system-noreply@example.com"
    smtp_starttls: bool | None = None     # 空 = サーバーが対応していれば使う。1 = 必ず使う（なければ失敗）。0 = 使わない
    smtp_timeout_seconds: float = 30

    # .env ファイルを自動的に読み込む設定
    model_config = SettingsConfigDict(
        env_file=env_file_to_load,
        env_file_encoding="utf-8",
        extra="ignore",  # .envに未定義の変数があっても無視する（安全対策）
        # 空の環境変数は「設定なし」として既定値を使う（docker compose が未設定の変数を空で渡すため。例: SMTP_PORT=）
        env_ignore_empty=True,
    )

settings = Settings()  # pyright: ignore[reportCallIssue]  # database_url は .env から読み込まれる
