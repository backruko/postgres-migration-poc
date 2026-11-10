from datetime import date

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from src.core.schemas.error import AUTH_ERRORS, COMMON_ERRORS
from src.domain_plan.schemas import PLAN_TEMPLATE_CREATE_EXAMPLES, PlanTemplateCreate, PlanTemplateUpdate
from src.domain_staff.auth import require_active, require_roles
from src.domain_staff.models import RoleCode
from src.infrastructure.database import get_db
from src.coordination.schemas import AttentionResponse, DailyDashboardResponse, SlaResponse, TemplateChangePreview, TemplateCreatePreview
from src.coordination.services import get_attention, get_daily_dashboard, get_sla_report
from src.coordination.template_preview import preview_template_create, preview_template_update

router = APIRouter(prefix="/ops", tags=["ops"])

# ==========================================
# 日次ダッシュボード（全顧客横断）エンドポイント
# ==========================================

@router.get("/daily", response_model=DailyDashboardResponse, responses={**COMMON_ERRORS})
def get_daily_dashboard_endpoint(
    target_date: date | None = Query(None, alias="date", description="表示する日 (例: 2026-11-02)。省略時は timezone での今日"),
    timezone: str = Query("Asia/Tokyo", description="日付の振り分けに使うタイムゾーン"),
    db: Session = Depends(get_db),
):
    """
    指定日に予定されている全顧客の予定を、顧客 → 業務の順にまとめて返します。
    その日の年間計画シートの日付と、各営業日カレンダーでの休日かどうかも返します。
    過去の日付では、無効化・取消された予定も理由付きで返します。
    """
    return get_daily_dashboard(db=db, target_date=target_date, tz_name=timezone)


@router.get("/attention", response_model=AttentionResponse, responses={**COMMON_ERRORS})
def get_attention_endpoint(
    limit: int = Query(200, ge=1, le=1000, description="最大件数（締切の早い順）"),
    db: Session = Depends(get_db),
):
    """
    完了・取消以外で、締切を過ぎた（遅れ）か「まもなく」の時刻を過ぎたオーダーを、日付によらず締切の早い順に返します。
    日次ダッシュボードの上の「締切が近い・遅れ」の一覧用（数分おきに読まれる想定。保存した締切を部分インデックスで引く）。
    """
    return get_attention(db=db, limit=limit)


@router.get("/sla", response_model=SlaResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_active()
def get_sla_report_endpoint(
    from_month: str | None = Query(None, alias="from", description="最初の月 YYYY-MM（省略時は to の 11 か月前）"),
    to_month: str | None = Query(None, alias="to", description="最後の月 YYYY-MM（省略時は今月、日本時間）"),
    client_code: str | None = Query(None, description="顧客コードで絞り込む（省略時は全顧客）"),
    db: Session = Depends(get_db),
):
    """
    SLA の集計: 顧客 × 締切の月（日本時間）。完了・遅れ・期限内率・平均遅れ（遅れたものだけ）・未完了の遅れ。
    取消したオーダーは数えない。期間は最大 36 か月。有効な職員なら誰でも。
    """
    return get_sla_report(db=db, from_month=from_month, to_month=to_month, client_code=client_code)


# ==========================================
# テンプレートの変更の、保存の前の確認（何も保存しない）
# ==========================================

# 確認は何も保存しないので、リリース担当にも開く（準備に加わってもらう。保存・作成は業務管理者だけ。2026-11-10）
PREVIEWERS = [RoleCode.RELEASER, RoleCode.ADMIN]


@router.post("/templates/preview", response_model=TemplateCreatePreview, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles(PREVIEWERS)
def preview_template_create_endpoint(
    template_in: PlanTemplateCreate = Body(..., openapi_examples=PLAN_TEMPLATE_CREATE_EXAMPLES),
    months: int = Query(3, ge=1, le=12, description="今日から何か月先までを確認するか（作成時に生成される範囲のほうが長ければ、そこまで）"),
    db: Session = Depends(get_db),
):
    """
    テンプレートをこの内容（POST /plans/templates と同じ本文）で作ったらできる予定を返します。**何も保存しません。**
    実際の作成と同じ処理を試し実行して取り消すので、休日の扱い・シートの日付からのずれなども、作ったときと同じになります。
    入力の誤りは、POST /plans/templates と同じエラーになります。リリース担当・業務管理者（作成そのものは業務管理者だけ）。
    """
    return preview_template_create(db=db, template_in=template_in, months=months)


@router.post("/templates/{template_id}/preview", response_model=TemplateChangePreview, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles(PREVIEWERS)
def preview_template_update_endpoint(
    template_id: int,
    template_in: PlanTemplateUpdate,
    months: int = Query(3, ge=1, le=12, description="今日から何か月先までを確認するか（生成済みの範囲のほうが長ければ、そこまで）"),
    db: Session = Depends(get_db),
):
    """
    テンプレートをこの内容（PATCH /plans/templates/{id} と同じ本文）で変えたら何が起きるかを返します。**何も保存しません。**
    実際の変更と同じ処理を試し実行して取り消すので、結果は保存したときと同じです（人が変更・取消した日は残る、など）。
    予定の違い（増える・無くなる・日時が変わる）と、追従するオーダー（日時が変わる・取り消される・矛盾になる）を返します。
    入力の誤りは、PATCH と同じエラーになります。リリース担当・業務管理者（保存そのものは業務管理者だけ）。
    """
    return preview_template_update(db=db, template_id=template_id, template_in=template_in, months=months)
