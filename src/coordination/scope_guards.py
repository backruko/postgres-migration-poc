"""
顧客の範囲の確認（担当顧客が限られた職員。src/domain_staff/scope.py）: 1件を扱うエンドポイントの「対象の顧客は範囲の中か」。

対象の顧客を小さなクエリ1つで引き、範囲の外なら本当に無いときと同じ 404 にします（ほかの顧客のものがあることも見せない）。
全顧客を扱える職員（今の職員すべて）では何も読まずに戻ります。対象が無いときは何もせず、サービスがいつもどおり 404 にします。
論理削除されたもの（業務・テンプレート）も引きます（復元・削除済みの参照も範囲で守る）。

使い方は2通り:
- パスの ID で確かめる: `@router.get("/{biz_id}", dependencies=[BIZ_IN_SCOPE])`（依存はパスの同じ名前の値を受け取る）
- 本文の値で確かめる: エンドポイントに `scope: ClientScope = Depends(get_client_scope)` を足し、`check_biz(db, scope, body.biz_id)`
どちらも `@client_scope(ScopeKind.CHECKED)` の目印と一緒に付けます（tests/test_client_scope.py が確かめる）。
"""
from typing import Any

from fastapi import Depends
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from src.domain_biz.models import BizDependency, BizMaster, SOPStepTemplate, SOPVersion
from src.domain_ope.models import OpeOrder, OpeSignal, OpeStep
from src.domain_plan.models import (
    PlanInstance, PlanTemplate, PlanTemplateChange, ReferenceSheet, ReferenceSheetEvent, ReferenceSheetMarker,
)
from src.domain_staff.auth import get_client_scope
from src.domain_staff.scope import ClientScope
from src.infrastructure.database import get_db


def _client_of(db: Session, stmt: Select[Any]) -> str | None:
    return db.scalars(stmt.execution_options(include_deleted=True)).first()


def _check(db: Session, scope: ClientScope, stmt: Select[Any], not_found_message: str) -> None:
    if not scope.limited:
        return
    code = _client_of(db, stmt)
    if code is not None:
        scope.require(code, not_found_message)


# ==========================================
# 確かめる関数（本文の値・サービスの中から）
# ==========================================
def check_client(scope: ClientScope, client_code: str | None) -> None:
    """顧客コードそのもの（業務の作成・顧客ごとの一覧）。範囲の外なら、無い顧客と同じ 404"""
    if client_code is not None:
        scope.require(client_code, f"Client with code '{client_code.strip().upper()}' does not exist.")


def check_biz(db: Session, scope: ClientScope, biz_id: int) -> None:
    _check(db, scope, select(BizMaster.client_code).where(BizMaster.id == biz_id), f"BizMaster(id={biz_id}) not found.")


def check_sop_version(db: Session, scope: ClientScope, version_id: int) -> None:
    _check(
        db, scope,
        select(BizMaster.client_code).join(SOPVersion, SOPVersion.biz_master_id == BizMaster.id).where(SOPVersion.id == version_id),
        f"SOPVersion(id={version_id}) not found.",
    )


def check_sop_step(db: Session, scope: ClientScope, step_id: int) -> None:
    _check(
        db, scope,
        select(BizMaster.client_code)
        .join(SOPVersion, SOPVersion.biz_master_id == BizMaster.id)
        .join(SOPStepTemplate, SOPStepTemplate.sop_version_id == SOPVersion.id)
        .where(SOPStepTemplate.id == step_id),
        f"SOPStepTemplate(id={step_id}) not found.",
    )


def check_biz_dependency(db: Session, scope: ClientScope, dependency_id: int) -> None:
    # 依存関係は同じ顧客の業務どうしだけ（2026-11-04）なので、下流の業務の顧客で決まる
    _check(
        db, scope,
        select(BizMaster.client_code).join(BizDependency, BizDependency.downstream_biz_id == BizMaster.id)
        .where(BizDependency.id == dependency_id),
        f"BizDependency(id={dependency_id}) not found.",
    )


def check_template(db: Session, scope: ClientScope, template_id: int) -> None:
    _check(
        db, scope,
        select(BizMaster.client_code).join(PlanTemplate, PlanTemplate.biz_master_id == BizMaster.id)
        .where(PlanTemplate.id == template_id),
        f"PlanTemplate(id={template_id}) not found.",
    )


def check_template_change(db: Session, scope: ClientScope, change_id: int) -> None:
    _check(
        db, scope,
        select(BizMaster.client_code).join(PlanTemplateChange, PlanTemplateChange.biz_master_id == BizMaster.id)
        .where(PlanTemplateChange.id == change_id),
        f"PlanTemplateChange(id={change_id}) not found.",
    )


def check_sheet(db: Session, scope: ClientScope, sheet_id: int) -> None:
    _check(db, scope, select(ReferenceSheet.client_code).where(ReferenceSheet.id == sheet_id),
           f"ReferenceSheet(id={sheet_id}) not found.")


