"""
業務間の依存関係の定義の変更を、受け手（coordination 層）に知らせます（plan の slot_events と同じ考え方）。

biz は ope を知らないため、定義の作成・削除のときに登録された受け手を同じトランザクションで呼び、
受け手がオーダー同士のつながり（ope_dependency）を作る・外す。受け手の例外は定義の変更ごと取り消される。
"""
from typing import Callable, Literal

from sqlalchemy.orm import Session

from src.domain_biz.models import BizDependency

DependencyChange = Literal["created", "deleted"]
DependencyListener = Callable[[Session, DependencyChange, BizDependency], None]

_listeners: list[DependencyListener] = []


def register_dependency_listener(listener: DependencyListener) -> None:
    """受け手を登録します（同じ受け手は1回だけ）。"""
    if listener not in _listeners:
        _listeners.append(listener)


def notify(db: Session, change: DependencyChange, dependency: BizDependency) -> None:
    """定義の作成（flush 後）・削除（削除の前）に呼びます。"""
    for listener in _listeners:
        listener(db, change, dependency)
