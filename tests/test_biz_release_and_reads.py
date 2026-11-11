# tests/test_biz_release_and_reads.py
"""
細かな権限（P.3、2026-11-11）: リリース設定の変更と履歴、SOP の版の記録・承認待ちの読み出し（名前付き）。
- リリース設定だけの変更（リリース担当も）: 送った項目だけ変わる。ほかの項目・空の本文・null は 422
- 履歴はどの API で変えても残る（業務管理者の業務の変更も）。値が変わらなければ残らない。新しい順、名前付き
- 版を返す形に作った人・直した人・依頼した人の名前。記録には誰の草稿か（承認）の名前も
- 承認待ちの一覧: 業務・顧客・ステップの数・依頼した人と作った人の名前。件数
※ 顧客コードの先頭文字は空きがないため "S" を共有する
"""
import uuid
from datetime import datetime, time, timedelta, timezone

import pytest
from pydantic import ValidationError

from tests.client_codes import new_client_code
from src.domain_biz import services as biz
from src.domain_biz.models import ReleaseMode, SOPVersionEventType
from src.domain_biz.schemas import (
    BizMasterCreate, BizMasterUpdate, BizReleaseUpdate, SOPStepTemplateCreate, SOPVersionCreate,
)
from src.domain_client.schemas import ClientCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

JST = timezone(timedelta(hours=9))
TOMORROW = (datetime.now(JST) + timedelta(days=1)).date()


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
    client_repo.create(db, obj_in=ClientCreate(code=code, name="release client"))
    biz_id = biz_master_repo.create(db, obj_in=BizMasterCreate(
        client_code=code, name="release biz", business_seq=1, manager_email=None, description=None,
    )).id

    def staff(name: str, *roles: RoleCode) -> int:
        return staff_services.create_staff(
            db, StaffCreate(login_id="rr-" + uuid.uuid4().hex[:10], name=name, roles=list(roles)),
            actor=None, status=StaffStatus.ACTIVE,
        ).id

    return {"code": code, "biz": biz_id, "releaser": staff("鈴木 リリース", RoleCode.RELEASER),
            "admin": staff("佐藤 管理", RoleCode.ADMIN)}


# ==========================================
# リリース設定
# ==========================================
def test_the_release_body_takes_only_release_fields():
    with pytest.raises(ValidationError):
        BizReleaseUpdate.model_validate({"name": "改名"})
    with pytest.raises(ValidationError):
        BizReleaseUpdate.model_validate({})
    with pytest.raises(ValidationError):
        BizReleaseUpdate.model_validate({"release_time": None})


def test_a_release_change_applies_the_sent_fields_and_is_recorded(db, world):
    before = biz.get_biz(db, world["biz"])
    assert (before.release_mode, before.release_time, before.release_days_before) == (ReleaseMode.MANUAL_APPROVAL, time(3, 0), 0)

    updated = biz.update_release(
        db, world["biz"], BizReleaseUpdate.model_validate(dict(release_mode=ReleaseMode.AUTOMATIC, release_time=time(5, 30))), actor_id=world["releaser"],
    )
    assert (updated.release_mode, updated.release_time, updated.release_days_before) == (ReleaseMode.AUTOMATIC, time(5, 30), 0)
    biz.update_release(db, world["biz"], BizReleaseUpdate.model_validate(dict(release_time=time(5, 30))), actor_id=world["releaser"])  # 変わらない

    (row,) = biz.list_release_changes(db, world["biz"])
    assert (row.changed_by_id, row.changed_by_name) == (world["releaser"], "鈴木 リリース")
    assert (row.before.release_mode, row.before.release_time) == (ReleaseMode.MANUAL_APPROVAL, time(3, 0))
    assert (row.after.release_mode, row.after.release_time, row.after.release_days_before) == (ReleaseMode.AUTOMATIC, time(5, 30), 0)


def test_the_admin_business_update_records_release_changes_too(db, world):
    biz.update_biz(db, world["biz"], BizMasterUpdate.model_validate(dict(name="名前だけ")), actor_id=world["admin"])  # リリース設定は変わらない
    biz.update_biz(db, world["biz"], BizMasterUpdate.model_validate(dict(release_days_before=0)), actor_id=world["admin"])  # 同じ値
    assert biz.list_release_changes(db, world["biz"]) == []

    biz.update_biz(db, world["biz"], BizMasterUpdate.model_validate(dict(description="x", release_days_before=2)), actor_id=world["admin"])
    biz.update_release(db, world["biz"], BizReleaseUpdate.model_validate(dict(release_days_before=1)), actor_id=world["releaser"])
    rows = biz.list_release_changes(db, world["biz"])
    assert [(r.changed_by_name, r.before.release_days_before, r.after.release_days_before) for r in rows] == [
        ("鈴木 リリース", 2, 1), ("佐藤 管理", 0, 2),
    ]


# ==========================================
# 版の名前・記録・承認待ち
# ==========================================
def test_versions_events_and_the_approval_list_carry_names(db, world):
    draft = biz.create_new_sop_version(db, world["biz"], SOPVersionCreate(version_tag="v1.0.0"), actor_id=world["releaser"])
    for title in ("受け取る", "確認する"):
        biz.create_step_template(
            db, draft.id, SOPStepTemplateCreate.model_validate({"title": title, "condition": {"type": "always"}}),
            actor_id=world["admin"],
        )
    biz.submit_draft(db, draft.id, actor_id=world["releaser"])

    shown = biz.describe_version(db, draft)
    assert (shown.created_by_name, shown.updated_by_name, shown.submitted_by_name) == ("鈴木 リリース", "佐藤 管理", "鈴木 リリース")

    (row,) = biz.list_submitted_drafts(db, client_code=world["code"])
    business = biz.get_biz(db, world["biz"])
    assert (row.version_id, row.version_tag, row.business_code, row.biz_name, row.client_code, row.step_count) == (
        draft.id, "v1.0.0", business.business_code, "release biz", world["code"], 2,
    )
    assert (row.submitted_by_name, row.created_by_name) == ("鈴木 リリース", "鈴木 リリース")
    count = biz.count_submitted_drafts(db)
    assert count >= 1

    biz.approve_and_schedule_version(db, world["biz"], draft.id, TOMORROW, actor_id=world["admin"])
    assert biz.list_submitted_drafts(db, client_code=world["code"]) == [] and biz.count_submitted_drafts(db) == count - 1
    events = biz.list_version_events(db, draft.id)
    assert [(e.event_type, e.actor_name) for e in events] == [
        (SOPVersionEventType.CREATED, "鈴木 リリース"), (SOPVersionEventType.SUBMITTED, "鈴木 リリース"),
        (SOPVersionEventType.APPROVED, "佐藤 管理"),
    ]
    assert events[-1].drafted_by_name == "鈴木 リリース" and events[0].drafted_by_name is None
