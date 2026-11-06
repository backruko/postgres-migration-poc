from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from sqlalchemy import select, and_, or_
from sqlalchemy.exc import IntegrityError

from src.infrastructure.repositories import biz_dependency_repo, biz_master_repo, client_repo, sop_version_repo, sop_step_repo
from src.domain_biz.models import (
    BizDependency, BizMaster, CompletionMode, ExecutionType, SOPVersion, SOPStepTemplate, SOPStatus, SopState,
)
from src.domain_biz.schemas import (
    BizDependencyCreate, BizDependencyResponse, BizMasterCreate, BizMasterUpdate, BizRef, SOPVersionCreate,
    SOPStepTemplateCreate,
)
from src.core.exceptions import BadRequestError, NotFoundError, DuplicatedRecordError
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


def update_biz(db: Session, biz_id: int, biz_in: BizMasterUpdate) -> BizMaster:
    db_obj = biz_master_repo.get(db, id=biz_id)
    if not db_obj:
        raise NotFoundError(f"BizMaster(id={biz_id}) not found for update.")
    _check_client_event_settings(db, db_obj, biz_in)

    try:
        return biz_master_repo.update(db, db_obj=db_obj, obj_in=biz_in)
    except IntegrityError:
        raise DuplicatedRecordError(f"Update failed: Unique constraint violation.")

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
def create_new_sop_version(db: Session, biz_id: int, version_in: SOPVersionCreate) -> SOPVersion:
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
            expired_at=None
        )
        db.add(new_draft)
        db.commit()
        db.refresh(new_draft)
        return new_draft
    except IntegrityError:
        raise DuplicatedRecordError(f"指定されたバージョンタグ '{version_in.version_tag}' は既に存在します。")


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
# 1. 【複数並行OK】現行マニュアルをコピーして「新しい草稿(DRAFT)」を作る
# ==========================================
def clone_active_to_draft(db: Session, biz_id: int, version_in: SOPVersionCreate) -> SOPVersion:
    """
    現行をベースに、ユーザーが指定した自由なタグ名(version_in.version_tag)で草稿を生成します。
    """
    current_active = get_current_active_version(db, biz_id=biz_id)
    
    # 1. 【システム用】DBの一意制約のため、裏側の整数連番は自動で「最大値 + 1」
    stmt_max = select(SOPVersion.version_number).where(SOPVersion.biz_master_id == biz_id).order_by(SOPVersion.version_number.desc()).limit(1)
    max_num = db.scalars(stmt_max).first() or current_active.version_number
    new_version_number = max_num + 1

    # 2. 【人間用】Pydanticでバリデーション済みの、人間が入力したタグをそのまま採用！
    new_draft = SOPVersion(
        biz_master_id=biz_id,
        version_number=new_version_number,  # 機械用（自動）
        version_tag=version_in.version_tag,   # 人間用（自由：v1.1.0-teamA など）
        status=SOPStatus.DRAFT,
        effective_at=None,
        expired_at=None
    )
    db.add(new_draft)
    db.flush()

    # 現行のステップ手順を丸ごとコピー
    for step in current_active.step_templates:
        copied_step = SOPStepTemplate(
            sop_version_id=new_draft.id,
            step_num=step.step_num,
            title=step.title,
            content=step.content,
            condition=step.condition,
            is_blocking=step.is_blocking
        )
        db.add(copied_step)
        
    db.commit()
    db.refresh(new_draft)
    return new_draft



# ==========================================
# 2. 【現行・未来ロック】ステップの手順を編集する
# ==========================================
def create_step_template(db: Session, version_id: int, step_in: SOPStepTemplateCreate) -> SOPStepTemplate:
    """特定のSOPバージョン内に、作業手順(ステップ)を新規作成します。"""
    db_version = sop_version_repo.get(db, id=version_id)
    if not db_version:
        raise NotFoundError(f"SOPVersion(id={version_id}) not found.")
        
    #【仕様ロック】ステータスが DRAFT 以外（ACTIVE や FUTURE）なら手順の追加・変更を絶対禁止
    if db_version.status != SOPStatus.DRAFT:
        raise BadRequestError("稼働中(ACTIVE)または予約確定済(FUTURE)のバージョンを書き換えることはできません。草稿(DRAFT)で編集してください。")
        
    try:
        return sop_step_repo.create_with_version(db, obj_in=step_in, sop_version_id=version_id)
    except IntegrityError:
        raise DuplicatedRecordError(f"Step number {step_in.step_num} already exists in this version.")


# ==========================================
# 3. 承認: 草稿を「予定」にする（発効は指定日の 0:00 日本時間。M2.1）
# ==========================================
def _versions_of(db: Session, biz_id: int) -> list[SOPVersion]:
    return list(db.scalars(
        select(SOPVersion).where(SOPVersion.biz_master_id == biz_id).order_by(SOPVersion.version_number)
    ).all())


def approve_and_schedule_version(db: Session, biz_id: int, draft_version_id: int, effective_date: date) -> SOPVersion:
    """
    草稿を承認し、effective_date の 0:00（日本時間）から有効にします（業務管理者）。
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

    draft.status = SOPStatus.APPROVED
    draft.effective_at = effective_at
    draft.expired_at = None
    db.commit()
    db.refresh(draft)
    return draft

# ==========================================
# 4. 承認の取消: 「予定」の版を草稿に戻し、今有効な版の終了日時を外す（M2.1）
# ==========================================
def cancel_future_version(db: Session, biz_id: int, future_version_id: int) -> SOPVersion:
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
    db.commit()
    db.refresh(version)
    return version

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
