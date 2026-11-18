# tests/test_instance_revert_rules.py
"""
人の変更を取り消してルールに戻す（revert_instance）の決まりのうち、test_plan.py が確かめていないものを固定する
（R.6 / C901 で関数を分ける前に。分けた後も同じ結果になること）。
- 断るときの文面（6 とおり）と、解除済みのエラーコード
- 確かめる順番: 解除済みは、過去になった発生枠より先に断る
- 戻した後、末端のオーバーライドは REVERTED で無効になり、同じ発生枠がルールから作り直される（キャッシュも）
"""
import re
from datetime import datetime, timedelta

import pytest

from tests.test_plan import JST, biz_id, daily_template, db, live  # noqa: F401  (fixture は名前で使う)
from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, DuplicatedRecordError, NotFoundError
from src.domain_plan import services
from src.domain_plan.models import InstanceOrigin, VoidReason
from src.domain_plan.schemas import PlanInstanceCancel, PlanInstanceOverride, PlanInstanceRevert, PlanOneOffCreate


def _exact(message: str) -> str:
    return f"^{re.escape(message)}$"


def test_an_unknown_instance_is_not_found(db, biz_id):
    with pytest.raises(NotFoundError, match=_exact("PlanInstance(id=999999999) not found.")):
        services.revert_instance(db, 999999999, PlanInstanceRevert())


def test_an_instance_of_a_deleted_template_is_refused(db, biz_id):
    t = daily_template(db, biz_id)
    target = live(db, t.id)[3]
    services.cancel_instance(db, target.id, PlanInstanceCancel())
    services.delete_template(db, t.id)

    with pytest.raises(BadRequestError, match=_exact(f"PlanTemplate(id={t.id}) of this instance has been deleted.")):
        services.revert_instance(db, target.id, PlanInstanceRevert())
    db.rollback()


def test_a_one_off_instance_is_refused(db, biz_id):
    t = daily_template(db, biz_id)
    start = datetime.now(JST).replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(days=2)
    one = services.add_one_off_instance(db, t.id, PlanOneOffCreate(scheduled_start=start))

    message = "One-off instances are not tied to the rule, so they cannot be reverted. Cancel it instead."
    with pytest.raises(BadRequestError, match=_exact(message)):
        services.revert_instance(db, one.id, PlanInstanceRevert())
    db.rollback()


def test_refusal_messages_and_already_reverted_comes_before_the_past(db, biz_id, monkeypatch):
    t = daily_template(db, biz_id)
    untouched, target = live(db, t.id)[1], live(db, t.id)[5]

    message = f"PlanInstance(id={untouched.id}) has not been manually changed or cancelled."
    with pytest.raises(BadRequestError, match=_exact(message)):
        services.revert_instance(db, untouched.id, PlanInstanceRevert())
    db.rollback()

    services.cancel_instance(db, target.id, PlanInstanceCancel())
    services.revert_instance(db, target.id, PlanInstanceRevert())
    message = f"The manual change of PlanInstance(id={target.id}) has already been reverted."
    with pytest.raises(DuplicatedRecordError, match=_exact(message)) as caught:
        services.revert_instance(db, target.id, PlanInstanceRevert())
    assert caught.value.error_code == ErrorCode.INSTANCE_ALREADY_VOIDED
    db.rollback()
    # 解除済みの発生枠が過去になっても、先に「解除済み」で断る
    monkeypatch.setattr(services, "_now", lambda: target.scheduled_start + timedelta(hours=1))
    with pytest.raises(DuplicatedRecordError):
        services.revert_instance(db, target.id, PlanInstanceRevert())
    db.rollback()

    other = live(db, t.id)[7]
    monkeypatch.undo()
    services.cancel_instance(db, other.id, PlanInstanceCancel())
    monkeypatch.setattr(services, "_now", lambda: other.scheduled_start + timedelta(hours=1))
    message = "The original occurrence is in the past and cannot be regenerated. Cancel or override it instead."
    with pytest.raises(BadRequestError, match=_exact(message)):
        services.revert_instance(db, other.id, PlanInstanceRevert())
    db.rollback()


def test_reverting_an_override_chain_voids_the_end_and_restores_the_slot(db, biz_id):
    t = daily_template(db, biz_id)
    target = live(db, t.id)[4]
    first = services.override_instance(db, target.id, PlanInstanceOverride(scheduled_start=target.scheduled_start + timedelta(hours=1)))
    end = services.override_instance(db, first.id, PlanInstanceOverride(scheduled_start=target.scheduled_start + timedelta(hours=2)))

    result = services.revert_instance(db, target.id, PlanInstanceRevert(note="戻す"))  # 起点を指定しても同じ

    assert result.voided_instance is not None
    assert (result.voided_instance.id, result.voided_instance.void_reason, result.voided_instance.void_note) == (
        end.id, VoidReason.REVERTED, "戻す")
    restored = result.restored_instance
    assert restored is not None and restored.origin == InstanceOrigin.GENERATED
    assert (restored.occurrence_date, restored.scheduled_start) == (target.occurrence_date, target.scheduled_start)
    assert restored.id not in {target.id, first.id, end.id}
    assert [i.id for i in live(db, t.id) if i.occurrence_date == target.occurrence_date] == [restored.id]
