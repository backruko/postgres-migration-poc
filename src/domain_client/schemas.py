from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# 顧客コードは英数字4文字（業務コード = 顧客コード + 連番4桁 のため）。作成後は変更できない（M1.1）
CLIENT_CODE_PATTERN = r"^[A-Za-z0-9]{4}$"

# 作成と応答に共通の項目
class ClientBase(BaseModel):
    # 前後の空白を落とす（貼り付けた名前やコードの空白で、別の顧客や 4 文字でないコードにならないように）
    model_config = ConfigDict(str_strip_whitespace=True)

    code: str = Field(..., min_length=4, max_length=4, description="client code must be 4 Characters")
    name: str = Field(..., min_length=1, max_length=100, description="client Name")

class ClientCreate(ClientBase):
    code: str = Field(..., pattern=CLIENT_CODE_PATTERN, description="client code: 4 letters or digits (upper-cased, fixed after creation)")

# 部分更新（送った項目だけ変わる）なので ClientBase を継承しない（継承すると必須の項目まで要る）。
# 顧客コードは変えられない（業務・シート・オーダーが参照している）ため、code を送ると 422 になる。
# 有効 / 無効は専用のエンドポイント（deactivate / activate）で変える
class ClientUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="client name max to 100"
    )


class ClientResponse(ClientBase):
    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
