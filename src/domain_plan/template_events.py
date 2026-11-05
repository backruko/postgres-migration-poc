"""
テンプレートの締切の設定の変更を、受け手（coordination 層）に知らせます（slot_events・biz の dependency_events と同じ考え方）。

plan は ope を知らないため、締切の決め方・知らせ・タイムゾーンが変わったときに登録された受け手を同じトランザクションで呼び、
受け手がリリース前のオーダーの締切を写し直す（N.3）。受け手の例外はテンプレートの変更ごと取り消される。
"""
from typing import Callable

from sqlalchemy.orm import Session

from src.domain_plan.models import PlanTemplate

TemplateListener = Callable[[Session, PlanTemplate], None]

_listeners: list[TemplateListener] = []


def register_template_listener(listener: TemplateListener) -> None:
    """受け手を登録します（同じ受け手は1回だけ）。"""
    if listener not in _listeners:
        _listeners.append(listener)


def notify_deadline_changed(db: Session, template: PlanTemplate) -> None:
    """締切の設定（またはタイムゾーン）を変えたテンプレートの、変更後（flush 後）に呼びます。"""
    for listener in _listeners:
        listener(db, template)
