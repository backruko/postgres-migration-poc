from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime

class BizMasterBase(BaseModel):
    client_code: str = Field(..., min_length=2, max_length=10)
    business_seq: int
    name: str
    manager_email: str | None = None
    description: str | None = None

class BizMasterCreate(BizMasterBase):
    pass # 登録時に必要な項目

class BizMasterResponse(BizMasterBase):
    id: int
    business_code: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True) # orm_mode=True の新しい書き方

class BizMasterUpdate(BaseModel):
    name: str | None = None
    manager_email: str | None = None
    description: str | None = None