# src/infrastructure/exception_handler/handlers.py

from fastapi import Request
from fastapi.responses import JSONResponse
import logging

from src.core.exceptions import BusinessException
from src.core.error_codes import ErrorCode

logger = logging.getLogger(__name__)

async def business_exception_handler(request: Request, exc: BusinessException) -> JSONResponse:
    """
    BusinessException (およびその子クラス) をキャッチして、
    フロントエンド向けの統一されたJSONフォーマットに変換する
    """
    # ログ出力 (必要に応じて)
    logger.warning(f"Business Error: {exc.error_code} - {exc.message}")

    return JSONResponse(
        status_code=exc.status_code,  # HTTPヘッダーのステータスコード
        content={
            "status_code": exc.status_code,  # フロントエンド用のステータスコード(JSON内)
            "error_code": exc.error_code,    # 'INVALID_CREDENTIALS' など
            "message": exc.message           # エラーメッセージ
        }
    )

async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    想定外のエラー (500 Internal Server Error) をキャッチする
    """
    logger.error(f"Unexpected Error: {str(exc)}", exc_info=True)
    
    return JSONResponse(
        status_code=500,
        content={
            "status_code": 500,
            "error_code": ErrorCode.INTERNAL_SERVER_ERROR,
            "message": "サーバー内部で予期せぬエラーが発生しました"
        }
    )