from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select, and_, or_
from sqlalchemy.exc import IntegrityError

from src.infrastructure.repositories import biz_master_repo, client_repo, sop_version_repo, sop_step_repo
from src.domain_biz.models import BizMaster, SOPVersion, SOPStepTemplate, SOPStatus
from src.domain_biz.schemas import BizMasterCreate, BizMasterUpdate, SOPVersionCreate, SOPStepTemplateCreate
from src.core.exceptions import BadRequestError, NotFoundError, DuplicatedRecordError

# ==========================================
# BizMaster Service
# ==========================================
def create_biz(db: Session, biz_in: BizMasterCreate) -> BizMaster:
    # 1. 存在チェック
    client = client_repo.get_by_code(db, code=biz_in.client_code)
    if not client:
        raise NotFoundError(f"Client with code '{biz_in.client_code}' does not exist.")
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

def get_bizes(db: Session, skip: int = 0, limit: int = 100) -> list[BizMaster]:
    return biz_master_repo.get_multi(db, skip=skip, limit=limit)

def update_biz(db: Session, biz_id: int, biz_in: BizMasterUpdate) -> BizMaster:
    db_obj = biz_master_repo.get(db, id=biz_id)
    if not db_obj:
        raise NotFoundError(f"BizMaster(id={biz_id}) not found for update.")  
        
    try:
        return biz_master_repo.update(db, db_obj=db_obj, obj_in=biz_in)
    except IntegrityError:
        raise DuplicatedRecordError(f"Update failed: Unique constraint violation.")

def delete_biz(db: Session, biz_id: int) -> BizMaster:
    deleted_obj = biz_master_repo.remove(db, id=biz_id)
    if not deleted_obj:
        # 見つからない(None)なら、ビジネスエラーに変換して raise する役割
        raise NotFoundError(f"BizMaster(id={biz_id}) not found for deletion.")
    return deleted_obj

def restore_biz(db: Session, biz_id: int) -> BizMaster:
    """
    論理削除された BizMaster レコードを復元します。
    """
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
def create_sop_version(db: Session, biz_id: int, version_in: SOPVersionCreate) -> SOPVersion:
    """特定業務(BizMaster)に対して、新しいSOPバージョンを新規作成します。"""
    # 1. 親(BizMaster)が存在し、論理削除されていないかチェック
    db_biz = biz_master_repo.get(db, id=biz_id)
    if not db_biz:
        raise NotFoundError(f"BizMaster(id={biz_id}) not found. Cannot create version.")
        
    try:
        # version_in に biz_master_id を紐付けてレポジトリで作成
        return sop_version_repo.create_with_biz(db, obj_in=version_in, biz_master_id=biz_id)
    except IntegrityError:
        # version_number や version_tag の一意制約違反(uq_biz_version_number 等)をキャッチ
        raise DuplicatedRecordError(f"Version number or tag already exists for this BizMaster.")

def get_latest_active_version(db: Session, biz_id: int) -> SOPVersion:
    """指定された業務の最新の 'active' なSOPバージョンを1件取得します(コメントアウトの処理）。"""
    # グローバルスコープにより、親が論理削除されていれば自動的に None になります
    stmt = (
        select(SOPVersion)
        .where(SOPVersion.biz_master_id == biz_id, SOPVersion.status == "active")
        .order_by(SOPVersion.version_number.desc())
        .limit(1)
    )
    latest_version = db.scalars(stmt).first()
    if not latest_version:
        raise NotFoundError(f"Active SOPVersion for BizMaster(id={biz_id}) not found.")
    return latest_version

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

