from pydantic import BaseModel, Field
from src.core.error_codes import ErrorCode

class ErrorResponse(BaseModel):
    status_code: int = Field(..., description="HTTPステータスコード")
    error_code: ErrorCode = Field(..., description="ビジネスエラーコード")
    message: str = Field(..., description="ユーザー（またはフロントエンド）向けのエラーメッセージ")

COMMON_ERRORS = {
    400: {"model": ErrorResponse, "description": "Bad Request(リクエスト不正)"},
    404: {"model": ErrorResponse, "description": "Not Found(データが見つからない)"},
    409: {"model": ErrorResponse, "description": "Conflict (Record already exists etc.)"},
    500: {"model": ErrorResponse, "description": "Internal Server Error(サーバーエラー)"},
}