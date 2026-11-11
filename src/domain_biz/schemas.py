import re
from datetime import date, datetime, time
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain_biz.models import (
    RELEASE_FIELDS, CompletionMode, DependencyKind, DependencyMatchRule, ExecutionType, ReleaseMode, SOPStatus,
    SOPVersionEventType, SopState,
)

# 明示的な null を受け付けない項目（signal_type は null で解除できる）
NOT_NULL_FIELDS = (*RELEASE_FIELDS, "execution_type", "completion_mode")


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
    # 省略すると顧客の次の空き番号（削除された業務の番号も使い回さない）。業務コードは作成後に変えられない（M1.1）
    business_seq: int | None = Field(None, gt=0, le=9999, description="sequence num 1-9999; omit for the client's next free number")

class BizMasterUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    manager_email: str | None = Field(None, max_length=100)
    description: str | None = Field(None)
    # リリース設定（新規作成時は既定値: 手動承認・03:00・当日。変更はこの PATCH で行う）
    release_mode: ReleaseMode | None = Field(None, description="MANUAL_APPROVAL or AUTOMATIC")
    release_time: time | None = Field(None, description="automatic release time of day (JST)")
    release_days_before: int | None = Field(None, ge=0, le=30, description="automatic release N days before the scheduled day")
    # 顧客側の出来事（Phase 7）。AUTOMATIC は LIGHTWEIGHT のみ・signal_type 必須（null で解除）
    execution_type: ExecutionType | None = Field(None, description="SOP (steps) or LIGHTWEIGHT (client-side event, no steps)")
    completion_mode: CompletionMode | None = Field(None, description="MANUAL (operator marks received) or AUTOMATIC (signal)")
    signal_type: str | None = Field(None, min_length=1, max_length=50, description="signal type that completes the orders (AUTOMATIC only)")

    @field_validator("signal_type")
    @classmethod
    def strip_signal_type(cls, v: str | None) -> str | None:
        return v.strip() if v else v

    @model_validator(mode="after")
    def check_release_not_null(self):
        nulls = sorted(f for f in NOT_NULL_FIELDS if f in self.model_fields_set and getattr(self, f) is None)
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
    execution_type: ExecutionType
    completion_mode: CompletionMode
    signal_type: str | None
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

class SOPVersionUpdate(SOPVersionCreate):
    """草稿のタグの変更（M2.2。承認済みの版は変えられない）。形式の確認は SOPVersionCreate と同じ"""

class SOPVersionScheduleIn(BaseModel):
    """草稿の承認（M2.1）: この日の 0:00（日本時間）から有効にする。明日以降"""
    model_config = ConfigDict(str_strip_whitespace=True)

    effective_date: date = Field(..., description="first day (JST) the version applies to; tomorrow or later")
    note: str | None = Field(default=None, max_length=500, description="optional comment on the approval (kept in the version's history)")

    @field_validator("note")
    @classmethod
    def blank_note_is_none(cls, v: str | None) -> str | None:
        return v or None


class BizReleaseUpdate(BaseModel):
    """業務のリリース設定だけの変更（P.3。リリース担当・業務管理者）: 送った項目だけ変える。ほかの項目は受け付けない"""
    model_config = ConfigDict(extra="forbid")

    release_mode: ReleaseMode | None = Field(None, description="MANUAL_APPROVAL or AUTOMATIC")
    release_time: time | None = Field(None, description="automatic release time of day (JST)")
    release_days_before: int | None = Field(None, ge=0, le=30, description="automatic release N days before the scheduled day")

    @model_validator(mode="after")
    def check_not_null(self):
        nulls = sorted(f for f in RELEASE_FIELDS if f in self.model_fields_set and getattr(self, f) is None)
        if nulls:
            raise ValueError(f"{nulls} must not be null")
        if not self.model_fields_set:
            raise ValueError("send at least one of " + ", ".join(RELEASE_FIELDS))
        return self


class BizReleaseValues(BaseModel):
    """リリース設定の 3 項目（履歴の変更前・変更後）"""
    release_mode: ReleaseMode
    release_time: time
    release_days_before: int


class BizReleaseChangeResponse(BaseModel):
    """リリース設定の変更 1 件（P.3）"""
    id: int
    biz_master_id: int
    changed_by_id: int | None
    changed_by_name: str | None = None
    changed_at: datetime
    before: BizReleaseValues
    after: BizReleaseValues


class SOPVersionReject(BaseModel):
    """依頼された草稿の差し戻し（P.2）: 理由は必須"""
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(..., min_length=1, max_length=500, description="why the draft is sent back (required)")

class SOPVersionResponse(BaseModel):
    id: int
    biz_master_id: int
    version_number: int
    version_tag: str
    status: SOPStatus = Field(description="stored: draft or approved")
    state: SopState = Field(description="draft / scheduled (approved, not yet in effect) / current / past, from the dates")
    effective_at: datetime | None
    expired_at: datetime | None = Field(description="end (exclusive): the next version's effective_at")
    # P.2: 誰が作った・直した・承認を依頼した（この機能より前の版は null）
    created_by_id: int | None = Field(default=None, description="staff who created the version (null before 2026-11-11)")
    updated_by_id: int | None = Field(default=None, description="staff who last changed the draft")
    updated_at: datetime | None = Field(default=None, description="when the draft was last changed")
    submitted_by_id: int | None = Field(default=None, description="staff who asked for approval (only while it waits)")
    submitted_at: datetime | None = Field(default=None, description="when approval was asked for (only while it waits)")
    # P.3: 名前（版を返す API はすべて付ける。職員が見つからなければ null）
    created_by_name: str | None = None
    updated_by_name: str | None = None
    submitted_by_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class SOPVersionEventResponse(BaseModel):
    """SOP の版の記録 1 件（P.3）"""
    id: int
    event_type: SOPVersionEventType
    actor_id: int | None
    actor_name: str | None = None
    occurred_at: datetime
    note: str | None = Field(description="reason of a rejection / comment on an approval")
    detail: dict[str, Any] | None = Field(
        description="created: cloned_from_version_id / cloned_from_tag; approved: effective_date, drafted_by_id (self-approval when it equals actor_id)"
    )
    drafted_by_name: str | None = Field(default=None, description="approved: name of the staff whose draft it was")

    model_config = ConfigDict(from_attributes=True)


