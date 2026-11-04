# tests/test_ope_dependency.py
"""
オーダー同士の依存関係（6.2: テーブルの制約のみ。つながりを作る処理は 6.3 以降）。
- 有効なつながりは下流・上流の組み合わせごとに1件。上流のオーダーが無い行（待ち先なし）は下流・上流の業務ごとに1件
- 外れた行（removed_at）は一意性の対象外で、理由が必須
- 突き合わせの規則ごとのキー（シート日付 / ルール上の日付）が必須
- 定義を削除すると、つながりは残り biz_dependency_id が NULL になる

※ テストごとに一意な顧客コードを使ってデータを分離しています（制約違反の確認は各テストの中でロールバックする）。
"""
import uuid
from datetime import date, datetime, time, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from src.domain_biz.models import BizDependency, DependencyKind, DependencyMatchRule
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope.models import OpeDependency, OpeOrder
from src.domain_plan import services as plan_services
from src.domain_plan import slot_events
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import RRuleTemplateCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

HARD, SHEET, DATE = DependencyKind.HARD, DependencyMatchRule.SAME_SHEET_EVENT, DependencyMatchRule.SAME_OCCURRENCE_DATE


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def setup(db, monkeypatch):
    """
    同じ顧客の業務2つ（上流・下流）と定義、それぞれの毎日の予定からオーダーを3件ずつ（直接作る）。
    ここではテーブルの制約だけを確かめるため、plan → オーダーの自動追従（4.3）を止める。
    """
    monkeypatch.setattr(slot_events, "_listeners", [])
    code = "L" + uuid.uuid4().hex[:3].upper()  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    client_repo.create(db, obj_in=ClientCreate(code=code, name="link client"))
    orders = {}
    biz_ids = {}
    for role, seq in (("up", 1), ("down", 2)):
        biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name=role, business_seq=seq, manager_email=None, description=None))
        biz_ids[role] = biz.id
        template = plan_services.create_template(db, RRuleTemplateCreate(
            biz_master_id=biz.id, name=role, template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
            start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
        ))
        live = sorted((i for i in plan_services.get_instances(db, template.id, limit=1000) if not i.voided), key=lambda i: i.scheduled_start)
        orders[role] = []
        for inst in live[:3]:
            order = OpeOrder(
                template_id=template.id, occurrence_date=inst.occurrence_date, current_instance_id=inst.id,
                biz_master_id=biz.id, client_code=code, scheduled_start=inst.scheduled_start, scheduled_end=inst.scheduled_end,
            )
            db.add(order)
            orders[role].append(order)
    definition = BizDependency(upstream_biz_id=biz_ids["up"], downstream_biz_id=biz_ids["down"], match_rule=DATE, kind=HARD)
    db.add(definition)
    db.commit()
    return {"up": [o.id for o in orders["up"]], "down": [o.id for o in orders["down"]], "up_biz": biz_ids["up"],
            "definition_id": definition.id, "day": orders["down"][0].occurrence_date}


def link(setup, down: int, up: int | None, **overrides) -> OpeDependency:
    values = dict(
        downstream_order_id=down, upstream_order_id=up, biz_dependency_id=setup["definition_id"],
        upstream_biz_id=setup["up_biz"], kind=HARD, match_rule=DATE, match_date=setup["day"],
    )
    values.update(overrides)
    return OpeDependency(**values)


def assert_rejected(db, obj, match: str):
    db.add(obj)
    with pytest.raises(IntegrityError, match=match):
        db.flush()
    db.rollback()


def test_one_active_link_per_pair_and_one_waiting_row_per_upstream_business(db, setup):
    down, up = setup["down"][0], setup["up"][0]
    db.add_all([link(setup, down, up), link(setup, setup["down"][1], None)])
    db.commit()

    assert_rejected(db, link(setup, down, up), "uq_ope_dependency_pair")
    assert_rejected(db, link(setup, setup["down"][1], None), "uq_ope_dependency_waiting")
    # 同じ下流が、別の上流のオーダーも待てる（上流の業務にその日の予定が2件ある場合など）
    db.add(link(setup, down, setup["up"][1]))
    db.commit()

    # 外れた行は一意性の対象外（同じ組み合わせを作り直せる）
    removed = link(setup, down, setup["up"][2], removed_at=datetime.now(timezone.utc), removed_reason="定義の削除")
    db.add_all([removed, link(setup, down, setup["up"][2])])
    db.commit()


def test_key_self_and_removed_checks(db, setup):
    down = setup["down"][2]
    assert_rejected(db, link(setup, down, down), "ck_ope_dependency_not_self")
    assert_rejected(db, link(setup, down, None, match_rule=SHEET), "ck_ope_dependency_sheet_key")
    assert_rejected(db, link(setup, down, None, match_date=None), "ck_ope_dependency_date_key")
    assert_rejected(db, link(setup, down, None, removed_at=datetime.now(timezone.utc)), "ck_ope_dependency_removed")
    assert_rejected(db, link(setup, down, None, removed_reason="x"), "ck_ope_dependency_removed")
    db.add(link(setup, down, None, match_rule=SHEET, match_date=None, match_sheet_event_id=901))
    db.commit()


def test_deleting_the_definition_keeps_the_links(db, setup):
    row = link(setup, setup["down"][0], setup["up"][0])
    db.add(row)
    db.commit()
    db.delete(db.get(BizDependency, setup["definition_id"]))
    db.commit()
    db.refresh(row)
    assert row.biz_dependency_id is None and row.kind == HARD and row.match_rule == DATE

    # つながりのあるオーダーは物理削除できない
    db.delete(db.get(OpeOrder, setup["up"][0]))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
