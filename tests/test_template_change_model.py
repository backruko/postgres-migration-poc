# tests/test_template_change_model.py
"""
テンプレートの変更依頼の表 plan_template_change（Level 2、D.1: データモデルとリポジトリだけ）。
- 承認待ちの下書きはテンプレートごとに 1 件まで（部分一意インデックス）。決まったあとは次の下書きを書ける
- 新規作成の下書きは template_id がないので、同じ業務に何件でも置ける
- 状態ごとに必要な項目は DB の CHECK 制約で守る（誰が決めたか・差し戻しの理由・直接保存・反映の記録）
- リポジトリ: 承認待ち一覧（業務ごと・全体の件数）、承認待ちのあるテンプレート、変更履歴の順
※ 顧客コードの先頭文字は空きがないため、test_template_preview.py と同じ "I" を使う（1 回の実行の中では重複しない）
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from tests.client_codes import new_client_code
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_plan import services as plan_services
from src.domain_plan.models import PlanTemplateChange, TemplateChangeKind, TemplateChangeStatus, TemplateType
from src.domain_plan.schemas import RRuleTemplateCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo, plan_template_change_repo

UTC = timezone.utc
NOW = datetime.now(UTC) - timedelta(hours=1)  # 承認待ちの drafted_at（DB の now()）より前


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
    code = new_client_code("I")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="change model test client"))
    return biz_master_repo.create(
        db, obj_in=BizMasterCreate(client_code=code, name="change model biz", business_seq=1, manager_email=None, description=None)
    ).id


def _staff(db, role: RoleCode) -> int:
    return staff_services.create_staff(
        db, StaffCreate(login_id="c-" + uuid.uuid4().hex[:10], name=f"change {role.value}", roles=[role]),
        actor=None, status=StaffStatus.ACTIVE,
    ).id


@pytest.fixture
def releaser(db):
    return _staff(db, RoleCode.RELEASER)


@pytest.fixture
def admin(db):
    return _staff(db, RoleCode.ADMIN)


@pytest.fixture
def template_id(db, biz_id):
    return plan_services.create_template(db, RRuleTemplateCreate.model_validate(dict(
        biz_master_id=biz_id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    ))).id


def update_draft(biz_id: int, template_id: int, drafted_by: int, **kwargs) -> PlanTemplateChange:
    data: dict = dict(
        biz_master_id=biz_id, template_id=template_id, kind=TemplateChangeKind.UPDATE,
        status=TemplateChangeStatus.DRAFT, base_revision=1, changes={"start_time": "10:00:00"}, drafted_by_id=drafted_by,
    )
    data.update(kwargs)
    return PlanTemplateChange(**data)


def create_draft(biz_id: int, drafted_by: int, **kwargs) -> PlanTemplateChange:
    data: dict = dict(
        biz_master_id=biz_id, kind=TemplateChangeKind.CREATE, status=TemplateChangeStatus.DRAFT,
        changes={"name": "New", "template_type": "rrule"}, drafted_by_id=drafted_by,
    )
    data.update(kwargs)
    return PlanTemplateChange(**data)


def rejected_by_db(db, change: PlanTemplateChange, constraint: str) -> None:
    """その行を入れると、指定の制約・インデックスで DB に断られる（ほかの行は残す）"""
    with pytest.raises(IntegrityError) as exc_info:
        with db.begin_nested():
            plan_template_change_repo.add(db, change=change)
    assert constraint in str(exc_info.value)


def test_a_new_draft_gets_defaults(db, biz_id, template_id, releaser):
    change = plan_template_change_repo.add(db, change=update_draft(biz_id, template_id, releaser))
    db.refresh(change)

    assert change.status == TemplateChangeStatus.DRAFT
    assert change.is_direct is False
    assert change.add_dates == [] and change.remove_dates == []
    assert change.drafted_at is not None and change.updated_at is not None
    assert change.decided_by_id is None and change.applied_revision is None


def test_one_pending_draft_per_template_and_a_new_one_once_it_is_decided(db, biz_id, template_id, releaser, admin):
    first = plan_template_change_repo.add(db, change=update_draft(biz_id, template_id, releaser))
    rejected_by_db(db, update_draft(biz_id, template_id, admin), "uq_plan_template_change_one_draft")

    # 本人が取り下げると、次の下書きを書ける
    first.status = TemplateChangeStatus.WITHDRAWN
    first.decided_by_id = releaser
    first.decided_at = NOW
    db.flush()
    second = plan_template_change_repo.add(db, change=update_draft(biz_id, template_id, admin))

    assert plan_template_change_repo.get_draft_for_template(db, template_id=template_id) is second


def test_several_create_drafts_can_wait_in_one_business(db, biz_id, releaser, admin):
    plan_template_change_repo.add(db, change=create_draft(biz_id, releaser))
    plan_template_change_repo.add(db, change=create_draft(biz_id, admin))

    assert len(plan_template_change_repo.get_drafts(db, biz_master_id=biz_id)) == 2


@pytest.mark.parametrize(
    "case, constraint",
    [
        ("create draft with a template", "ck_plan_template_change_template"),
        ("approved create without its template", "ck_plan_template_change_template"),
        ("update without a template", "ck_plan_template_change_template"),
        ("update without base revision", "ck_plan_template_change_base_revision"),
        ("create with base revision", "ck_plan_template_change_base_revision"),
        ("delete with changes", "ck_plan_template_change_changes"),
        ("update without changes", "ck_plan_template_change_changes"),
        ("dates on a delete", "ck_plan_template_change_dates"),
        ("draft with a decider", "ck_plan_template_change_decided"),
        ("decided without a time", "ck_plan_template_change_decided"),
        ("rejected without a reason", "ck_plan_template_change_reject_note"),
        ("withdrawn by someone else", "ck_plan_template_change_withdrawn_by"),
        ("direct save still a draft", "ck_plan_template_change_direct"),
        ("direct save approved by someone else", "ck_plan_template_change_direct"),
        ("approved without applied revision", "ck_plan_template_change_applied"),
        ("rejected with applied revision", "ck_plan_template_change_applied"),
        ("approved update without before values", "ck_plan_template_change_before"),
        ("approved create with before values", "ck_plan_template_change_before"),
    ],
)
def test_the_database_refuses_rows_that_break_the_rules(db, biz_id, template_id, releaser, admin, case, constraint):
    approved = dict(status=TemplateChangeStatus.APPROVED, decided_by_id=admin, decided_at=NOW, applied_revision=2)
    rows = {
        "create draft with a template": create_draft(biz_id, releaser, template_id=template_id),
        "approved create without its template": create_draft(biz_id, releaser, **approved),
        "update without a template": update_draft(biz_id, None, releaser),  # type: ignore[arg-type]
        "update without base revision": update_draft(biz_id, template_id, releaser, base_revision=None),
        "create with base revision": create_draft(biz_id, releaser, base_revision=1),
        "delete with changes": update_draft(biz_id, template_id, releaser, kind=TemplateChangeKind.DELETE),
        "update without changes": update_draft(biz_id, template_id, releaser, changes=None),
        "dates on a delete": update_draft(
            biz_id, template_id, releaser, kind=TemplateChangeKind.DELETE, changes=None, add_dates=[date(2026, 12, 1)],
        ),
        "draft with a decider": update_draft(biz_id, template_id, releaser, decided_by_id=admin, decided_at=NOW),
        "decided without a time": update_draft(
            biz_id, template_id, releaser, status=TemplateChangeStatus.REJECTED, decided_by_id=admin, decision_note="no",
        ),
        "rejected without a reason": update_draft(
            biz_id, template_id, releaser, status=TemplateChangeStatus.REJECTED, decided_by_id=admin, decided_at=NOW,
        ),
        "withdrawn by someone else": update_draft(
            biz_id, template_id, releaser, status=TemplateChangeStatus.WITHDRAWN, decided_by_id=admin, decided_at=NOW,
        ),
        "direct save still a draft": update_draft(biz_id, template_id, admin, is_direct=True),
        "direct save approved by someone else": update_draft(
            biz_id, template_id, releaser, is_direct=True, before_values={"start_time": "09:00:00"}, **approved,
        ),
        "approved without applied revision": update_draft(
            biz_id, template_id, releaser, before_values={}, **{**approved, "applied_revision": None},
        ),
        "rejected with applied revision": update_draft(
            biz_id, template_id, releaser, status=TemplateChangeStatus.REJECTED, decided_by_id=admin, decided_at=NOW,
            decision_note="no", applied_revision=2,
        ),
        "approved update without before values": update_draft(biz_id, template_id, releaser, **approved),
        "approved create with before values": create_draft(
            biz_id, releaser, template_id=template_id, before_values={}, **approved,
        ),
    }
    rejected_by_db(db, rows[case], constraint)


def test_rows_that_follow_the_rules_are_accepted(db, biz_id, template_id, releaser, admin):
    approved = dict(status=TemplateChangeStatus.APPROVED, decided_at=NOW, applied_revision=2)
    rows = [
        # 業務管理者の直接保存（下書き即承認）
        update_draft(biz_id, template_id, admin, is_direct=True, decided_by_id=admin, before_values={"a": 1}, **approved),
        # 業務管理者が自分の下書きを承認（自己承認。直接保存ではない）
        update_draft(biz_id, template_id, admin, decided_by_id=admin, before_values={"a": 1}, **approved),
        # 指定日の追加と削除
        update_draft(biz_id, template_id, releaser, changes={}, add_dates=[date(2026, 12, 1)], remove_dates=[date(2026, 12, 2)],
                     decided_by_id=admin, before_values={"a": 1}, **approved),
        # 承認された新規作成（作られたテンプレートを持つ。変更前の値はない）
        create_draft(biz_id, releaser, template_id=template_id, decided_by_id=admin, **{**approved, "applied_revision": 1}),
        # 承認された削除（項目の変更はない）
        update_draft(biz_id, template_id, releaser, kind=TemplateChangeKind.DELETE, changes=None,
                     decided_by_id=admin, before_values={"a": 1}, **{**approved, "applied_revision": 1}),
        # 差し戻し（理由つき）
        update_draft(biz_id, template_id, releaser, status=TemplateChangeStatus.REJECTED, decided_by_id=admin,
                     decided_at=NOW, decision_note="時刻が違う"),
    ]
    for row in rows:
        plan_template_change_repo.add(db, change=row)

    assert len(plan_template_change_repo.get_history(db, template_id=template_id)) == len(rows)


def test_pending_lists_counts_and_history_order(db, biz_id, template_id, releaser, admin):
    biz = biz_master_repo.get(db, id=biz_id)
    assert biz is not None
    other_biz = biz_master_repo.create(
        db, obj_in=BizMasterCreate(
            client_code=biz.client_code, name="other biz", business_seq=2,
            manager_email=None, description=None,
        )
    ).id
    before = plan_template_change_repo.count_drafts(db)

    # 履歴: 承認済み（古い）→ 差し戻し（新しい）→ 承認待ち（いちばん新しく書いた）
    older = update_draft(biz_id, template_id, admin, is_direct=True, decided_by_id=admin, decided_at=NOW - timedelta(days=2),
                         status=TemplateChangeStatus.APPROVED, applied_revision=2, before_values={"a": 1})
    newer = update_draft(biz_id, template_id, releaser, status=TemplateChangeStatus.REJECTED, decided_by_id=admin,
                         decided_at=NOW - timedelta(days=1), decision_note="no")
    for row in (older, newer):
        plan_template_change_repo.add(db, change=row)
    pending = plan_template_change_repo.add(db, change=update_draft(biz_id, template_id, releaser))
    new_here = plan_template_change_repo.add(db, change=create_draft(biz_id, releaser))
    new_there = plan_template_change_repo.add(db, change=create_draft(other_biz, releaser))

    assert plan_template_change_repo.count_drafts(db) == before + 3
    assert [c.id for c in plan_template_change_repo.get_drafts(db, biz_master_id=biz_id)] == [pending.id, new_here.id]
    assert new_there.id in [c.id for c in plan_template_change_repo.get_drafts(db)]
    assert plan_template_change_repo.get_draft_template_ids(db, template_ids=[template_id, template_id + 10_000]) == {
        template_id: pending.id
    }
    assert plan_template_change_repo.get_draft_template_ids(db, template_ids=[]) == {}
    # 承認待ちは書いた日時（いま）で並ぶので、決まった 2 件より上
    assert [c.id for c in plan_template_change_repo.get_history(db, template_id=template_id)] == [pending.id, newer.id, older.id]
    assert plan_template_change_repo.get_locked(db, id=pending.id) is pending
