from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Response, status
from sqlalchemy.orm import Session

from src.core.schemas.error import AUTH_ERRORS, COMMON_ERRORS
from src.domain_staff.auth import get_current_staff, require_active
from src.domain_staff.models import StaffUser
from src.infrastructure.database import get_db
from src.domain_plan.schemas import (
    BusinessCalendarCreate,
    BusinessCalendarResponse,
    BusinessCalendarUpdate,
    CacheRebuildResponse,
    CancelledInstanceResponse,
    ClientCalendarResponse,
    ClientOccurrenceResponse,
    PLAN_TEMPLATE_CREATE_EXAMPLES,
    CalendarChangeResult,
    HolidayResponse,
    HolidaysAdd,
    ExplicitDatesAdd,
    PlanInstanceCancel,
    PlanInstanceOverride,
    PlanInstanceResponse,
    PlanInstanceRevert,
    PlanOneOffCreate,
    PlanRevertResponse,
    PlanOccurrenceResponse,
    PlanTemplateCreate,
    PlanTemplateResponse,
    PlanTemplateUpdate,
    ReferenceSheetCreate,
    ReferenceSheetEventResponse,
    ReferenceSheetEventsCreate,
    ReferenceSheetEventUpdate,
    ReferenceSheetImport,
    ReferenceSheetImportResult,
    ReferenceSheetMarkerCreate,
    ReferenceSheetMarkerResponse,
    ReferenceSheetMarkerUpdate,
    ReferenceSheetResponse,
)
from src.domain_plan.services import (
    add_holidays, create_calendar, delete_holiday, get_calendar, get_calendars, get_holidays, update_calendar,
    add_explicit_dates, add_marker, add_one_off_instance, add_sheet_events, cancel_instance, create_sheet, create_template, delete_sheet_event,
    delete_template, get_cancelled_instances, get_client_calendar, restore_cancelled_instance, get_client_occurrences, get_instances, get_occurrences, get_sheet, get_sheets, get_template, get_templates,
    import_sheet, override_instance, revert_sheet_event, update_marker, rebuild_cache, remove_explicit_date, restore_template, revert_instance, update_sheet_event,
    update_template,
)

router = APIRouter(prefix="/plans", tags=["plans"])

# ==========================================
# BusinessCalendar（営業日カレンダー）エンドポイント
# ==========================================

@router.post("/calendars", response_model=BusinessCalendarResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS})
def create_calendar_endpoint(calendar_in: BusinessCalendarCreate, db: Session = Depends(get_db)):
    return create_calendar(db=db, calendar_in=calendar_in)

@router.get("/calendars", response_model=list[BusinessCalendarResponse], responses={**COMMON_ERRORS})
def get_calendars_endpoint(db: Session = Depends(get_db)):
    return get_calendars(db=db)

@router.get("/calendars/{calendar_id}", response_model=BusinessCalendarResponse, responses={**COMMON_ERRORS})
def get_calendar_endpoint(calendar_id: int, db: Session = Depends(get_db)):
    return get_calendar(db=db, calendar_id=calendar_id)

@router.patch("/calendars/{calendar_id}", response_model=BusinessCalendarResponse, responses={**COMMON_ERRORS})
def update_calendar_endpoint(calendar_id: int, calendar_in: BusinessCalendarUpdate, db: Session = Depends(get_db)):
    """週末の曜日を変更すると、このカレンダーを使う全テンプレートの未来の予定が再計算されます。"""
    return update_calendar(db=db, calendar_id=calendar_id, calendar_in=calendar_in)

@router.get("/calendars/{calendar_id}/holidays", response_model=list[HolidayResponse], responses={**COMMON_ERRORS})
def get_holidays_endpoint(calendar_id: int, year: int | None = None, db: Session = Depends(get_db)):
    return get_holidays(db=db, calendar_id=calendar_id, year=year)

@router.post("/calendars/{calendar_id}/holidays", response_model=CalendarChangeResult, responses={**COMMON_ERRORS})
def add_holidays_endpoint(calendar_id: int, holidays_in: HolidaysAdd, db: Session = Depends(get_db)):
    """
    休日を一括登録します（登録済みの日付は名前のみ更新）。
    このカレンダーを使う全テンプレートの未来の予定が、休日ポリシーに従って再計算されます。
    今日より前（日本時間）の日付は追加も名前の変更もできません（400）。
    """
    return add_holidays(db=db, calendar_id=calendar_id, holidays_in=holidays_in)

