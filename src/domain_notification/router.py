"""通知の仕組みのエンドポイント（N.4b: 届かなかったアラームの一覧と「確認した」。ルール・宛先の画面と API は N.5）"""
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.core.schemas.error import AUTH_ERRORS, COMMON_ERRORS
from src.domain_notification.failures import acknowledge_failure, get_failures
from src.domain_notification.schemas import FailedNotification, NotificationFailuresResponse
from src.domain_staff.auth import ScopeKind, client_scope, get_client_scope, get_current_staff, require_active, require_roles
from src.domain_staff.models import RoleCode, StaffUser
from src.domain_staff.scope import ClientScope
from src.infrastructure.database import get_db

router = APIRouter(prefix="/notifications", tags=["notifications"])

# 「確認した」を押せる人（日々の運用。閲覧者・監査担当・システム管理者は見るだけ。2026-11-12 の決定）
ACKNOWLEDGERS = [RoleCode.OPERATOR, RoleCode.CHECKER, RoleCode.RELEASER, RoleCode.ADMIN]


@router.get("/failures", response_model=NotificationFailuresResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_active()
@client_scope(ScopeKind.FILTERED)
def get_failures_endpoint(db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope)):
    """
    届かなかったアラーム（再試行の後も宛先のどれかに届かなかった通知）と、いま再試行中の配信の数。
    まだ確認されていないものは古くても返し、確認したものは 7 日間返す。日次ダッシュボードが 2 分ごとに読む。
    """
    return get_failures(db, scope=scope)


@router.post("/{notification_id}/acknowledge-failure", response_model=FailedNotification, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles(ACKNOWLEDGERS)
@client_scope(ScopeKind.CHECKED)
def acknowledge_failure_endpoint(
    notification_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope),
    current_staff: StaffUser = Depends(get_current_staff),
):
    """届かなかったアラームを「確認した」にする（だれが・いつを残す。もう確認済みなら最初の確認のまま）。届いた通知は 400"""
    return acknowledge_failure(db, notification_id, actor=current_staff, scope=scope)


# ==========================================
# 通知の設定（宛先・ルール。N.5a）
# 読むのは有効な職員なら誰でも（範囲のある職員には、その顧客と全顧客のもの）。変えるのは業務管理者だけ
# （範囲のある業務管理者は自分の顧客のものだけ。全顧客のものは 403）。2026-11-12 の決定
# ==========================================
from src.domain_notification import services as settings  # noqa: E402
from src.domain_notification.schemas import (  # noqa: E402
    DestinationIn, DestinationOut, NotificationOptions, RuleIn, RuleOut, TestRequested,
)
from src.domain_notification.settings_views import describe_destinations, describe_rules, options  # noqa: E402

EDITORS = [RoleCode.ADMIN]
RESPONSES: dict[int | str, dict[str, Any]] = {**COMMON_ERRORS, **AUTH_ERRORS}


@router.get("/options", response_model=NotificationOptions, responses=RESPONSES)
@require_active()
@client_scope(ScopeKind.SHARED)
def get_options_endpoint():
    """ルールの画面で選べる出来事の種類と文面"""
    return options()


@router.get("/destinations", response_model=list[DestinationOut], responses=RESPONSES)
@require_active()
@client_scope(ScopeKind.FILTERED)
def list_destinations_endpoint(
    include_deleted: bool = False, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope),
):
    """宛先の一覧（名前の順）。使っているルールと、最後のテスト送信の結果も"""
    return describe_destinations(db, settings.list_destinations(db, scope=scope, include_deleted=include_deleted))


@router.post("/destinations", response_model=DestinationOut, status_code=201, responses=RESPONSES)
@require_roles(EDITORS)
@client_scope(ScopeKind.CHECKED)
def create_destination_endpoint(
    body: DestinationIn, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope),
    current_staff: StaffUser = Depends(get_current_staff),
):
    destination = settings.create_destination(
        db, name=body.name, address=body.address, client_code=body.client_code, owner=body.owner,
        actor_id=current_staff.id, scope=scope,
    )
    destination.enabled = body.enabled
    db.commit()
    return describe_destinations(db, [destination])[0]


@router.put("/destinations/{destination_id}", response_model=DestinationOut, responses=RESPONSES)
@require_roles(EDITORS)
@client_scope(ScopeKind.CHECKED)
def update_destination_endpoint(
    destination_id: int, body: DestinationIn, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope),
    current_staff: StaffUser = Depends(get_current_staff),
):
    """宛先を直す（アドレスを直すと、まだ再試行中の配信も新しいアドレスへ送る）"""
    destination = settings.update_destination(
        db, destination_id, name=body.name, address=body.address, client_code=body.client_code, owner=body.owner,
        enabled=body.enabled, actor_id=current_staff.id, scope=scope,
    )
    db.commit()
    return describe_destinations(db, [destination])[0]


@router.delete("/destinations/{destination_id}", response_model=DestinationOut, responses=RESPONSES)
@require_roles(EDITORS)
@client_scope(ScopeKind.CHECKED)
def delete_destination_endpoint(
    destination_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope),
    current_staff: StaffUser = Depends(get_current_staff),
):
    """宛先を削除する（元に戻せる）。ルールが使っていれば 409（details.rules に、そのルール）"""
    destination = settings.delete_destination(db, destination_id, actor_id=current_staff.id, scope=scope)
    db.commit()
    return describe_destinations(db, [destination])[0]


@router.post("/destinations/{destination_id}/restore", response_model=DestinationOut, responses=RESPONSES)
@require_roles(EDITORS)
@client_scope(ScopeKind.CHECKED)
def restore_destination_endpoint(
    destination_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope),
    current_staff: StaffUser = Depends(get_current_staff),
):
    destination = settings.restore_destination(db, destination_id, actor_id=current_staff.id, scope=scope)
    db.commit()
    return describe_destinations(db, [destination])[0]


