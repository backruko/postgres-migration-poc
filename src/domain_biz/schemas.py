import re
from datetime import datetime, time

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain_biz.models import ReleaseMode, SOPStatus

RELEASE_FIELDS = ("release_mode", "release_time", "release_days_before")


class BizMasterBase(BaseModel):
    client_code: str = Field(..., min_length=4, max_length=4, description="Client Code for 4 Characters")
    name: str = Field(..., max_length=100, description="Name for Business")
    manager_email: str | None = Field(None, max_length=100, description="Person in Charge of")
    description: str | None = Field(None, description="Business Description")

    @field_validator('client_code')  # 这里的名字必须和上面的字段名一模一样
    @classmethod
    def to_uppercase(cls, v: str) -> str:
        return v.upper() if v else v

class BizMasterCreate(BizMasterBase):
    business_seq: int = Field(..., gt=0, description="sequence num > 0")

class BizMasterUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    manager_email: str | None = Field(None, max_length=100)
    description: str | None = Field(None)
    # リリース設定（新規作成時は既定値: 手動承認・03:00・当日。変更はこの PATCH で行う）
    release_mode: ReleaseMode | None = Field(None, description="MANUAL_APPROVAL or AUTOMATIC")
    release_time: time | None = Field(None, description="automatic release time of day (JST)")
    release_days_before: int | None = Field(None, ge=0, le=30, description="automatic release N days before the scheduled day")

    @model_validator(mode="after")
    def check_release_not_null(self):
        nulls = sorted(f for f in RELEASE_FIELDS if f in self.model_fields_set and getattr(self, f) is None)
        if nulls:
            raise ValueError(f"{nulls} must not be null")
        return self

class BizMasterResponse(BizMasterBase):
    id: int
    business_seq: int
    business_code: str
    release_mode: ReleaseMode
    release_time: time = Field(description="automatic release time of day (JST)")
    release_days_before: int
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True) # orm_mode=True の新しい書き方

# ==========================================
# 📊 SOPVersion Schemas
# ==========================================
class SOPVersionCreate(BaseModel):
    # 💡 ユーザー自身に自由に入力してもらう（例として見本を提示）
    version_tag: str = Field(
        ..., 
        examples=["v1.1.0", "v2.0.0-draft-teamA"],
        description="セマンティックバージョニング形式(vX.Y.Z[-サフィックス])で入力してください"
    )

    @field_validator("version_tag")
    @classmethod
    def validate_version_tag_format(cls, v: str) -> str:
        # v1.0.0, v12.3.4, v1.0.0-draft-teamA などを許可する正規表現
        pattern = r"^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.-]+)?$"
        if not re.match(pattern, v):
            raise ValueError(
                "バージョンタグのフォーマットが不正です。'v1.0.0' や 'v1.1.0-teamA' の形式で入力してください。"
            )
        return v

class SOPVersionUpdate(BaseModel):
    """リポジトリの型定義を満足させるためだけの空スキーマ（実際には使わない）"""
    pass

class SOPVersionScheduleIn(BaseModel):
    """【権限者承認用】未来実装(FUTURE)へ昇格させる際の発効日指定スキーマ"""
    effective_at: datetime = Field(..., description="予約発効日時(UTCまたはタイムゾーン付き)")

class SOPVersionResponse(BaseModel):
    id: int
    biz_master_id: int
    version_number: int
    version_tag: str
    status: SOPStatus
    effective_at: datetime | None
    expired_at: datetime | None

    class Config:
        from_attributes = True  # SQLAlchemyモデルから自動変換できるようにする設定 (Pydantic v2)


# ==========================================
# 📝 SOPStepTemplate Schemas
# ==========================================
class SOPStepTemplateCreate(BaseModel):
    step_num: int = Field(..., gt=0, description="ステップの実行順序（1以上の整数）")
    title: str = Field(..., max_length=100, description="ステップタイトル")
    content: str | None = Field(None, description="具体的な手順・マニュアル内容")

class SOPStepTemplateResponse(BaseModel):
    id: int
    sop_version_id: int
    step_num: int
    title: str
    content: str | None

    class Config:
        from_attributes = True

class SOPStepTemplateUpdate(BaseModel):
    """リポジトリの型定義(CRUDBase)を通過させるためだけの空スキーマ（実際には使いません）"""
    pass