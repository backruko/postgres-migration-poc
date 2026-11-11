from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from sqlalchemy import func, select, and_, or_
from sqlalchemy.exc import IntegrityError

from src.infrastructure.repositories import (
    biz_dependency_repo, biz_master_repo, biz_release_change_repo, client_repo, sop_version_event_repo, sop_version_repo,
    sop_step_repo,
)
from src.domain_biz.models import (
    RELEASE_FIELDS, BizDependency, BizMaster, CompletionMode, ExecutionType, SOPVersion, SOPVersionEventType,
    SOPStepTemplate, SOPStatus, SopState,
)
from src.domain_biz.schemas import (
    BizDependencyCreate, BizDependencyResponse, BizMasterCreate, BizMasterUpdate, BizRef, BizReleaseChangeResponse,
    BizReleaseUpdate, BizReleaseValues, SOPVersionCreate, SOPVersionEventResponse, SOPVersionResponse,
    SOPStepOrderIn, SOPStepTemplateCreate, SOPStepTemplateUpdate, SOPVersionUpdate, SubmittedSOPDraftResponse,
)
from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, DuplicatedRecordError, ForbiddenError, NotFoundError
from src.domain_biz import delete_checks, dependency_events, version_checks
from src.domain_client.services import require_active_client

LOCAL_TIMEZONE = ZoneInfo("Asia/Tokyo")  # SOP の発効日・業務の「今日」は日本時間

# ==========================================
# BizMaster Service
# ==========================================
MAX_BUSINESS_SEQ = 9999  # 業務コード = 顧客コード + 連番4桁（LPAD 4）のため


def create_biz(db: Session, biz_in: BizMasterCreate) -> BizMaster:
    # 顧客が有効であること。顧客の行をロックして採番する（同時の登録・無効化と重ならない）
    client = require_active_client(db, biz_in.client_code, lock=True)
    used = biz_master_repo.max_business_seq(db, client_code=client.code)
    if biz_in.business_seq is None:
        # 次の空き番号 = 削除された業務も含めた最大値 + 1（業務コードは使い回さない）
        if used >= MAX_BUSINESS_SEQ:
            raise BadRequestError(f"Client {client.code} has no business number left (max {MAX_BUSINESS_SEQ}).")
        biz_in = biz_in.model_copy(update={"business_seq": used + 1})
    else:
        taken = db.scalars(
            select(BizMaster)
            .where(BizMaster.client_code == client.code, BizMaster.business_seq == biz_in.business_seq)
            .execution_options(include_deleted=True)
        ).first()
        if taken:
            state = "a deleted business" if taken.is_deleted else "another business"
            raise DuplicatedRecordError(f"Business code {taken.business_code} is already used by {state}; codes are never reused.")
    try:
        return biz_master_repo.create(db, obj_in=biz_in)
    except IntegrityError:
        # 重複エラーが発生した場合、データベースのロールバックはリポジトリまたは共通処理に任せる前提
        raise DuplicatedRecordError(f"BizMaster with unique key already exists.")

def get_biz(db: Session, biz_id: int) -> BizMaster:
    db_obj = biz_master_repo.get(db, id=biz_id)
    if not db_obj:
        raise NotFoundError(f"BizMaster(id={biz_id}) not found.")
    return db_obj

def get_bizes(
    db: Session, skip: int = 0, limit: int = 100, client_code: str | None = None, include_deleted: bool = False,
) -> list[BizMaster]:
    code = client_code.strip().upper() if client_code else None
    return biz_master_repo.get_list(db, client_code=code, include_deleted=include_deleted, skip=skip, limit=limit)

def _check_client_event_settings(db: Session, biz: BizMaster, biz_in: BizMasterUpdate) -> None:
    """
    顧客側の出来事の設定（Phase 7）を、変更後の値で確かめる（DB の CHECK・一意制約の前に、分かりやすい理由で返す）。
    信号での完了は LIGHTWEIGHT のみ・信号の種類が必須・信号の種類は同じ顧客の業務どうしで一意。
    """
    data = biz_in.model_dump(exclude_unset=True)
    execution = data.get("execution_type", biz.execution_type)
    completion = data.get("completion_mode", biz.completion_mode)
    signal = data.get("signal_type", biz.signal_type)
    if completion == CompletionMode.AUTOMATIC and execution != ExecutionType.LIGHTWEIGHT:
        raise BadRequestError("Automatic completion (by signal) is only for LIGHTWEIGHT businesses.")
    if completion == CompletionMode.AUTOMATIC and not signal:
        raise BadRequestError("signal_type is required for automatic completion.")
    if completion != CompletionMode.AUTOMATIC and signal:
        raise BadRequestError("signal_type is only for automatic completion; clear it (null) or set completion_mode AUTOMATIC.")
    if signal:
        other = db.scalars(select(BizMaster).where(
            BizMaster.client_code == biz.client_code, BizMaster.signal_type == signal, BizMaster.id != biz.id,
        )).first()
        if other:
            raise DuplicatedRecordError(f"Signal type '{signal}' is already used by {other.business_code} of client {biz.client_code}.")


