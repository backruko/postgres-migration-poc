from fastapi import FastAPI
from src.domain_client.router import router as client_router

app = FastAPI(title="Schedule System")

# クライアントのAPIを本体に登録！
app.include_router(client_router)

@app.get("/")
def read_root():
    return {"message": "API is running!"}