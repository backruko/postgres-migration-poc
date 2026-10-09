import pytest
# 注意：要把下面的路径换成你项目里 BizMaster 真正的位置
from src.domain_biz.models import BizMaster 

def test_bizmaster_业务代码和顾客代码不匹配时会报错():
    # 1. 先建一个空的空壳
    biz = BizMaster(name="测试")
    
    # 2. 先赋值 client_code
    biz.client_code = "CLI_A"
    
    # 3. 再赋值错误的 business_code，期待它抛出 ValueError
    with pytest.raises(ValueError, match="プレフィックスは顧客コード"):
        biz.business_code = "C_001"