def update_biz(db: Session, biz_id: int, biz_in: BizMasterUpdate, actor_id: int | None = None) -> BizMaster:
    """業務の設定を変えます（業務管理者）。リリース設定が変わったら、その履歴も同じトランザクションで残す"""
    db_obj = _get_biz_locked(db, biz_id)
    _check_client_event_settings(db, db_obj, biz_in)
    sent = biz_in.model_dump(exclude_unset=True)
    _record_release_change(db, db_obj, {f: sent[f] for f in RELEASE_FIELDS if f in sent}, actor_id)

    try:
        return biz_master_repo.update(db, db_obj=db_obj, obj_in=biz_in)
    except IntegrityError:
        raise DuplicatedRecordError(f"Update failed: Unique constraint violation.")


# ==========================================
# リリース設定（細かな権限、P.3、2026-11-11）: リリース担当も変えられる。どの API で変えても履歴を残す
# ==========================================
def _get_biz_locked(db: Session, biz_id: int) -> BizMaster:
    """業務を行ロック付きで（変更前の値を確かに読むため。同時の変更は順に）"""
    biz = db.scalars(select(BizMaster).where(BizMaster.id == biz_id).with_for_update()).first()
    if not biz:
        raise NotFoundError(f"BizMaster(id={biz_id}) not found for update.")
    return biz


def _release_values(mode, release_time, days_before) -> dict:
    """リリース設定の 3 項目を JSON にできる形で（履歴の before / after）"""
    return {
        "release_mode": mode.value if hasattr(mode, "value") else str(mode),
        "release_time": release_time.isoformat(),
        "release_days_before": int(days_before),
    }


def _record_release_change(db: Session, biz: BizMaster, changes: dict, actor_id: int | None) -> None:
    """リリース設定の変更（送られた値）が今の値と違えば、履歴を 1 行足す（書き出しは呼び出し側のコミットで）"""
    if not changes:
        return
    current = {f: getattr(biz, f) for f in RELEASE_FIELDS}
    before = _release_values(*(current[f] for f in RELEASE_FIELDS))
    after = _release_values(*(changes.get(f, current[f]) for f in RELEASE_FIELDS))
    if before != after:
        biz_release_change_repo.add(db, biz_id=biz.id, changed_by_id=actor_id, before=before, after=after)


def update_release(db: Session, biz_id: int, release_in: BizReleaseUpdate, actor_id: int | None) -> BizMaster:
    """業務のリリース設定（モード・時刻・何日前）だけを変えます（リリース担当・業務管理者）。すぐに効く"""
    biz = _get_biz_locked(db, biz_id)
    changes = release_in.model_dump(exclude_unset=True)
    _record_release_change(db, biz, changes, actor_id)
    for field, value in changes.items():
        setattr(biz, field, value)
    db.commit()
    db.refresh(biz)
    return biz


def list_release_changes(db: Session, biz_id: int) -> list[BizReleaseChangeResponse]:
    """業務のリリース設定の変更履歴（新しい順。削除された業務は 404）"""
    get_biz(db, biz_id)
    rows = biz_release_change_repo.list_for_biz(db, biz_id=biz_id)
    names = staff_names(db, {r.changed_by_id for r in rows})
    return [
        BizReleaseChangeResponse(
            id=r.id, biz_master_id=r.biz_master_id, changed_by_id=r.changed_by_id, changed_by_name=names.get(r.changed_by_id),
            changed_at=r.changed_at, before=BizReleaseValues.model_validate(r.before_values),
            after=BizReleaseValues.model_validate(r.after_values),
        )
        for r in rows
    ]


# ==========================================
# 職員の名前（記録・一覧に付ける。まとめて 1 回で読む）
# ==========================================
def staff_names(db: Session, ids: set[int | None]) -> dict[int | None, str]:
    from src.domain_staff.models import StaffUser  # biz → staff は名前を読むだけ（モデルの循環を避ける）

    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    return {row.id: row.name for row in db.execute(select(StaffUser.id, StaffUser.name).where(StaffUser.id.in_(wanted)))}

def delete_biz(db: Session, biz_id: int) -> BizMaster:
    """
    業務を論理削除します。使われている間（依存関係の定義・テンプレート・未完了のオーダーが残る間）は断ります（M1.1）。
    テンプレートを先に削除すると、未リリース・リリース済のオーダーは取消になります。
    """
    biz = biz_master_repo.get(db, id=biz_id)
    if not biz:
        # 見つからない(None)なら、ビジネスエラーに変換して raise する役割
        raise NotFoundError(f"BizMaster(id={biz_id}) not found for deletion.")
    reasons: list[str] = []
    dependencies = biz_dependency_repo.get_for_biz(db, biz_id=biz.id)
    if dependencies:
        reasons.append(f"{len(dependencies)} dependency definition(s)")
    reasons.extend(delete_checks.reasons_not_to_delete(db, biz))
    if reasons:
        raise BadRequestError(f"Business {biz.business_code} is still in use: " + "; ".join(reasons) + ". Remove them first.")
    deleted_obj = biz_master_repo.remove(db, id=biz_id)
    if not deleted_obj:
        raise NotFoundError(f"BizMaster(id={biz_id}) not found for deletion.")
    return deleted_obj

