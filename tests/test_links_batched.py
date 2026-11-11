# tests/test_links_batched.py
"""
links.refresh_orders（まとめて直す）の結果は、1 件ずつ refresh_order したのと同じ（2026-11-11、オーダーの作成をまとめた件）。
同じ場面を 2 回、別の顧客のデータで作る: 1 回はそのまま（まとめて）、1 回は refresh_orders を refresh_order の
繰り返しに差し替えて。つながりの行（取り外したものも理由ごと）を、id によらない形にして比べる。
- 同じ日付: 下流のルールの変更でできた下流 / 上流のルールの変更でできた上流（待ち先なしの行が埋まる）/ 上流が 2 つ
- 直前の上流（latest_before）: 上流側・下流側
- 同じシート日付: 定義のあとにシートのテンプレートを作る（両側）+ キーの無い下流
- 上流が取消済みしか無い日にできた下流は、最新の取消済みを待つ
- 取消: 下流の取消 / 直前の上流の取消（下流は前の上流に付け替わる）
※ 顧客コードの先頭文字は空きがないため "I" を共有する
"""
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from tests.client_codes import new_client_code
from src.domain_biz import services as biz_services
from src.domain_biz.models import DependencyMatchRule
from src.domain_biz.schemas import BizDependencyCreate, BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope import links
from src.domain_ope.models import OpeDependency, OpeOrder, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan.models import PlanTemplate, TemplateType
from src.domain_plan.schemas import PlanTemplateUpdate, ReferenceSheetImport, ReferenceSheetTemplateCreate, RRuleTemplateCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

JST = timezone(timedelta(hours=9))
SHEET, DATE, LATEST = (
    DependencyMatchRule.SAME_SHEET_EVENT, DependencyMatchRule.SAME_OCCURRENCE_DATE, DependencyMatchRule.LATEST_BEFORE,
)
MWF, DAILY = "FREQ=WEEKLY;BYDAY=MO,WE,FR", "FREQ=DAILY"
MOCK_SHEET_PATH = Path(__file__).resolve().parent.parent / "mock_data" / "desensitized_mock_data.json"


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


class World:
    """1 回分の場面: 顧客 1 つ、業務「上流」「下流」"""

    def __init__(self, db):
        self.db = db
        self.code = new_client_code("I")
        client_repo.create(db, obj_in=ClientCreate(code=self.code, name="batched links"))
        self.up, self.down = (
            biz_master_repo.create(db, obj_in=BizMasterCreate(
                client_code=self.code, name=name, business_seq=seq, manager_email=None, description=None,
            )).id
            for seq, name in ((1, "上流"), (2, "下流"))
        )

    def template(self, biz_id: int, name: str, rrule: str, at: time = time(9, 0)) -> int:
        return plan_services.create_template(self.db, RRuleTemplateCreate(
            biz_master_id=biz_id, name=name, template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
            start_time=at, duration_minutes=60, anchor_date=datetime.now(JST).date(), rrule=rrule,
        )).id

    def rule(self, template_id: int, rrule: str) -> None:
        plan_services.update_template(self.db, template_id, PlanTemplateUpdate(rrule=rrule))

    def define(self, rule: DependencyMatchRule, lookback_days: int | None = None) -> None:
        biz_services.create_dependency(self.db, self.down, BizDependencyCreate(
            upstream_biz_id=self.up, match_rule=rule, lookback_days=lookback_days,
        ))

    def sheet_templates(self) -> None:
        data = json.loads(MOCK_SHEET_PATH.read_text(encoding="utf-8"))
        data["client_code"] = self.code
        sheet = plan_services.import_sheet(self.db, ReferenceSheetImport.model_validate(data)).sheet
        for biz_id, offset in ((self.up, 0), (self.down, 1)):
            plan_services.create_template(self.db, ReferenceSheetTemplateCreate(
                biz_master_id=biz_id, name="本締", template_type=TemplateType.REFERENCE_SHEET_BASED,
                start_time=time(9, 0), duration_minutes=60, reference_sheet_id=sheet.id, reference_event_marker="本締",
                offset_days=offset, offset_minutes=0,
            ))

    def snapshot(self) -> tuple[list[tuple], list[tuple]]:
        """オーダーとつながり（取り外したものも）を、id によらない形で"""
        db = self.db
        db.expire_all()
        orders = {o.id: o for o in db.scalars(select(OpeOrder).where(OpeOrder.biz_master_id.in_([self.up, self.down])))}
        names = {t.id: t.name for t in db.scalars(select(PlanTemplate).where(PlanTemplate.biz_master_id.in_([self.up, self.down])))}
        sheet_rank = {sid: i for i, sid in enumerate(sorted({o.source_sheet_event_id for o in orders.values()} - {None}))}

        def norm(order_id: int | None) -> tuple | None:
            if order_id is None:
                return None
            o = orders[order_id]
            return ("up" if o.biz_master_id == self.up else "down", names[o.template_id], o.occurrence_date,
                    o.scheduled_start.isoformat(), o.status.value, sheet_rank.get(o.source_sheet_event_id))

        rows = db.scalars(select(OpeDependency).where(OpeDependency.downstream_order_id.in_(list(orders))))
        link_rows = sorted(
            (norm(r.downstream_order_id), norm(r.upstream_order_id), r.match_rule.value, r.kind.value,
             sheet_rank.get(r.match_sheet_event_id), r.match_date, r.removed_at is not None, r.removed_reason)
            for r in rows
        )
        return sorted(norm(i) for i in orders), link_rows  # type: ignore[type-var]


