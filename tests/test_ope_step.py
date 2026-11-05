# tests/test_ope_step.py
"""
domain_ope のステップのテスト（5.2: テーブルの制約のみ。ステップを作る・動かす処理は 5.3 以降）。
- 1つのオーダーに、同じ番号・同じテンプレートのステップは1件（番号はテンプレートのまま）
- ステップテンプレートは、それを使ったステップがある限り削除できない（作成時の写しも持つ）
- ステップの記録は追記のみ（DB トリガーで UPDATE / DELETE を拒否）・人 / システム・状態の変更・差し戻しの理由

※ テストごとに一意な顧客コードを使ってデータを分離しています（制約違反の確認は各テストの中でロールバックする）。
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.client_codes import new_client_code
from src.domain_biz.models import SOPStatus, SOPStepTemplate, SOPVersion
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope.models import EventSource, OpeOrder, OpeStep, OpeStepEvent, StepEventType, StepState
from src.domain_plan.models import DeadlineMode
from src.domain_plan import services as plan_services
from src.domain_plan import slot_events
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import RRuleTemplateCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

JST = timezone(timedelta(hours=9))


# ==========================================
# Fixtures / Helpers
# ==========================================
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
    顧客・業務・承認済み SOP バージョン（ステップテンプレート3件）・毎日のテンプレートと、オーダー1件。
    ここではテーブルの制約だけを確かめるため、plan → オーダーの自動追従（4.3）を止めてオーダーを直接作る。
    """
    monkeypatch.setattr(slot_events, "_listeners", [])
    # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    code = new_client_code("P")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="ope step test client"))
    biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="ope step biz", business_seq=1, manager_email=None, description=None))
    version = SOPVersion(
        biz_master_id=biz.id, version_number=1, version_tag="v1.0.0", status=SOPStatus.ACTIVE,
        effective_at=datetime(2026, 1, 1, tzinfo=JST),
    )
    db.add(version)
    db.flush()
    step_templates = [
        SOPStepTemplate(sop_version_id=version.id, step_num=n, title=f"手順{n}", content=f"内容{n}", is_blocking=(n == 2))
        for n in (1, 2, 3)
    ]
    db.add_all(step_templates)
    template = plan_services.create_template(db, RRuleTemplateCreate(
        biz_master_id=biz.id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    ))
    inst = next(i for i in plan_services.get_instances(db, template.id, limit=1000) if not i.voided)
    order = OpeOrder(
        template_id=template.id, occurrence_date=inst.occurrence_date, current_instance_id=inst.id,
        biz_master_id=biz.id, client_code=code, scheduled_start=inst.scheduled_start, scheduled_end=inst.scheduled_end,
        deadline_mode=DeadlineMode.END, deadline_timezone="Asia/Tokyo", deadline_at=inst.scheduled_end,
    )
    db.add(order)
    db.commit()
    return {"order_id": order.id, "version_id": version.id, "step_templates": step_templates}


def step_for(setup, step_template: SOPStepTemplate, **overrides) -> OpeStep:
    """ステップテンプレートからステップを作る（5.3 の処理の代わりに、テストでは直接作る）"""
    values = dict(
        order_id=setup["order_id"], step_template_id=step_template.id, step_num=step_template.step_num,
        title=step_template.title, content=step_template.content, is_blocking=step_template.is_blocking,
    )
    values.update(overrides)
    return OpeStep(**values)


def assert_rejected(db, obj, match: str):
    """追加すると制約違反になることを確認する（確認後はロールバックしてセッションを使える状態に戻す）"""
    db.add(obj)
    with pytest.raises(IntegrityError, match=match):
        db.flush()
    db.rollback()


def make_staff(db):
    return staff_services.create_staff(
        db, StaffCreate(login_id="p-" + uuid.uuid4().hex[:10], name="step tester", roles=[RoleCode.OPERATOR]),
        actor=None, status=StaffStatus.ACTIVE,
    )


# ==========================================
# ステップ
# ==========================================
def test_steps_are_unique_per_order(db, setup):
    first, second, third = setup["step_templates"]
    # 条件に合わなかった手順2は作らない: 番号はテンプレートのまま（1, 3）
    s1, s3 = step_for(setup, first), step_for(setup, third)
    db.add_all([s1, s3])
    db.commit()
    assert s1.state == StepState.LOCKED and s3.step_num == 3 and not s3.is_blocking

    # 同じオーダーに同じ番号・同じテンプレートのステップは作れない・番号は1以上
    assert_rejected(db, step_for(setup, second, step_num=1), "uq_ope_step_order_num")
    assert_rejected(db, step_for(setup, first, step_num=2), "uq_ope_step_order_template")
    assert_rejected(db, step_for(setup, second, step_num=0), "ck_ope_step_num_positive")