@router.delete("/calendars/{calendar_id}/holidays/{holiday_date}", response_model=CalendarChangeResult, responses={**COMMON_ERRORS})
def delete_holiday_endpoint(calendar_id: int, holiday_date: date, db: Session = Depends(get_db)):
    """休日を削除します（その日は営業日に戻ります）。今日より前（日本時間）の休日は削除できません（400）。"""
    return delete_holiday(db=db, calendar_id=calendar_id, holiday_date=holiday_date)

# ==========================================
# ReferenceSheet（年間計画シート）エンドポイント
# ==========================================

@router.post("/sheets", response_model=ReferenceSheetResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS})
def create_sheet_endpoint(sheet_in: ReferenceSheetCreate, db: Session = Depends(get_db)):
    return create_sheet(db=db, sheet_in=sheet_in)

@router.post("/sheets/import", response_model=ReferenceSheetImportResult, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS})
def import_sheet_endpoint(
    import_in: ReferenceSheetImport,
    response: Response,
    replace_events: bool = Query(False, description="true: delete dates not in the file so the sheet matches it exactly"),
    db: Session = Depends(get_db),
):
    """
    年間計画シート（マーカーと日付）を一括で取り込みます。
    新規作成時は 201、既存シート（client_code + sheet_name が一致）の更新時は 200 を返します。
    同じファイルを再度取り込んでも変化はありません。全件1トランザクションで、エラー時は何も反映されません。
    """
    result = import_sheet(db=db, import_in=import_in, replace_events=replace_events)
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return result

@router.get("/sheets", response_model=list[ReferenceSheetResponse], responses={**COMMON_ERRORS})
def get_sheets_endpoint(client_code: str, db: Session = Depends(get_db)):
    return get_sheets(db=db, client_code=client_code)

@router.get("/sheets/{sheet_id}", response_model=ReferenceSheetResponse, responses={**COMMON_ERRORS})
def get_sheet_endpoint(sheet_id: int, db: Session = Depends(get_db)):
    return get_sheet(db=db, sheet_id=sheet_id)

@router.post("/sheets/{sheet_id}/markers", response_model=ReferenceSheetResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS})
def add_marker_endpoint(sheet_id: int, marker_in: ReferenceSheetMarkerCreate, db: Session = Depends(get_db)):
    return add_marker(db=db, sheet_id=sheet_id, marker_in=marker_in)

@router.patch("/sheets/{sheet_id}/markers/{marker}", response_model=ReferenceSheetMarkerResponse, responses={**COMMON_ERRORS})
def update_marker_endpoint(sheet_id: int, marker: str, marker_in: ReferenceSheetMarkerUpdate, db: Session = Depends(get_db)):
    """
    マーカーの説明・生成ルールを変更します。
    rule を指定するとルールから日付を再生成します（人が編集・追加・取消した日付は常に維持）。
    rule に null を指定すると生成をやめ、今ある日付をそのまま残します（出どころ・取消の記録も残る）。
    rule: null と同時に cancel_future_dates: true を指定すると、今日以降の日付をすべて取り消します（業務が終わった場合。過去は残る）。
    """
    return update_marker(db=db, sheet_id=sheet_id, marker=marker, marker_in=marker_in)

@router.post("/sheets/{sheet_id}/markers/{marker}/events", response_model=ReferenceSheetMarkerResponse, responses={**COMMON_ERRORS})
def add_sheet_events_endpoint(sheet_id: int, marker: str, events_in: ReferenceSheetEventsCreate, db: Session = Depends(get_db)):
    """
    マーカーに日付を追加します（登録済みの日付は無視）。
    このマーカーを参照している全テンプレートの未来の予定が自動で再計算されます。
    """
    return add_sheet_events(db=db, sheet_id=sheet_id, marker=marker, events_in=events_in)

@router.patch("/sheets/events/{event_id}", response_model=ReferenceSheetEventResponse, responses={**COMMON_ERRORS})
def update_sheet_event_endpoint(event_id: int, event_in: ReferenceSheetEventUpdate, db: Session = Depends(get_db)):
    return update_sheet_event(db=db, event_id=event_id, event_in=event_in)

@router.post("/sheets/events/{event_id}/revert", response_model=ReferenceSheetEventResponse, responses={**COMMON_ERRORS})
def revert_sheet_event_endpoint(event_id: int, db: Session = Depends(get_db)):
    """生成マーカーの日付に対する人の編集・取消を取り消し、ルールの日付に戻します。"""
    return revert_sheet_event(db=db, event_id=event_id)

@router.delete("/sheets/events/{event_id}", response_model=ReferenceSheetEventResponse, responses={**COMMON_ERRORS})
def delete_sheet_event_endpoint(event_id: int, db: Session = Depends(get_db)):
    """日付を削除します。生成マーカーのルール由来の日付は、再生成で復活しないよう「取消」として記録されます。"""
    return delete_sheet_event(db=db, event_id=event_id)

