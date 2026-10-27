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

    # .env ファイルを自動的に読み込む設定
    model_config = SettingsConfigDict(
        env_file=env_file_to_load, 
        env_file_encoding="utf-8",
        extra="ignore"  # .envに未定義の変数があっても無視する（安全対策）
    )

settings = Settings()  # pyright: ignore[reportCallIssue]  # database_url は .env から読み込まれる