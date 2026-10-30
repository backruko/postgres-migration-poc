import logging

# ログの設定（ファイルとコンソールの両方に出力する）
logging.basicConfig(
    level=logging.INFO,  # INFOレベル以上のログを出力
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("app.log", encoding="utf-8"),  # ファイルに保存
        logging.StreamHandler()                            # コンソールにも表示
    ]
)

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.core.exceptions import BusinessException
from src.infrastructure.exception_handler.handlers import (
    business_exception_handler,
    global_exception_handler
)

from src.domain_client.router import router as client_router
from src.domain_biz.router import router as biz_router 
from src.domain_plan.router import router as plan_router
from src.coordination.router import router as ops_router
from src.domain_staff.router import router as staff_router
from src.coordination import order_sync

# plan の発生枠の変更をオーダーに反映する受け手を登録（同じトランザクションで追従する）
order_sync.register()

app = FastAPI(title="Schedule System")

# --------- CORS（.env の CORS_ORIGINS で指定した場合のみ有効） ---------
from src.infrastructure.config import settings

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# --------- エラーハンドラーの登録 ---------
# BusinessExceptionが起きたら business_exception_handler を呼ぶ
app.add_exception_handler(BusinessException, business_exception_handler)

# その他の想定外のExceptionが起きたら global_exception_handler を呼ぶ
app.add_exception_handler(Exception, global_exception_handler)
# ----------------------------------------

# 動作確認用のテストAPI
from src.core.exceptions import UnauthorizedError
from src.core.error_codes import ErrorCode

@app.get("/test-error")
async def test_error():
    # Service層の代わりにここでエラーを発生させてみる
    raise UnauthorizedError(
        message="パスワードが間違っています(テスト)",
        error_code=ErrorCode.INVALID_CREDENTIALS
    )

# クライアントのAPIを本体に登録！
app.include_router(client_router)
app.include_router(biz_router) 
app.include_router(plan_router)
app.include_router(ops_router)
app.include_router(staff_router)

@app.get("/")
def read_root():
    return {"message": "API is running!"}