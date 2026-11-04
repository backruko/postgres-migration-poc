# tests/test_biz.py
"""
domain_biz のテスト（業務のリリース設定）。

※ Service層は内部で commit するため、テストごとに一意な顧客コードを使ってデータを分離しています。
"""
import uuid
from datetime import time

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from src.domain_biz import services
from src.domain_biz.models import ReleaseMode
from src.domain_biz.schemas import BizMasterCreate, BizMasterResponse, BizMasterUpdate
from src.domain_client.schemas import ClientCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import client_repo


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def biz(db):
    # test_plan.py ("T") / test_ops.py ("D") の顧客とコードが衝突しないよう、別の先頭文字を使う
    code = "B" + uuid.uuid4().hex[:3].upper()
    client_repo.create(db, obj_in=ClientCreate(code=code, name="biz test client"))
    return services.create_biz(
        db, BizMasterCreate(client_code=code, name="release test", business_seq=1, manager_email=None, description=None)
    )


def test_new_business_gets_default_release_settings(biz):
    # 新規作成は既定値: 手動承認・03:00（夜間バッチの後）・予定日当日
    assert biz.release_mode == ReleaseMode.MANUAL_APPROVAL
    assert biz.release_time == time(3, 0) and biz.release_days_before == 0
    response = BizMasterResponse.model_validate(biz)
    assert response.release_mode == ReleaseMode.MANUAL_APPROVAL and response.release_time == time(3, 0)


def test_release_settings_are_updated_by_patch(db, biz):
    updated = services.update_biz(db, biz.id, BizMasterUpdate.model_validate(
        {"release_mode": ReleaseMode.AUTOMATIC, "release_time": time(21, 30), "release_days_before": 1}
    ))
    assert (updated.release_mode, updated.release_time, updated.release_days_before) == (ReleaseMode.AUTOMATIC, time(21, 30), 1)

    # 指定しなかった項目は変わらない
    renamed = services.update_biz(db, biz.id, BizMasterUpdate.model_validate({"name": "renamed"}))
    assert renamed.name == "renamed" and renamed.release_mode == ReleaseMode.AUTOMATIC and renamed.release_days_before == 1


def test_release_settings_validation(db, biz):
    with pytest.raises(ValidationError, match="must not be null"):
        BizMasterUpdate.model_validate({"release_mode": None})
    with pytest.raises(ValidationError):
        BizMasterUpdate.model_validate({"release_days_before": 31})
    with pytest.raises(ValidationError):
        BizMasterUpdate.model_validate({"release_days_before": -1})
    with pytest.raises(ValidationError):
        BizMasterUpdate.model_validate({"release_mode": "whenever"})
    # 名前・説明は従来どおり null を送れる（release の項目だけ null を拒否）
    assert BizMasterUpdate.model_validate({"description": None}).description is None

    # DB の CHECK 制約でも範囲外を拒否する（スキーマを通らない直接の更新に備える）
    biz.release_days_before = 99
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ==========================================
# ステップの条件・is_blocking（5.1）
# ==========================================
def test_step_template_condition_and_is_blocking(db, biz):
    from datetime import datetime, timedelta, timezone as tz
    from src.domain_biz.models import SOPStatus, SOPVersion
    from src.domain_biz.schemas import SOPStepTemplateCreate, SOPStepTemplateResponse, SOPVersionCreate

    draft = services.create_new_sop_version(db, biz.id, SOPVersionCreate(version_tag="v1.0.0"))
    plain = services.create_step_template(db, draft.id, SOPStepTemplateCreate.model_validate({"step_num": 1, "title": "データ確認"}))
    annual = services.create_step_template(db, draft.id, SOPStepTemplateCreate.model_validate(
        {"step_num": 2, "title": "年次照合", "condition": {"type": "month_in", "months": [4, 10, 4]}, "is_blocking": True}
    ))
    weekly = services.create_step_template(db, draft.id, SOPStepTemplateCreate.model_validate(
        {"step_num": 3, "title": "週次まとめ", "condition": {"type": "first_in_week"}}
    ))
    # 既定は always・ブロックしない。月は重複を除いて並べる
    assert (plain.condition, plain.is_blocking) == ({"type": "always"}, False)
    assert (annual.condition, annual.is_blocking) == ({"type": "month_in", "months": [4, 10]}, True)
    assert weekly.condition == {"type": "first_in_week"}
    assert SOPStepTemplateResponse.model_validate(annual).condition["months"] == [4, 10]

    for bad in ({"type": "month_in", "months": []}, {"type": "month_in", "months": [13]}, {"type": "sometimes"}):
        with pytest.raises(ValidationError):
            SOPStepTemplateCreate.model_validate({"step_num": 9, "title": "x", "condition": bad})

    # 現行版を複製した草稿にも、条件と is_blocking が引き継がれる
    draft.status = SOPStatus.ACTIVE
    draft.effective_at = datetime.now(tz.utc) - timedelta(days=1)
    db.commit()
    clone = services.clone_active_to_draft(db, biz.id, SOPVersionCreate(version_tag="v1.1.0"))
    copied = {s.step_num: (s.condition, s.is_blocking) for s in services.get_steps_by_version(db, clone.id)}
    assert copied == {1: ({"type": "always"}, False), 2: ({"type": "month_in", "months": [4, 10]}, True), 3: ({"type": "first_in_week"}, False)}
