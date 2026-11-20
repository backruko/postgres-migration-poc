# tests/test_sheet_editing.py
"""
年間計画シートの編集。
- シートの名前・期間: 名前は同じ顧客で重複不可。期間を延ばすとルールのマーカーが新しい期間まで作り、参照する
  テンプレートの予定も直る。縮めるのは、外に残る日付（取り込み・人の日付）が無いときだけ（ルールの未来の日付は消える）
- マーカーの名前の変更: 参照するテンプレート（削除済みも）が同じ名前に変わり、予定は動かない。業務管理者の操作は
  有効なテンプレートの履歴に残る。重複・承認待ちの下書き（テンプレートにある / 本文が指す）は 409
- マーカーの削除: テンプレート（有効・削除済み）か承認待ちの下書きが参照すれば 409（名前を挙げる）。使われていなければ日付ごと消える
※ 顧客コードの先頭文字は空きがないため "N" を共有する
"""
import uuid
from datetime import time, timedelta, timezone

import pytest
from sqlalchemy import select

from tests.client_codes import new_client_code
from src.core.clock import local_date_of, utc_now
from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, BusinessException
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_plan import services as plan
from src.domain_plan import template_changes
from src.domain_plan.models import (
    PlanInstance, PlanTemplate, PlanTemplateChange, ReferenceSheetMarker, TemplateChangeKind, TemplateType,
)
from src.domain_plan.schemas import (
    MarkerRule, PlanTemplateUpdate, ReferenceSheetCreate, ReferenceSheetEventsCreate, ReferenceSheetMarkerCreate,
    ReferenceSheetMarkerUpdate, ReferenceSheetTemplateCreate, ReferenceSheetUpdate, TemplateCreateDraft, TemplateUpdateDraft,
)
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

JST = timezone(timedelta(hours=9))

# シートの期間と作る予定の範囲を、同じ「今日」から決めるため
pytestmark = pytest.mark.usefixtures("frozen_clock")




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
    code = new_client_code("N")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="sheet editing"))
    biz_id = biz_master_repo.create(db, obj_in=BizMasterCreate(
        client_code=code, name="sheet biz", business_seq=1, manager_email=None, description=None,
    )).id
    today = local_date_of(utc_now())
    sheet = plan.create_sheet(db, ReferenceSheetCreate(
        client_code=code, name="計画 " + uuid.uuid4().hex[:4], period_start=today - timedelta(days=30),
        period_end=today + timedelta(days=120),
    ))

    def staff(*roles: RoleCode) -> int:
        return staff_services.create_staff(
            db, StaffCreate(login_id="se-" + uuid.uuid4().hex[:10], name="sheet editor", roles=list(roles)),
            actor=None, status=StaffStatus.ACTIVE,
        ).id

    return {"code": code, "biz": biz_id, "sheet": sheet.id, "today": today,
            "admin": staff(RoleCode.ADMIN), "releaser": staff(RoleCode.RELEASER)}


def hand_marker(db, w, name: str, days: list[int]) -> None:
    """日付を手で選んだマーカー（ルールなし）"""
    plan.add_marker(db, w["sheet"], ReferenceSheetMarkerCreate(marker=name))
    plan.add_sheet_events(db, w["sheet"], name, ReferenceSheetEventsCreate(dates=[w["today"] + timedelta(days=d) for d in days]))


def rule_marker(db, w, name: str) -> None:
    """毎週月曜日（ルールで日付を作るマーカー）"""
    plan.add_marker(db, w["sheet"], ReferenceSheetMarkerCreate(
        marker=name, rule=MarkerRule(rrule="FREQ=WEEKLY;BYDAY=MO", anchor_date=w["today"] - timedelta(days=30)),
    ))


def body(w, marker: str, name: str = "本処理") -> ReferenceSheetTemplateCreate:
    return ReferenceSheetTemplateCreate(
        biz_master_id=w["biz"], name=name + " " + uuid.uuid4().hex[:4], template_type=TemplateType.REFERENCE_SHEET_BASED,
        start_time=time(9, 0), duration_minutes=60, reference_sheet_id=w["sheet"], reference_event_marker=marker,
        offset_days=0, offset_minutes=0,
    )


def sheet_template(db, w, marker: str) -> int:
    return plan.create_template(db, body(w, marker)).id


def dates_of(db, w, marker: str) -> list:
    db.expire_all()
    m = db.scalars(select(ReferenceSheetMarker).where(
        ReferenceSheetMarker.sheet_id == w["sheet"], ReferenceSheetMarker.marker == marker,
    )).one()
    return sorted(e.event_date for e in m.events if not e.is_cancelled)