def restore_biz(db: Session, biz_id: int) -> BizMaster:
    """
    論理削除された BizMaster レコードを復元します（顧客が有効なときだけ）。
    """
    deleted = db.scalars(
        select(BizMaster).where(BizMaster.id == biz_id, BizMaster.is_deleted.is_(True)).execution_options(include_deleted=True)
    ).first()
    if deleted:
        require_active_client(db, deleted.client_code, lock=True)
    # 1. Repositoryのrestoreを呼び出す
    # （内部でスコープ解除、復元、重複チェック、DuplicatedRecordErrorの送出まで完結しています）
    restored_obj = biz_master_repo.restore(db, id=biz_id)
    
    # 2. そもそもデータが存在しない、または既に有効なデータだった場合は None が返ってくるので、
    # 適切なビジネスエラー（NotFoundError）に変換して raise します
    if not restored_obj:
        raise NotFoundError(f"BizMaster(id={biz_id}) not found or already active.")
        
    return restored_obj

# ==========================================
# SOPVersion Service
# ==========================================
def create_new_sop_version(db: Session, biz_id: int, version_in: SOPVersionCreate, actor_id: int | None = None) -> SOPVersion:
    """
    【新規立ち上げ用】業務(BizMaster)に対して、完全に新しい最初の草稿(DRAFT)を作成します。
    コピー元がない、一番最初のまっさらな手順書を作るときに使用します。
    """
    # 1. 親(BizMaster)が存在するかチェック
    db_biz = biz_master_repo.get(db, id=biz_id)
    if not db_biz:
        raise NotFoundError(f"BizMaster(id={biz_id})が見つかりません。")

    # 2. 最初の作成なので、システム用の内部連番は「1」からスタート
    # 万が一、すでにデータがある場合は「最大値 + 1」にするガードを入れておくと安全です
    stmt_max = select(SOPVersion.version_number).where(SOPVersion.biz_master_id == biz_id).order_by(SOPVersion.version_number.desc()).limit(1)
    max_num = db.scalars(stmt_max).first()
    new_version_number = (max_num + 1) if max_num else 1

    try:
        new_draft = SOPVersion(
            biz_master_id=biz_id,
            version_number=new_version_number,
            version_tag=version_in.version_tag,  # ユーザーが指定した「v1.0.0」など
            status=SOPStatus.DRAFT,              # 最初は下書きからスタート
            effective_at=None,
            expired_at=None,
            **_authored(actor_id),
        )
        db.add(new_draft)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise DuplicatedRecordError(f"Version tag '{version_in.version_tag}' already exists for this business.")
    sop_version_event_repo.add(db, version=new_draft, event_type=SOPVersionEventType.CREATED, actor_id=actor_id)
    db.commit()
    db.refresh(new_draft)
    return new_draft


# ==========================================
# SOPStepTemplate Service
# ==========================================

def get_steps_by_version(db: Session, version_id: int) -> list[SOPStepTemplate]:
    """特定のSOPバージョンに含まれるステップ一覧を、実行順序(step_num)通りに並べて取得します。"""
    db_version = sop_version_repo.get(db, id=version_id)
    if not db_version:
        raise NotFoundError(f"SOPVersion(id={version_id}) not found.")
        
    stmt = (
        select(SOPStepTemplate)
        .where(SOPStepTemplate.sop_version_id == version_id)
        .order_by(SOPStepTemplate.step_num.asc())  # ステップ順に綺麗にソート
    )
    return list(db.scalars(stmt).all())


# ==========================================
# 0. 【現在時刻基準】今まさに有効な現行バージョンを取得するクエリ
# ==========================================
def list_approved_versions(db: Session, biz_id: int) -> list[SOPVersion]:
    """業務の承認済み（草稿以外）のバージョン、新しい順。オーダーに指定（pin）できるバージョンの選択肢に使います。"""
    get_biz(db, biz_id)  # 存在しない・削除済みの業務は 404
    return sop_version_repo.get_approved(db, biz_master_id=biz_id)


def get_current_active_version(db: Session, biz_id: int) -> SOPVersion:
    """現在日時を基準に、自動的に『今有効な現行バージョン』を1件取得します。"""
    now = datetime.now(timezone.utc)

    stmt = (
        select(SOPVersion)
        .where(
            SOPVersion.biz_master_id == biz_id,
            SOPVersion.status != SOPStatus.DRAFT, 
            SOPVersion.effective_at <= now,
            or_(SOPVersion.expired_at == None, SOPVersion.expired_at > now)
        )
        .order_by(SOPVersion.version_number.desc())
        .limit(1)
    )
    current_version = db.scalars(stmt).first()
    if not current_version:
        raise NotFoundError(f"現在有効なSOPバージョン(BizMaster ID: {biz_id})が見つかりません。")
    return current_version