@router.post("/destinations/{destination_id}/test", response_model=TestRequested, status_code=202, responses=RESPONSES)
@require_roles(EDITORS)
@client_scope(ScopeKind.CHECKED)
def test_destination_endpoint(
    destination_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope),
    current_staff: StaffUser = Depends(get_current_staff),
):
    """
    テスト送信を頼む（202）。ワーカーが本物のアラームと同じ道で 1 分以内に 1 回だけ送り、結果は宛先の一覧の last_test に出る
    """
    event_id = settings.request_destination_test(db, destination_id, actor=current_staff, scope=scope)
    db.commit()
    return TestRequested(event_id=event_id)


@router.get("/rules", response_model=list[RuleOut], responses=RESPONSES)
@require_active()
@client_scope(ScopeKind.FILTERED)
def list_rules_endpoint(
    include_deleted: bool = False, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope),
):
    """ルールの一覧（名前の順）"""
    return describe_rules(db, settings.list_rules(db, scope=scope, include_deleted=include_deleted))


def _rule_fields(body: RuleIn) -> dict:
    return dict(
        name=body.name, event_types=body.event_types, destination_ids=body.destination_ids, template_code=body.template_code,
        client_code=body.client_code, biz_master_id=body.biz_master_id, dedupe_window_minutes=body.dedupe_window_minutes,
        quiet_start=body.quiet_start, quiet_end=body.quiet_end, quiet_mode=body.quiet_mode, enabled=body.enabled,
    )


@router.post("/rules", response_model=RuleOut, status_code=201, responses=RESPONSES)
@require_roles(EDITORS)
@client_scope(ScopeKind.CHECKED)
def create_rule_endpoint(
    body: RuleIn, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope),
    current_staff: StaffUser = Depends(get_current_staff),
):
    rule = settings.create_rule(db, **_rule_fields(body), actor_id=current_staff.id, scope=scope)
    db.commit()
    return describe_rules(db, [rule])[0]


@router.put("/rules/{rule_id}", response_model=RuleOut, responses=RESPONSES)
@require_roles(EDITORS)
@client_scope(ScopeKind.CHECKED)
def update_rule_endpoint(
    rule_id: int, body: RuleIn, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope),
    current_staff: StaffUser = Depends(get_current_staff),
):
    """ルールを直す（すべての項目を送る）。変えた後に処理する出来事から効く"""
    rule = settings.update_rule(db, rule_id, **_rule_fields(body), actor_id=current_staff.id, scope=scope)
    db.commit()
    return describe_rules(db, [rule])[0]


@router.delete("/rules/{rule_id}", response_model=RuleOut, responses=RESPONSES)
@require_roles(EDITORS)
@client_scope(ScopeKind.CHECKED)
def delete_rule_endpoint(
    rule_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope),
    current_staff: StaffUser = Depends(get_current_staff),
):
    rule = settings.delete_rule(db, rule_id, actor_id=current_staff.id, scope=scope)
    db.commit()
    return describe_rules(db, [rule])[0]


@router.post("/rules/{rule_id}/restore", response_model=RuleOut, responses=RESPONSES)
@require_roles(EDITORS)
@client_scope(ScopeKind.CHECKED)
def restore_rule_endpoint(
    rule_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope),
    current_staff: StaffUser = Depends(get_current_staff),
):
    """削除したルールを元に戻す。使っていた宛先が削除されていれば 400（details.destinations）"""
    rule = settings.restore_rule(db, rule_id, actor_id=current_staff.id, scope=scope)
    db.commit()
    return describe_rules(db, [rule])[0]


# ==========================================
# 通知の記録（N.5b）。有効な職員なら誰でも（範囲のある職員には、その顧客のものだけ）
# ==========================================
from datetime import datetime  # noqa: E402

from fastapi import Query  # noqa: E402

from src.domain_notification.log import get_log_entry, list_log  # noqa: E402
from src.domain_notification.models import NotificationStatus  # noqa: E402
from src.domain_notification.schemas import LogEntryDetail, NotificationLogResponse  # noqa: E402


@router.get("/log", response_model=NotificationLogResponse, responses=RESPONSES)
@require_active()
@client_scope(ScopeKind.FILTERED)
def list_log_endpoint(
    status: NotificationStatus | None = Query(None, description="状態で絞る"),
    kind: str | None = Query(None, description="alarm（ルールのアラーム）/ test（宛先のテスト送信）。空ならどちらも"),
    client_code: str | None = Query(None),
    rule_id: int | None = Query(None),
    event_id: int | None = Query(None, description="この出来事から作った通知（出来事 → 通知のつながり）"),
    order_id: int | None = Query(None, description="このオーダーの出来事から作った通知"),
    created_from: datetime | None = Query(None, alias="from", description="作った日時がこれ以降（タイムゾーンがなければ日本時間）"),
    created_to: datetime | None = Query(None, alias="to", description="作った日時がこれより前"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope),
):
    """通知の記録（作った日時の新しい順）: 出来事・ルール・宛先ごとの配信と、1 回ごとの試み（送ったアドレス・相手の返事）"""
    return list_log(
        db, status=status, kind=kind, client_code=client_code, rule_id=rule_id, event_id=event_id, order_id=order_id,
        created_from=created_from, created_to=created_to, limit=limit, scope=scope,
    )


@router.get("/log/{notification_id}", response_model=LogEntryDetail, responses=RESPONSES)
@require_active()
@client_scope(ScopeKind.CHECKED)
def get_log_entry_endpoint(notification_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope)):
    """通知 1 件（本文・画面へのリンクも）"""
    return get_log_entry(db, notification_id, scope=scope)
