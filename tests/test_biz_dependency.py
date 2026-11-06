# tests/test_biz_dependency.py
"""
業務間の依存関係の定義（6.1）のテスト: 作成・一覧（上流・下流の両方向）・削除と、保存しない組み合わせ
（自分自身・別の顧客・二重登録・循環・削除された業務）。定義の変更は業務管理者のみ。

※ テストごとに一意な顧客コードを使ってデータを分離しています。
"""

from tests.openapi_auth import needs_login
import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from tests.client_codes import new_client_code
from src.core.exceptions import BadRequestError, DuplicatedRecordError, NotFoundError
from src.domain_biz import services as biz_services
from src.domain_biz.models import BizDependency, DependencyKind, DependencyMatchRule
from src.domain_biz.schemas import BizDependencyCreate, BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

SHEET, DATE = DependencyMatchRule.SAME_SHEET_EVENT, DependencyMatchRule.SAME_OCCURRENCE_DATE


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def client_with_businesses(db, count: int) -> list[int]:
    code = new_client_code("G")  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    client_repo.create(db, obj_in=ClientCreate(code=code, name="dependency client"))
    return [
        biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name=f"biz {n}", business_seq=n, manager_email=None, description=None)).id
        for n in range(1, count + 1)
    ]


def link(db, upstream: int, downstream: int, rule=SHEET, kind=DependencyKind.HARD):
    return biz_services.create_dependency(db, downstream, BizDependencyCreate(upstream_biz_id=upstream, match_rule=rule, kind=kind))


def test_create_list_and_delete(db):
    receive, process, report = client_with_businesses(db, 3)
    dep = link(db, receive, process)
    assert (dep.upstream.id, dep.downstream.id, dep.match_rule, dep.kind) == (receive, process, SHEET, DependencyKind.HARD)
    info = link(db, process, report, rule=DATE, kind=DependencyKind.INFORMATIONAL)

    # 一覧は上流・下流の両方向（process は両方に出る）
    assert [d.id for d in biz_services.list_dependencies(db, process)] == [dep.id, info.id]
    assert [d.id for d in biz_services.list_dependencies(db, receive)] == [dep.id]

    deleted = biz_services.delete_dependency(db, dep.id)
    assert deleted.id == dep.id and db.get(BizDependency, dep.id) is None
    assert [d.id for d in biz_services.list_dependencies(db, process)] == [info.id]
    with pytest.raises(NotFoundError):
        biz_services.delete_dependency(db, dep.id)


def test_refused_combinations(db):
    a, b, c = client_with_businesses(db, 3)
    (other,) = client_with_businesses(db, 1)
    link(db, a, b)
    link(db, b, c)

    with pytest.raises(BadRequestError, match="itself"):
        link(db, a, a)
    with pytest.raises(BadRequestError, match="same client"):
        link(db, other, a)
    with pytest.raises(DuplicatedRecordError):
        link(db, a, b, rule=DATE)  # 同じ組み合わせは、ルールが違っても1件
    with pytest.raises(BadRequestError, match="cycle"):
        link(db, c, a)  # a → b → c → a
    with pytest.raises(BadRequestError, match="cycle"):
        link(db, b, a)  # 逆向きの直接の循環
    link(db, a, c)  # 循環しない近道は作れる

    # 削除された業務とは結べない（定義の残る業務は delete_biz では消せない（M1.1）ので、リポジトリで直接論理削除する）
    with pytest.raises(BadRequestError, match="dependency definition"):
        biz_services.delete_biz(db, c)
    biz_master_repo.remove(db, id=c)
    with pytest.raises(NotFoundError):
        link(db, c, b)


def test_db_refuses_self_and_duplicate_pairs(db):
    a, b = client_with_businesses(db, 2)
    db.add(BizDependency(upstream_biz_id=a, downstream_biz_id=a, match_rule=SHEET))
    with pytest.raises(IntegrityError, match="ck_biz_dependency_not_self"):
        db.flush()
    db.rollback()
    db.add_all([
        BizDependency(upstream_biz_id=a, downstream_biz_id=b, match_rule=SHEET),
        BizDependency(upstream_biz_id=a, downstream_biz_id=b, match_rule=DATE),
    ])
    with pytest.raises(IntegrityError, match="uq_biz_dependency_pair"):
        db.flush()
    db.rollback()


def test_latest_before_needs_a_lookback(db):
    monthly, daily = client_with_businesses(db, 2)
    dep = biz_services.create_dependency(db, daily, BizDependencyCreate(
        upstream_biz_id=monthly, match_rule=DependencyMatchRule.LATEST_BEFORE, lookback_days=35,
    ))
    assert (dep.match_rule, dep.lookback_days) == (DependencyMatchRule.LATEST_BEFORE, 35)
    assert biz_services.list_dependencies(db, daily)[0].lookback_days == 35
    # 他のルールでは lookback_days は空
    up, down = client_with_businesses(db, 2)
    assert link(db, up, down, rule=DATE).lookback_days is None

    # 入力: 直前の上流には必須、他のルールには指定できない、1〜366 日
    with pytest.raises(ValidationError, match="required for latest_before"):
        BizDependencyCreate(upstream_biz_id=monthly, match_rule=DependencyMatchRule.LATEST_BEFORE)
    with pytest.raises(ValidationError, match="only used with latest_before"):
        BizDependencyCreate(upstream_biz_id=monthly, match_rule=DATE, lookback_days=7)
    with pytest.raises(ValidationError):
        BizDependencyCreate(upstream_biz_id=monthly, match_rule=DependencyMatchRule.LATEST_BEFORE, lookback_days=0)

    # DB でも同じ組み合わせを拒否する
    a, b = client_with_businesses(db, 2)
    db.add(BizDependency(upstream_biz_id=a, downstream_biz_id=b, match_rule=DependencyMatchRule.LATEST_BEFORE))
    with pytest.raises(IntegrityError, match="ck_biz_dependency_lookback_rule"):
        db.flush()
    db.rollback()
    db.add(BizDependency(upstream_biz_id=a, downstream_biz_id=b, match_rule=SHEET, lookback_days=3))
    with pytest.raises(IntegrityError, match="ck_biz_dependency_lookback_rule"):
        db.flush()
    db.rollback()


def test_only_admins_change_definitions():
    from main import app

    paths = app.openapi()["paths"]
    # すべてログインが必要（LG.2）。変更（作成・削除）は業務管理者（ロールは test_plan_settings_guard.py で確認）
    assert needs_login(paths["/biz/{biz_id}/dependencies"]["post"])
    assert needs_login(paths["/biz/dependencies/{dependency_id}"]["delete"])
    assert needs_login(paths["/biz/{biz_id}/dependencies"]["get"])