def check_sheet_event(db: Session, scope: ClientScope, event_id: int) -> None:
    _check(
        db, scope,
        select(ReferenceSheet.client_code)
        .join(ReferenceSheetMarker, ReferenceSheetMarker.sheet_id == ReferenceSheet.id)
        .join(ReferenceSheetEvent, ReferenceSheetEvent.marker_id == ReferenceSheetMarker.id)
        .where(ReferenceSheetEvent.id == event_id),
        f"ReferenceSheetEvent(id={event_id}) not found.",
    )


def check_instance(db: Session, scope: ClientScope, instance_id: int) -> None:
    _check(
        db, scope,
        select(BizMaster.client_code)
        .join(PlanTemplate, PlanTemplate.biz_master_id == BizMaster.id)
        .join(PlanInstance, PlanInstance.template_id == PlanTemplate.id)
        .where(PlanInstance.id == instance_id),
        f"PlanInstance(id={instance_id}) not found.",
    )


def check_order(db: Session, scope: ClientScope, order_id: int) -> None:
    _check(db, scope, select(OpeOrder.client_code).where(OpeOrder.id == order_id), f"Order(id={order_id}) not found.")


def check_order_step(db: Session, scope: ClientScope, step_id: int) -> None:
    _check(
        db, scope,
        select(OpeOrder.client_code).join(OpeStep, OpeStep.order_id == OpeOrder.id).where(OpeStep.id == step_id),
        f"Step(id={step_id}) not found.",
    )


def check_signal(db: Session, scope: ClientScope, signal_id: int) -> None:
    _check(db, scope, select(OpeSignal.client_code).where(OpeSignal.id == signal_id), f"Signal(id={signal_id}) not found.")


# ==========================================
# パスの ID で確かめる依存（dependencies=[...] に付ける）
# ==========================================
def _client_code_in_scope(client_code: str, scope: ClientScope = Depends(get_client_scope)) -> None:
    check_client(scope, client_code)


def _biz_in_scope(biz_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope)) -> None:
    check_biz(db, scope, biz_id)


def _sop_version_in_scope(version_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope)) -> None:
    check_sop_version(db, scope, version_id)


def _sop_step_in_scope(step_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope)) -> None:
    check_sop_step(db, scope, step_id)


def _biz_dependency_in_scope(
    dependency_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope),
) -> None:
    check_biz_dependency(db, scope, dependency_id)


def _template_in_scope(template_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope)) -> None:
    check_template(db, scope, template_id)


def _template_change_in_scope(change_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope)) -> None:
    check_template_change(db, scope, change_id)


def _sheet_in_scope(sheet_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope)) -> None:
    check_sheet(db, scope, sheet_id)


def _sheet_event_in_scope(event_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope)) -> None:
    check_sheet_event(db, scope, event_id)


def _instance_in_scope(instance_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope)) -> None:
    check_instance(db, scope, instance_id)


def _order_in_scope(order_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope)) -> None:
    check_order(db, scope, order_id)


def _order_step_in_scope(step_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope)) -> None:
    check_order_step(db, scope, step_id)


def _signal_in_scope(signal_id: int, db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope)) -> None:
    check_signal(db, scope, signal_id)


CLIENT_CODE_IN_SCOPE = Depends(_client_code_in_scope)
SHEET_IN_SCOPE = Depends(_sheet_in_scope)
SHEET_EVENT_IN_SCOPE = Depends(_sheet_event_in_scope)
INSTANCE_IN_SCOPE = Depends(_instance_in_scope)
ORDER_IN_SCOPE = Depends(_order_in_scope)
ORDER_STEP_IN_SCOPE = Depends(_order_step_in_scope)
SIGNAL_IN_SCOPE = Depends(_signal_in_scope)
BIZ_IN_SCOPE = Depends(_biz_in_scope)
SOP_VERSION_IN_SCOPE = Depends(_sop_version_in_scope)
SOP_STEP_IN_SCOPE = Depends(_sop_step_in_scope)
BIZ_DEPENDENCY_IN_SCOPE = Depends(_biz_dependency_in_scope)
TEMPLATE_IN_SCOPE = Depends(_template_in_scope)
TEMPLATE_CHANGE_IN_SCOPE = Depends(_template_change_in_scope)

# パスの ID で確かめる依存のすべて（目印 CHECKED のルートに、このどれかか ClientScope の引数があることをテストが確かめる）
SCOPE_GUARDS = frozenset({
    _client_code_in_scope, _biz_in_scope, _sop_version_in_scope, _sop_step_in_scope, _biz_dependency_in_scope,
    _template_in_scope, _template_change_in_scope, _sheet_in_scope, _sheet_event_in_scope, _instance_in_scope,
    _order_in_scope, _order_step_in_scope, _signal_in_scope,
})
