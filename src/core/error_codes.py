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
    FOUR_EYES = "FOUR_EYES"  # ステップの提出者は、そのステップを承認・差し戻しできない
    STAFF_NOT_ACTIVE = "STAFF_NOT_ACTIVE"  # 職員アカウントが利用開始前・利用停止中
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    PASSWORD_EXPIRED = "PASSWORD_EXPIRED"
    PASSWORD_CHANGE_REQUIRED = "PASSWORD_CHANGE_REQUIRED"  # 仮パスワードのまま: 本人が変えるまでほかの操作はできない
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"  # ログインの失敗が続いたため、しばらくログインできない
    
    # リソース系 (404)
    NOT_FOUND = "NOT_FOUND"
    
    # 競合・DB制約系 (409)
    RECORD_ALREADY_EXISTS = "RECORD_ALREADY_EXISTS"
    INSTANCE_ALREADY_VOIDED = "INSTANCE_ALREADY_VOIDED"
    ORDER_IN_PROGRESS = "ORDER_IN_PROGRESS"  # 作業中のオーダーの予定は人が変更・取消できない
    DELETE_FAILED_RECORD_IN_USE = "DELETE_FAILED_RECORD_IN_USE"
    TEMPLATE_DRAFT_PENDING = "TEMPLATE_DRAFT_PENDING"  # 承認待ちの下書きがあるテンプレートは、ほかの変更・下書きができない
    TEMPLATE_CHANGE_DECIDED = "TEMPLATE_CHANGE_DECIDED"  # 承認・差し戻し・取り下げが済んだ下書きは、もう直せない
    TEMPLATE_CHANGED_MEANWHILE = "TEMPLATE_CHANGED_MEANWHILE"  # 下書きを確かめている間にテンプレートが変わった（やり直す）
    
    # サーバー内部エラー (500)
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"