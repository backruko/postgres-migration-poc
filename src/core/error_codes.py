# src/core/error_codes.py
from enum import Enum

class ErrorCode(str, Enum):
    """
    フロントエンドとバックエンドで共有するエラーコードの定数群。
    意味が直感的にわかるように、すべて大文字の文字列（スネークケース）で定義します。
    """
    
    # 汎用エラー (400)
    BAD_REQUEST = "BAD_REQUEST"
    IMMUTABLE_RECORD = "IMMUTABLE_RECORD"
    
    # 認証・権限系 (401, 403)
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    STAFF_NOT_ACTIVE = "STAFF_NOT_ACTIVE"  # 職員アカウントが利用開始前・利用停止中
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    PASSWORD_EXPIRED = "PASSWORD_EXPIRED"
    
    # リソース系 (404)
    NOT_FOUND = "NOT_FOUND"
    
    # 競合・DB制約系 (409)
    RECORD_ALREADY_EXISTS = "RECORD_ALREADY_EXISTS"
    INSTANCE_ALREADY_VOIDED = "INSTANCE_ALREADY_VOIDED"
    DELETE_FAILED_RECORD_IN_USE = "DELETE_FAILED_RECORD_IN_USE"
    
    # サーバー内部エラー (500)
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"