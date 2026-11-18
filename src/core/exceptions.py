# src/core/exceptions.py

from typing import Any

from .error_codes import ErrorCode

class BusinessException(Exception):
    """
    すべてのビジネスロジックエラーの基底クラス。
    details: 画面が自分の言葉で理由を書くための、形の決まった情報（任意。message は英語のまま。例: 使われているマーカーの
    テンプレート一覧）。あるときだけ応答の JSON に入る。
    """
    def __init__(self, error_code: str, message: str, status_code: int = 400, details: dict[str, Any] | None = None):
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        self.details = details


class BadRequestError(BusinessException):
    """
    リクエストパラメータ不正、または一般的なビジネスルール違反
    (デフォルト HTTP Status: 400)
    """
    def __init__(self, message: str = "Bad request", error_code: str = ErrorCode.BAD_REQUEST, details: dict[str, Any] | None = None):
        super().__init__(error_code=error_code, message=message, status_code=400, details=details)

class UnauthorizedError(BusinessException):
    """
    認証エラー
    (ログイン失敗、トークン未付与など)
    (デフォルト HTTP Status: 401)
    """
    def __init__(self, message: str = "Authentication failed", error_code: str = ErrorCode.UNAUTHORIZED, details: dict[str, Any] | None = None):
        super().__init__(error_code=error_code, message=message, status_code=401, details=details)

class ForbiddenError(BusinessException):
    """
    権限エラー
    (必要なロールを持っていない、アカウントが利用停止中など)
    (デフォルト HTTP Status: 403)
    """
    def __init__(self, message: str = "Permission denied", error_code: str = ErrorCode.FORBIDDEN, details: dict[str, Any] | None = None):
        super().__init__(error_code=error_code, message=message, status_code=403, details=details)

class NotFoundError(BusinessException):
    """
    データ未検出
    (インスタンス生成時に適用可能なテンプレートが見つからない場合など)
    (デフォルト HTTP Status: 404)
    """
    def __init__(self, message: str = "Resource not found", error_code: str = ErrorCode.NOT_FOUND, details: dict[str, Any] | None = None):
        super().__init__(error_code=error_code, message=message, status_code=404, details=details)


class DuplicatedRecordError(BusinessException):
    """
    一意制約違反
    (createやupdate時、すでに同じ期間のテンプレートが存在する場合など)
    (デフォルト HTTP Status: 409)
    """
    def __init__(self, message: str = "Record already exists", error_code: str = ErrorCode.RECORD_ALREADY_EXISTS, details: dict[str, Any] | None = None):
        super().__init__(error_code=error_code, message=message, status_code=409, details=details)


class RecordReferencedException(BusinessException):
    """
    外部キー制約違反
    (削除時、他のテーブルから参照されていて削除できない場合など)
    (デフォルト HTTP Status: 409)
    """
    def __init__(self, message: str = "Cannot delete record due to existing references.", error_code: str = ErrorCode.DELETE_FAILED_RECORD_IN_USE, details: dict[str, Any] | None = None):
        super().__init__(error_code=error_code, message=message, status_code=409, details=details)
