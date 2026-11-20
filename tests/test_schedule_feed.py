# tests/test_schedule_feed.py
"""
mfa への作業の開始の時刻表（GET /ops/schedule-feed。並行稼働）のテスト。
- 時刻は予定の開始。開始の順。窓は from から days 日先まで（タイムゾーンのない from は日本時間）
- 分類: 既定は不定期・スペシャルだけ（定例は出ない）。categories で変えられる。未設定の分類は定例。臨時の追加はスペシャル
- 状態: 未リリース・リリース済だけ。取消・保留・作業中・完了は出ない
- key・名前・締切。顧客の範囲。読み取り専用の API キーで読める。分類の値の確かめ
"""
from datetime import datetime, timedelta, UTC
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from tests.test_deadline_watch import biz_id, db, make_order  # noqa: F401  同じ準備（毎日 9:00 開始・締切は開始の 5 時間後）
from src.coordination.schedule_feed import get_schedule_feed
from src.core.exceptions import BadRequestError
from src.domain_ope.models import OpeOrder, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan.category import category_of
from src.domain_plan.schemas import PlanOneOffCreate, UIMetadata
from src.domain_staff.auth import api_key_allows
from src.domain_staff.models import RoleCode
from src.domain_staff.scope import ClientScope

JST = ZoneInfo("Asia/Tokyo")


def feed(db, o: OpeOrder, **kw):
    """この顧客の分だけ、その日の開始の 1 時間前から 1 日（テスト用 DB のほかのオーダーと混ざらないように）"""
    kw.setdefault("scope", ClientScope.of([o.client_code]))
    kw.setdefault("start", o.scheduled_start - timedelta(hours=1))
    kw.setdefault("days", 1)
    return get_schedule_feed(db, **kw)


def listed(response) -> set[int]:
    return {i.order_id for i in response.items}


def test_only_adhoc_and_special_tasks_by_default(db, biz_id):
    routine = make_order(db, biz_id)  # 分類なし = 定例
    adhoc = make_order(db, biz_id, ui_metadata={"category": "adhoc"})
    special = make_order(db, biz_id, ui_metadata={"category": "special"}, start_time=datetime(2000, 1, 1, 10, 0).time())
    marked_routine = make_order(db, biz_id, ui_metadata={"category": "regular", "bg_color": "#eeeeee"})
    result = feed(db, adhoc)
    assert listed(result) == {adhoc.id, special.id} and result.categories == ["adhoc", "special"]
    assert listed(feed(db, adhoc, categories=("regular",))) == {routine.id, marked_routine.id}
    assert listed(feed(db, adhoc, categories=("regular", "adhoc", "special"))) == {routine.id, adhoc.id, special.id, marked_routine.id}


def test_lines_start_at_the_scheduled_start_with_names_and_deadline(db, biz_id):
    a = make_order(db, biz_id, ui_metadata={"category": "adhoc"})
    b = make_order(db, biz_id, ui_metadata={"category": "special"}, start_time=datetime(2000, 1, 1, 8, 0).time())
    result = feed(db, a, start=b.scheduled_start - timedelta(minutes=5))
    assert [i.order_id for i in result.items] == [b.id, a.id]  # 開始の順
    item = next(i for i in result.items if i.order_id == a.id)
    assert (item.scheduled_start, item.deadline_at, item.category, item.one_off, item.status) == (
        a.scheduled_start, a.deadline_at, "adhoc", False, OrderStatus.PENDING,
    )
    assert (item.title, item.biz_name, item.client_name, item.client_code) == ("Watch daily", "watch biz", "watch client", a.client_code)
    assert item.biz_code and item.occurrence_date == a.occurrence_date
    assert item.key == f"order:{a.id}:start:{a.scheduled_start.astimezone(UTC).isoformat()}"


def test_only_orders_that_still_need_doing(db, biz_id):
    orders = [make_order(db, biz_id, ui_metadata={"category": "adhoc"}, index=i) for i in (3, 4, 5, 6, 7)]
    ready, started, held, done, gone = orders
    now = datetime.now(UTC)
    ready.status, ready.released_at = OrderStatus.READY, now
    started.status, started.released_at = OrderStatus.IN_PROGRESS, now
    held.status, held.status_before_hold = OrderStatus.ON_HOLD, OrderStatus.PENDING
    done.status, done.released_at, done.finished_at = OrderStatus.FINISHED, now, now
    gone.status = OrderStatus.CANCELED
    db.flush()
    result = feed(db, ready, start=ready.scheduled_start - timedelta(hours=1), days=7)
    assert {o.id for o in orders} & listed(result) == {ready.id}
    assert next(i for i in result.items if i.order_id == ready.id).status == OrderStatus.READY


def test_one_off_additions_count_as_special(db, biz_id):
    routine = make_order(db, biz_id)  # 定例のテンプレート
    start = routine.scheduled_start + timedelta(hours=3)
    plan_services.add_one_off_instance(db, routine.template_id, PlanOneOffCreate(scheduled_start=start))
    db.expire_all()
    result = feed(db, routine)
    [one_off] = [i for i in result.items if i.scheduled_start == start]
    assert (one_off.category, one_off.one_off) == ("special", True)
    assert routine.id not in listed(result)  # テンプレートの予定は定例のまま出ない