def create_step_template(db: Session, version_id: int, step_in: SOPStepTemplateCreate) -> SOPStepTemplate:
    """特定のSOPバージョン内に、作業手順(ステップ)を新規作成します。"""
    # 1. 親(SOPVersion)が存在するかチェック（親の親であるBizMasterが消えている場合も自動で弾かれます）
    db_version = sop_version_repo.get(db, id=version_id)
    if not db_version:
        raise NotFoundError(f"SOPVersion(id={version_id}) not found. Cannot create step.")
        
    try:
        return sop_step_repo.create_with_version(db, obj_in=step_in, sop_version_id=version_id)
    except IntegrityError:
        # 同一バージョン内で step_num が重複した場合(uq_sop_version_step_num)をキャッチ
        raise DuplicatedRecordError(f"Step number {step_in.step_num} already exists in this version.")

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
            content=step.content
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
# 👑 3. 【予約枠は常に1件】権限者が1つの草稿を選び「未来実装(FUTURE)」に昇格
# ==========================================
def approve_and_schedule_version(db: Session, biz_id: int, draft_version_id: int, effective_at: datetime) -> SOPVersion:
    """【権限者専用】選ばれたDRAFTを承認し、未来の発効日に向けた1件限りの予約枠(FUTURE)へ昇格させます。"""
    now = datetime.now(timezone.utc)
    if effective_at <= now:
        raise BadRequestError("発効日（有効開始日）は現在時刻より未来である必要があります。")

    #【仕様ロック】すでに『FUTURE（未来実装）』の予約が他に存在しないかチェック
    stmt_future = select(SOPVersion).where(SOPVersion.biz_master_id == biz_id, SOPVersion.status == SOPStatus.FUTURE)
    existing_future = db.scalars(stmt_future).first()
    if existing_future:
        raise BadRequestError(f"既に別の予約（{existing_future.version_tag}）が存在します。同時に複数の予約は登録できません。")

    # 昇格対象のDRAFTを取得
    draft_version = sop_version_repo.get(db, id=draft_version_id)
    if not draft_version or draft_version.status != SOPStatus.DRAFT:
        raise NotFoundError("承認対象の有効な草稿(DRAFT)が見つかりません。")

    # 現在稼働している無期限のエース（現行版）を探す
    stmt_active = select(SOPVersion).where(SOPVersion.biz_master_id == biz_id, SOPVersion.status == SOPStatus.ACTIVE, SOPVersion.expired_at == None)
    current_active = db.scalars(stmt_active).first()

    # 【連動処理】現行版の「失効日」を、予約の「発効日」の1秒前に自動連結して蓋をする
    if current_active:
        current_active.expired_at = effective_at - timedelta(seconds=1)
        db.add(current_active)

    # DRAFTを正式に予約（FUTURE）へ昇格
    draft_version.status = SOPStatus.FUTURE
    draft_version.effective_at = effective_at
    draft_version.expired_at = None  # 次の予約が入るまで無期限扱いにする

    db.add(draft_version)
    db.commit()
    db.refresh(draft_version)
    return draft_version


# ==========================================
# 4. 【自動期間復元】予約（FUTURE）をキャンセルし、現行版の期限を無限に戻す
# ==========================================
def cancel_future_version(db: Session, biz_id: int, future_version_id: int) -> SOPVersion:
    """【権限者専用】予約(FUTURE)を取り消し、現行版を再び『無期限』に自動復元した上で、データをDRAFTへと安全に引き戻します。"""
    future_version = sop_version_repo.get(db, id=future_version_id)
    if not future_version or future_version.status != SOPStatus.FUTURE:
        raise NotFoundError("取り消し対象の未来実装(FUTURE)バージョンが見つかりません。")

    # 【連動処理】この予約のせいで期限を区切られていた現行版を逆探知する
    stmt_active = select(SOPVersion).where(
        SOPVersion.biz_master_id == biz_id,
        SOPVersion.status == SOPStatus.ACTIVE,
        SOPVersion.expired_at == future_version.effective_at - timedelta(seconds=1)
    )
    current_active = db.scalars(stmt_active).first()

    if current_active:
        # 予約が消滅するため、現行版の終了期限の蓋を取り払い、再び「無期限（None）」に復元
        current_active.expired_at = None
        db.add(current_active)

    # 予約データを削除せず、安全に再編集可能な「DRAFT」へ引き戻す（これで再利用や別案としての検討が可能）
    future_version.status = SOPStatus.DRAFT
    future_version.effective_at = None
    
    db.add(future_version)
    db.commit()
    db.refresh(future_version)
    return future_version

