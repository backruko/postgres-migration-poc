# src/infrastructure/repositories/__init__.py
from .BizMaster import biz_master_repo
from .Client import client_repo
# from .staff import staff_repo

# 可选：限制 import * 时暴露的变量
__all__ = ["client_repo", "biz_master_repo",]