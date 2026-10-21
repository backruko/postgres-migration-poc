from datetime import date, datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from src.core.schemas.error import COMMON_ERRORS
from src.infrastructure.database import get_db
from src.domain_plan.schemas import (
    CacheRebuildResponse,
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
    ReferenceSheetMarkerCreate,
    ReferenceSheetMarkerResponse,
    ReferenceSheetResponse,
)
from src.domain_plan.services import (
    add_explicit_dates, add_marker, add_one_off_instance, add_sheet_events, cancel_instance, create_sheet, create_template, delete_sheet_event,
    delete_template, get_instances, get_occurrences, get_sheet, get_sheets, get_template, get_templates,
    override_instance, rebuild_cache, remove_explicit_date, restore_template, revert_instance, update_sheet_event,
    update_template,
)

router = APIRouter(prefix="/plans", tags=["plans"])

# ==========================================
# ReferenceSheet（年間計画シート）エンドポイント
# ==========================================

@router.post("/sheets", response_model=ReferenceSheetResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS})
def create_sheet_endpoint(sheet_in: ReferenceSheetCreate, db: Session = Depends(get_db)):
    return create_sheet(db=db, sheet_in=sheet_in)

@router.get("/sheets", response_model=list[ReferenceSheetResponse], responses={**COMMON_ERRORS})
def get_sheets_endpoint(client_code: str, db: Session = Depends(get_db)):
    return get_sheets(db=db, client_code=client_code)

@router.get("/sheets/{sheet_id}", response_model=ReferenceSheetResponse, responses={**COMMON_ERRORS})
def get_sheet_endpoint(sheet_id: int, db: Session = Depends(get_db)):
    return get_sheet(db=db, sheet_id=sheet_id)

@router.post("/sheets/{sheet_id}/markers", response_model=ReferenceSheetResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS})
def add_marker_endpoint(sheet_id: int, marker_in: ReferenceSheetMarkerCreate, db: Session = Depends(get_db)):
    return add_marker(db=db, sheet_id=sheet_id, marker_in=marker_in)

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

@router.delete("/sheets/events/{event_id}", response_model=ReferenceSheetEventResponse, responses={**COMMON_ERRORS})
def delete_sheet_event_endpoint(event_id: int, db: Session = Depends(get_db)):
    return delete_sheet_event(db=db, event_id=event_id)

# ==========================================
# PlanTemplate エンドポイント
# ==========================================

@router.post("/templates", response_model=PlanTemplateResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS})
def create_template_endpoint(template_in: PlanTemplateCreate, db: Session = Depends(get_db)):
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
    """顧客指定日を追加します（登録済みの日付は無視）。"""
    return add_explicit_dates(db=db, template_id=template_id, dates=dates_in.dates)

@router.delete("/templates/{template_id}/dates/{local_date}", response_model=PlanTemplateResponse, responses={**COMMON_ERRORS})
def remove_explicit_date_endpoint(template_id: int, local_date: date, db: Session = Depends(get_db)):
    return remove_explicit_date(db=db, template_id=template_id, local_date=local_date)

@router.get("/templates/{template_id}/instances", response_model=list[PlanInstanceResponse], responses={**COMMON_ERRORS})
def get_instances_endpoint(template_id: int, skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """無効化済みを含む全インスタンス（監査用）。"""
    return get_instances(db=db, template_id=template_id, skip=skip, limit=limit)

@router.post("/templates/{template_id}/instances", response_model=PlanInstanceResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS})
def add_one_off_instance_endpoint(template_id: int, one_off_in: PlanOneOffCreate, db: Session = Depends(get_db)):
    """
    発生枠に紐づかない単発のインスタンスを追加します（scheduled_end 省略時はテンプレートの所要時間）。
    テンプレートのルールを変更しても維持されます。
    """
    return add_one_off_instance(db=db, template_id=template_id, one_off_in=one_off_in)

@router.post("/templates/{template_id}/cache/rebuild", response_model=CacheRebuildResponse, responses={**COMMON_ERRORS})
def rebuild_cache_endpoint(template_id: int, db: Session = Depends(get_db)):
    return CacheRebuildResponse(template_id=template_id, occurrences=rebuild_cache(db=db, template_id=template_id))

# ==========================================
# PlanInstance 手動変更エンドポイント
# ==========================================

@router.post("/instances/{instance_id}/override", response_model=PlanInstanceResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS})
def override_instance_endpoint(instance_id: int, override_in: PlanInstanceOverride, db: Session = Depends(get_db)):
    """
    特定の1件だけ時刻を変更します（延期・前倒し）。テンプレートのルールは変わりません。
    元のインスタンスは MANUAL で無効化され、新しい時刻のインスタンスが追記されます（履歴は不変）。
    以後テンプレートやシート日付が変更されても、この手動変更は維持されます。
    """
    return override_instance(db=db, instance_id=instance_id, override_in=override_in)

@router.post("/instances/{instance_id}/cancel", response_model=PlanInstanceResponse, responses={**COMMON_ERRORS})
def cancel_instance_endpoint(instance_id: int, cancel_in: PlanInstanceCancel | None = None, db: Session = Depends(get_db)):
    """特定の1件だけ取り消します。以後の再生成でも復活しません。"""
    return cancel_instance(db=db, instance_id=instance_id, cancel_in=cancel_in or PlanInstanceCancel())

@router.post("/instances/{instance_id}/revert", response_model=PlanRevertResponse, responses={**COMMON_ERRORS})
def revert_instance_endpoint(instance_id: int, revert_in: PlanInstanceRevert | None = None, db: Session = Depends(get_db)):
    """
    手動変更・取消を取り消し、その発生枠をテンプレートのルールに戻します。
    置き換えの連鎖のどのインスタンスIDを指定しても構いません。
    """
    return revert_instance(db=db, instance_id=instance_id, revert_in=revert_in or PlanInstanceRevert())

# ==========================================
# Occurrence（カレンダー表示用）エンドポイント
# ==========================================

@router.get("/occurrences", response_model=list[PlanOccurrenceResponse], responses={**COMMON_ERRORS})
def get_occurrences_endpoint(
    biz_id: int,
    start: datetime = Query(..., description="タイムゾーン付き (例: 2026-11-01T00:00:00+09:00)"),
    end: datetime = Query(..., description="タイムゾーン付き、start から最大366日"),
    db: Session = Depends(get_db),
):
    return get_occurrences(db=db, biz_id=biz_id, start=start, end=end)