def _one_by_one(db, orders):
    for order in orders:
        links.refresh_order(db, order)


# 場面: World を受け取り、上流・下流のテンプレートと定義を作り、ルールを変える
def date_downstream_rule_change(w: World) -> None:
    w.template(w.up, "U", DAILY)
    w.define(DATE)
    down = w.template(w.down, "D", MWF)
    w.rule(down, DAILY)  # 下流ができる（下流として直す）


def date_upstream_rule_change_and_second_upstream(w: World) -> None:
    w.define(DATE)
    w.template(w.down, "D", DAILY)  # 上流がまだ無い日 → 待ち先なし
    up = w.template(w.up, "U", MWF)
    w.rule(up, DAILY)  # 上流ができる（待ち先なしの行が埋まる）
    w.template(w.up, "U2", DAILY, at=time(7, 0))  # 同じ日の上流がもう 1 つ（つながりが足される）


def latest_before_both_sides(w: World) -> None:
    w.define(LATEST, lookback_days=2)
    up = w.template(w.up, "U", MWF)
    down = w.template(w.down, "D", MWF, at=time(10, 0))
    w.rule(up, DAILY)  # 上流側: 直前の上流が変わる下流
    w.rule(down, DAILY)  # 下流側: 新しい下流の直前の上流


def sheet_both_sides(w: World) -> None:
    w.define(SHEET)
    w.template(w.down, "D", DAILY)  # シート日付の無い下流（当てはまらない）
    w.sheet_templates()  # 上流・下流のシートのテンプレート（それぞれまとめて作られる）


def cancelled_upstream_fallback(w: World) -> None:
    w.define(DATE)
    up = w.template(w.up, "U", DAILY)
    down = w.template(w.down, "D", MWF)
    w.rule(up, "FREQ=WEEKLY;BYDAY=TU,TH")  # 月水金ほかの上流が取消（下流は取消済みを指す）
    w.rule(down, DAILY)  # 取消済みしか無い日の下流 → 最新の取消済みを待つ / 火木は生きている上流


def date_downstream_cancelled(w: World) -> None:
    w.template(w.up, "U", DAILY)
    w.define(DATE)
    down = w.template(w.down, "D", DAILY)
    w.rule(down, MWF)  # 下流が取消（取消済みの下流のつながりは直さない）


def latest_before_upstream_cancelled(w: World) -> None:
    w.define(LATEST, lookback_days=3)
    up = w.template(w.up, "U", DAILY)
    w.template(w.down, "D", DAILY, at=time(10, 0))
    w.rule(up, MWF)  # 上流が取消 → 下流の直前の上流が前の日に変わる（取消済みのまま待つものもある）


@pytest.mark.parametrize("scenario", [
    date_downstream_rule_change, date_upstream_rule_change_and_second_upstream, latest_before_both_sides,
    sheet_both_sides, cancelled_upstream_fallback, date_downstream_cancelled, latest_before_upstream_cancelled,
], ids=lambda f: f.__name__)
def test_batched_links_equal_one_by_one(db, monkeypatch, scenario):
    batched = World(db)
    scenario(batched)
    with monkeypatch.context() as m:
        m.setattr(links, "refresh_orders", _one_by_one)
        reference = World(db)
        scenario(reference)

    orders, link_rows = batched.snapshot()
    ref_orders, ref_links = reference.snapshot()
    assert orders == ref_orders
    assert link_rows == ref_links
    assert any(row[1] is not None for row in link_rows)  # 場面がつながりを作っている（空どうしの比較ではない）
    if scenario is cancelled_upstream_fallback:
        assert any(row[1] is not None and row[1][4] == OrderStatus.CANCELED.value and not row[6] for row in link_rows)
    if scenario is sheet_both_sides:
        assert any(row[4] is not None and not row[6] for row in link_rows)
