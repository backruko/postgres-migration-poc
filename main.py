from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from src.infrastructure.exceptions import DuplicatedRecordError, NotFoundError 

from src.domain_client.router import router as client_router
from src.domain_biz.router import router as biz_router 

app = FastAPI(title="Schedule System")

@app.exception_handler(DuplicatedRecordError)
async def duplicated_record_exception_handler(request: Request, exc: DuplicatedRecordError):
    return JSONResponse(
        status_code=400,
        content={"detail": exc.message},
    )

@app.exception_handler(NotFoundError)
async def not_found_exception_handler(request: Request, exc: NotFoundError):
    return JSONResponse(
        status_code=404,  
        content={"detail": exc.message},
    )

# クライアントのAPIを本体に登録！
app.include_router(client_router)
app.include_router(biz_router) 

@app.get("/")
def read_root():
    return {"message": "API is running!"}