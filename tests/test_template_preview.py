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
    assert inst[5].occurrence_date not in moved_dates  # 作業中のオーダーの予定は動かさない
    manual = [o for o in p.occurrences if o.manual]
    assert [o.scheduled_start for o in manual] == [moved_by_person.scheduled_start]
    assert inst[12].occurrence_date not in {o.occurrence_date for o in p.occurrences}

    # オーダー: 作業中は予定ごとそのまま（矛盾にもならない）、それ以外（リリース済み・保留中も）は追従
    effect = {e.order_id: e for e in p.order_effects}
    assert working.id not in effect and not [e for e in p.order_effects if e.effect == "conflict"]
    [kept] = [o for o in p.occurrences if o.occurrence_date == inst[5].occurrence_date]
    assert kept.scheduled_start == inst[5].scheduled_start
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

    # 月曜の枠が無くなり、火曜の枠が増える（別の発生枠なので「日時が変わる」ではない）。作業中のオーダーの月曜だけは残る
    assert p.generation_changed and not p.moved
    assert len(p.removed) >= 11 and all(o.occurrence_date.isoweekday() == 1 for o in p.removed)
    assert len(p.added) >= 12 and all(o.occurrence_date.isoweekday() == 2 for o in p.added)
    assert inst[2].occurrence_date not in {o.occurrence_date for o in p.removed}
    assert sorted(o.occurrence_date for o in p.occurrences if o.occurrence_date.isoweekday() == 1) == [inst[2].occurrence_date]
    # 月曜のオーダーは取消（作業中のものは、予定ごとそのまま）、火曜の分は新しくできる
    effects = {e.order_id: e.effect for e in p.order_effects}
    assert working.id not in effects and set(effects.values()) == {"canceled"}
    assert len(effects) == len(p.removed)
    assert p.orders_total == len(p.removed) + 1 and p.orders_created == len(p.added)

    # 実際の保存でも同じ
    plan_services.update_template(db, t.id, change)
    db.expire_all()
    after = [i for i in live(db, t.id) if in_range(p, i.scheduled_start)]
    assert [o.scheduled_start for o in p.occurrences][: len(after)] == [i.scheduled_start for i in after]
    canceled = {
        o.id for o in db.scalars(select(OpeOrder).where(OpeOrder.template_id == t.id, OpeOrder.status == OrderStatus.CANCELED))
    }
    assert {e.order_id for e in p.order_effects if e.effect == "canceled"} & {row.id for row in before[7]} == canceled
    still_working = db.get(OpeOrder, working.id)
    assert still_working is not None and not still_working.conflict and still_working.current_instance_id == inst[2].id


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


def test_preview_endpoints_need_a_login_and_releaser_or_admin_while_saving_stays_admin_only():
    from fastapi.routing import APIRoute

    from main import app
    from src.coordination.router import router

    paths = app.openapi()["paths"]
    for path in ("/ops/templates/{template_id}/preview", "/ops/templates/preview"):
        op = paths[path]["post"]
        assert needs_login(op) and {"401", "403"} <= set(op["responses"]), path
    routes = [r for r in router.routes if isinstance(r, APIRoute) and r.path.endswith("/preview")]
    assert len(routes) == 2
    # 確認は何も保存しないので、リリース担当にも開く（2026-11-10）
    for route in routes:
        assert set(getattr(route.endpoint, "__required_roles__")) == {RoleCode.RELEASER, RoleCode.ADMIN}, route.path

    # 保存・作成・取り込みは、業務管理者だけのまま
    from src.domain_plan.router import router as plan_router

    admin_only = {("/plans/templates", "POST"), ("/plans/templates/{template_id}", "PATCH"), ("/plans/sheets/import", "POST")}
    seen = set()
    for route in plan_router.routes:
        if isinstance(route, APIRoute):
            for method in route.methods or ():
                if (route.path, method) in admin_only:
                    seen.add((route.path, method))
                    assert set(getattr(route.endpoint, "__required_roles__")) == {RoleCode.ADMIN}, route.path
    assert seen == admin_only


# ==========================================
# もう始まった発生枠は、テンプレートを変えてももう一度作らない
# ==========================================
def test_a_slot_that_already_started_today_is_not_generated_again(db, biz_id, monkeypatch):
    from src.coordination import order_sync

    # 昨日の時点で作る → 今日 0:05 の予定（もう過ぎている）とそのオーダーがある
    real = datetime.now(UTC)
    monkeypatch.setattr(plan_services, "_now", lambda: real - timedelta(days=1))
    monkeypatch.setattr(order_sync, "_now", lambda: real - timedelta(days=1))
    t = template(db, biz_id, start_time=time(0, 5))
    monkeypatch.undo()

    def todays():
        db.expire_all()
        return [i for i in live(db, t.id) if i.occurrence_date == today()]

    [old] = todays()
    order = order_of(db, old.id)

    # 時刻を今日のこれから（23:55）に変える: 今日の分は増えない（確認にも出ない）。明日以降は動く
    change = PlanTemplateUpdate(start_time=time(23, 55))
    p = preview_template_update(db, t.id, change, months=1)
    assert not p.added and len(p.moved) > 25
    # 範囲の最後の日だけは「無くなる」に出ることがある（新しい時刻が生成済みの範囲の外に出る。範囲が延びるとまた作られる）
    assert all(o.occurrence_date > today() + timedelta(days=60) for o in p.removed) and len(p.removed) <= 1
    assert today() not in {o.occurrence_date for o in p.occurrences}

    plan_services.update_template(db, t.id, change)
    [still] = todays()
    assert still.id == old.id and still.scheduled_start == old.scheduled_start
    order = db.get(OpeOrder, order.id)
    assert order is not None and order.current_instance_id == old.id and order.status == OrderStatus.PENDING
    tomorrow = next(i for i in live(db, t.id) if i.occurrence_date == today() + timedelta(days=1))
    assert tomorrow.scheduled_start.astimezone(JST).strftime("%H:%M") == "23:55"