# ==========================================
# PlanTemplate エンドポイント
# ==========================================

@router.post("/templates", response_model=PlanTemplateResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS})
def create_template_endpoint(
    
    # template_in: Annotated[PlanTemplateCreate, Body(openapi_examples=PLAN_TEMPLATE_CREATE_EXAMPLES)],
    template_in: PlanTemplateCreate = Body(..., openapi_examples=PLAN_TEMPLATE_CREATE_EXAMPLES),
    db: Session = Depends(get_db),
):  
    """
    テンプレートを作成し、今から90日先までのインスタンスを生成します。
    template_type により必須項目が変わります（rrule / explicit_dates / reference_sheet_based）。
    """
    return create_template(db=db, template_in=template_in)

@router.get("/templates", response_model=list[PlanTemplateResponse], responses={**COMMON_ERRORS})
def get_templates_endpoint(biz_id: int, db: Session = Depends(get_db)):
    return get_templates(db=db, biz_id=biz_id)

@router.get("/templates/{template_id}", response_model=PlanTemplateResponse, responses={**COMMON_ERRORS})
def get_template_endpoint(template_id: int, db: Session = Depends(get_db)):
    return get_template(db=db, template_id=template_id)

@router.patch("/templates/{template_id}", response_model=PlanTemplateResponse, responses={**COMMON_ERRORS})
def update_template_endpoint(template_id: int, template_in: PlanTemplateUpdate, db: Session = Depends(get_db)):
    """
    生成に影響する項目を変更するとリビジョンが上がり、未来のインスタンスが再照合されます（過去分は不変）。
    """
    return update_template(db=db, template_id=template_id, template_in=template_in)

@router.delete("/templates/{template_id}", response_model=PlanTemplateResponse, responses={**COMMON_ERRORS})
def delete_template_endpoint(template_id: int, db: Session = Depends(get_db)):
    return delete_template(db=db, template_id=template_id)

@router.post("/templates/{template_id}/restore", response_model=PlanTemplateResponse, responses={**COMMON_ERRORS})
def restore_template_endpoint(template_id: int, db: Session = Depends(get_db)):
    return restore_template(db=db, template_id=template_id)

@router.post("/templates/{template_id}/dates", response_model=PlanTemplateResponse, responses={**COMMON_ERRORS})
def add_explicit_dates_endpoint(template_id: int, dates_in: ExplicitDatesAdd, db: Session = Depends(get_db)):
    """顧客指定日を追加します(登録済みの日付は無視)。"""
    return add_explicit_dates(db=db, template_id=template_id, dates=dates_in.dates)

@router.delete("/templates/{template_id}/dates/{local_date}", response_model=PlanTemplateResponse, responses={**COMMON_ERRORS})
def remove_explicit_date_endpoint(template_id: int, local_date: date, db: Session = Depends(get_db)):
    return remove_explicit_date(db=db, template_id=template_id, local_date=local_date)

