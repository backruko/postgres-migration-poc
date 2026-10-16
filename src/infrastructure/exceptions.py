# src/core/exceptions.py

class DuplicatedRecordError(Exception):
    """create or update  Unique constraint violation"""
    def __init__(self, message: str = "Record already exists"):
        self.message = message
        super().__init__(self.message)

class NotFoundError(Exception):
    """update or serch not found"""
    def __init__(self, message: str = "Resource not found"):
        self.message = message
        super().__init__(self.message)