def live_instances(db, template_id: int) -> set[int]:
    db.expire_all()
    return set(db.scalars(select(PlanInstance.id).where(PlanInstance.template_id == template_id)))


def code_of(exc: pytest.ExceptionInfo) -> str:
    assert isinstance(exc.value, BusinessException)
    return exc.value.error_code


# ==========================================
# シートの名前・期間
# ==========================================
def test_sheet_name_is_unique_per_client(db, world):
    other = plan.create_sheet(db, ReferenceSheetCreate(
        client_code=world["code"], name="別のシート", period_start=world["today"], period_end=world["today"] + timedelta(days=10),
    ))
    renamed = plan.update_sheet(db, world["sheet"], ReferenceSheetUpdate(name="改名したシート"))
    assert renamed.name == "改名したシート"
    with pytest.raises(BusinessException) as exc:
        plan.update_sheet(db, other.id, ReferenceSheetUpdate(name="改名したシート"))
    assert exc.value.status_code == 409


def test_extending_the_period_generates_rule_dates_and_moves_templates(db, world):
    rule_marker(db, world, "週次")
    template_id = sheet_template(db, world, "週次")
    end = world["today"] + timedelta(days=120)
    assert max(dates_of(db, world, "週次")) <= end

    plan.update_sheet(db, world["sheet"], ReferenceSheetUpdate(period_end=end + timedelta(days=60)))
    extended = dates_of(db, world, "週次")
    assert max(extended) > end + timedelta(days=50)  # 新しい月まで作られた
    template = plan._get_locked_template(db, template_id)
    plan._ensure_materialized(db, template, utc_now() + timedelta(days=200))
    db.commit()
    starts = {i.scheduled_start.astimezone(JST).date() for i in db.scalars(select(PlanInstance).where(PlanInstance.template_id == template_id))}
    assert max(extended) in starts


def test_shortening_needs_no_dates_left_outside(db, world):
    rule_marker(db, world, "週次")
    hand_marker(db, world, "受領", [100])
    end = world["today"] + timedelta(days=120)
    with pytest.raises(BadRequestError):
        plan.update_sheet(db, world["sheet"], ReferenceSheetUpdate(period_end=world["today"] + timedelta(days=60)))
    db.rollback()
    # 手の日付の後ろまでなら縮められる。ルールの未来の日付は新しい期間の外の分だけ消える
    new_end = world["today"] + timedelta(days=101)
    plan.update_sheet(db, world["sheet"], ReferenceSheetUpdate(period_end=new_end))
    assert max(dates_of(db, world, "週次")) <= new_end < end
    assert dates_of(db, world, "受領") == [world["today"] + timedelta(days=100)]


# ==========================================
# マーカーの名前の変更
# ==========================================
def test_renaming_a_marker_carries_its_templates_without_moving_plans(db, world):
    hand_marker(db, world, "受領", [10, 20, 30])
    live = sheet_template(db, world, "受領")
    deleted = sheet_template(db, world, "受領")
    plan.delete_template(db, deleted)
    before = live_instances(db, live)

    plan.update_marker(db, world["sheet"], "受領", ReferenceSheetMarkerUpdate(name="データ受領"), actor_id=world["admin"])

    db.expire_all()
    assert db.get(PlanTemplate, live).reference_event_marker == "データ受領"
    assert db.scalars(select(PlanTemplate.reference_event_marker).where(PlanTemplate.id == deleted)
                      .execution_options(include_deleted=True)).one() == "データ受領"
    assert live_instances(db, live) == before  # 日付は同じ: 予定は作り直さない
    assert dates_of(db, world, "データ受領") == [world["today"] + timedelta(days=d) for d in (10, 20, 30)]
    (row,) = db.scalars(select(PlanTemplateChange).where(PlanTemplateChange.template_id == live, PlanTemplateChange.is_direct.is_(True),
                                                         PlanTemplateChange.kind == TemplateChangeKind.UPDATE)).all()
    assert row.changes == {"reference_event_marker": "データ受領"} and row.before_values["reference_event_marker"] == "受領"
    assert row.decided_by_id == world["admin"]