class SubmittedSOPDraftCount(BaseModel):
    count: int


class SubmittedSOPDraftResponse(BaseModel):
    """承認を依頼された SOP の草稿（承認待ちの一覧の 1 行。P.3）"""
    version_id: int
    version_tag: str
    biz_master_id: int
    business_code: str
    biz_name: str
    client_code: str
    step_count: int
    submitted_by_id: int
    submitted_by_name: str | None
    submitted_at: datetime
    created_by_id: int | None
    created_by_name: str | None


# ==========================================
# 📝 SOPStepTemplate Schemas
# ==========================================
# ==========================================
# ステップを含める条件（オーダーの予定日で判定。バージョンとともに固定される）
# ==========================================
class AlwaysCondition(BaseModel):
    """すべてのオーダーに含める（既定）"""
    type: Literal["always"] = "always"


class MonthInCondition(BaseModel):
    """予定日の月が months に含まれるオーダーだけに含める（例: 年次の照合は4月のみ）"""
    type: Literal["month_in"] = "month_in"
    months: list[int] = Field(..., min_length=1, description="months 1-12")

    @field_validator("months")
    @classmethod
    def check_months(cls, v: list[int]) -> list[int]:
        if any(m < 1 or m > 12 for m in v):
            raise ValueError("months must be between 1 and 12")
        return sorted(set(v))


class FirstInWeekCondition(BaseModel):
    """そのテンプレートの、その ISO 週で最初の予定のオーダーだけに含める（例: 週次のまとめ）"""
    type: Literal["first_in_week"] = "first_in_week"


StepCondition = Annotated[Union[AlwaysCondition, MonthInCondition, FirstInWeekCondition], Field(discriminator="type")]


class SOPStepTemplateCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    step_num: int | None = Field(None, gt=0, description="ステップの実行順序（1以上の整数）。省略すると最後（最大 + 1）")
    title: str = Field(..., min_length=1, max_length=100, description="ステップタイトル")
    content: str | None = Field(None, description="具体的な手順・マニュアル内容")
    condition: StepCondition = Field(default_factory=AlwaysCondition, description="when the step is included in an order")
    is_blocking: bool = Field(False, description="the next step opens only after this one is approved (otherwise on submit)")

class SOPStepTemplateResponse(BaseModel):
    id: int
    sop_version_id: int
    step_num: int
    title: str
    content: str | None
    condition: dict[str, Any]
    is_blocking: bool

    class Config:
        from_attributes = True

class SOPStepTemplateUpdate(BaseModel):
    """草稿のステップの変更（送った項目だけ変わる。M2.2）。番号は並べ替え（SOPStepOrderIn）で変える"""
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(None, min_length=1, max_length=100)
    content: str | None = Field(None, description="null で消す")
    condition: StepCondition | None = None
    is_blocking: bool | None = None

    @model_validator(mode="after")
    def check_not_null(self):
        nulls = sorted(f for f in ("title", "condition", "is_blocking") if f in self.model_fields_set and getattr(self, f) is None)
        if nulls:
            raise ValueError(f"{nulls} must not be null")
        return self


class SOPStepOrderIn(BaseModel):
    """草稿のステップの並び（すべてのステップの id を新しい順に。番号は 1 から振り直す）"""
    step_ids: list[int] = Field(..., min_length=1)


class SOPVersionDetailResponse(SOPVersionResponse):
    """バージョンとそのステップ（番号順）"""
    steps: list[SOPStepTemplateResponse]


# ==========================================
# 業務間の依存関係（Phase 6）
# ==========================================
class BizDependencyCreate(BaseModel):
    """この業務（下流）が、upstream_biz_id の業務（上流）を待つ"""
    upstream_biz_id: int
    match_rule: DependencyMatchRule
    kind: DependencyKind = DependencyKind.HARD
    lookback_days: int | None = Field(
        default=None, ge=1, le=366,
        description="latest_before only (required there): how many days back an upstream order may be",
    )

    @model_validator(mode="after")
    def _lookback_only_for_latest_before(self):
        if self.match_rule == DependencyMatchRule.LATEST_BEFORE and self.lookback_days is None:
            raise ValueError("lookback_days is required for latest_before")
        if self.match_rule != DependencyMatchRule.LATEST_BEFORE and self.lookback_days is not None:
            raise ValueError("lookback_days is only used with latest_before")
        return self


class BizRef(BaseModel):
    id: int
    business_code: str
    name: str

    model_config = ConfigDict(from_attributes=True)


class BizDependencyResponse(BaseModel):
    id: int
    upstream: BizRef
    downstream: BizRef
    match_rule: DependencyMatchRule
    kind: DependencyKind
    lookback_days: int | None = Field(description="latest_before only: how many days back an upstream order may be")
    created_at: datetime