# ==========================================
# 1. 新しい版: 最新の承認済みの版（予定があればそれ、なければ現行）を写した草稿。承認済みが無ければ空（M2.2）
# ==========================================
def _authored(actor_id: int | None) -> dict:
    """新しい草稿の作った人・最後に直した人と日時"""
    return {"created_by_id": actor_id, "updated_by_id": actor_id, "updated_at": datetime.now(timezone.utc)}


def _touch(draft: SOPVersion, actor_id: int | None) -> None:
    """草稿を直した人と日時（タグ・ステップの変更のたび）"""
    draft.updated_by_id = actor_id
    draft.updated_at = datetime.now(timezone.utc)


def _next_version_number(db: Session, biz_id: int) -> int:
    max_num = db.scalar(select(func.max(SOPVersion.version_number)).where(SOPVersion.biz_master_id == biz_id))
    return (max_num or 0) + 1


def _newest_approved(db: Session, biz_id: int) -> SOPVersion | None:
    """発効日時の最も新しい承認済みの版（予定の版があればそれ。過去の版になっていても、それより新しい版が無ければそれ）"""
    return db.scalars(
        select(SOPVersion)
        .where(SOPVersion.biz_master_id == biz_id, SOPVersion.status == SOPStatus.APPROVED)
        .order_by(SOPVersion.effective_at.desc(), SOPVersion.version_number.desc())
        .limit(1)
    ).first()


def clone_active_to_draft(db: Session, biz_id: int, version_in: SOPVersionCreate, actor_id: int | None = None) -> SOPVersion:
    """
    新しい草稿を作ります（リリース担当・業務管理者）。最新の承認済みの版のステップ（条件・承認で次へも）を写す。
    承認済みの版がまだ無い業務（最初の SOP）は、空の草稿になる。草稿は複数並べて作れる。
    """
    get_biz(db, biz_id)
    source = _newest_approved(db, biz_id)
    draft = SOPVersion(
        biz_master_id=biz_id, version_number=_next_version_number(db, biz_id), version_tag=version_in.version_tag,
        status=SOPStatus.DRAFT, effective_at=None, expired_at=None, **_authored(actor_id),
    )
    db.add(draft)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise DuplicatedRecordError(f"Version tag '{version_in.version_tag}' already exists for this business.")
    if source is not None:
        for step in sorted(source.step_templates, key=lambda s: s.step_num):
            db.add(SOPStepTemplate(
                sop_version_id=draft.id, step_num=step.step_num, title=step.title, content=step.content,
                condition=step.condition, is_blocking=step.is_blocking,
            ))
    sop_version_event_repo.add(
        db, version=draft, event_type=SOPVersionEventType.CREATED, actor_id=actor_id,
        detail={"cloned_from_version_id": source.id, "cloned_from_tag": source.version_tag} if source is not None else None,
    )
    db.commit()
    db.refresh(draft)
    return draft


# ==========================================
# 2. 草稿の編集（タグ・ステップの追加 / 変更 / 削除 / 並べ替え・草稿の削除）。承認済みの版は変えられない（M2.2）
# ==========================================
def list_versions(db: Session, biz_id: int, include_drafts: bool = False) -> list[SOPVersion]:
    """業務の版、新しい順。include_drafts=False なら承認済みだけ（オーダーに指定できる選択肢）"""
    get_biz(db, biz_id)  # 存在しない・削除済みの業務は 404
    if not include_drafts:
        return sop_version_repo.get_approved(db, biz_master_id=biz_id)
    return list(db.scalars(
        select(SOPVersion).where(SOPVersion.biz_master_id == biz_id).order_by(SOPVersion.version_number.desc())
    ).all())


def get_version(db: Session, version_id: int) -> SOPVersion:
    version = sop_version_repo.get(db, id=version_id)
    if not version:
        raise NotFoundError(f"SOPVersion(id={version_id}) not found.")
    return version


def _get_draft(db: Session, version_id: int, *, lock: bool = False, allow_submitted: bool = False) -> SOPVersion:
    """
    草稿（承認済みの版は 400）。承認を依頼中の草稿は、allow_submitted でなければ直せない（409 SOP_DRAFT_SUBMITTED:
    依頼した内容のまま判断されるよう。取り下げか差し戻しで草稿に戻る）
    """
    stmt = select(SOPVersion).where(SOPVersion.id == version_id)
    version = db.scalars(stmt.with_for_update() if lock else stmt).first()
    if not version:
        raise NotFoundError(f"SOPVersion(id={version_id}) not found.")
    if version.status != SOPStatus.DRAFT:
        raise BadRequestError(f"{version.version_tag} is approved and can no longer be changed; edit a new draft instead.")
    if version.submitted_at is not None and not allow_submitted:
        raise DuplicatedRecordError(
            f"{version.version_tag} is waiting for approval and cannot be changed; withdraw it first.",
            error_code=ErrorCode.SOP_DRAFT_SUBMITTED,
        )
    return version


