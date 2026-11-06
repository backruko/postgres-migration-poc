"""
業務を削除してよいかを、ほかのドメインに確かめます（M1.1）。

biz は plan / ope を知らないため、削除する前に登録された確認を呼びます（テンプレート・オーダーが残っていないか）。
確認は、削除できない理由（なければ空のリスト）を返します。登録は coordination 層が起動時に行います。
"""
from typing import Callable

from sqlalchemy.orm import Session

from src.domain_biz.models import BizMaster

DeleteCheck = Callable[[Session, BizMaster], list[str]]

_checks: list[DeleteCheck] = []


def register_delete_check(check: DeleteCheck) -> None:
    """確認を登録します（同じ確認は1回だけ）。"""
    if check not in _checks:
        _checks.append(check)


def reasons_not_to_delete(db: Session, biz: BizMaster) -> list[str]:
    """登録されたすべての確認の理由をまとめて返します。"""
    reasons: list[str] = []
    for check in _checks:
        reasons.extend(check(db, biz))
    return reasons
