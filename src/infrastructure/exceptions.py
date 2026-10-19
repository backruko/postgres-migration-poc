# src/core/exceptions.py

class DuplicatedRecordError(Exception):
    """create or update Unique constraint violation"""
    def __init__(self, message: str = "Record already exists"):
        super().__init__(message)  

class NotFoundError(Exception):
    """update or search not found"""
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message)  