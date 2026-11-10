"""
試し実行（dry run）: 本物の処理をそのまま動かし、結果を読み取ってから、必ず取り消す。

「保存したら何が起きるか」を先に見せる（テンプレートの変更の確認など）ために使います。本物の処理と別の計算を持たないので、
見せた内容と、実際に保存したときの結果が食い違いません。

    with dry_run(db):
        ...            # コミットしない処理を呼ぶ（flush まで）
        result = ...   # ORM のオブジェクトではなく、値に写しておく（抜けるとロールバックで読めなくなる）

- 抜けるとき（正常でも例外でも）にロールバックする
- 中でコミットしようとすると RuntimeError（コミットする関数を間違って呼んでも、何も保存されない）
"""
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import event
from sqlalchemy.orm import Session

_KEY = "dry_run"


@contextmanager
def dry_run(db: Session) -> Iterator[None]:
    db.info[_KEY] = True
    try:
        yield
    finally:
        try:
            db.rollback()
        finally:
            db.info.pop(_KEY, None)


@event.listens_for(Session, "before_commit")
def _refuse_commit(session: Session) -> None:
    if session.info.get(_KEY):
        raise RuntimeError("commit() was called inside dry_run(); a dry run must not save anything.")
