# tests/test_template_deadline.py
"""
テンプレートの締切（N.1）のテスト: 決め方（予定の終了 / 開始から / 固定の時刻）と知らせ（何分前）。
- 既定は 予定の終了・知らせなし（0）
- 締切の項目は生成に影響しない（変えても改訂・再生成しない）
- 決め方を変えると使わない項目は空になる。組み合わせの誤り・同じ日の開始より前の固定の時刻は 400、DB でも拒否する

※ テストごとに一意な顧客コードを使ってデータを分離しています。
"""
from datetime import date, time

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from tests.client_codes import new_client_code
from src.core.exceptions import BadRequestError
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_plan import services
from src.domain_plan.models import DeadlineMode, PlanInstance, PlanTemplate, TemplateType
from src.domain_plan.schemas import PlanTemplateUpdate, RRuleTemplateCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def biz_id(db):
    code = new_client_code("F")  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    client_repo.create(db, obj_in=ClientCreate(code=code, name="deadline client"))
    return biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="deadline biz", business_seq=1, manager_email=None, description=None)).id


def create(db, biz_id, **kwargs):
    data = dict(
        biz_master_id=biz_id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    )
    data.update(kwargs)
    return services.create_template(db, RRuleTemplateCreate.model_validate(data))


def live_instance_ids(db, template_id: int) -> list[int]:
    return list(db.scalars(select(PlanInstance.id).where(PlanInstance.template_id == template_id).order_by(PlanInstance.id)))


def test_defaults_are_scheduled_end_and_no_warning(db, biz_id):
    t = create(db, biz_id)
    assert (t.deadline_mode, t.deadline_offset_minutes, t.deadline_time, t.deadline_day_offset, t.warn_before_minutes) == (
        DeadlineMode.END, None, None, None, 0,
    )


def test_deadline_changes_do_not_regenerate(db, biz_id):
    t = create(db, biz_id)
    before = live_instance_ids(db, t.id)
    updated = services.update_template(db, t.id, PlanTemplateUpdate(deadline_mode=DeadlineMode.OFFSET, deadline_offset_minutes=300, warn_before_minutes=30))
    assert (updated.deadline_mode, updated.deadline_offset_minutes, updated.warn_before_minutes) == (DeadlineMode.OFFSET, 300, 30)
    assert updated.revision == t.revision  # 改訂は上がらない
    assert live_instance_ids(db, t.id) == before  # 予定は作り直されない


def test_switching_the_mode_clears_unused_fields(db, biz_id):
    t = create(db, biz_id, deadline_mode=DeadlineMode.OFFSET, deadline_offset_minutes=120)
    # 固定の時刻へ: 開始からの分は空になり、何日後は 0
    fixed = services.update_template(db, t.id, PlanTemplateUpdate(deadline_mode=DeadlineMode.FIXED_TIME, deadline_time=time(15, 0)))
    assert (fixed.deadline_offset_minutes, fixed.deadline_time, fixed.deadline_day_offset) == (None, time(15, 0), 0)
    # 夜をまたぐ: 翌日の 6:00
    overnight = services.update_template(db, t.id, PlanTemplateUpdate(deadline_time=time(6, 0), deadline_day_offset=1))
    assert (overnight.deadline_time, overnight.deadline_day_offset) == (time(6, 0), 1)
    # 予定の終了へ: すべて空
    end = services.update_template(db, t.id, PlanTemplateUpdate(deadline_mode=DeadlineMode.END))
    assert (end.deadline_offset_minutes, end.deadline_time, end.deadline_day_offset) == (None, None, None)


def test_refused_combinations(db, biz_id):
    with pytest.raises(ValidationError, match="requires deadline_offset_minutes"):
        RRuleTemplateCreate.model_validate(dict(
            biz_master_id=biz_id, name="x", template_type="rrule", start_time="09:00", duration_minutes=60,
            anchor_date="2026-01-01", rrule="FREQ=DAILY", deadline_mode="offset",
        ))
    with pytest.raises(ValidationError):
        PlanTemplateUpdate(warn_before_minutes=1441)

    t = create(db, biz_id)
    with pytest.raises(BadRequestError, match="'end' uses no other deadline fields"):
        services.update_template(db, t.id, PlanTemplateUpdate(deadline_offset_minutes=30))
    db.rollback()
    # 固定の時刻が同じ日の開始（9:00）以前 → 翌日以降を指定してもらう
    with pytest.raises(BadRequestError, match="after the start"):
        services.update_template(db, t.id, PlanTemplateUpdate(deadline_mode=DeadlineMode.FIXED_TIME, deadline_time=time(8, 30)))
    db.rollback()
    with pytest.raises(BadRequestError, match="after the start"):
        create(db, biz_id, deadline_mode=DeadlineMode.FIXED_TIME, deadline_time=time(9, 0))
    db.rollback()


def test_db_refuses_inconsistent_deadline_fields(db, biz_id):
    t = create(db, biz_id)
    with pytest.raises(IntegrityError, match="ck_plan_template_deadline_offset"):
        db.execute(text("UPDATE plan_template SET deadline_mode = 'OFFSET' WHERE id = :id"), {"id": t.id})
    db.rollback()
    with pytest.raises(IntegrityError, match="ck_plan_template_deadline_range"):
        db.execute(text("UPDATE plan_template SET warn_before_minutes = -1 WHERE id = :id"), {"id": t.id})
    db.rollback()
    assert db.get(PlanTemplate, t.id).deadline_mode == DeadlineMode.END  # type: ignore[union-attr]
