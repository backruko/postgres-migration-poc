from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime

class BizMasterBase(BaseModel):
    client_code: str = Field(..., min_length=4, max_length=4, description="Client Code for 4 Characters")
    name: str = Field(..., max_length=100, description="Name for Business")
    manager_email: str | None = Field(None, max_length=100, description="Person in Charge of")
    description: str | None = Field(None, description="Business Description")

class BizMasterCreate(BizMasterBase):
    business_seq: int = Field(..., gt=0, description="sequence num > 0")

class BizMasterUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    manager_email: str | None = Field(None, max_length=100)
    description: str | None = Field(None)    

class BizMasterResponse(BizMasterBase):
    id: int
    business_seq: int
    business_code: str
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True) # orm_mode=True の新しい書き方
