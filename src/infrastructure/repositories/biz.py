from src.infrastructure.repositories.base import SoftCRUDBase
from src.domain_biz.models import BizMaster
from src.domain_client.schemas import BizCreate, BizUpdate

class CRUDBiz(SoftCRUDBase[BizMaster, BizCreate, BizUpdate]):
    def __init__(self):
        # 告诉父类：删除时，请把 code 字段加上 _del_时间戳，防止唯一索引冲突
        super().__init__(model=BizMaster, default_scramble_fields=["code"])

# 实例化单例
biz_repo = CRUDBiz()