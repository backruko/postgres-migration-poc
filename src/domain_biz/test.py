# tests/domain_biz/test_bizmaster.py
import pytest
from sqlalchemy.exc import IntegrityError
from src.infrastructure.database import Base, engine, SessionLocal
from src.domain_biz.models import BizMaster

@pytest.fixture(scope="function")
def db_session():
    # 这里的 engine 已经是被 pytest.ini 替换成 sqlite 内存库的 engine 了！
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)

# 测试用例保持不变...
def test_bizmaster_正常に保存できるか(db_session):
    biz = BizMaster(business_code="C_001", client_code="CLI_A", name="测试")
    db_session.add(biz)
    db_session.commit()
    assert biz.id is not None