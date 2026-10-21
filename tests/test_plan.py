# tests/test_plan.py
"""
domain_plan のテスト。
- エンジン（純粋関数）のテスト
- Service層のテスト（テスト用DB。conftest.py が create_all でテーブルと不変トリガーを作成します）

※ Service層は内部で commit するため、テストごとに一意な顧客コードを使ってデータを分離しています。
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from src.core.exceptions import BadRequestError, DuplicatedRecordError
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_plan import engine, services
from src.domain_plan.models import InstanceOrigin, TemplateType, VoidReason
from src.domain_plan.schemas import (
    ExplicitDatesTemplateCreate,
    PlanInstanceCancel,
    PlanInstanceOverride,
    PlanInstanceRevert,
    PlanOneOffCreate,
    PlanTemplateUpdate,
    RRuleTemplateCreate,
    UIMetadata,
)
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

UTC = timezone.utc
JST = timezone(timedelta(hours=9))


# ==========================================
# Fixtures / Helpers
# ==========================================
@pytest.fixture
def db():
    # 論理削除の全局scopeは SessionLocal に登録されているため、アプリと同じ SessionLocal を使う
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def biz_id(db):
    code = "T" + uuid.uuid4().hex[:3].upper()
    client_repo.create(db, obj_in=ClientCreate(code=code, name="test client"))
    biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="test biz", business_seq=1, manager_email=None, description=None))
    return biz.id


def daily_template(db, biz_id, **kwargs):
    data = dict(
        biz_master_id=biz_id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    )
    data.update(kwargs)
    return services.create_template(db, RRuleTemplateCreate.model_validate(data))


def live(db, template_id, origin=None):
    return sorted(
        (
            i for i in services.get_instances(db, template_id, limit=1000)
            if not i.voided and (origin is None or i.origin == origin)
        ),
        key=lambda i: i.scheduled_start,
    )


def by_id(db, template_id, instance_id):
    return next(i for i in services.get_instances(db, template_id, limit=1000) if i.id == instance_id)


def cache_rows(db, biz_id, days=120):
    now = datetime.now(UTC)
    return services.get_occurrences(db, biz_id, now, now + timedelta(days=days))


# ==========================================
# Engine（純粋関数）
# ==========================================
WINDOW = (datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))


def test_engine_rrule_keeps_wall_clock_across_dst():
    occs = engine.expand_rrule(
        rrule="FREQ=DAILY", anchor_date=date(2026, 10, 30), start_time=time(9, 0), tz_name="America/New_York",
        duration_minutes=60, window_start=WINDOW[0], window_end=WINDOW[1],
    )
    by_day = {o.occurrence_date: o.start.hour for o in occs}
    assert by_day[date(2026, 10, 31)] == 13  # EDT (UTC-4)
    assert by_day[date(2026, 11, 2)] == 14   # EST (UTC-5)


def test_engine_sheet_offset_is_local_and_slot_is_event_date():
    [occ] = engine.expand_sheet_events(
        events=[(7, date(2026, 11, 10))], start_time=time(9, 0), offset_minutes=-1440, tz_name="Asia/Tokyo",
        duration_minutes=30, window_start=WINDOW[0], window_end=WINDOW[1],
    )
    assert occ.start.astimezone(JST) == datetime(2026, 11, 9, 9, 0, tzinfo=JST)
    assert occ.occurrence_date == date(2026, 11, 10)
    assert occ.source_sheet_event_id == 7


def test_engine_skips_holidays_and_window_is_half_open():
    occs = engine.expand_explicit_dates(
        dates=[date(2026, 11, 3), date(2026, 11, 4), date(2026, 12, 1)], start_time=time(0, 0), tz_name="UTC",
        duration_minutes=60, window_start=WINDOW[0], window_end=WINDOW[1], holidays=[date(2026, 11, 3)],
    )
    # 11/3 は休日、12/1 00:00 は窓の上端（含まない）
    assert [o.occurrence_date for o in occs] == [date(2026, 11, 4)]


# ==========================================
# Override / Cancel / Revert
# ==========================================
def test_override_survives_template_revision(db, biz_id):
    t = daily_template(db, biz_id)
    target = live(db, t.id)[3]

    ov = services.override_instance(
        db, target.id, PlanInstanceOverride(scheduled_start=target.scheduled_start + timedelta(hours=2))
    )
    assert ov.origin == InstanceOrigin.OVERRIDE and ov.overrides_instance_id == target.id

    services.update_template(db, t.id, PlanTemplateUpdate(start_time=time(10, 0)))
    generated_dates = [i.occurrence_date for i in live(db, t.id, InstanceOrigin.GENERATED)]
    assert target.occurrence_date not in generated_dates
    assert any(i.id == ov.id for i in live(db, t.id))


def test_revert_override_puts_slot_back_under_rule(db, biz_id):
    t = daily_template(db, biz_id)
    target = live(db, t.id)[3]
    ov = services.override_instance(
        db, target.id, PlanInstanceOverride(scheduled_start=target.scheduled_start + timedelta(hours=2))
    )
    others_before = {i.id for i in live(db, t.id)} - {ov.id}

    result = services.revert_instance(db, ov.id, PlanInstanceRevert(note="mistake"))

    assert result.voided_instance is not None and result.voided_instance.id == ov.id
    assert result.voided_instance.void_reason == VoidReason.REVERTED
    assert result.voided_instance.void_note == "mistake"
    restored = result.restored_instance
    assert restored is not None
    assert restored.origin == InstanceOrigin.GENERATED
    assert restored.occurrence_date == target.occurrence_date
    assert restored.scheduled_start == target.scheduled_start
    # 他の発生枠はそのまま（作り直されていない）
    assert {i.id for i in live(db, t.id)} - {restored.id} == others_before
    cached = {c.instance_id for c in cache_rows(db, biz_id)}
    assert restored.id in cached and ov.id not in cached


def test_revert_cancel_and_revert_from_middle_of_chain(db, biz_id):
    t = daily_template(db, biz_id)
    first, second = live(db, t.id)[2], live(db, t.id)[4]

    # 取消 -> 解除
    services.cancel_instance(db, first.id, PlanInstanceCancel())
    result = services.revert_instance(db, first.id, PlanInstanceRevert())
    assert result.voided_instance is None
    assert result.restored_instance is not None and result.restored_instance.occurrence_date == first.occurrence_date

    # 連鎖（元 -> ov1 -> ov2）の途中のIDを指定しても連鎖全体が解除される
    ov1 = services.override_instance(db, second.id, PlanInstanceOverride(scheduled_start=second.scheduled_start + timedelta(hours=1)))
    ov2 = services.override_instance(db, ov1.id, PlanInstanceOverride(scheduled_start=second.scheduled_start + timedelta(hours=3)))
    result = services.revert_instance(db, ov1.id, PlanInstanceRevert())
    assert result.voided_instance is not None and result.voided_instance.id == ov2.id
    assert result.restored_instance is not None and result.restored_instance.occurrence_date == second.occurrence_date

    # 解除後に再び手動変更でき、その判断も再生成で維持される
    again = services.cancel_instance(db, result.restored_instance.id, PlanInstanceCancel())
    assert again.voided
    services.update_template(db, t.id, PlanTemplateUpdate(duration_minutes=45))
    assert second.occurrence_date not in [i.occurrence_date for i in live(db, t.id, InstanceOrigin.GENERATED)]


def test_revert_rejections(db, biz_id, monkeypatch):
    t = daily_template(db, biz_id)
    untouched, target = live(db, t.id)[1], live(db, t.id)[5]

    with pytest.raises(BadRequestError):
        services.revert_instance(db, untouched.id, PlanInstanceRevert())  # 手動変更されていない

    services.cancel_instance(db, target.id, PlanInstanceCancel())
    services.revert_instance(db, target.id, PlanInstanceRevert())
    with pytest.raises(DuplicatedRecordError):
        services.revert_instance(db, target.id, PlanInstanceRevert())  # 解除済み

    # 元の発生枠が過去になっている場合は戻せない
    other = live(db, t.id)[7]
    services.cancel_instance(db, other.id, PlanInstanceCancel())
    future_now = other.scheduled_start + timedelta(hours=1)
    monkeypatch.setattr(services, "_now", lambda: future_now)
    with pytest.raises(BadRequestError):
        services.revert_instance(db, other.id, PlanInstanceRevert())


# ==========================================
# One-off（単発追加）
# ==========================================
def test_one_off_instance_lifecycle(db, biz_id):
    t = daily_template(db, biz_id, duration_minutes=90)
    start = datetime.now(JST).replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(days=2)

    one = services.add_one_off_instance(db, t.id, PlanOneOffCreate(scheduled_start=start))
    assert one.origin == InstanceOrigin.ONE_OFF and one.overrides_instance_id is None
    assert one.scheduled_end - one.scheduled_start == timedelta(minutes=90)  # テンプレートの所要時間
    assert one.occurrence_date == start.date()
    assert any(c.instance_id == one.id and c.origin == InstanceOrigin.ONE_OFF for c in cache_rows(db, biz_id))

    # 同時刻にルール生成分があっても作成できる（一意制約はルール生成分のみ）
    gen = live(db, t.id, InstanceOrigin.GENERATED)[0]
    services.add_one_off_instance(db, t.id, PlanOneOffCreate(scheduled_start=gen.scheduled_start))

    # テンプレートを変更しても単発追加は維持される
    services.update_template(db, t.id, PlanTemplateUpdate(start_time=time(11, 0)))
    assert any(i.id == one.id for i in live(db, t.id))

    # 単発追加はルールに戻せない（取消は可能）
    with pytest.raises(BadRequestError):
        services.revert_instance(db, one.id, PlanInstanceRevert())
    moved = services.override_instance(db, one.id, PlanInstanceOverride(scheduled_start=start + timedelta(hours=1)))
    with pytest.raises(BadRequestError):
        services.revert_instance(db, moved.id, PlanInstanceRevert())
    assert services.cancel_instance(db, moved.id, PlanInstanceCancel()).void_reason == VoidReason.MANUAL

    # テンプレート削除で単発追加も無効化される
    services.delete_template(db, t.id)
    assert all(i.voided for i in services.get_instances(db, t.id, limit=1000) if i.scheduled_start >= datetime.now(UTC))


# ==========================================
# ui_metadata
# ==========================================
def test_ui_metadata_merge_and_propagation(db, biz_id):
    t = daily_template(db, biz_id, ui_metadata=UIMetadata(bg_color="#FF0000", text_color="#FFFFFF", icon="money-bag"))
    assert t.ui_metadata == {"bg_color": "#FF0000", "text_color": "#FFFFFF", "icon": "money-bag"}
    assert all(c.ui_metadata == t.ui_metadata for c in cache_rows(db, biz_id))

    # インスタンス側の上書きはキー単位でマージ（未知のキーもそのまま保存）
    target, other = live(db, t.id)[2], live(db, t.id)[4]
    ov = services.override_instance(
        db, target.id,
        PlanInstanceOverride(
            scheduled_start=target.scheduled_start + timedelta(hours=1),
            ui_metadata=UIMetadata(bg_color="#00FF00", is_bold=True, badge="urgent"),  # type: ignore[call-arg]
        ),
    )
    assert ov.ui_metadata == {"bg_color": "#00FF00", "is_bold": True, "badge": "urgent"}
    cache = {c.instance_id: c for c in cache_rows(db, biz_id)}
    assert cache[ov.id].ui_metadata == {
        "bg_color": "#00FF00", "text_color": "#FFFFFF", "icon": "money-bag", "is_bold": True, "badge": "urgent",
    }

    # 再オーバーライドで ui_metadata を省略すると元の値を引き継ぐ
    ov2 = services.override_instance(db, ov.id, PlanInstanceOverride(scheduled_start=target.scheduled_start + timedelta(hours=2)))
    assert ov2.ui_metadata == ov.ui_metadata

    # テンプレートの変更はリビジョンを上げずにキャッシュの実効値へ反映
    revision = t.revision
    t = services.update_template(db, t.id, PlanTemplateUpdate(ui_metadata=UIMetadata(bg_color="#0000FF")))
    assert t.revision == revision
    cache = {c.instance_id: c for c in cache_rows(db, biz_id)}
    assert cache[other.id].ui_metadata == {"bg_color": "#0000FF"}
    assert cache[ov2.id].ui_metadata == {"bg_color": "#00FF00", "is_bold": True, "badge": "urgent"}

    # null でクリア
    t = services.update_template(db, t.id, PlanTemplateUpdate(ui_metadata=None))
    assert t.ui_metadata is None
    cache = {c.instance_id: c for c in cache_rows(db, biz_id)}
    assert cache[other.id].ui_metadata is None

    # キャッシュ再構築でも同じ実効値になる
    services.rebuild_cache(db, t.id)
    assert {c.instance_id: c.ui_metadata for c in cache_rows(db, biz_id)} == {k: v.ui_metadata for k, v in cache.items()}


def test_ui_metadata_validation():
    with pytest.raises(ValidationError):
        UIMetadata(bg_color="red")
    with pytest.raises(ValidationError):
        UIMetadata(is_bold="maybe")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        UIMetadata(note="x" * 3000)  # type: ignore[call-arg]
    assert UIMetadata(bg_color="#abc", icon=None).to_dict() == {"bg_color": "#abc"}


# ==========================================
# 不変性
# ==========================================
def test_instances_are_immutable_in_db(db, biz_id):
    t = services.create_template(db, ExplicitDatesTemplateCreate(
        biz_master_id=biz_id, name="Dates", template_type=TemplateType.EXPLICIT_DATES,
        start_time=time(9, 0), duration_minutes=30, dates=[date.today() + timedelta(days=3)],
    ))
    [instance] = live(db, t.id)
    for sql in ["UPDATE plan_instance SET ui_metadata = '{}' WHERE id = :id", "DELETE FROM plan_instance WHERE id = :id"]:
        with pytest.raises(Exception, match="immutable"):
            db.execute(text(sql), {"id": instance.id})
        db.rollback()
