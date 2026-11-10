# tests/test_template_preview.py
"""
テンプレートの変更の、保存の前の確認（POST /ops/templates/{id}/preview。Level 1、P.1）。
- 確認の結果は、実際に保存したときの結果と同じ（予定の違い・オーダーへの影響。人が変更・取消した日は残る）
- 確認は何も保存しない
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import func, select

from tests.client_codes import new_client_code
from tests.openapi_auth import needs_login
from src.coordination.template_preview import preview_template_create, preview_template_update
from src.core.exceptions import BadRequestError, NotFoundError
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope.models import OpeOrder, OpeOrderEvent, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan.models import PlanInstance, PlanInstanceVoid, PlanTemplate, TemplateType
from src.domain_plan.schemas import PlanInstanceCancel, PlanInstanceOverride, PlanTemplateUpdate, RRuleTemplateCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.dry_run import dry_run
from src.infrastructure.repositories import biz_master_repo, client_repo

UTC = timezone.utc
JST = timezone(timedelta(hours=9))


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
    client_repo.create(db, obj_in=ClientCreate(code=code, name="preview test client"))
    return biz_master_repo.create(
        db, obj_in=BizMasterCreate(client_code=code, name="preview biz", business_seq=1, manager_email=None, description=None)
    ).id


@pytest.fixture
def staff(db):
    return staff_services.create_staff(
        db, StaffCreate(login_id="p-" + uuid.uuid4().hex[:10], name="preview tester", roles=[RoleCode.RELEASER]),
        actor=None, status=StaffStatus.ACTIVE,
    )


def template(db, biz_id, **kwargs):
    data = dict(
        biz_master_id=biz_id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    )
    data.update(kwargs)
    return plan_services.create_template(db, RRuleTemplateCreate.model_validate(data))


def live(db, template_id):
    return sorted(
        (i for i in plan_services.get_instances(db, template_id, limit=2000) if not i.voided), key=lambda i: i.scheduled_start
    )


def order_of(db, instance_id) -> OpeOrder:
    db.expire_all()
    return db.scalars(select(OpeOrder).where(OpeOrder.current_instance_id == instance_id, OpeOrder.status != OrderStatus.CANCELED)).one()


def set_status(db, order, status, before=None):
    order = db.get(OpeOrder, order.id)
    if status in (OrderStatus.READY, OrderStatus.IN_PROGRESS) or before in (OrderStatus.READY, OrderStatus.IN_PROGRESS):
        order.released_at = datetime.now(UTC)
    order.status = status
    order.status_before_hold = before
    db.commit()
    return order


def stored(template_id):
    """データベースに保存されている状態（別のセッションで読む）。確認の前後で変わらないことを確かめるためのもの"""
    with SessionLocal() as s:
        t = s.get(PlanTemplate, template_id)
        assert t is not None
        instances = s.execute(
            select(PlanInstance.id, PlanInstance.scheduled_start, PlanInstance.scheduled_end)
            .where(PlanInstance.template_id == template_id).order_by(PlanInstance.id)
        ).all()
        voids = s.scalar(
            select(func.count()).select_from(PlanInstanceVoid).join(PlanInstance, PlanInstance.id == PlanInstanceVoid.instance_id)
            .where(PlanInstance.template_id == template_id)
        )
        orders = s.execute(
            select(OpeOrder.id, OpeOrder.status, OpeOrder.scheduled_start, OpeOrder.conflict, OpeOrder.current_instance_id)
            .where(OpeOrder.template_id == template_id).order_by(OpeOrder.id)
        ).all()
        events = s.scalar(
            select(func.count()).select_from(OpeOrderEvent).join(OpeOrder, OpeOrder.id == OpeOrderEvent.order_id)
            .where(OpeOrder.template_id == template_id)
        )
        return (t.revision, t.start_time, t.rrule, t.name, t.materialized_until, instances, voids, orders, events)


def in_range(preview, moment: datetime) -> bool:
    return preview.range_start <= moment < preview.range_end


def today() -> date:
    return datetime.now(JST).date()


def not_today(items):
    """
    今日の分を除く。今日の予定は、テストを動かす時刻によって「もう過ぎた」か「これから」かが変わる
    （過ぎた予定は変更の対象外で、新しい時刻がこれからなら今日に新しい予定が増える）ため、比べる対象から外す
    """
    return [x for x in items if x.occurrence_date != today()]


# ==========================================
# 確認の結果 = 実際の保存の結果。確認は何も保存しない
# ==========================================
def test_preview_matches_the_real_change_and_saves_nothing(db, biz_id, staff):
    t = template(db, biz_id)
    inst = live(db, t.id)
    # 人が 1 日を動かし、1 日を取り消す（テンプレートを変えても残る）
    moved_by_person = plan_services.override_instance(db, inst[10].id, PlanInstanceOverride(
        scheduled_start=inst[10].scheduled_start + timedelta(hours=2), note="顧客都合",
    ), actor_staff_id=staff.id)
    plan_services.cancel_instance(db, inst[12].id, PlanInstanceCancel(note="無し"), actor_staff_id=staff.id)
    ready, held, working = order_of(db, inst[3].id), order_of(db, inst[4].id), order_of(db, inst[5].id)
    ready = set_status(db, ready, OrderStatus.READY)
    held = set_status(db, held, OrderStatus.ON_HOLD, before=OrderStatus.PENDING)
    working = set_status(db, working, OrderStatus.IN_PROGRESS)
    db.expire_all()

    before = stored(t.id)
    change = PlanTemplateUpdate(start_time=time(14, 0))
    p = preview_template_update(db, t.id, change, months=3)

    # 何も保存されていない
    assert stored(t.id) == before
    assert plan_services.get_template(db, t.id).start_time == time(9, 0)

    # 予定: 人が動かした日・取り消した日以外は、すべて 14 時に動く
    assert p.generation_changed and not not_today(p.added) and not p.removed
    assert p.manual_unchanged == 1
    assert all(m.after_start.astimezone(JST).hour == 14 and m.before_start.astimezone(JST).hour == 9 for m in p.moved)
    moved_dates = {m.occurrence_date for m in p.moved}
    assert inst[10].occurrence_date not in moved_dates and inst[12].occurrence_date not in moved_dates
    manual = [o for o in p.occurrences if o.manual]
    assert [o.scheduled_start for o in manual] == [moved_by_person.scheduled_start]
    assert inst[12].occurrence_date not in {o.occurrence_date for o in p.occurrences}

    # オーダー: 作業中は矛盾、それ以外（リリース済み・保留中も）は追従
    effect = {e.order_id: e for e in p.order_effects}
    assert effect[working.id].effect == "conflict" and effect[working.id].status == OrderStatus.IN_PROGRESS
    assert "作業中" in (effect[working.id].reason or "")
    assert effect[ready.id].effect == "rescheduled" and effect[ready.id].status == OrderStatus.READY
    new_start = effect[ready.id].new_scheduled_start
    assert new_start is not None and new_start.astimezone(JST).hour == 14
    assert effect[held.id].effect == "rescheduled" and effect[held.id].status == OrderStatus.ON_HOLD
    assert order_of(db, moved_by_person.id).id not in effect
    assert not [e for e in p.order_effects if e.effect == "canceled"]

    # 実際に保存して比べる（範囲の中だけ。確認は生成済みの範囲より先まで作って比べることがある）
    plan_services.update_template(db, t.id, change)
    db.expire_all()
    after = [i for i in live(db, t.id) if in_range(p, i.scheduled_start)]
    generated_until = before[4]  # 保存では、生成済みの範囲までが作り直される
    assert generated_until is not None
    assert [(o.scheduled_start, o.scheduled_end) for o in p.occurrences if o.scheduled_start < generated_until] == [
        (i.scheduled_start, i.scheduled_end) for i in after
    ]
    before_orders = {row.id: row for row in before[7]}
    real_rescheduled, real_conflict = set(), set()
    todays = {row.id for row in before[7] if row.scheduled_start.astimezone(JST).date() == today()}
    for o in db.scalars(select(OpeOrder).where(OpeOrder.template_id == t.id)):
        old = before_orders.get(o.id)
        if old is None or old.status in (OrderStatus.FINISHED, OrderStatus.CANCELED) or o.id in todays:
            continue
        if o.conflict and not old.conflict:
            real_conflict.add(o.id)
        elif o.scheduled_start != old.scheduled_start:
            real_rescheduled.add(o.id)
    # 確認は生成済みの範囲より先まで作って比べることがある。その先の分は、保存の時点ではまだオーダーが無い
    known = set(before_orders) - todays
    assert {e.order_id for e in p.order_effects if e.effect == "conflict"} == real_conflict
    assert {e.order_id for e in p.order_effects if e.effect == "rescheduled"} & known == real_rescheduled
    assert len(real_rescheduled) > 80


def test_rule_change_removes_and_adds_slots_and_cancels_their_orders(db, biz_id):
    t = template(db, biz_id, rrule="FREQ=WEEKLY;BYDAY=MO", name="Weekly")
    inst = live(db, t.id)
    working = set_status(db, order_of(db, inst[2].id), OrderStatus.IN_PROGRESS)
    before = stored(t.id)

    change = PlanTemplateUpdate(rrule="FREQ=WEEKLY;BYDAY=TU")
    p = preview_template_update(db, t.id, change, months=3)
    assert stored(t.id) == before

    # 月曜の枠が無くなり、火曜の枠が増える（別の発生枠なので「日時が変わる」ではない）
    assert p.generation_changed and not p.moved
    assert len(p.removed) >= 12 and all(o.occurrence_date.isoweekday() == 1 for o in p.removed)
    assert len(p.added) >= 12 and all(o.occurrence_date.isoweekday() == 2 for o in p.added)
    assert [o.occurrence_date.isoweekday() for o in p.occurrences] == [2] * len(p.occurrences)
    # 月曜のオーダーは取消（作業中だけは矛盾）、火曜の分は新しくできる
    effects = {e.order_id: e.effect for e in p.order_effects}
    assert effects[working.id] == "conflict"
    assert sorted(effects.values()).count("canceled") == len(p.removed) - 1
    assert p.orders_total == len(p.removed) and p.orders_created == len(p.added)

    # 実際の保存でも同じ
    plan_services.update_template(db, t.id, change)
    db.expire_all()
    after = [i for i in live(db, t.id) if in_range(p, i.scheduled_start)]
    assert [o.scheduled_start for o in p.occurrences][: len(after)] == [i.scheduled_start for i in after]
    canceled = {
        o.id for o in db.scalars(select(OpeOrder).where(OpeOrder.template_id == t.id, OpeOrder.status == OrderStatus.CANCELED))
    }
    assert {e.order_id for e in p.order_effects if e.effect == "canceled"} & {row.id for row in before[7]} == canceled
    assert db.get(OpeOrder, working.id).conflict


def test_name_and_deadline_changes_touch_no_dates(db, biz_id):
    t = template(db, biz_id)
    before = stored(t.id)
    p = preview_template_update(db, t.id, PlanTemplateUpdate(name="別の名前", warn_before_minutes=30), months=1)
    assert stored(t.id) == before
    assert not p.generation_changed
    assert not p.added and not p.removed and not p.moved and not p.order_effects and p.orders_created == 0
    assert len(p.occurrences) > 80 and p.orders_total == len(p.occurrences)


def test_longer_range_than_generated_is_previewed_without_saving_the_extension(db, biz_id):
    t = template(db, biz_id)
    generated_until = plan_services.get_template(db, t.id).materialized_until
    assert generated_until is not None
    before = stored(t.id)
    p = preview_template_update(db, t.id, PlanTemplateUpdate(start_time=time(14, 0)), months=6)
    assert stored(t.id) == before
    assert p.range_end > generated_until + timedelta(days=60)
    # 生成済みの先の分も「日時が変わる」として見える（今の設定で作ったものと比べる）。新しいオーダーとしては数えない
    assert len(not_today(p.moved)) == len(not_today(p.occurrences)) > 170
    # 1 か月を指定しても、生成済みの範囲（90 日）までは見せる（変更はそこまで作り直すため）
    short = preview_template_update(db, t.id, PlanTemplateUpdate(start_time=time(14, 0)), months=1)
    assert short.range_end == generated_until


def test_errors_are_the_same_as_the_real_change_and_nothing_is_saved(db, biz_id):
    t = template(db, biz_id)
    before = stored(t.id)
    with pytest.raises(BadRequestError):  # 繰り返しルールのテンプレートに、シート用の項目
        preview_template_update(db, t.id, PlanTemplateUpdate(offset_days=1))
    with pytest.raises(NotFoundError):
        preview_template_update(db, 999_999_999, PlanTemplateUpdate(start_time=time(14, 0)))
    assert stored(t.id) == before
    # 確認の後も、同じセッションでふつうに保存できる（試し実行の目印が残っていない）
    plan_services.update_template(db, t.id, PlanTemplateUpdate(name="保存できる"))
    assert stored(t.id)[3] == "保存できる"


def test_dry_run_refuses_a_commit_and_rolls_back(db, biz_id):
    t = template(db, biz_id)
    before = stored(t.id)
    with pytest.raises(RuntimeError, match="dry_run"):
        with dry_run(db):
            plan_services.update_template(db, t.id, PlanTemplateUpdate(start_time=time(14, 0)))  # コミットする関数
    assert stored(t.id) == before
    db.commit()  # 抜けた後はコミットできる


# ==========================================
# 新しいテンプレートの確認（P.2）
# ==========================================
def new_template(biz_id, **kwargs) -> RRuleTemplateCreate:
    data = dict(
        biz_master_id=biz_id, name="New", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    )
    data.update(kwargs)
    return RRuleTemplateCreate.model_validate(data)


def counts(biz_id):
    with SessionLocal() as s:
        return (
            s.scalar(select(func.count()).select_from(PlanTemplate).where(PlanTemplate.biz_master_id == biz_id)),
            s.scalar(select(func.count()).select_from(OpeOrder).where(OpeOrder.biz_master_id == biz_id)),
        )


def test_create_preview_matches_the_real_creation_and_saves_nothing(db, biz_id):
    body = new_template(biz_id, rrule="FREQ=WEEKLY;BYDAY=MO,TH")
    p = preview_template_create(db, body, months=3)
    assert counts(biz_id) == (0, 0)  # テンプレートもオーダーもできていない
    assert len(p.occurrences) >= 24 and {o.occurrence_date.isoweekday() for o in p.occurrences} == {1, 4}
    assert all(o.scheduled_start.astimezone(JST).hour == 9 and not o.manual for o in p.occurrences)
    assert p.orders_created == len(p.occurrences)

    # 実際に作ると、生成される範囲（90 日先）までは同じ予定になる
    t = plan_services.create_template(db, body)
    generated_until = plan_services.get_template(db, t.id).materialized_until
    assert generated_until is not None
    real = live(db, t.id)
    assert [o.scheduled_start for o in p.occurrences if o.scheduled_start < generated_until] == [i.scheduled_start for i in real]
    assert counts(biz_id) == (1, len(real))


def test_create_preview_reaches_the_next_occurrence_of_a_yearly_job(db, biz_id):
    # 年 1 回（7 月 1 日）: 3 か月の範囲には無くても、作成時に次の予定まで生成されるので、そこまで見せる
    body = new_template(biz_id, rrule="FREQ=YEARLY;BYMONTH=7;BYMONTHDAY=1", start_time=time(23, 59))
    p = preview_template_create(db, body, months=1)
    assert len(p.occurrences) == 1 and (p.occurrences[0].occurrence_date.month, p.occurrences[0].occurrence_date.day) == (7, 1)
    assert p.range_end > p.occurrences[0].scheduled_start and p.orders_created == 1
    assert counts(biz_id) == (0, 0)


def test_create_preview_errors_are_the_same_as_the_real_creation(db, biz_id):
    with pytest.raises(NotFoundError):
        preview_template_create(db, new_template(999_999_999))
    with pytest.raises(NotFoundError):  # 無い営業日カレンダー
        preview_template_create(db, new_template(biz_id, holiday_policy="following", business_calendar_id=999_999_999))
    assert counts(biz_id) == (0, 0)


def test_preview_endpoints_need_a_login_and_admin():
    from fastapi.routing import APIRoute

    from main import app
    from src.coordination.router import router

    paths = app.openapi()["paths"]
    for path in ("/ops/templates/{template_id}/preview", "/ops/templates/preview"):
        op = paths[path]["post"]
        assert needs_login(op) and {"401", "403"} <= set(op["responses"]), path
    routes = [r for r in router.routes if isinstance(r, APIRoute) and r.path.endswith("/preview")]
    assert len(routes) == 2
    for route in routes:
        assert set(getattr(route.endpoint, "__required_roles__")) == {RoleCode.ADMIN}, route.path
