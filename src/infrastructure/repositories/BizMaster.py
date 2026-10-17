from src.infrastructure.repositories.base import SoftCRUDBase
from src.domain_biz.models import BizMaster
from src.domain_biz.schemas import BizMasterCreate, BizMasterUpdate

class BizMasterRepository(SoftCRUDBase[BizMaster, BizMasterCreate, BizMasterUpdate]):
    pass

biz_master_repo = BizMasterRepository(BizMaster)