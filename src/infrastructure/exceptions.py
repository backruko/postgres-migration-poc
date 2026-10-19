# src/infrastructure/exceptions.py
class BusinessError(Exception):
    """すべての業務・ビジネス論理例外の基底クラス"""
    def __init__(self, message: str, code: int = 40000):
        super().__init__(message)
        self.message = message
        self.code = code

class BadRequestError(BusinessError):
    """リクエストパラメータ不正、またはビジネスルール違反(例：テンプレート期間の重複・不在)"""
    def __init__(self, message: str = "Bad request"):
        super().__init__(message, code=40001) # 固有のコードを割り当て

class DuplicatedRecordError(BusinessError):
    """一意制約違反(createやupdate時、すでに同じ期間のテンプレートが存在する場合など)"""
    def __init__(self, message: str = "Record already exists"):
        super().__init__(message, code=40002) 

class NotFoundError(BusinessError):
    """データ未検出(インスタンス生成時に適用可能なテンプレートが見つからない場合など)"""
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, code=40003)  

class ReferencedRecordError(BusinessError):
    """外部キー制約(削除時他のテーブルから参照されている場合など)"""
    def __init__(self, message: str = "Delete restricted: Referenced"):
        super().__init__(message, code=40004)  

