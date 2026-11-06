# tests/test_sop_versions.py
"""
SOP バージョンの状態（M2.1）のテスト: 保存は草稿 / 承認済みだけで、予定 / 現行 / 過去は日付から決まる。
承認（発効日は明日以降の 0:00 日本時間・ステップの無い草稿は不可・予定は 1 つまで・今有効な版をつなぐ）と、
承認の取消（予定のときだけ・オーダーが指定している版は不可・前の版の終了日時を外す）。

※ テストごとに一意な顧客コードを使ってデータを分離しています。
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from tests.client_codes import new_client_code
from src.core.exceptions import BadRequestError, NotFoundError
from src.domain_biz import services as biz_services
from src.domain_biz.models import SOPStatus, SOPVersion, SopState
from src.domain_biz.schemas import BizMasterCreate, SOPStepTemplateCreate, SOPVersionCreate, SOPVersionResponse
from src.domain_client import services as client_services
from src.domain_client.schemas import ClientCreate
from src.domain_ope import services as ope
from src.domain_ope.models import OpeOrder
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import RRuleTemplateCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import sop_version_repo

JST = ZoneInfo("Asia/Tokyo")


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def biz(db):
    code = new_client_code("X")  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    client_services.create_client(db, ClientCreate(code=code, name="sop version client"))
    return biz_services.create_biz(
        db, BizMasterCreate(client_code=code, name="sop biz", business_seq=None, manager_email=None, description=None)
    )


def today_jst() -> date:
    return datetime.now(timezone.utc).astimezone(JST).date()


def draft_with_step(db, biz_id, tag):
    draft = biz_services.create_new_sop_version(db, biz_id, SOPVersionCreate(version_tag=tag))
    biz_services.create_step_template(db, draft.id, SOPStepTemplateCreate.model_validate({"step_num": 1, "title": "確認"}))
    db.refresh(draft)
    return draft


def current_version(db, biz_id, tag="v1.0.0"):
    """発効済みの版（ORM で直接、10 日前から有効）"""
    v = SOPVersion(
        biz_master_id=biz_id, version_number=99, version_tag=tag, status=SOPStatus.APPROVED,
        effective_at=datetime.combine(today_jst() - timedelta(days=10), time(0), tzinfo=JST),
    )
    db.add(v)
    db.commit()
    return v


def test_states_come_from_the_dates(db, biz):
    draft = biz_services.create_new_sop_version(db, biz.id, SOPVersionCreate(version_tag="v0.1.0"))
    assert draft.state == SopState.DRAFT and SOPVersionResponse.model_validate(draft).state == SopState.DRAFT

    start = datetime(2026, 12, 1, tzinfo=JST)
    v = SOPVersion(biz_master_id=biz.id, version_number=5, version_tag="v5.0.0", status=SOPStatus.APPROVED,
                   effective_at=start, expired_at=start + timedelta(days=30))
    assert v.state_at(start - timedelta(seconds=1)) == SopState.SCHEDULED
    assert v.state_at(start) == SopState.CURRENT
    assert v.state_at(start + timedelta(days=30)) == SopState.PAST


def test_approve_schedules_from_midnight_and_chains_the_current_version(db, biz):
    old = current_version(db, biz.id)
    empty = biz_services.create_new_sop_version(db, biz.id, SOPVersionCreate(version_tag="v1.9.0-empty"))
    with pytest.raises(BadRequestError, match="no steps"):
        biz_services.approve_and_schedule_version(db, biz.id, empty.id, today_jst() + timedelta(days=1))

    new = draft_with_step(db, biz.id, "v2.0.0")
    with pytest.raises(BadRequestError, match="tomorrow or later"):
        biz_services.approve_and_schedule_version(db, biz.id, new.id, today_jst())

    day = today_jst() + timedelta(days=3)
    approved = biz_services.approve_and_schedule_version(db, biz.id, new.id, day)
    start = datetime.combine(day, time(0), tzinfo=JST)
    assert approved.status == SOPStatus.APPROVED and approved.state == SopState.SCHEDULED
    assert approved.effective_at == start and approved.expired_at is None
    db.refresh(old)
    assert old.expired_at == start and old.state == SopState.CURRENT

    # オーダーの予定日時で解決される版: 前日までは旧版、その日の 0:00 から新版
    before = sop_version_repo.get_effective_at(db, biz_master_id=biz.id, at=start - timedelta(seconds=1))
    on_the_day = sop_version_repo.get_effective_at(db, biz_master_id=biz.id, at=start)
    assert before is not None and before.id == old.id
    assert on_the_day is not None and on_the_day.id == approved.id

    # 予定は 1 つまで
    another = draft_with_step(db, biz.id, "v3.0.0")
    with pytest.raises(BadRequestError, match="already scheduled"):
        biz_services.approve_and_schedule_version(db, biz.id, another.id, day + timedelta(days=7))
    # 承認済みは承認し直せない・別の業務の草稿は見つからない
    with pytest.raises(NotFoundError):
        biz_services.approve_and_schedule_version(db, biz.id, approved.id, day + timedelta(days=7))
    with pytest.raises(NotFoundError):
        biz_services.approve_and_schedule_version(db, biz.id + 1_000_000, another.id, day + timedelta(days=7))


def test_first_version_needs_no_predecessor_and_versions_chain_again_after_taking_effect(db, biz):
    first = draft_with_step(db, biz.id, "v1.0.0")
    approved = biz_services.approve_and_schedule_version(db, biz.id, first.id, today_jst() + timedelta(days=1))
    assert approved.state == SopState.SCHEDULED

    # 発効した（日付を過去に動かして再現）あとなら、次の版を承認できる（以前は FUTURE が残り、2 つ目の承認ができなかった）
    approved.effective_at = datetime.combine(today_jst() - timedelta(days=1), time(0), tzinfo=JST)
    db.commit()
    second = draft_with_step(db, biz.id, "v1.1.0")
    nxt = biz_services.approve_and_schedule_version(db, biz.id, second.id, today_jst() + timedelta(days=2))
    db.refresh(approved)
    assert approved.state == SopState.CURRENT and approved.expired_at == nxt.effective_at


def test_cancel_only_while_scheduled_and_unused(db, biz):
    old = current_version(db, biz.id)
    with pytest.raises(BadRequestError, match="already taken effect"):
        biz_services.cancel_future_version(db, biz.id, old.id)

    new = draft_with_step(db, biz.id, "v2.0.0")
    day = today_jst() + timedelta(days=5)
    biz_services.approve_and_schedule_version(db, biz.id, new.id, day)

    # 予定の版をオーダーに指定すると、草稿に戻せない
    template = plan_services.create_template(db, RRuleTemplateCreate.model_validate(dict(
        biz_master_id=biz.id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    )))
    staff = staff_services.create_staff(
        db, StaffCreate(login_id="x-" + uuid.uuid4().hex[:10], name="sop tester", roles=[RoleCode.RELEASER]),
        actor=None, status=StaffStatus.ACTIVE,
    )
    order = db.scalars(select(OpeOrder).where(OpeOrder.template_id == template.id).order_by(OpeOrder.scheduled_start)).first()
    assert order is not None
    ope.pin_sop_version(db, order.id, staff.id, new.id)
    with pytest.raises(BadRequestError, match="1 order"):
        biz_services.cancel_future_version(db, biz.id, new.id)

    ope.unpin_sop_version(db, order.id, staff.id)
    canceled = biz_services.cancel_future_version(db, biz.id, new.id)
    assert canceled.status == SOPStatus.DRAFT and canceled.state == SopState.DRAFT
    assert canceled.effective_at is None and canceled.expired_at is None
    db.refresh(old)
    assert old.expired_at is None and old.state == SopState.CURRENT
    with pytest.raises(NotFoundError):
        biz_services.cancel_future_version(db, biz.id, new.id)  # もう草稿
