# tests/test_sop_drafting_model.py
"""
細かな権限（P.1、2026-11-11）のデータの形: SOP の版の作成者・承認の依頼、版の記録、リリース設定の変更履歴。
- 承認の依頼は草稿だけ・依頼した人と日時はそろう（CHECK）。差し戻しの記録は理由が要る。変わらない履歴の行は作れない
- 版の記録は古い順。草稿を削除すると記録も消える
- 承認待ちの一覧: 依頼の古い順、顧客・業務で絞れる、削除された業務のものは出ない。件数も同じ
- リリース設定の履歴は新しい順
※ 顧客コードの先頭文字は空きがないため "S" を共有する
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.client_codes import new_client_code
from src.domain_biz import services as biz_services
from src.domain_biz.models import BizMaster, SOPStatus, SOPVersion, SOPVersionEvent, SOPVersionEventType
from src.domain_biz.schemas import BizMasterCreate, SOPVersionCreate
from src.domain_client.schemas import ClientCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import (
    biz_master_repo, biz_release_change_repo, client_repo, sop_version_event_repo,
)

UTC = timezone.utc


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def world(db):
    code = new_client_code("S")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="drafting client"))
    biz = [
        biz_master_repo.create(db, obj_in=BizMasterCreate(
            client_code=code, name=f"drafting {seq}", business_seq=seq, manager_email=None, description=None,
        ))
        for seq in (1, 2)
    ]
    staff = staff_services.create_staff(
        db, StaffCreate(login_id="pd-" + uuid.uuid4().hex[:10], name="drafter", roles=[RoleCode.RELEASER]),
        actor=None, status=StaffStatus.ACTIVE,
    )
    return {"code": code, "biz": [b.id for b in biz], "staff": staff.id}


def draft(db, biz_id: int, tag: str = "v1.0.0") -> SOPVersion:
    return biz_services.create_new_sop_version(db, biz_id, SOPVersionCreate(version_tag=tag))


def submit(db, version: SOPVersion, staff_id: int, at: datetime) -> None:
    version.submitted_by_id, version.submitted_at = staff_id, at
    db.commit()


def test_submission_needs_both_fields_and_only_drafts_can_be_submitted(db, world):
    version = draft(db, world["biz"][0])
    version.submitted_at = datetime.now(UTC)  # 依頼した人が無い
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    version = db.get(SOPVersion, version.id)
    version.status, version.effective_at = SOPStatus.APPROVED, datetime.now(UTC) + timedelta(days=1)
    version.submitted_by_id, version.submitted_at = world["staff"], datetime.now(UTC)  # 承認済みの版は依頼できない
    with pytest.raises(IntegrityError):
        db.commit()


def test_a_rejection_needs_a_reason(db, world):
    version = draft(db, world["biz"][0])
    sop_version_event_repo.add(db, version=version, event_type=SOPVersionEventType.REJECTED, actor_id=world["staff"])
    with pytest.raises(IntegrityError):
        db.commit()


def test_events_are_listed_oldest_first_and_go_with_a_deleted_draft(db, world):
    version = draft(db, world["biz"][0])
    for event_type, note in ((SOPVersionEventType.CREATED, None), (SOPVersionEventType.SUBMITTED, None),
                             (SOPVersionEventType.REJECTED, "手順 2 が足りない")):
        sop_version_event_repo.add(db, version=version, event_type=event_type, actor_id=world["staff"], note=note)
        db.commit()
    events = sop_version_event_repo.list_for_version(db, version_id=version.id)
    assert [(e.event_type, e.note) for e in events] == [
        (SOPVersionEventType.CREATED, None), (SOPVersionEventType.SUBMITTED, None),
        (SOPVersionEventType.REJECTED, "手順 2 が足りない"),
    ]

    biz_services.delete_draft(db, version.id)
    assert db.scalars(select(SOPVersionEvent).where(SOPVersionEvent.sop_version_id == version.id)).all() == []


def test_submitted_drafts_are_listed_oldest_first_and_skip_deleted_businesses(db, world):
    first, second = world["biz"]
    now = datetime.now(UTC)
    newer, older, not_submitted = draft(db, first, "v1.0.0"), draft(db, second, "v1.0.0"), draft(db, first, "v2.0.0")
    submit(db, newer, world["staff"], now)
    submit(db, older, world["staff"], now - timedelta(hours=1))

    count_before = sop_version_event_repo.count_submitted(db)
    mine = [v.id for v in sop_version_event_repo.list_submitted(db, client_code=world["code"])]
    assert mine == [older.id, newer.id] and not_submitted.id not in mine
    assert [v.id for v in sop_version_event_repo.list_submitted(db, biz_master_id=first)] == [newer.id]

    # 業務を削除すると、その業務の依頼は一覧にも件数にも出ない
    biz = db.get(BizMaster, second)
    biz.soft_delete()
    db.commit()
    assert [v.id for v in sop_version_event_repo.list_submitted(db, client_code=world["code"])] == [newer.id]
    assert sop_version_event_repo.count_submitted(db) == count_before - 1


def test_release_changes_are_listed_newest_first_and_must_change_something(db, world):
    biz_id = world["biz"][0]
    values = {"release_mode": "MANUAL_APPROVAL", "release_time": "03:00:00", "release_days_before": 0}
    first = biz_release_change_repo.add(
        db, biz_id=biz_id, changed_by_id=world["staff"], before=values, after={**values, "release_time": "05:30:00"},
    )
    db.commit()
    second = biz_release_change_repo.add(
        db, biz_id=biz_id, changed_by_id=None, before={**values, "release_time": "05:30:00"}, after={**values, "release_days_before": 1},
    )
    db.commit()
    rows = biz_release_change_repo.list_for_biz(db, biz_id=biz_id)
    assert [r.id for r in rows] == [second.id, first.id]
    assert rows[1].after_values["release_time"] == "05:30:00" and rows[0].changed_by_id is None

    biz_release_change_repo.add(db, biz_id=biz_id, changed_by_id=None, before=values, after=dict(values))
    with pytest.raises(IntegrityError):
        db.commit()
