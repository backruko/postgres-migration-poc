"""
オーダーのステップの組み立て（5.3）。

- ステップテンプレートの条件（always / month_in / first_in_week）は、オーダーの予定日（日本時間）で判定する。
  first_in_week = そのテンプレートの有効な予定（臨時を含む）のうち、その ISO 週（月〜日、日本時間）で最初のもの。
- リリース前（pending）は、使う予定の SOP バージョンから毎回計算する（プレビュー。行は作らない）。
- リリース時に、固定したバージョンの条件に合うステップだけを作る（合わないものは作らず、リリースの記録に残す）。
- リリース後は固定。予定が動いて条件の結果が変わったら、order_sync が矛盾フラグを立てる（ここでは比較だけ）。
  矛盾を「plan に合わせる」で解決したときだけ、開始前のオーダーのステップを作り直す（古い行は retired_at で外す）。
- 状態の進み方（5.4）: オーダーの開始で最初のステップが開く（locked → todo）。提出で次のステップが開く。
  ただし is_blocking のステップは承認されるまで次を開かない。差し戻しはそのステップだけ todo に戻す。
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from src.domain_biz.models import BizMaster, ExecutionType, SOPStepTemplate, SOPVersion
from src.domain_biz.schemas import AlwaysCondition, FirstInWeekCondition, MonthInCondition, StepCondition
from src.domain_ope.models import EventSource, OpeOrder, OpeStep, OrderStatus, StepEventType, StepState
from src.infrastructure.repositories import ope_step_repo, plan_cache_repo

LOCAL_TIMEZONE = ZoneInfo("Asia/Tokyo")

_condition_adapter: TypeAdapter[StepCondition] = TypeAdapter(StepCondition)


def scheduled_day(order: OpeOrder) -> date:
    return order.scheduled_start.astimezone(LOCAL_TIMEZONE).date()


def week_start(day: date) -> date:
    """ISO 週の月曜日"""
    return day - timedelta(days=day.weekday())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ==========================================
# 条件の判定
# ==========================================
def _is_first_in_week(db: Session, order: OpeOrder) -> bool:
    """オーダーの予定が、そのテンプレートのその週の有効な予定のうち最初か（同じ開始日時のものがあっても最初とみなす）"""
    monday = week_start(scheduled_day(order))
    start = datetime.combine(monday, time.min, tzinfo=LOCAL_TIMEZONE)
    starts = plan_cache_repo.starts_for_template(
        db, template_id=order.template_id, start=start, end=start + timedelta(days=7)
    )
    return not any(s < order.scheduled_start for s, instance_id in starts if instance_id != order.current_instance_id)


def condition_matches(db: Session, condition: dict[str, Any], order: OpeOrder) -> bool:
    parsed = _condition_adapter.validate_python(condition)
    if isinstance(parsed, AlwaysCondition):
        return True
    if isinstance(parsed, MonthInCondition):
        return scheduled_day(order).month in parsed.months
    assert isinstance(parsed, FirstInWeekCondition)
    return _is_first_in_week(db, order)


@dataclass
class StepPlan:
    """SOP バージョンのステップテンプレートを、オーダーの予定日で振り分けた結果（番号順）"""
    included: list[SOPStepTemplate]
    skipped: list[SOPStepTemplate]

    @property
    def template_ids(self) -> list[int]:
        return [t.id for t in self.included]

    def detail(self) -> dict[str, Any]:
        """記録（リリース・作り直し）に残す内容"""
        return {
            "created": [t.step_num for t in self.included],
            "skipped": [{"step_num": t.step_num, "step_template_id": t.id, "condition": t.condition} for t in self.skipped],
        }


def plan_steps(db: Session, order: OpeOrder, version: SOPVersion | None) -> StepPlan:
    """バージョンのステップテンプレートのうち、オーダーの予定日の条件に合うもの・合わないもの（バージョンなし → 空）"""
    plan = StepPlan(included=[], skipped=[])
    if version is None:
        return plan
    biz = db.get(BizMaster, order.biz_master_id)
    if biz is not None and biz.execution_type == ExecutionType.LIGHTWEIGHT:
        return plan  # 顧客側の出来事（LIGHTWEIGHT）はステップなし（受領の記録・信号で完了する）
    for template in sorted(version.step_templates, key=lambda t: t.step_num):
        (plan.included if condition_matches(db, template.condition, order) else plan.skipped).append(template)
    return plan


# ==========================================
# 作成・比較・作り直し
# ==========================================
def create_steps(
    db: Session, order: OpeOrder, version: SOPVersion | None, source: EventSource, actor_staff_id: int | None,
) -> dict[str, Any]:
    """リリース時に、条件に合うステップを作ります（すべて locked。オーダーの開始で最初のステップが開く）。"""
    plan = plan_steps(db, order, version)
    _add_steps(db, order, plan, source, actor_staff_id)
    return plan.detail()


def _add_steps(db: Session, order: OpeOrder, plan: StepPlan, source: EventSource, actor_staff_id: int | None) -> None:
    for template in plan.included:
        step = OpeStep(
            order_id=order.id, step_template_id=template.id, step_num=template.step_num, title=template.title,
            content=template.content, is_blocking=template.is_blocking, state=StepState.LOCKED,
        )
        db.add(step)
        db.flush()
        ope_step_repo.add_event(
            db, step=step, event_type=StepEventType.CREATED, to_state=StepState.LOCKED, source=source,
            actor_staff_id=actor_staff_id,
        )


# ==========================================
# 状態の変更（5.4）
# ==========================================
def set_state(
    db: Session, step: OpeStep, to: StepState, source: EventSource, actor_staff_id: int | None,
    reason: str | None = None, detail: dict[str, Any] | None = None,
) -> None:
    from_state = step.state
    step.state = to
    ope_step_repo.add_event(
        db, step=step, event_type=StepEventType.STATE_CHANGED, from_state=from_state, to_state=to, source=source,
        actor_staff_id=actor_staff_id, reason=reason, detail=detail,
    )


def open_first_step(db: Session, order: OpeOrder) -> None:
    """オーダーの開始で最初のステップを開く（システムの記録。ステップの無いオーダーは何もしない）"""
    active = ope_step_repo.get_active(db, order_id=order.id)
    if active and active[0].state == StepState.LOCKED:
        set_state(db, active[0], StepState.TODO, EventSource.SYSTEM, None, detail={"cause": "order started"})


def open_next_step(db: Session, order: OpeOrder, after: OpeStep, cause: str) -> None:
    """after の次のステップがまだ開いていなければ開く（差し戻し後の再提出などで、すでに開いていれば何もしない）"""
    for step in ope_step_repo.get_active(db, order_id=order.id):
        if step.step_num > after.step_num:
            if step.state == StepState.LOCKED:
                set_state(db, step, StepState.TODO, EventSource.SYSTEM, None, detail={"cause": cause, "after_step": after.step_num})
            return


def all_approved(db: Session, order: OpeOrder) -> bool:
    """ステップがあり、すべて承認済みか（ステップの無いオーダーは False: 人が完了にする）"""
    active = ope_step_repo.get_active(db, order_id=order.id)
    return bool(active) and all(s.state == StepState.APPROVED for s in active)


def is_unstarted_release(order: OpeOrder) -> bool:
    """リリース済みで、まだ開始していない（ステップがすべて locked のまま）オーダーか"""
    return order.released_at is not None and (
        order.status == OrderStatus.READY
        or (order.status == OrderStatus.ON_HOLD and order.status_before_hold == OrderStatus.READY)
    )


@dataclass
class StepSetChange:
    """固定したステップと、今の予定日で判定した条件の結果の違い（ステップ番号）"""
    added: list[int]
    removed: list[int]
    plan: StepPlan

    def describe(self) -> str:
        parts = []
        if self.added:
            parts.append("追加: " + ", ".join(str(n) for n in self.added))
        if self.removed:
            parts.append("除外: " + ", ".join(str(n) for n in self.removed))
        return "、".join(parts)


def step_set_change(db: Session, order: OpeOrder) -> StepSetChange | None:
    """リリース済みのオーダーのステップが、今の予定日での条件の結果と違えばその内容（同じなら None）"""
    version = db.get(SOPVersion, order.frozen_sop_version_id) if order.frozen_sop_version_id else None
    plan = plan_steps(db, order, version)
    current = {s.step_template_id: s.step_num for s in ope_step_repo.get_active(db, order_id=order.id)}
    wanted = {t.id: t.step_num for t in plan.included}
    if set(current) == set(wanted):
        return None
    return StepSetChange(
        added=sorted(n for tid, n in wanted.items() if tid not in current),
        removed=sorted(n for tid, n in current.items() if tid not in wanted),
        plan=plan,
    )


def rebuild_steps(
    db: Session, order: OpeOrder, change: StepSetChange, source: EventSource, actor_staff_id: int | None,
) -> dict[str, Any]:
    """
    開始前のオーダーのステップを、今の予定日の条件で作り直します（矛盾を「plan に合わせる」で解決したとき）。
    外れるステップは retired_at を付けて残し（記録あり）、新しく作る（両方にあるステップもいったん外して作り直す）。
    """
    assert is_unstarted_release(order)
    now = _now()
    for step in ope_step_repo.get_active(db, order_id=order.id):
        step.retired_at = now
        ope_step_repo.add_event(
            db, step=step, event_type=StepEventType.RETIRED, from_state=step.state, source=source,
            actor_staff_id=actor_staff_id,
        )
    db.flush()  # 外した行を先に書き、同じ番号の新しい行と一意性が衝突しないようにする
    _add_steps(db, order, change.plan, source, actor_staff_id)
    return {"added": change.added, "removed": change.removed, **change.plan.detail()}