def test_window_and_japan_time_start(db, biz_id):
    o = make_order(db, biz_id, ui_metadata={"category": "adhoc"})
    assert o.id not in listed(feed(db, o, start=o.scheduled_start + timedelta(minutes=1)))  # 過ぎた開始は出ない
    assert o.id in listed(feed(db, o, start=o.scheduled_start))  # 窓の始まりちょうどは出る
    assert o.id not in listed(feed(db, o, start=o.scheduled_start - timedelta(days=1)))  # 1 日の窓の外（次の日の始まりは含まない）
    naive = o.scheduled_start.astimezone(JST).replace(tzinfo=None) - timedelta(minutes=10)
    result = feed(db, o, start=naive)
    assert o.id in listed(result) and result.window_start.utcoffset() == timedelta(hours=9)
    assert result.window_end - result.window_start == timedelta(days=1)


def test_client_scope_limit_and_the_agent_key(db, biz_id):
    o = make_order(db, biz_id, ui_metadata={"category": "adhoc"})
    assert listed(feed(db, o, scope=ClientScope.of(["ZZZ9"]))) == set()
    one = feed(db, o, days=7, limit=1)
    assert len(one.items) == 1 and one.truncated

    class Agent:
        role_codes = (RoleCode.SYSTEM_AGENT,)
    assert api_key_allows(Agent(), ("GET", "/ops/schedule-feed"))  # pyright: ignore[reportArgumentType]


def test_category_values_are_checked_and_unknown_means_regular():
    assert UIMetadata.model_validate({"category": "special"}).to_dict() == {"category": "special"}
    with pytest.raises(ValidationError):
        UIMetadata.model_validate({"category": "urgent"})
    assert category_of(None) == "regular" and category_of({"category": "weird"}) == "regular" and category_of({"category": "adhoc"}) == "adhoc"


# ==========================================
# 開始時刻に知らせる（start_prompt）
# ==========================================
def test_start_prompt_defaults_by_business_and_filters_the_feed(db, biz_id):
    from src.domain_biz.models import BizMaster, ExecutionType
    from src.domain_plan.models import PlanTemplate

    fixed = make_order(db, biz_id, ui_metadata={"category": "adhoc"})
    waiting = make_order(db, biz_id, ui_metadata={"category": "adhoc"}, start_prompt=False)  # ファイルを待つ作業
    assert db.get(PlanTemplate, fixed.template_id).start_prompt is True  # pyright: ignore[reportOptionalMemberAccess]
    assert db.get(PlanTemplate, waiting.template_id).start_prompt is False  # pyright: ignore[reportOptionalMemberAccess]
    result = feed(db, fixed)
    assert fixed.id in listed(result) and waiting.id not in listed(result)

    # 顧客側の出来事（外からの合図を待つ）の業務のテンプレートは、指定がなければオフ
    biz = db.get(BizMaster, biz_id)
    assert biz is not None
    biz.execution_type = ExecutionType.LIGHTWEIGHT
    db.flush()
    receipt = make_order(db, biz_id, ui_metadata={"category": "special"})
    assert db.get(PlanTemplate, receipt.template_id).start_prompt is False  # pyright: ignore[reportOptionalMemberAccess]
    assert receipt.id not in listed(feed(db, receipt))
    forced = make_order(db, biz_id, ui_metadata={"category": "special"}, start_prompt=True)  # 明示すればオンにできる
    assert forced.id in listed(feed(db, forced))


def test_one_off_additions_follow_their_templates_start_prompt(db, biz_id):
    waiting = make_order(db, biz_id, start_prompt=False)
    start = waiting.scheduled_start + timedelta(hours=3)
    plan_services.add_one_off_instance(db, waiting.template_id, PlanOneOffCreate(scheduled_start=start))
    db.expire_all()
    assert not [i for i in feed(db, waiting).items if i.scheduled_start == start]


def test_changing_start_prompt_does_not_regenerate_and_is_in_the_history(db, biz_id):
    from src.domain_plan.models import PlanTemplate
    from src.domain_plan.schemas import PlanTemplateUpdate
    from src.domain_plan.services import template_settings

    o = make_order(db, biz_id, ui_metadata={"category": "adhoc"})
    template = db.get(PlanTemplate, o.template_id)
    assert template is not None
    revision, instance = template.revision, o.current_instance_id
    plan_services.update_template(db, template.id, PlanTemplateUpdate(start_prompt=False))
    db.expire_all()
    template = db.get(PlanTemplate, o.template_id)
    order = db.get(OpeOrder, o.id)
    assert template is not None and order is not None
    assert template.start_prompt is False and template.revision == revision  # 予定を作り直さない
    assert order.current_instance_id == instance and order.status == OrderStatus.PENDING
    assert template_settings(template)["start_prompt"] is False  # 変更の前後・履歴に入る項目
    assert o.id not in listed(feed(db, o))
    with pytest.raises(BadRequestError, match="must not be null"):
        plan_services.update_template(db, template.id, PlanTemplateUpdate.model_validate({"start_prompt": None}))  # null は不可
