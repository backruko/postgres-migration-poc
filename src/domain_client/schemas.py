from pydantic import BaseModel

# 登録時にユーザーから受け取るデータ
class ClientCreate(BaseModel):
    code: str
    name: str

# APIのレスポンスとして返すデータ（DBから取り出したもの）
class ClientResponse(ClientCreate):
    id: int
    
    class Config:
        from_attributes = True  # ORM(SQLAlchemy)のデータをPydanticに変換する設定