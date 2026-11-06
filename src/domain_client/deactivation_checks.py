"""
顧客を無効にしてよいかを、ほかのドメインに確かめます（M1.1）。

client はほかのドメイン（biz など）を知らないため、無効にする前に登録された確認を呼びます。
確認は、無効にできない理由（なければ空のリスト）を返します。登録は coordination 層が起動時に行います。
"""
from typing import Callable

from sqlalchemy.orm import Session

from src.domain_client.models import ClientMaster

DeactivationCheck = Callable[[Session, ClientMaster], list[str]]

_checks: list[DeactivationCheck] = []


def register_deactivation_check(check: DeactivationCheck) -> None:
    """確認を登録します（同じ確認は1回だけ）。"""
    if check not in _checks:
        _checks.append(check)


def reasons_not_to_deactivate(db: Session, client: ClientMaster) -> list[str]:
    """登録されたすべての確認の理由をまとめて返します。"""
    reasons: list[str] = []
    for check in _checks:
        reasons.extend(check(db, client))
    return reasons
