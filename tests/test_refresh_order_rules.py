# tests/test_refresh_order_rules.py
"""
links.refresh_order（オーダーができた・動いた・取り消されたときに、つながりを直す）の決まりのうち、ほかのテストが
通っていない場合を固定する（refresh_order を refresh_orders に寄せる前に。寄せた後も同じ結果になること）。
- キーの無い上流のオーダー（シート日付の無い臨時の予定）を直しても、どの下流にも触らない
- 1 つの業務が 2 つの定義の上流（下流の業務が別々）: 見つかった下流は、それぞれ自分の定義でだけ直す
- 時刻だけが動いた（only_time_based）: 直前の上流（latest_before）だけを直し、同じ日付のつながりには触らない
- 依存関係の定義が無い業務（ほとんど）は、問い合わせ 1 回で終える（予定の生成で大量に呼ばれるため）
状態は普段どおり（テンプレートと定義 → オーダーの追従）に作り、1 つのオーダーだけを直接変えて refresh_order を呼ぶ
"""
from datetime import date, time, timedelta

import pytest
from sqlalchemy import event, select

from tests.test_links_batched import DAILY, DATE, JST, LATEST, SHEET, World, db  # noqa: F401  (fixture は名前で使う)
from src.core.clock import local_date_of, utc_now
from src.domain_biz import services as biz_services
from src.domain_biz.schemas import BizDependencyCreate, BizMasterCreate
from src.domain_ope import links
from src.domain_ope.models import OpeDependency, OpeOrder
from src.infrastructure.repositories import biz_master_repo

# 「3 日後」の枠と直前の上流を、実行の日や時刻によらず同じ日にするため
pytestmark = pytest.mark.usefixtures("frozen_clock")




def order_of(db, template_id: int, day: date) -> OpeOrder:
    return db.scalars(select(OpeOrder).where(OpeOrder.template_id == template_id, OpeOrder.occurrence_date == day)).one()


def links_of(db, downstream: OpeOrder) -> list[OpeDependency]:
    return list(db.scalars(select(OpeDependency).where(OpeDependency.downstream_order_id == downstream.id).order_by(OpeDependency.id)))


def active(db, downstream: OpeOrder) -> list[OpeDependency]:
    return [link for link in links_of(db, downstream) if link.removed_at is None]


def state(db, orders: list[OpeOrder]) -> list[tuple]:
    db.flush()
    db.expire_all()
    return [(o.id, link.id, link.upstream_order_id, link.removed_at, link.removed_reason) for o in orders for link in links_of(db, o)]


def move(order: OpeOrder, hours: int) -> None:
    """オーダーの時刻を動かす（予定の開始・終了と締切をそろえて。テンプレートの時刻を変えたときと同じ）"""
    shift = timedelta(hours=hours)
    order.scheduled_start, order.scheduled_end, order.deadline_at = (
        order.scheduled_start + shift, order.scheduled_end + shift, order.deadline_at + shift,
    )


def two_definitions_world(db):
    """
    上流の業務 U が 2 つの定義の上流: 下流 D は同じ日付、下流 D2 は直前の上流（2 日前まで）。
    U は毎日 9:00、D と D2 は毎日 10:00。3 日後の U・D・D2 と、その前の日の U を返す
    """
    w = World(db)
    d2 = biz_master_repo.create(db, obj_in=BizMasterCreate(
        client_code=w.code, name="下流2", business_seq=3, manager_email=None, description=None,
    )).id
    w.define(DATE)
    biz_services.create_dependency(db, d2, BizDependencyCreate(upstream_biz_id=w.up, match_rule=LATEST, lookback_days=2))
    up, down = w.template(w.up, "U", DAILY), w.template(w.down, "D", DAILY, at=time(10, 0))
    down2 = w.template(d2, "D2", DAILY, at=time(10, 0))
    day = local_date_of(utc_now()) + timedelta(days=3)
    u, u_before = order_of(db, up, day), order_of(db, up, day - timedelta(days=1))
    d, dd = order_of(db, down, day), order_of(db, down2, day)
    assert [link.upstream_order_id for link in active(db, d)] == [u.id]   # 同じ日付の上流
    assert [link.upstream_order_id for link in active(db, dd)] == [u.id]  # 10:00 の直前の上流は同じ日の 9:00
    return u, u_before, d, dd


def test_an_upstream_order_without_a_key_touches_no_downstream(db):
    w = World(db)
    w.define(SHEET)
    w.sheet_templates()  # 上流・下流のシートのテンプレート（シート日付でつながる）
    plain = w.template(w.up, "シート日付なし", DAILY)  # 上流の業務の、シート日付の無いオーダー
    day = local_date_of(utc_now()) + timedelta(days=3)
    keyless = order_of(db, plain, day)
    assert keyless.source_sheet_event_id is None
    downstream = list(db.scalars(select(OpeOrder).where(OpeOrder.biz_master_id == w.down)))
    before = state(db, downstream)
    assert any(row[2] is not None for row in before)  # シート日付でつながった下流がある

    links.refresh_order(db, keyless)
    assert state(db, downstream) == before


def test_each_found_downstream_is_rechecked_only_by_its_own_definition(db):
    u, u_before, d, dd = two_definitions_world(db)
    [date_link] = active(db, d)
    move(u, 2)  # 9:00 → 11:00。下流の 10:00 より後
    db.flush()

    links.refresh_order(db, u)
    db.expire_all()
    # D（同じ日付）: 日付は変わらないので、同じ行のまま
    assert [(link.id, link.upstream_order_id) for link in active(db, d)] == [(date_link.id, u.id)]
    # D2（直前の上流）: 前の日の 9:00 に付け替わり、前の行は理由つきで外れる
    assert [link.upstream_order_id for link in active(db, dd)] == [u_before.id]
    [removed] = [link for link in links_of(db, dd) if link.removed_at is not None]
    assert (removed.upstream_order_id, removed.removed_reason) == (u.id, links.REASON_UPSTREAM_CHANGED)


def test_only_time_based_rechecks_latest_before_and_leaves_date_links_alone(db):
    u, u_before, d, dd = two_definitions_world(db)
    # D の同じ日付のつながりを外しておく: ふつうに直せば作り直される。時刻だけなら作り直されない
    [date_link] = active(db, d)
    date_link.removed_at, date_link.removed_reason = utc_now(), "テストで外した"
    move(u, 2)  # 9:00 → 11:00
    db.flush()

    links.refresh_order(db, u, only_time_based=True)
    db.expire_all()
    assert active(db, d) == []                                                    # 同じ日付は直さない
    assert [link.upstream_order_id for link in active(db, dd)] == [u_before.id]   # 直前の上流は直す

    links.refresh_order(db, u)  # 比べるため: ふつうに直すと、同じ日付のつながりもできる
    db.expire_all()
    assert [link.upstream_order_id for link in active(db, d)] == [u.id]


def test_a_business_without_definitions_costs_one_statement(db):
    w = World(db)
    template = w.template(w.up, "定義なし", DAILY)
    order = order_of(db, template, local_date_of(utc_now()) + timedelta(days=3))
    db.flush()
    statements: list[str] = []
    engine = db.get_bind()

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        links.refresh_order(db, order)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(statements) == 1, statements  # 業務の依存関係の定義を読むだけ
