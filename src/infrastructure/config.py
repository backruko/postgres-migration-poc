# src/infrastructure/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # 環境を指定。デフォルトは "dev"
    environment: str = "dev"  
    
    database_url: str

    # .env ファイルを自動的に読み込む設定
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        extra="ignore"  # .envに未定義の変数があっても無視する（安全対策）
    )

settings = Settings()