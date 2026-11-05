"""
オーダー同士のつながり（ope_dependency）を、業務間の定義（biz_dependency）から作る・直す（6.3）。

- 突き合わせ: same_sheet_event = 同じシート日付（source_sheet_event_id）、same_occurrence_date = 同じルール上の日付。
  下流のオーダーにキーが無ければ（シート由来でない臨時の予定など）、その定義は当てはまらない（つながりなし）。
- 上流の候補は、上流の業務の同じキーのオーダー。取消されていないものがあればそれ（複数なら全部）、
  取消済みしか無ければ最新の取消済み（「上流が取消」のまま待つ。人が強制開始か取消を判断する）、
  何も無ければ待ち先なしの行（upstream_order_id NULL。条件を満たさない）。
- 直すのは開始前の下流のオーダーだけ（pending / ready / それらから保留）。開始後・完了・取消のつながりはそのまま。
- 合わなくなったつながりは消さずに removed_at と理由を付ける。待ち先なしの行は、上流が見つかったらその行を埋める。
"""
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain_biz.models import BizDependency, BizMaster, DependencyKind, DependencyMatchRule
from src.domain_ope.models import OpeDependency, OpeOrder, OpeOrderEvent, OrderEventType, OrderStatus
from src.infrastructure.repositories import biz_dependency_repo, ope_dependency_repo, ope_order_repo

NOT_STARTED = (OrderStatus.PENDING, OrderStatus.READY)
FINAL = (OrderStatus.FINISHED, OrderStatus.CANCELED)


def is_late(order: OpeOrder, now: datetime) -> bool:
    """遅れ = 予定の終了時刻を過ぎても完了・取消になっていない（保存しない。表示のたびに計算する。Phase 7）"""
    return order.status not in FINAL and order.scheduled_end < now

REASON_DEFINITION_DELETED = "依存関係の定義が削除されました"
REASON_UPSTREAM_CHANGED = "待つ上流のオーダーが変わりました"
REASON_RULE_NOT_APPLICABLE = "定義の突き合わせの条件に当てはまらなくなりました"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_not_started(order: OpeOrder) -> bool:
    return order.status in NOT_STARTED or (
        order.status == OrderStatus.ON_HOLD and order.status_before_hold in NOT_STARTED
    )


def _key(order: OpeOrder, rule: DependencyMatchRule) -> int | date | None:
    if rule == DependencyMatchRule.SAME_SHEET_EVENT:
        return order.source_sheet_event_id
    if rule == DependencyMatchRule.SAME_OCCURRENCE_DATE:
        return order.occurrence_date
    # 直前の上流（latest_before）は L.2 で作る。それまではつながりを作らない（キーが無い = 当てはまらない扱い）
    return None


def _upstream_targets(db: Session, dep: BizDependency, key: int | date) -> list[OpeOrder]:
    """待つ上流のオーダー（取消されていないもの全部 / 無ければ最新の取消済み1件 / それも無ければ空）"""
    candidates = ope_dependency_repo.get_orders_matching(db, biz_id=dep.upstream_biz_id, rule=dep.match_rule, key=key)
    live = [o for o in candidates if o.status != OrderStatus.CANCELED]
    if live:
        return live
    return candidates[-1:]


def _remove(link: OpeDependency, reason: str, now: datetime) -> None:
    link.removed_at = now
    link.removed_reason = reason


def resolve(db: Session, order: OpeOrder, dep: BizDependency) -> None:
    """1つの定義について、下流のオーダーのつながりを今の状態に合わせる（開始前のオーダーのみ）"""
    if not is_not_started(order) or dep.downstream_biz_id != order.biz_master_id:
        return
    now = _now()
    current = ope_dependency_repo.get_active(db, downstream_order_id=order.id, biz_dependency_id=dep.id)
    key = _key(order, dep.match_rule)
    if key is None:
        for link in current:
            _remove(link, REASON_RULE_NOT_APPLICABLE, now)
        return

    targets = {o.id for o in _upstream_targets(db, dep, key) if o.id != order.id}
    linked = {link.upstream_order_id for link in current if link.upstream_order_id is not None}
    waiting = [link for link in current if link.upstream_order_id is None]
    for link in current:
        if link.upstream_order_id is not None and link.upstream_order_id not in targets:
            _remove(link, REASON_UPSTREAM_CHANGED, now)

    missing = sorted(targets - linked)
    for link in waiting:
        if missing:
            link.upstream_order_id = missing.pop(0)  # 待ち先なしの行を、見つかった上流で埋める
        elif linked & targets:
            _remove(link, REASON_UPSTREAM_CHANGED, now)  # ほかの行で上流が見つかっている
    for upstream_id in missing:
        db.add(_link(order, dep, key, upstream_id))
    if not targets and not waiting:
        db.add(_link(order, dep, key, None))  # 待ち先なし（条件を満たさない）
    db.flush()


def _link(order: OpeOrder, dep: BizDependency, key: int | date, upstream_order_id: int | None) -> OpeDependency:
    return OpeDependency(
        downstream_order_id=order.id, upstream_order_id=upstream_order_id, biz_dependency_id=dep.id,
        upstream_biz_id=dep.upstream_biz_id, kind=dep.kind, match_rule=dep.match_rule,
        match_sheet_event_id=key if isinstance(key, int) else None,
        match_date=key if isinstance(key, date) else None,
    )


