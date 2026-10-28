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

# 職員の確認・ロールの判定があるエンドポイント用（COMMON_ERRORS と合わせて指定する）
AUTH_ERRORS = {
    401: {"model": ErrorResponse, "description": "Unauthorized(操作する職員が指定されていない・存在しない)"},
    403: {"model": ErrorResponse, "description": "Forbidden(ロールが無い・アカウントが利用開始前／利用停止中)"},
}