def test_renaming_is_refused_for_a_taken_name_or_waiting_drafts(db, world):
    hand_marker(db, world, "受領", [10])
    hand_marker(db, world, "発送", [11])
    with pytest.raises(BusinessException) as exc:
        plan.update_marker(db, world["sheet"], "受領", ReferenceSheetMarkerUpdate(name="発送"))
    assert exc.value.status_code == 409
    db.rollback()

    # 下書きの本文がマーカーを指す（新しいテンプレートの下書き）
    create = template_changes.create_draft(db, TemplateCreateDraft(kind=TemplateChangeKind.CREATE, template=body(world, "受領")), world["releaser"])
    with pytest.raises(BusinessException) as exc:
        plan.update_marker(db, world["sheet"], "受領", ReferenceSheetMarkerUpdate(name="受領2"))
    assert code_of(exc) == ErrorCode.TEMPLATE_DRAFT_PENDING
    db.rollback()
    template_changes.withdraw_draft(db, create.id, world["releaser"])

    # 参照しているテンプレートに承認待ちの下書きがある
    template_id = sheet_template(db, world, "発送")
    update = template_changes.create_draft(db, TemplateUpdateDraft(
        kind=TemplateChangeKind.UPDATE, template_id=template_id, changes=PlanTemplateUpdate.model_validate({"duration_minutes": 90}),
    ), world["releaser"])
    with pytest.raises(BusinessException) as exc:
        plan.update_marker(db, world["sheet"], "発送", ReferenceSheetMarkerUpdate(name="発送2"))
    assert code_of(exc) == ErrorCode.TEMPLATE_DRAFT_PENDING
    db.rollback()
    template_changes.withdraw_draft(db, update.id, world["releaser"])
    plan.update_marker(db, world["sheet"], "発送", ReferenceSheetMarkerUpdate(name="発送2"))
    db.expire_all()
    assert db.get(PlanTemplate, template_id).reference_event_marker == "発送2"


# ==========================================
# マーカーの削除
# ==========================================
def test_a_used_marker_cannot_be_deleted_and_an_unused_one_goes_with_its_dates(db, world):
    hand_marker(db, world, "受領", [10])
    template_id = sheet_template(db, world, "受領")
    with pytest.raises(BusinessException) as exc:
        plan.delete_marker(db, world["sheet"], "受領")
    assert code_of(exc) == ErrorCode.DELETE_FAILED_RECORD_IN_USE and f"#{template_id}" in exc.value.message
    # 画面が日本語で書くための理由: 参照しているテンプレートの ID と名前
    name = db.get(PlanTemplate, template_id).name
    assert exc.value.details == {
        "marker": "受領", "templates": [{"id": template_id, "name": name}], "deleted_templates": [], "draft_change_ids": [],
    }
    db.rollback()

    plan.delete_template(db, template_id)  # 削除済みでも復元できるため、まだ消せない
    with pytest.raises(BusinessException) as exc:
        plan.delete_marker(db, world["sheet"], "受領")
    assert "deleted template" in exc.value.message
    assert exc.value.details is not None
    assert exc.value.details["templates"] == [] and exc.value.details["deleted_templates"] == [{"id": template_id, "name": name}]
    db.rollback()

    hand_marker(db, world, "未使用", [12, 13])
    draft = template_changes.create_draft(db, TemplateCreateDraft(kind=TemplateChangeKind.CREATE, template=body(world, "未使用")), world["releaser"])
    with pytest.raises(BusinessException) as exc:
        plan.delete_marker(db, world["sheet"], "未使用")
    assert "waiting for approval" in exc.value.message
    assert exc.value.details is not None and exc.value.details["draft_change_ids"] == [draft.id]
    db.rollback()
    template_changes.withdraw_draft(db, draft.id, world["releaser"])

    sheet = plan.delete_marker(db, world["sheet"], "未使用")
    assert [m.marker for m in sheet.markers] == ["受領"]


# ==========================================
# まとめての日付の変更
# ==========================================
from src.domain_plan.models import PlanInstanceVoid, ReferenceSheetEvent  # noqa: E402
from src.domain_plan.schemas import ReferenceSheetEventsBatch  # noqa: E402
from tests.test_order_sync_batching import StatementCounter  # noqa: E402


def events_of(db, w, marker: str) -> dict:
    db.expire_all()
    m = db.scalars(select(ReferenceSheetMarker).where(
        ReferenceSheetMarker.sheet_id == w["sheet"], ReferenceSheetMarker.marker == marker,
    )).one()
    return {e.event_date: e for e in m.events}