def test_step_templates_in_use_cannot_be_deleted(db, setup):
    first = setup["step_templates"][0]
    step = step_for(setup, first)
    db.add(step)
    db.commit()

    # ステップテンプレートも、その SOP バージョンも物理削除できない（バージョンの削除はテンプレートへ CASCADE する）
    # ※ オーダーには固定バージョンを持たせていないので、ここで止めるのはステップの外部キー
    db.delete(db.get(SOPStepTemplate, first.id))
    with pytest.raises(IntegrityError, match="ope_step_step_template_id_fkey"):
        db.commit()
    db.rollback()
    db.delete(db.get(SOPVersion, setup["version_id"]))
    with pytest.raises(IntegrityError, match="ope_step_step_template_id_fkey"):
        db.commit()
    db.rollback()

    # 写しはテンプレートの後の変更に影響されない（草稿に戻されて書き換えられた場合など）
    template = db.get(SOPStepTemplate, first.id)
    assert template is not None
    template.title = "書き換え後"
    db.commit()
    db.refresh(step)
    assert step.title == "手順1" and step.content == "内容1"


# ==========================================
# ステップの記録（追記のみ）
# ==========================================
def test_step_events_are_append_only(db, setup):
    staff = make_staff(db)
    step = step_for(setup, setup["step_templates"][0])
    db.add(step)
    db.flush()
    created = OpeStepEvent(step_id=step.id, event_type=StepEventType.CREATED, to_state=StepState.LOCKED, source=EventSource.SYSTEM)
    opened = OpeStepEvent(
        step_id=step.id, event_type=StepEventType.STATE_CHANGED, from_state=StepState.LOCKED, to_state=StepState.TODO,
        source=EventSource.SYSTEM,
    )
    submitted = OpeStepEvent(
        step_id=step.id, event_type=StepEventType.STATE_CHANGED, from_state=StepState.TODO, to_state=StepState.SUBMITTED,
        source=EventSource.HUMAN, actor_staff_id=staff.id, detail={"note": "free form"},
    )
    db.add_all([created, opened, submitted])
    db.commit()
    assert created.occurred_at is not None and submitted.detail == {"note": "free form"}

    # 記録は変更も削除もできない（DB トリガー）
    submitted.reason = "書き換え"
    with pytest.raises(DBAPIError, match="ope_step_event is immutable"):
        db.commit()
    db.rollback()
    db.delete(created)
    with pytest.raises(DBAPIError, match="ope_step_event is immutable"):
        db.commit()
    db.rollback()

    # 記録のあるステップは物理削除できない
    db.delete(db.get(OpeStep, step.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_step_event_checks(db, setup):
    staff = make_staff(db)
    step = step_for(setup, setup["step_templates"][1])
    db.add(step)
    db.commit()

    def event(**values):
        return OpeStepEvent(step_id=step.id, **values)

    def changed(from_state, to_state, **values):
        return event(event_type=StepEventType.STATE_CHANGED, from_state=from_state, to_state=to_state, **values)

    # 人の操作は職員が必須・システムの処理は職員なし
    assert_rejected(db, changed(StepState.TODO, StepState.SUBMITTED, source=EventSource.HUMAN), "ck_ope_step_event_actor")
    assert_rejected(
        db, changed(StepState.LOCKED, StepState.TODO, source=EventSource.SYSTEM, actor_staff_id=staff.id),
        "ck_ope_step_event_actor",
    )
    # 状態の変更は前後の状態が必須・作成は作成時の状態が必須
    assert_rejected(
        db, event(event_type=StepEventType.STATE_CHANGED, to_state=StepState.TODO, source=EventSource.SYSTEM),
        "ck_ope_step_event_state_change",
    )
    assert_rejected(db, event(event_type=StepEventType.CREATED, source=EventSource.SYSTEM), "ck_ope_step_event_created")
    # 差し戻し（submitted → todo）は理由が必須
    assert_rejected(
        db, changed(StepState.SUBMITTED, StepState.TODO, source=EventSource.HUMAN, actor_staff_id=staff.id),
        "ck_ope_step_event_reject_reason",
    )

    # 正しい記録は通る（理由付きの差し戻し・理由なしの提出と承認）
    db.add_all([
        changed(StepState.TODO, StepState.SUBMITTED, source=EventSource.HUMAN, actor_staff_id=staff.id),
        changed(StepState.SUBMITTED, StepState.TODO, source=EventSource.HUMAN, actor_staff_id=staff.id, reason="金額の確認漏れ"),
        changed(StepState.SUBMITTED, StepState.APPROVED, source=EventSource.HUMAN, actor_staff_id=staff.id),
    ])
    db.commit()