# ==========================================
# 作業が終わった・始まった発生枠は、テンプレートを変えても動かさない（2026-11-10）
# ==========================================
def finish(db, order):
    order = db.get(OpeOrder, order.id)
    order.released_at = order.finished_at = datetime.now(UTC)
    order.status = OrderStatus.FINISHED
    db.commit()
    return order


def test_a_finished_order_keeps_its_occurrence_when_the_template_changes(db, biz_id):
    # 例: 毎月 26 日 9:00。今月分のオーダーは先に完了している。時刻を 11:00 に変える
    t = template(db, biz_id, rrule="FREQ=MONTHLY;BYMONTHDAY=26", name="Monthly 26")
    first, second, third = live(db, t.id)[:3]
    done = finish(db, order_of(db, first.id))

    change = PlanTemplateUpdate(start_time=time(11, 0))
    p = preview_template_update(db, t.id, change, months=3)
    # 確認: 動くのは 2 件だけ。完了した分は、今の時刻のまま予定の一覧に残る
    assert [m.occurrence_date for m in p.moved] == [second.occurrence_date, third.occurrence_date]
    assert not p.added and not p.removed
    assert p.occurrences[0].scheduled_start == first.scheduled_start
    assert {e.order_id for e in p.order_effects} == {order_of(db, second.id).id, order_of(db, third.id).id}

    plan_services.update_template(db, t.id, change)
    db.expire_all()
    now_first, now_second = live(db, t.id)[:2]
    # 完了した分の予定は同じインスタンスのまま（オーダーとつながったまま）。次の分は 11:00 に動いた
    assert now_first.id == first.id and now_first.scheduled_start == first.scheduled_start
    assert now_second.id != second.id and now_second.scheduled_start.astimezone(JST).hour == 11
    done = db.get(OpeOrder, done.id)
    assert done is not None and done.status == OrderStatus.FINISHED and done.current_instance_id == now_first.id
    assert order_of(db, now_first.id).id == done.id  # 日次ダッシュボードは、この行に完了のオーダーを出せる


def test_started_work_stays_but_a_holiday_still_flags_work_in_progress(db, biz_id):
    """
    テンプレートの変更では、作業中のオーダーの予定は動かさない（矛盾にもしない）。
    休日の追加（システムが枠を動かす別の理由）では、これまでどおり予定は動き、作業中のオーダーは矛盾になる。完了した分はどちらでも動かない
    """
    from src.domain_plan.schemas import BusinessCalendarCreate, HolidayIn, HolidaysAdd

    calendar = plan_services.create_calendar(
        db, BusinessCalendarCreate(code="T_PV_" + uuid.uuid4().hex[:8].upper(), name="preview test", weekend_days=[])
    )
    t = template(db, biz_id, business_calendar_id=calendar.id, holiday_policy="following")
    inst = live(db, t.id)
    working = set_status(db, order_of(db, inst[5].id), OrderStatus.IN_PROGRESS)
    done = finish(db, order_of(db, inst[8].id))

    # テンプレートの変更: どちらの予定もそのまま
    plan_services.update_template(db, t.id, PlanTemplateUpdate(start_time=time(14, 0)))
    db.expire_all()
    working, done = db.get(OpeOrder, working.id), db.get(OpeOrder, done.id)
    assert working is not None and done is not None
    assert not working.conflict and working.current_instance_id == inst[5].id and working.scheduled_start == inst[5].scheduled_start
    assert done.current_instance_id == inst[8].id
    by_date = {i.occurrence_date: i for i in live(db, t.id)}
    assert by_date[inst[5].occurrence_date].id == inst[5].id and by_date[inst[8].occurrence_date].id == inst[8].id
    assert by_date[inst[6].occurrence_date].scheduled_start.astimezone(JST).hour == 14

    # 休日の追加: 作業中の分は予定が翌日へ動き、オーダーは矛盾。完了した分は動かない
    plan_services.add_holidays(db, calendar.id, HolidaysAdd(holidays=[
        HolidayIn(holiday_date=inst[5].occurrence_date, name="test"), HolidayIn(holiday_date=inst[8].occurrence_date, name="test"),
    ]))
    db.expire_all()
    working, done = db.get(OpeOrder, working.id), db.get(OpeOrder, done.id)
    assert working is not None and done is not None
    assert working.conflict and working.status == OrderStatus.IN_PROGRESS
    assert not done.conflict and done.status == OrderStatus.FINISHED
    live_ids = {i.id for i in live(db, t.id)}
    assert inst[5].id not in live_ids  # 休日になった日の予定は無くなった（翌営業日の予定と重なるため 1 件にまとまる）
    assert inst[8].id in live_ids      # 完了した分は、休日になってもそのまま
