"""
テンプレートの変更の、保存の前の確認（Level 1、2026-11-10）。

業務管理者がテンプレートを変えると、未来の予定がすぐに作り直され、オーダーも追従します。保存の前に「何が起きるか」を
見せるため、本物の変更（plan の update_template と同じ処理 + オーダーの追従）を試し実行し、結果を読み取ってから取り消します
（infrastructure.dry_run）。別の計算を持たないので、人が変更・取消した日が残ること、作業中のオーダーが矛盾になることなども、
実際に保存したときと同じになります。何も保存されません。

plan は ope を知らないため、予定（plan）とオーダー（ope）の両方を見るこの確認は coordination に置きます。
"""
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.coordination.schemas import (
    PreviewMove,
    PreviewOccurrence,
    PreviewOrderEffect,
    TemplateChangePreview,
)
from src.domain_ope.models import OpeOrder, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan.schemas import PlanTemplateUpdate
from src.domain_plan.services import DryRunOccurrence
from src.infrastructure.dry_run import dry_run

LOCAL_TIMEZONE = ZoneInfo("Asia/Tokyo")
FINAL = (OrderStatus.FINISHED, OrderStatus.CANCELED)

OrderSnapshot = dict[int, tuple[OrderStatus, datetime, datetime, bool, str | None]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _range_end(now: datetime, months: int) -> datetime:
    """今日（日本時間）から months か月後の日の終わり（次の日の 0:00）"""
    last_day: date = now.astimezone(LOCAL_TIMEZONE).date() + relativedelta(months=months)
    return datetime.combine(last_day + timedelta(days=1), time(0, 0), tzinfo=LOCAL_TIMEZONE).astimezone(timezone.utc)


def _orders(db: Session, template_id: int) -> OrderSnapshot:
    """テンプレートのオーダーの今の値（ORM のキャッシュではなく、データベースの今の値を読む）"""
    db.flush()
    stmt = select(
        OpeOrder.id, OpeOrder.status, OpeOrder.scheduled_start, OpeOrder.scheduled_end, OpeOrder.conflict, OpeOrder.conflict_reason
    ).where(OpeOrder.template_id == template_id)
    return {row.id: (row.status, row.scheduled_start, row.scheduled_end, row.conflict, row.conflict_reason) for row in db.execute(stmt)}


def _out(o: DryRunOccurrence) -> PreviewOccurrence:
    return PreviewOccurrence(
        occurrence_date=o.occurrence_date, scheduled_start=o.scheduled_start, scheduled_end=o.scheduled_end, manual=o.manual
    )


def _order_effects(before: OrderSnapshot, after: OrderSnapshot) -> list[PreviewOrderEffect]:
    effects: list[PreviewOrderEffect] = []
    for order_id, (status, start, end, conflict, _) in before.items():
        if status in FINAL:
            continue  # 完了・取消のオーダーは影響を受けない
        new_status, new_start, new_end, new_conflict, new_reason = after[order_id]
        effect: Literal["rescheduled", "canceled", "conflict"]
        if new_status == OrderStatus.CANCELED:
            effect = "canceled"
        elif new_conflict and not conflict:
            effect = "conflict"
        elif (new_start, new_end) != (start, end):
            effect = "rescheduled"
        else:
            continue
        effects.append(PreviewOrderEffect(
            order_id=order_id, effect=effect, status=status, scheduled_start=start,
            new_scheduled_start=new_start if effect == "rescheduled" else None,
            reason=new_reason if effect == "conflict" else None,
        ))
    return sorted(effects, key=lambda e: (e.scheduled_start, e.order_id))


def preview_template_update(
    db: Session, template_id: int, template_in: PlanTemplateUpdate, months: int = 3
) -> TemplateChangePreview:
    """
    テンプレートをこの内容で変えたら何が起きるかを返します（何も保存しません）。
    - 範囲: 今から months か月後まで（生成済みの範囲のほうが長ければ、そこまで。変更は生成済みの範囲すべてを作り直すため）
    - 予定: 変更の後の予定の一覧と、変更の前との違い（増える・無くなる・日時が変わる）。同じ発生枠どうしを比べる
    - オーダー: 追従して日時が変わる・取り消される・矛盾になるもの（変更の前の状態つき）と、新しくできる数
    入力の誤り・変更が拒否される場合は、実際の保存と同じエラーになります。
    """
    now = _now()
    with dry_run(db):
        before, until = plan_services.dry_run_materialize(db, template_id, since=now, until=_range_end(now, months))
        orders_before = _orders(db, template_id)
        after, generation_changed = plan_services.dry_run_update(db, template_id, template_in, since=now, until=until)
        orders_after = _orders(db, template_id)

    before_by_slot = {o.slot: o for o in before}
    after_by_slot = {o.slot: o for o in after}
    added = [_out(o) for slot, o in after_by_slot.items() if slot not in before_by_slot]
    removed = [_out(o) for slot, o in before_by_slot.items() if slot not in after_by_slot]
    moved = [
        PreviewMove(
            occurrence_date=o.occurrence_date,
            before_start=before_by_slot[slot].scheduled_start, before_end=before_by_slot[slot].scheduled_end,
            after_start=o.scheduled_start, after_end=o.scheduled_end,
        )
        for slot, o in after_by_slot.items()
        if slot in before_by_slot
        and (before_by_slot[slot].scheduled_start, before_by_slot[slot].scheduled_end) != (o.scheduled_start, o.scheduled_end)
    ]

    return TemplateChangePreview(
        template_id=template_id,
        range_start=now,
        range_end=until,
        generation_changed=generation_changed,
        occurrences=[_out(o) for o in after],
        added=sorted(added, key=lambda o: o.scheduled_start),
        removed=sorted(removed, key=lambda o: o.scheduled_start),
        moved=sorted(moved, key=lambda m: m.before_start),
        manual_unchanged=sum(1 for o in after if o.manual),
        orders_total=sum(1 for status, *_ in orders_before.values() if status not in FINAL),
        orders_created=len(orders_after.keys() - orders_before.keys()),
        order_effects=_order_effects(orders_before, orders_after),
    )
