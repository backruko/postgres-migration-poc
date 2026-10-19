from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from src.infrastructure.exceptions import (
    BusinessError,
    DuplicatedRecordError,
    NotFoundError,
    ReferencedRecordError,
    BadRequestError
)
from src.domain_client.router import router as client_router
from src.domain_biz.router import router as biz_router 

app = FastAPI(title="Schedule System")

@app.exception_handler(DuplicatedRecordError)
async def duplicated_record_exception_handler(request: Request, exc: DuplicatedRecordError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": str(exc)},
    )

@app.exception_handler(NotFoundError)
async def not_found_exception_handler(request: Request, exc: NotFoundError):
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND, 
        content={"detail": str(exc)}, 
    )

@app.exception_handler(ReferencedRecordError)
async def referenced_record_exception_handler(request: Request, exc: ReferencedRecordError):
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc)},
    )



# @app.exception_handler(BusinessError)
# async def business_error_handler(request: Request, exc: BusinessError):
#     """
#     BusinessErrorを継承したすべての例外をここで横取りしてフォーマットを統一する
#     """
#     # 基本は 400 Bad Request
#     http_status = status.HTTP_400_BAD_REQUEST
    
#     # 404など、特定のステータスコードにしたい例外だけ分岐する
#     if isinstance(exc, NotFoundError):
#         http_status = status.HTTP_404_NOT_FOUND
        
#     # フロントエンドが必ず受け取るJSONの構造（ErrorResponseの形）
#     response_content = {
#         "error_code": exc.code,
#         "detail": exc.message
#     }
    
#     return JSONResponse(
#         status_code=http_status,
#         content=response_content
#     )

# クライアントのAPIを本体に登録！
app.include_router(client_router)
app.include_router(biz_router) 

@app.get("/")
def read_root():
    return {"message": "API is running!"}