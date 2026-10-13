from sqlalchemy import CHAR, String
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database import Base 

class ClientMaster(Base):
    __tablename__ = "client_master" 
    
    code: Mapped[str] = mapped_column(CHAR(4), primary_key=True, comment="顧客コード")
    name: Mapped[str] = mapped_column(String(100), comment="顧客名")