def test_a_batch_follows_the_single_date_rules(db, world):
    plan.import_sheet(db, plan.ReferenceSheetImport.model_validate({
        "client_code": world["code"], "sheet_name": db.get(plan.ReferenceSheet, world["sheet"]).name,
        "period_start": (world["today"] - timedelta(days=30)).isoformat(), "period_end": (world["today"] + timedelta(days=120)).isoformat(),
        "markers": [{"marker": "受領"}], "events": [{"marker": "受領", "event_date": (world["today"] + timedelta(days=d)).isoformat()} for d in (5, 6)],
    }))
    plan.add_sheet_events(db, world["sheet"], "受領", ReferenceSheetEventsCreate(dates=[world["today"] + timedelta(days=7)]))
    ev = events_of(db, world, "受領")
    imported, added = ev[world["today"] + timedelta(days=5)], ev[world["today"] + timedelta(days=7)]
    d = lambda n: world["today"] + timedelta(days=n)  # noqa: E731

    plan.change_sheet_events(db, world["sheet"], "受領", ReferenceSheetEventsBatch(add=[d(8), d(9)], remove=[imported.id, added.id]))
    ev = events_of(db, world, "受領")
    assert ev[d(5)].is_cancelled and ev[d(5)].original_date == d(5)  # 取り込んだ日付 → 取消の記録
    assert d(7) not in ev  # 人が足した日付 → 消える
    assert {d(6), d(8), d(9)} <= {k for k, e in ev.items() if not e.is_cancelled}

    for bad in (
        ReferenceSheetEventsBatch(add=[d(6)], remove=[ev[d(6)].id]),  # 同じ日付を足しつつ消す
        ReferenceSheetEventsBatch(remove=[999_999_999]),  # このマーカーの日付ではない
        ReferenceSheetEventsBatch(remove=[ev[d(5)].id]),  # 取り消し済み
        ReferenceSheetEventsBatch(add=[d(-1)]),  # 今日より前
        ReferenceSheetEventsBatch(add=[d(500)]),  # 期間の外
    ):
        with pytest.raises(BadRequestError):
            plan.change_sheet_events(db, world["sheet"], "受領", bad)
        db.rollback()
    past = ReferenceSheetEvent(event_date=d(-3))
    db.scalars(select(ReferenceSheetMarker).where(ReferenceSheetMarker.sheet_id == world["sheet"])).one().events.append(past)
    db.commit()
    with pytest.raises(BadRequestError):
        plan.change_sheet_events(db, world["sheet"], "受領", ReferenceSheetEventsBatch(remove=[past.id]))  # 実績は消せない


def test_a_batch_recalculates_templates_once_and_ends_like_single_calls(db, world):
    d = lambda n: world["today"] + timedelta(days=n)  # noqa: E731
    hand_marker(db, world, "一括", list(range(1, 41)))
    hand_marker(db, world, "一件ずつ", list(range(1, 41)))
    batch_t, single_t = sheet_template(db, world, "一括"), sheet_template(db, world, "一件ずつ")
    adds = [d(n) for n in range(41, 71)]  # 30 日足す
    removes = lambda name: [e.id for k, e in events_of(db, world, name).items() if k <= d(10)]  # noqa: E731  10 日消す

    with StatementCounter() as batch:
        plan.change_sheet_events(db, world["sheet"], "一括", ReferenceSheetEventsBatch(add=adds, remove=removes("一括")))
    ids = removes("一件ずつ")
    with StatementCounter() as singles:
        for day in adds:
            plan.add_sheet_events(db, world["sheet"], "一件ずつ", ReferenceSheetEventsCreate(dates=[day]))
        for event_id in ids:
            plan.delete_sheet_event(db, event_id)

    def state(name: str, template_id: int):
        dates = sorted(k for k, e in events_of(db, world, name).items() if not e.is_cancelled)
        starts = sorted(db.scalars(
            select(PlanInstance.scheduled_start)
            .outerjoin(PlanInstanceVoid, PlanInstanceVoid.instance_id == PlanInstance.id)
            .where(PlanInstance.template_id == template_id, PlanInstanceVoid.instance_id.is_(None))
        ))
        return dates, starts

    assert state("一括", batch_t) == state("一件ずつ", single_t)
    assert batch.count * 10 < singles.count, (batch.count, singles.count)



# ==========================================
# エラーの details: あるときだけ応答に入る
# ==========================================
def test_business_error_body_has_details_only_when_given():
    import asyncio
    import json

    from src.core.exceptions import BadRequestError, DuplicatedRecordError
    from src.infrastructure.exception_handler.handlers import business_exception_handler

    def body(exc) -> dict:
        return json.loads(asyncio.run(business_exception_handler(None, exc)).body)  # type: ignore[arg-type]

    assert body(BadRequestError("plain")) == {"status_code": 400, "error_code": "BAD_REQUEST", "message": "plain"}
    with_details = body(DuplicatedRecordError("in use", error_code=ErrorCode.DELETE_FAILED_RECORD_IN_USE, details={"marker": "X"}))
    assert with_details["details"] == {"marker": "X"} and with_details["status_code"] == 409


def test_marker_delete_documents_its_409_details():
    from main import app

    op = app.openapi()["paths"]["/plans/sheets/{sheet_id}/markers/{marker}"]["delete"]
    ref = op["responses"]["409"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/MarkerInUseError")
    details = app.openapi()["components"]["schemas"]["MarkerInUseDetails"]["properties"]
    assert set(details) == {"marker", "templates", "deleted_templates", "draft_change_ids"}
