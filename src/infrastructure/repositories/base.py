from typing import Any, Dict, Generic, List, Optional, Type, TypeVar, Union

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy.orm import Session

# SQLAlchemyのベースモデルをインポートします（プロジェクトのパスに合わせて変更してください）
# 例: from src.infrastructure.database import Base
from sqlalchemy.orm import DeclarativeBase 
class Base(DeclarativeBase):
    pass

# ジェネリクス用の型変数を定義します
# ModelType: SQLAlchemyのORMモデル
# CreateSchemaType: Pydanticの作成用スキーマ
# UpdateSchemaType: Pydanticの更新用スキーマ
ModelType = TypeVar("ModelType", bound=Base)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)


class CRUDBase(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    """
    デフォルトの CRUD (作成・読み取り・更新・削除) メソッドを持つ基底クラス。
    """

    def __init__(self, model: Type[ModelType]):
        """
        CRUDオブジェクトを初期化します。

        :param model: SQLAlchemyのモデルクラスを指定します。
        """
        self.model = model

    def get(self, db: Session, id: Any) -> Optional[ModelType]:
        """
        IDを指定して、単一のレコードを取得します。
        """
        return db.query(self.model).filter(self.model.id == id).first()

    def get_multi(
        self, db: Session, *, skip: int = 0, limit: int = 100
    ) -> List[ModelType]:
        """
        複数のレコードをリストとして取得します（ページネーション対応）。

        :param skip: スキップするレコード数（オフセット）
        :param limit: 取得する最大レコード数
        """
        return db.query(self.model).offset(skip).limit(limit).all()

    def create(self, db: Session, *, obj_in: CreateSchemaType) -> ModelType:
        """
        新しいレコードをデータベースに作成します。
        """
        # Pydanticモデルを辞書（dict）に変換します (Pydantic V2対応)
        obj_in_data = jsonable_encoder(obj_in)
        
        # 辞書を展開してSQLAlchemyのモデルインスタンスを作成します
        db_obj = self.model(**obj_in_data)
        
        # データベースに保存します
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        
        return db_obj

    def update(
        self,
        db: Session,
        *,
        db_obj: ModelType,
        obj_in: Union[UpdateSchemaType, Dict[str, Any]]
    ) -> ModelType:
        """
        既存のレコードを更新します。

        :param db_obj: 更新対象の現在のデータベースオブジェクト（取得済みのもの）
        :param obj_in: 更新したいデータ（Pydanticモデル、または辞書）
        """
        # 現在のデータベースオブジェクトを辞書に変換します
        obj_data = jsonable_encoder(db_obj)

        # 入力データが辞書で渡されたか、Pydanticモデルで渡されたかを判定し、抽出します
        if isinstance(obj_in, dict):
            update_data = obj_in
        else:
            # exclude_unset=True は「明示的に設定されていない項目は更新しない(無視する)」ための重要な設定です (Pydantic V2対応)
            update_data = obj_in.model_dump(exclude_unset=True)

        # 現在のデータに対して、新しいデータのキーが存在すれば上書きします
        for field in obj_data:
            if field in update_data:
                setattr(db_obj, field, update_data[field])

        # データベースに変更を保存します
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        
        return db_obj

    def remove(self, db: Session, *, id: int) -> ModelType:
        """
        IDを指定して、レコードをデータベースから削除します。
        """
        # 削除対象のレコードを取得します
        obj = db.query(self.model).get(id)
        
        # データベースから削除します
        db.delete(obj)
        db.commit()
        
        # 削除したオブジェクトの情報を返します（必要に応じてフロントエンドに返すため）
        return obj