from typing import Any, Generic, TypeVar, cast

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from src.infrastructure.database import Base
from src.core.mixin import SoftDeleteMixin
from src.core.exceptions import DuplicatedRecordError

# モデル・作成用スキーマ・更新用スキーマの型（各ドメインのリポジトリが具体的な型で使う）
ModelType = TypeVar("ModelType", bound=Base)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)


class CRUDBase(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    """
    デフォルトの CRUD (作成・読み取り・更新・削除) メソッドを持つ基底クラス。
    """

    def __init__(self, model: type[ModelType]):
        """
        CRUDオブジェクトを初期化します。

        :param model: SQLAlchemyのモデルクラスを指定します。
        """
        self.model = model

    def get(self, db: Session, id: Any) -> ModelType | None:
        """
        IDを指定して、単一のレコードを取得します。
        """
        stmt = select(self.model).where(self.model.id == id)
        return db.scalars(stmt).first()

    def create(self, db: Session, *, obj_in: CreateSchemaType) -> ModelType:
        """
        新しいレコードをデータベースに作成します。
        """
        obj_in_data = jsonable_encoder(obj_in)
        db_obj = self.model(**obj_in_data)
        db.add(db_obj)
        try:
            db.commit()
            db.refresh(db_obj)
            return db_obj
        except IntegrityError as e:
            db.rollback()
            # 一意の制約の違反は利用者の入力の問題なので、500 ではなく重複のエラー（409）として返す
            raise DuplicatedRecordError(
                message="Create failed: Record already exists in the database."
            ) from e

    def update(
        self,
        db: Session,
        *,
        db_obj: ModelType,
        obj_in: UpdateSchemaType | dict[str, Any]
    ) -> ModelType:
        """
        既存のレコードを更新します。

        :param db_obj: 更新対象の現在のデータベースオブジェクト（取得済みのもの）
        :param obj_in: 更新したいデータ （Pydanticモデル、または辞書）
        """
        obj_data = jsonable_encoder(db_obj)
        # exclude_unset: 送られなかった項目は変えない（部分更新。送らなかった項目が None で上書きされないように）
        update_data = obj_in if isinstance(obj_in, dict) else obj_in.model_dump(exclude_unset=True)

        for field in obj_data:
            if field in update_data:
                setattr(db_obj, field, update_data[field])

        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)

        return db_obj

    def remove(self, db: Session, *, id: int) -> ModelType | None:
        """
        IDを指定して、レコードをデータベースから削除します。
        """
        # db.get ではなく select で取る: db.get はセッションに読み込み済みの行をそのまま返し、
        # 論理削除の自動の絞り込み（database.py）を通らないことがあるため
        stmt = select(self.model).where(self.model.id == id)
        obj = db.scalars(stmt).first()

        if obj:
            db.delete(obj)
            db.commit()

        return obj  # 呼び出し側が、消したものを応答に使えるように


class SoftCRUDBase(CRUDBase[ModelType, CreateSchemaType, UpdateSchemaType]):

    def remove(self, db: Session, *, id: int) -> ModelType | None:
        # 論理削除の自動の絞り込みで、もう削除した行は見つからない（None を返し、二度目の削除は何もしない）
        stmt = select(self.model).where(self.model.id == id)
        obj = db.scalars(stmt).first()

        if not obj:
            return None

        cast(SoftDeleteMixin, obj).soft_delete()

        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def restore(self, db: Session, *, id: int) -> ModelType | None:
        """
        IDを指定して、論理削除されたレコードを復元します。
        """
        # 削除した行を探すので、論理削除の自動の絞り込みを外す
        stmt = (
            select(self.model)
            .where(self.model.id == id)
            .execution_options(include_deleted=True)
        )
        obj = db.scalars(stmt).first()

        # 絞り込みを外したので、削除されていない行も見つかる。それは復元の対象ではない
        if not obj or not cast(SoftDeleteMixin, obj).is_deleted:
            return None

        cast(SoftDeleteMixin, obj).restore()

        try:
            db.commit()
            db.refresh(obj)
            return obj
        except IntegrityError as e:
            db.rollback()
            raise DuplicatedRecordError(
                message="Restore failed: Unique constraint conflict. The original value is already taken by another active record."
            ) from e