def _steps_of(db: Session, version_id: int) -> list[SOPStepTemplate]:
    return list(db.scalars(
        select(SOPStepTemplate).where(SOPStepTemplate.sop_version_id == version_id).order_by(SOPStepTemplate.step_num)
    ).all())


def rename_draft(db: Session, version_id: int, version_in: SOPVersionUpdate, actor_id: int | None = None) -> SOPVersion:
    draft = _get_draft(db, version_id, lock=True)
    draft.version_tag = version_in.version_tag
    _touch(draft, actor_id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicatedRecordError(f"Version tag '{version_in.version_tag}' already exists for this business.")
    db.refresh(draft)
    return draft


def delete_draft(db: Session, version_id: int) -> SOPVersion:
    """草稿を削除します（ステップごと）。草稿はオーダーに使われない"""
    draft = _get_draft(db, version_id, lock=True)
    db.delete(draft)
    db.commit()
    return draft


def create_step_template(db: Session, version_id: int, step_in: SOPStepTemplateCreate, actor_id: int | None = None) -> SOPStepTemplate:
    """草稿にステップを追加します。番号を省くと最後（最大 + 1）"""
    draft = _get_draft(db, version_id, lock=True)  # 同時の追加で同じ番号にならないよう版をロック
    steps = _steps_of(db, draft.id)
    step_num = step_in.step_num if step_in.step_num is not None else max((s.step_num for s in steps), default=0) + 1
    if any(s.step_num == step_num for s in steps):
        raise DuplicatedRecordError(f"Step number {step_num} already exists in {draft.version_tag}.")
    step = SOPStepTemplate(
        sop_version_id=draft.id, step_num=step_num, title=step_in.title, content=step_in.content,
        condition=step_in.condition.model_dump(), is_blocking=step_in.is_blocking,
    )
    db.add(step)
    _touch(draft, actor_id)
    db.commit()
    db.refresh(step)
    return step


def _get_draft_step(db: Session, step_id: int) -> tuple[SOPStepTemplate, SOPVersion]:
    step = db.get(SOPStepTemplate, step_id)
    if step is None:
        raise NotFoundError(f"SOPStepTemplate(id={step_id}) not found.")
    return step, _get_draft(db, step.sop_version_id, lock=True)


def update_step_template(db: Session, step_id: int, step_in: SOPStepTemplateUpdate, actor_id: int | None = None) -> SOPStepTemplate:
    """草稿のステップの変更（送った項目だけ）"""
    step, draft = _get_draft_step(db, step_id)
    data = step_in.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(step, field, value)
    _touch(draft, actor_id)
    db.commit()
    db.refresh(step)
    return step


def delete_step_template(db: Session, step_id: int, actor_id: int | None = None) -> SOPStepTemplate:
    """草稿のステップの削除（残りの番号はそのまま。詰めるときは並べ替え）"""
    step, draft = _get_draft_step(db, step_id)
    db.delete(step)
    _touch(draft, actor_id)
    db.commit()
    return step


def reorder_steps(db: Session, version_id: int, order_in: SOPStepOrderIn, actor_id: int | None = None) -> list[SOPStepTemplate]:
    """草稿のステップを、渡された id の順に 1 から振り直します（すべてのステップの id がちょうど 1 回ずつ必要）"""
    draft = _get_draft(db, version_id, lock=True)
    steps = {s.id: s for s in _steps_of(db, draft.id)}
    if sorted(order_in.step_ids) != sorted(steps):
        raise BadRequestError(f"step_ids must list every step of {draft.version_tag} exactly once ({sorted(steps)}).")
    # 一意制約（版 × 番号）にぶつからないよう、いったん大きな番号へ逃がしてから振り直す
    offset = max(s.step_num for s in steps.values()) + len(steps)
    for n, step_id in enumerate(order_in.step_ids, start=1):
        steps[step_id].step_num = offset + n
    db.flush()
    for n, step_id in enumerate(order_in.step_ids, start=1):
        steps[step_id].step_num = n
    _touch(draft, actor_id)
    db.commit()
    return _steps_of(db, draft.id)


# ==========================================
# 2b. 承認の依頼（P.2、2026-11-11）: リリース担当が書いた草稿を、業務管理者に判断してもらう
# ==========================================
def submit_draft(db: Session, version_id: int, actor_id: int) -> SOPVersion:
    """
    草稿の承認を依頼します（リリース担当・業務管理者）。依頼中は誰も直せない。ステップの無い草稿は依頼できない
    （承認できないため）。もう依頼中なら 409 SOP_DRAFT_SUBMITTED。
    """
    draft = _get_draft(db, version_id, lock=True)
    if not draft.step_templates:
        raise BadRequestError(f"{draft.version_tag} has no steps; add at least one step before asking for approval.")
    draft.submitted_by_id = actor_id
    draft.submitted_at = datetime.now(timezone.utc)
    sop_version_event_repo.add(db, version=draft, event_type=SOPVersionEventType.SUBMITTED, actor_id=actor_id)
    db.commit()
    db.refresh(draft)
    return draft


def _get_submitted(db: Session, version_id: int) -> SOPVersion:
    draft = _get_draft(db, version_id, lock=True, allow_submitted=True)
    if draft.submitted_at is None:
        raise DuplicatedRecordError(
            f"{draft.version_tag} is not waiting for approval.", error_code=ErrorCode.SOP_DRAFT_NOT_SUBMITTED,
        )
    return draft


def _back_to_draft(draft: SOPVersion) -> None:
    draft.submitted_by_id = None
    draft.submitted_at = None


def withdraw_draft(db: Session, version_id: int, actor_id: int, is_admin: bool) -> SOPVersion:
    """承認の依頼を取り下げ、草稿に戻します（依頼した本人か業務管理者。ほかの人は 403）"""
    draft = _get_submitted(db, version_id)
    if draft.submitted_by_id != actor_id and not is_admin:
        raise ForbiddenError("Only the staff member who asked for approval, or an admin, can withdraw it.")
    _back_to_draft(draft)
    sop_version_event_repo.add(db, version=draft, event_type=SOPVersionEventType.WITHDRAWN, actor_id=actor_id)
    db.commit()
    db.refresh(draft)
    return draft


def reject_draft(db: Session, version_id: int, actor_id: int, reason: str) -> SOPVersion:
    """依頼された草稿を差し戻し、草稿に戻します（業務管理者。理由は必須で記録に残る）"""
    draft = _get_submitted(db, version_id)
    _back_to_draft(draft)
    sop_version_event_repo.add(db, version=draft, event_type=SOPVersionEventType.REJECTED, actor_id=actor_id, note=reason)
    db.commit()
    db.refresh(draft)
    return draft


# ==========================================
# 3. 承認: 草稿を「予定」にする（発効は指定日の 0:00 日本時間。M2.1）
# ==========================================
def _versions_of(db: Session, biz_id: int) -> list[SOPVersion]:
    return list(db.scalars(
        select(SOPVersion).where(SOPVersion.biz_master_id == biz_id).order_by(SOPVersion.version_number)
    ).all())


def approve_and_schedule_version(
    db: Session, biz_id: int, draft_version_id: int, effective_date: date, actor_id: int | None = None, note: str | None = None,
) -> SOPVersion:
    """
    草稿を承認し、effective_date の 0:00（日本時間）から有効にします（業務管理者）。
    承認を依頼された草稿も、依頼されていない草稿（業務管理者が自分で書いたものなど）も承認できる。依頼は外れる。
    - effective_date は明日以降。業務に「予定」（承認済みで未発効）の版が既にあれば断る（予定は 1 つまで）
    - ステップの無い草稿は承認しない
    - 今有効な版（終了日時なし）は、新しい版の発効日時で終わる
    その日以降に予定され、まだリリースされていない・指定（pin）されていないオーダーが新しい版を使う。
    """
    biz = biz_master_repo.get(db, id=biz_id)
    if not biz:
        raise NotFoundError(f"BizMaster(id={biz_id}) not found.")
    # 同じ業務の承認を同時に走らせない（予定は 1 つまで・版のつなぎ目を守る）
    db.execute(select(BizMaster.id).where(BizMaster.id == biz_id).with_for_update())

    now = datetime.now(timezone.utc)
    today = now.astimezone(LOCAL_TIMEZONE).date()
    if effective_date <= today:
        raise BadRequestError(f"The effective date must be tomorrow or later (today is {today.isoformat()} JST).")
    effective_at = datetime.combine(effective_date, time(0, 0), tzinfo=LOCAL_TIMEZONE)

    versions = _versions_of(db, biz_id)
    draft = next((v for v in versions if v.id == draft_version_id), None)
    if draft is None or draft.status != SOPStatus.DRAFT:
        raise NotFoundError(f"Draft SOPVersion(id={draft_version_id}) of BizMaster(id={biz_id}) not found.")
    if not draft.step_templates:
        raise BadRequestError(f"{draft.version_tag} has no steps; add at least one step before approving.")
    scheduled = next((v for v in versions if v.state_at(now) == SopState.SCHEDULED), None)
    if scheduled:
        raise BadRequestError(
            f"{scheduled.version_tag} is already scheduled from {scheduled.effective_at:%Y-%m-%d}; cancel it first or wait until it takes effect."
        )

    # 今有効な版（終了日時のない承認済み）を、新しい版の発効日時で終える
    for v in versions:
        if v.status == SOPStatus.APPROVED and v.expired_at is None:
            v.expired_at = effective_at

    # 誰の草稿を承認したか（依頼した人。依頼されていなければ作った人）: 本人の承認（自己承認）を記録から分かるように
    drafted_by_id = draft.submitted_by_id if draft.submitted_by_id is not None else draft.created_by_id
    _back_to_draft(draft)
    draft.status = SOPStatus.APPROVED
    draft.effective_at = effective_at
    draft.expired_at = None
    sop_version_event_repo.add(
        db, version=draft, event_type=SOPVersionEventType.APPROVED, actor_id=actor_id, note=note,
        detail={"effective_date": effective_date.isoformat(), "drafted_by_id": drafted_by_id},
    )
    db.commit()
    db.refresh(draft)
    return draft

# ==========================================
# 4. 承認の取消: 「予定」の版を草稿に戻し、今有効な版の終了日時を外す（M2.1）
# ==========================================
def cancel_future_version(db: Session, biz_id: int, future_version_id: int, actor_id: int | None = None) -> SOPVersion:
    """
    まだ発効していない（予定の）版の承認を取り消し、草稿に戻します（業務管理者）。
    発効した版・オーダーが指定（pin）している版・リリースで固定した版は戻せない（理由は登録された確認が返す）。
    """
    db.execute(select(BizMaster.id).where(BizMaster.id == biz_id).with_for_update())
    now = datetime.now(timezone.utc)
    versions = _versions_of(db, biz_id)
    version = next((v for v in versions if v.id == future_version_id), None)
    if version is None or version.status != SOPStatus.APPROVED:
        raise NotFoundError(f"Approved SOPVersion(id={future_version_id}) of BizMaster(id={biz_id}) not found.")
    if version.state_at(now) != SopState.SCHEDULED:
        raise BadRequestError(f"{version.version_tag} has already taken effect; its approval can no longer be canceled.")
    reasons = version_checks.reasons_not_to_cancel(db, version)
    if reasons:
        raise BadRequestError(f"{version.version_tag} cannot go back to a draft: " + "; ".join(reasons) + ".")

    # この版の発効日時で終わっていた版を、再び終了日時なしにする
    for v in versions:
        if v.id != version.id and v.status == SOPStatus.APPROVED and v.expired_at == version.effective_at:
            v.expired_at = None

    version.status = SOPStatus.DRAFT
    version.effective_at = None
    version.expired_at = None
    sop_version_event_repo.add(db, version=version, event_type=SOPVersionEventType.CANCELLED, actor_id=actor_id)
    db.commit()
    db.refresh(version)
    return version

# ==========================================
# SOP の版を返す形・記録・承認待ち（P.3、2026-11-11）
# ==========================================
def describe_versions(db: Session, versions: list[SOPVersion]) -> list[SOPVersionResponse]:
    """版に職員の名前を付けて返す形に（名前はまとめて 1 回で読む）"""
    names = staff_names(db, {i for v in versions for i in (v.created_by_id, v.updated_by_id, v.submitted_by_id)})
    return [
        SOPVersionResponse.model_validate(v).model_copy(update={
            "created_by_name": names.get(v.created_by_id), "updated_by_name": names.get(v.updated_by_id),
            "submitted_by_name": names.get(v.submitted_by_id),
        })
        for v in versions
    ]


def describe_version(db: Session, version: SOPVersion) -> SOPVersionResponse:
    return describe_versions(db, [version])[0]


def list_version_events(db: Session, version_id: int) -> list[SOPVersionEventResponse]:
    """版の記録（古い順。承認の記録には誰の草稿かの名前も）"""
    get_version(db, version_id)
    events = sop_version_event_repo.list_for_version(db, version_id=version_id)
    drafted = {e.id: (e.detail or {}).get("drafted_by_id") for e in events}
    names = staff_names(db, {e.actor_id for e in events} | set(drafted.values()))
    return [
        SOPVersionEventResponse.model_validate(e).model_copy(update={
            "actor_name": names.get(e.actor_id), "drafted_by_name": names.get(drafted[e.id]),
        })
        for e in events
    ]


def list_submitted_drafts(db: Session, client_code: str | None = None) -> list[SubmittedSOPDraftResponse]:
    """承認を依頼された草稿（すべての業務。依頼の古い順。削除された業務のものは出ない）"""
    drafts = sop_version_event_repo.list_submitted(db, client_code=client_code)
    bizes = {b.id: b for b in db.scalars(select(BizMaster).where(BizMaster.id.in_({d.biz_master_id for d in drafts})))}
    steps = dict(db.execute(
        select(SOPStepTemplate.sop_version_id, func.count()).where(SOPStepTemplate.sop_version_id.in_([d.id for d in drafts]))
        .group_by(SOPStepTemplate.sop_version_id)
    ).all())
    names = staff_names(db, {i for d in drafts for i in (d.submitted_by_id, d.created_by_id)})
    result = []
    for d in drafts:
        b = bizes[d.biz_master_id]
        assert d.submitted_by_id is not None and d.submitted_at is not None
        result.append(SubmittedSOPDraftResponse(
            version_id=d.id, version_tag=d.version_tag, biz_master_id=b.id, business_code=b.business_code, biz_name=b.name,
            client_code=b.client_code, step_count=steps.get(d.id, 0), submitted_by_id=d.submitted_by_id,
            submitted_by_name=names.get(d.submitted_by_id), submitted_at=d.submitted_at,
            created_by_id=d.created_by_id, created_by_name=names.get(d.created_by_id),
        ))
    return result


def count_submitted_drafts(db: Session) -> int:
    return sop_version_event_repo.count_submitted(db)


# ==========================================
# 業務間の依存関係（Phase 6）
# ==========================================
def _dependency_response(db: Session, dep: BizDependency) -> BizDependencyResponse:
    # 論理削除された業務も表示できるよう、全局の論理削除フィルタを外して読む
    def ref(biz_id: int) -> BizRef:
        biz = db.scalars(
            select(BizMaster).where(BizMaster.id == biz_id).execution_options(include_deleted=True)
        ).one()
        return BizRef.model_validate(biz)

    return BizDependencyResponse(
        id=dep.id, upstream=ref(dep.upstream_biz_id), downstream=ref(dep.downstream_biz_id),
        match_rule=dep.match_rule, kind=dep.kind, lookback_days=dep.lookback_days, created_at=dep.created_at,
    )


def list_dependencies(db: Session, biz_id: int) -> list[BizDependencyResponse]:
    """その業務が上流または下流になっている依存関係"""
    get_biz(db, biz_id)
    return [_dependency_response(db, d) for d in biz_dependency_repo.get_for_biz(db, biz_id=biz_id)]


def list_client_dependencies(db: Session, client_code: str) -> list[BizDependencyResponse]:
    """顧客の業務どうしの依存関係すべて（設定画面の一覧用）"""
    return [_dependency_response(db, d) for d in biz_dependency_repo.get_for_client(db, client_code=client_code.upper())]


def _creates_cycle(edges: list[tuple[int, int]], upstream: int, downstream: int) -> bool:
    """upstream → downstream を足すと循環するか（downstream から既存の定義をたどって upstream に着くか）"""
    following: dict[int, list[int]] = {}
    for up, down in edges:
        following.setdefault(up, []).append(down)
    seen, stack = set(), [downstream]
    while stack:
        node = stack.pop()
        if node == upstream:
            return True
        if node not in seen:
            seen.add(node)
            stack.extend(following.get(node, []))
    return False


def create_dependency(db: Session, downstream_biz_id: int, dep_in: BizDependencyCreate) -> BizDependencyResponse:
    """
    この業務（下流）が上流の業務を待つ定義を作ります。同じ顧客の業務どうしのみ（2026-11-04 の決定）。
    自分自身・同じ組み合わせの二重登録・循環（A → B → A など）は保存しない。
    """
    downstream = get_biz(db, downstream_biz_id)
    if dep_in.upstream_biz_id == downstream_biz_id:
        raise BadRequestError("A business cannot depend on itself.")
    upstream = get_biz(db, dep_in.upstream_biz_id)
    if upstream.client_code != downstream.client_code:
        raise BadRequestError(
            f"Dependencies link businesses of the same client only ({upstream.business_code} is {upstream.client_code}, "
            f"{downstream.business_code} is {downstream.client_code})."
        )
    if biz_dependency_repo.get_pair(db, upstream_biz_id=upstream.id, downstream_biz_id=downstream.id):
        raise DuplicatedRecordError(f"{downstream.business_code} already depends on {upstream.business_code}.")
    if _creates_cycle(biz_dependency_repo.get_edges(db), upstream.id, downstream.id):
        raise BadRequestError(
            f"{downstream.business_code} → {upstream.business_code} would create a cycle of dependencies."
        )
    dep = BizDependency(
        upstream_biz_id=upstream.id, downstream_biz_id=downstream.id, match_rule=dep_in.match_rule, kind=dep_in.kind,
        lookback_days=dep_in.lookback_days,
    )
    db.add(dep)
    db.flush()
    dependency_events.notify(db, "created", dep)  # 受け手（coordination）が既存のオーダーにつながりを作る
    db.commit()
    db.refresh(dep)
    return _dependency_response(db, dep)


def delete_dependency(db: Session, dependency_id: int) -> BizDependencyResponse:
    dep = db.get(BizDependency, dependency_id)
    if dep is None:
        raise NotFoundError(f"BizDependency(id={dependency_id}) not found.")
    response = _dependency_response(db, dep)
    dependency_events.notify(db, "deleted", dep)  # 受け手がつながりに「外れた」を付ける（行は残る）
    db.delete(dep)
    db.commit()
    return response