# ==========================================
# きっかけごとの入口
# ==========================================
def refresh_order(db: Session, order: OpeOrder) -> None:
    """
    オーダーができた・動いた・取り消されたときに呼ぶ。
    このオーダー自身のつながり（下流として）と、このオーダーを待ちうる下流のオーダー（上流として）を直す。
    """
    # 定義の無い業務（ほとんど）は1回の参照で終える（予定の生成で大量のオーダーが作られるため）
    defs = biz_dependency_repo.get_for_biz(db, biz_id=order.biz_master_id)
    if not defs:
        return
    db.flush()
    for dep in defs:
        if dep.downstream_biz_id == order.biz_master_id:
            resolve(db, order, dep)
    upstream_defs = [dep for dep in defs if dep.upstream_biz_id == order.biz_master_id]
    if not upstream_defs:
        return
    affected: dict[int, OpeOrder] = {}
    for downstream_id in ope_dependency_repo.get_active_downstream_ids(db, upstream_order_id=order.id):
        downstream = db.get(OpeOrder, downstream_id)
        if downstream is not None:
            affected[downstream.id] = downstream
    for dep in upstream_defs:
        key = _key(order, dep.match_rule)
        if key is not None:
            for downstream in ope_dependency_repo.get_orders_matching(db, biz_id=dep.downstream_biz_id, rule=dep.match_rule, key=key):
                affected[downstream.id] = downstream
    for downstream in affected.values():
        for dep in upstream_defs:
            if dep.downstream_biz_id == downstream.biz_master_id:
                resolve(db, downstream, dep)


def definition_created(db: Session, dep: BizDependency) -> None:
    """定義ができたら、下流の業務の開始前のオーダーすべてにつながりを作る"""
    db.flush()
    for order in ope_dependency_repo.get_open_orders_of(db, biz_id=dep.downstream_biz_id):
        resolve(db, order, dep)


def definition_deleted(db: Session, dep: BizDependency) -> None:
    """定義が削除されるときは、そのつながりすべてに「外れた」を付ける（開始後のオーダーのものも。行は残る）"""
    now = _now()
    for link in ope_dependency_repo.get_active_by_definition(db, biz_dependency_id=dep.id):
        _remove(link, REASON_DEFINITION_DELETED, now)
    db.flush()


# ==========================================
# 開始の判定（6.4）
# ==========================================
def is_met(db: Session, link: OpeDependency) -> bool:
    """つながりを満たしているか = 上流のオーダーが完了している（待ち先なし・取消・保留・作業中は満たさない）"""
    if link.upstream_order_id is None:
        return False
    upstream = db.get(OpeOrder, link.upstream_order_id)
    return upstream is not None and upstream.status == OrderStatus.FINISHED


def unmet_hard_links(db: Session, order: OpeOrder) -> list[OpeDependency]:
    """開始を止める、満たされていない hard のつながり（informational は止めない）"""
    db.flush()
    return [
        link for link in ope_dependency_repo.get_active(db, downstream_order_id=order.id)
        if link.kind == DependencyKind.HARD and not is_met(db, link)
    ]


def describe(db: Session, link: OpeDependency) -> str:
    """止めている理由（エラーメッセージ・記録用）"""
    biz = db.get(BizMaster, link.upstream_biz_id)
    code = biz.business_code if biz else f"biz {link.upstream_biz_id}"
    if link.upstream_order_id is None:
        key = link.match_date.isoformat() if link.match_date else f"sheet event {link.match_sheet_event_id}"
        return f"no order of {code} for {key}"
    upstream = db.get(OpeOrder, link.upstream_order_id)
    return f"order {link.upstream_order_id} of {code} is {upstream.status.value if upstream else 'missing'}"


def overridden_link_ids(events: list[OpeOrderEvent]) -> set[int]:
    """依存関係の承認（DEPENDENCY_OVERRIDE_APPROVED）が対象にしたつながり"""
    ids: set[int] = set()
    for e in events:
        if e.event_type == OrderEventType.DEPENDENCY_OVERRIDE_APPROVED and e.detail:
            ids.update(int(i) for i in e.detail.get("links", []))
    return ids


def blocked_order_ids(db: Session, orders: list[OpeOrder]) -> set[int]:
    """
    依存関係で開始できないオーダー（開始前で、満たされず承認もされていない hard のつながりがある）。
    ダッシュボードの一覧用に、オーダーの数によらず数回のクエリで判定する。
    """
    candidates = {o.id: o for o in orders if is_not_started(o)}
    hard = [
        link for link in ope_dependency_repo.get_active_for_orders(db, downstream_order_ids=list(candidates))
        if link.kind == DependencyKind.HARD
    ]
    if not hard:
        return set()
    upstream_ids = {link.upstream_order_id for link in hard if link.upstream_order_id is not None}
    finished = set(db.scalars(
        select(OpeOrder.id).where(OpeOrder.id.in_(upstream_ids), OpeOrder.status == OrderStatus.FINISHED)
    )) if upstream_ids else set()
    events = ope_order_repo.get_events_of_type(
        db, order_ids=list({link.downstream_order_id for link in hard}), event_type=OrderEventType.DEPENDENCY_OVERRIDE_APPROVED,
    )
    covered = overridden_link_ids(events)
    return {
        link.downstream_order_id for link in hard
        if link.upstream_order_id not in finished and link.id not in covered
    }
