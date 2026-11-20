import logging
import os
from logging.handlers import RotatingFileHandler

# ログの設定（ファイルとコンソールの両方に出力する）。
# ファイルは大きさでローテーションする: LOG_MAX_MB（既定 10）を超えたら app.log → app.log.1 → … と送り、
# LOG_BACKUPS（既定 5）個より古いものは消す（合計で最大 約 (LOG_BACKUPS + 1) × LOG_MAX_MB）。
# ファイル名は LOG_FILE で変えられる（同じフォルダで別のサーバーを動かすときは別の名前にする:
# Windows では、開いている別のプロセスがあるとローテーションの名前の変更に失敗する）。
# ログがもう設定済み（テストで別のスクリプトが先に設定したとき）なら basicConfig は何もしないので、
# ファイルも開かない（開いたまま捨てられるファイルを作らない）。
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,  # INFOレベル以上のログを出力
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            RotatingFileHandler(
                os.environ.get("LOG_FILE", "app.log"),
                maxBytes=int(os.environ.get("LOG_MAX_MB", "10")) * 1024 * 1024,
                backupCount=int(os.environ.get("LOG_BACKUPS", "5")),
                encoding="utf-8",
            ),
            logging.StreamHandler(),  # コンソールにも表示
        ],
    )

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
from src.domain_staff.auth_router import router as auth_router
from src.domain_staff.auth import require_login
from fastapi import Depends
from src.domain_ope.router import router as ope_router
from src.domain_events.router import router as events_router
from src.domain_notification.router import router as notification_router
from src.worker_health.router import health_router, router as worker_health_router
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

# クライアントのAPIを本体に登録！
# すべての API はログインが必要。ログイン・ログアウト・パスワードの変更は auth_router が自分で扱う
LOGIN = [Depends(require_login)]
app.include_router(auth_router)
app.include_router(client_router, dependencies=LOGIN)
app.include_router(biz_router, dependencies=LOGIN)
app.include_router(plan_router, dependencies=LOGIN)
app.include_router(ops_router, dependencies=LOGIN)
app.include_router(staff_router, dependencies=LOGIN)
app.include_router(ope_router, dependencies=LOGIN)
app.include_router(events_router, dependencies=LOGIN)  # 出来事の一覧（読み取りのみ）
app.include_router(notification_router, dependencies=LOGIN)  # 届かなかったアラーム
app.include_router(worker_health_router, dependencies=LOGIN)  # ワーカーの健康のまとめ（並行稼働）
app.include_router(health_router)  # ワーカーが動いているか（ログインなし。外の監視用）
