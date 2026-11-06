"""
SOP バージョンの承認を取り消して（予定 → 草稿）よいかを、ほかのドメインに確かめます（M2.1）。

草稿に戻すと内容を編集できるようになるため、オーダーが指定（pin）している・リリースで固定した版は戻さない。
biz は ope を知らないため、登録された確認を呼びます。確認は、取り消せない理由（なければ空のリスト）を返します。
登録は coordination 層（master_guards）が起動時に行います。
"""
from typing import Callable

from sqlalchemy.orm import Session

from src.domain_biz.models import SOPVersion

CancelCheck = Callable[[Session, SOPVersion], list[str]]

_checks: list[CancelCheck] = []


def register_cancel_check(check: CancelCheck) -> None:
    """確認を登録します（同じ確認は1回だけ）。"""
    if check not in _checks:
        _checks.append(check)


def reasons_not_to_cancel(db: Session, version: SOPVersion) -> list[str]:
    """登録されたすべての確認の理由をまとめて返します。"""
    reasons: list[str] = []
    for check in _checks:
        reasons.extend(check(db, version))
    return reasons