@router.get("/templates/{template_id}/instances", response_model=list[PlanInstanceResponse], responses={**COMMON_ERRORS})
def get_instances_endpoint(template_id: int, skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """無効化済みを含む全インスタンス(監査用)。"""
    return get_instances(db=db, template_id=template_id, skip=skip, limit=limit)

@router.post("/templates/{template_id}/cache/rebuild", response_model=CacheRebuildResponse, responses={**COMMON_ERRORS})
def rebuild_cache_endpoint(template_id: int, db: Session = Depends(get_db)):
    return CacheRebuildResponse(template_id=template_id, occurrences=rebuild_cache(db=db, template_id=template_id))

@router.post("/templates/{template_id}/instances", response_model=PlanInstanceResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_active()
def add_one_off_instance_endpoint(
    template_id: int, one_off_in: PlanOneOffCreate, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff),
):
    """
    発生枠に紐づかない単発のインスタンスを追加します(scheduled_end 省略時はテンプレートの所要時間)。
    テンプレートのルールを変更しても維持されます。
    """
    return add_one_off_instance(db=db, template_id=template_id, one_off_in=one_off_in, actor_staff_id=current_staff.id)

# ==========================================
# PlanInstance 手動変更エンドポイント
# ==========================================

@router.post("/instances/{instance_id}/override", response_model=PlanInstanceResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_active()
def override_instance_endpoint(
    instance_id: int, override_in: PlanInstanceOverride, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff),
):
    """
    特定の1件だけ時刻を変更します(延期・前倒し)。テンプレートのルールは変わりません。
    元のインスタンスは MANUAL で無効化され、新しい時刻のインスタンスが追記されます(履歴は不変)。
    以後テンプレートやシート日付が変更されても、この手動変更は維持されます。
    """
    return override_instance(db=db, instance_id=instance_id, override_in=override_in, actor_staff_id=current_staff.id)

@router.post("/instances/{instance_id}/cancel", response_model=PlanInstanceResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_active()
def cancel_instance_endpoint(
    instance_id: int, cancel_in: PlanInstanceCancel | None = None, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff),
):
    """特定の1件だけ取り消します。以後の再生成でも復活しません。"""
    return cancel_instance(db=db, instance_id=instance_id, cancel_in=cancel_in or PlanInstanceCancel(), actor_staff_id=current_staff.id)

@router.post("/instances/{instance_id}/restore", response_model=PlanInstanceResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_active()
def restore_cancelled_instance_endpoint(instance_id: int, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff)):
    """
    取り消した予定を、取消の直前と同じ日時で元に戻します（臨時の予定・日時を変更した予定も可）。
    時刻が過ぎた予定は戻せません。取消の記録は履歴として残ります。
    """
    return restore_cancelled_instance(db=db, instance_id=instance_id, actor_staff_id=current_staff.id)

@router.post("/instances/{instance_id}/revert", response_model=PlanRevertResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_active()
def revert_instance_endpoint(
    instance_id: int, revert_in: PlanInstanceRevert | None = None, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff),
):
    """
    手動変更・取消を取り消し、その発生枠をテンプレートのルールに戻します。
    置き換えの連鎖のどのインスタンスIDを指定しても構いません。
    """
    return revert_instance(db=db, instance_id=instance_id, revert_in=revert_in or PlanInstanceRevert(), actor_staff_id=current_staff.id)

# ==========================================
# Occurrence(カレンダー表示用)エンドポイント
# ==========================================

@router.get("/occurrences", response_model=list[PlanOccurrenceResponse], responses={**COMMON_ERRORS})
def get_occurrences_endpoint(
    biz_id: int,
    start: datetime = Query(..., description="タイムゾーン付き (例: 2026-11-01T00:00:00+09:00)"),
    end: datetime = Query(..., description="タイムゾーン付き、start から最大366日"),
    db: Session = Depends(get_db),
):
    return get_occurrences(db=db, biz_id=biz_id, start=start, end=end)

@router.get("/clients/{client_code}/occurrences", response_model=list[ClientOccurrenceResponse], responses={**COMMON_ERRORS})
def get_client_occurrences_endpoint(
    client_code: str,
    start: datetime = Query(..., description="タイムゾーン付き (例: 2026-11-01T00:00:00+09:00)"),
    end: datetime = Query(..., description="タイムゾーン付き、start から最大366日"),
    db: Session = Depends(get_db),
):
    """顧客に属する全業務の予定を、開始時刻順に一覧で取得します（業務コード・業務名付き）。"""
    return get_client_occurrences(db=db, client_code=client_code, start=start, end=end)

@router.get("/clients/{client_code}/calendar", response_model=ClientCalendarResponse, responses={**COMMON_ERRORS})
def get_client_calendar_endpoint(
    client_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="表示する月 (例: 2026-11)"),
    timezone: str = Query("Asia/Tokyo", description="日付の振り分けに使うタイムゾーン"),
    holiday_calendar: str | None = Query(None, description="休日名を付ける営業日カレンダーのコード (例: JP_BANK)"),
    db: Session = Depends(get_db),
):
    """
    顧客に属する全業務の予定を、月カレンダーの形で返します。
    その月の全日（予定の無い日も含む）と、日ごとの予定（開始時刻順）、凡例用のテンプレート一覧を返すため、
    そのままカレンダーに描画できます。
    """
    return get_client_calendar(
        db=db, client_code=client_code, month=month, tz_name=timezone, holiday_calendar=holiday_calendar
    )

@router.get("/clients/{client_code}/cancelled", response_model=list[CancelledInstanceResponse], responses={**COMMON_ERRORS})
def get_cancelled_instances_endpoint(
    client_code: str,
    start: datetime = Query(..., description="タイムゾーン付き (例: 2026-11-01T00:00:00+09:00)"),
    end: datetime = Query(..., description="タイムゾーン付き、start から最大366日"),
    db: Session = Depends(get_db),
):
    """顧客に属する全業務の、取り消された予定の一覧（元に戻せるかどうか付き）。カレンダーには表示されない予定です。"""
    return get_cancelled_instances(db=db, client_code=client_code, start=start, end=end)
