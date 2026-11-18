from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# 顧客コードは英数字4文字（業務コード = 顧客コード + 連番4桁 のため）。作成後は変更できない（M1.1）
CLIENT_CODE_PATTERN = r"^[A-Za-z0-9]{4}$"

# ==========================================
# 1. Base（共通属性）
# ここには「作成時」や「取得時」に必ず存在する【必須の基本項目】だけを定義します。
# ==========================================
class ClientBase(BaseModel):
    #  Pydantic V2の model_config で、すべての文字列入力を自動でstripする設定
    model_config = ConfigDict(str_strip_whitespace=True)

    code: str = Field(..., min_length=4, max_length=4, description="client code must be 4 Characters")
    name: str = Field(..., min_length=1, max_length=100, description="client Name")

# ==========================================
# 2. Create（作成用スキーマ）
# Baseを継承します。作成時に新しく必要な項目があればここに追加します。
# （パスワードなど、取得時には返さないが作成時に必要なもの等）
# ==========================================
class ClientCreate(ClientBase):
    code: str = Field(..., pattern=CLIENT_CODE_PATTERN, description="client code: 4 letters or digits (upper-cased, fixed after creation)")

# ==========================================
# 3. Update（更新用スキーマ）
# 【重要】Baseを継承しません。
# PATCH（部分更新）の意図を明確にするため、あえて独立させ、全て Optional で定義します。
# 顧客コードは変更できない（業務・シート・オーダーが参照している）ため、code を送ると 422 になります。
# 有効 / 無効は専用のエンドポイント（deactivate / activate）で変えます。
# ==========================================
class ClientUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="client name max to 100"
    )
# ==========================================
# 4. Response（レスポンス用スキーマ）
# DBから取得したデータをフロントに返す形です。Baseの必須項目に加え、IDなどを持ちます。
# ==========================================
class ClientResponse(ClientBase):
    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    # ORMモデル（SQLAlchemy等）からの変換を許可する厳格な設定
    model_config = ConfigDict(from_attributes=True)
