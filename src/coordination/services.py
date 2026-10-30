from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from src.core.exceptions import BadRequestError
from src.coordination.schemas import (
    DailyBusiness,
    DailyCalendarStatus,
    DailyClient,
    DailyDashboardResponse,
    DailyMilestone,
    DailyOccurrence,
    DailyOrderSummary,
    DailyVoidedItem,
)
from src.domain_biz.models import BizMaster
from src.domain_plan import services as plan_services
from src.domain_plan.models import validate_timezone_name
from src.infrastructure.repositories import (
    business_calendar_repo,
    client_repo,
    ope_order_repo,
    plan_cache_repo,
    plan_instance_repo,
    reference_sheet_repo,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_daily_dashboard(db: Session, target_date: date | None, tz_name: str) -> DailyDashboardResponse:
    """
    指定日（省略時は指定タイムゾーンでの今日）に予定されている全顧客の予定・シート日付・休日を返します。
    - 予定はその日に開始するもの（カレンダーと同じ振り分け）。未生成の区間は先に生成します（2年先まで）
    - 過去の日付では、無効化・取消された予定と、取り消されたシート日付も理由付きで返します
    """
    try:
        tz = ZoneInfo(validate_timezone_name(tz_name))
    except ValueError as e:
        raise BadRequestError(str(e))
    today = _now().astimezone(tz).date()
    day = target_date or today
    is_past = day < today
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)

    plan_services.materialize_all_templates(db, end)

    # 業務ごとに集める（キー: 業務ID）
    businesses: dict[int, DailyBusiness] = {}
    client_of_biz: dict[int, str] = {}

    def business(biz: BizMaster) -> DailyBusiness:
        if biz.id not in businesses:
            businesses[biz.id] = DailyBusiness(
                biz_master_id=biz.id, biz_code=biz.business_code, biz_name=biz.name, occurrences=[], voided=[]
            )
            client_of_biz[biz.id] = biz.client_code
        return businesses[biz.id]

    occurrences = plan_cache_repo.query_starting_in(db, start=start, end=end)
    # 予定ごとのオーダー（1回のクエリで取得。取消済みのオーダーは除く）
    orders = ope_order_repo.get_active_by_instances(db, instance_ids=[cache.instance_id for cache, _, _ in occurrences])
    for cache, instance, biz in occurrences:
        order = orders.get(cache.instance_id)
        business(biz).occurrences.append(DailyOccurrence(
            instance_id=cache.instance_id,
            template_id=cache.template_id,
            title=cache.title,
            starts_at=cache.starts_at,
            ends_at=cache.ends_at,
            occurrence_date=instance.occurrence_date,
            origin=cache.origin,
            is_override=cache.is_override,
            source_sheet_event_id=instance.source_sheet_event_id,
            ui_metadata=cache.ui_metadata,
            order=DailyOrderSummary(
                id=order.id, status=order.status, conflict=order.conflict, conflict_reason=order.conflict_reason,
            ) if order else None,
        ))

    if is_past:
        for instance, void, template, biz in plan_instance_repo.get_voided_starting_in(db, start=start, end=end):
            business(biz).voided.append(DailyVoidedItem(
                instance_id=instance.id,
                template_id=template.id,
                title=template.name,
                starts_at=instance.scheduled_start,
                ends_at=instance.scheduled_end,
                origin=instance.origin,
                void_reason=void.reason,
                void_note=void.note,
                voided_at=void.voided_at,
            ))

    milestones: dict[str, list[DailyMilestone]] = defaultdict(list)
    for event, marker, sheet in reference_sheet_repo.get_events_on(db, day=day, include_cancelled=is_past):
        milestones[sheet.client_code].append(DailyMilestone(
            event_id=event.id,
            sheet_id=sheet.id,
            sheet_name=sheet.name,
            marker=marker.marker,
            marker_description=marker.description,
            is_generated=marker.is_generated,
            rule_date=event.rule_date,
            original_date=event.original_date,
            is_manual=event.is_manual,
            is_cancelled=event.is_cancelled,
        ))

    # 顧客 → 業務の順にまとめる（どちらもコード順）
    biz_by_client: dict[str, list[DailyBusiness]] = defaultdict(list)
    for biz_id, b in businesses.items():
        biz_by_client[client_of_biz[biz_id]].append(b)
    codes = sorted(set(biz_by_client) | set(milestones))
    names = {c.code: c.name for c in client_repo.get_by_codes(db, codes=codes)} if codes else {}
    clients = [
        DailyClient(
            client_code=code,
            client_name=names.get(code, code),
            milestones=milestones.get(code, []),
            businesses=sorted(biz_by_client.get(code, []), key=lambda b: b.biz_code),
        )
        for code in codes
    ]

    calendars = []
    for calendar in business_calendar_repo.get_all(db):
        holiday = next(iter(business_calendar_repo.get_holidays(db, calendar_id=calendar.id, start=day, end=day)), None)
        calendars.append(DailyCalendarStatus(
            calendar_id=calendar.id,
            code=calendar.code,
            name=calendar.name,
            is_business_day=day.isoweekday() not in calendar.weekend_days and holiday is None,
            holiday_name=(holiday.name or "休日") if holiday is not None else None,
        ))

    return DailyDashboardResponse(
        date=day, timezone=tz_name, start=start, end=end, total=len(occurrences),
        calendars=calendars, clients=clients,
    )
