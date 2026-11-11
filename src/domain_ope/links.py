"""
オーダー同士のつながり（ope_dependency）を、業務間の定義（biz_dependency）から作る・直す（6.3）。

- 突き合わせ: same_sheet_event = 同じシート日付（source_sheet_event_id）、same_occurrence_date = 同じルール上の日付。
  下流のオーダーにキーが無ければ（シート由来でない臨時の予定など）、その定義は当てはまらない（つながりなし）。
- 上流の候補は、上流の業務の同じキーのオーダー。取消されていないものがあればそれ（複数なら全部）、
  取消済みしか無ければ最新の取消済み（「上流が取消」のまま待つ。人が強制開始か取消を判断する）、
  何も無ければ待ち先なしの行（upstream_order_id NULL。条件を満たさない）。
- 直すのは開始前の下流のオーダーだけ（pending / ready / それらから保留）。開始後・完了・取消のつながりはそのまま。
- 合わなくなったつながりは消さずに removed_at と理由を付ける。待ち先なしの行は、上流が見つかったらその行を埋める。
- latest_before（直前の上流。L.2、2026-11-05 の決定）: キーではなく時刻で決める。下流の予定開始以前で、
  lookback_days 日前までに開始する上流のオーダーのうち最新の1件（取消済みでも。人が判断するまで待つ）。
  無ければ待ち先なしの行。上流のオーダーができた・動いた・取り消されたら、その後 lookback_days 日の間に
  開始する開始前の下流のオーダーを直し、下流が動いたら（時刻だけでも）その下流を直す。
"""
from bisect import bisect_right
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain_biz.models import BizDependency, BizMaster, DependencyKind, DependencyMatchRule
from src.domain_ope.models import OpeDependency, OpeOrder, OpeOrderEvent, OrderEventType, OrderStatus
from src.infrastructure.repositories import biz_dependency_repo, ope_dependency_repo, ope_order_repo

NOT_STARTED = (OrderStatus.PENDING, OrderStatus.READY)
FINAL = (OrderStatus.FINISHED, OrderStatus.CANCELED)


def is_late(order: OpeOrder, now: datetime) -> bool:
    """遅れ = 保存した締切（deadline_at）を過ぎても完了・取消になっていない（N.3。締切の決め方はテンプレートごと）"""
    return order.status not in FINAL and order.deadline_at < now


def is_due_soon(order: OpeOrder, now: datetime) -> bool:
    """まもなく = 「まもなく」の開始（warn_at）以降で締切前、かつ完了・取消になっていない（知らせなしのオーダーは出ない）"""
    return order.status not in FINAL and order.warn_at is not None and order.warn_at <= now < order.deadline_at

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
    """キーで突き合わせるルールのキー（latest_before はキーを使わないので None）"""
    if rule == DependencyMatchRule.SAME_SHEET_EVENT:
        return order.source_sheet_event_id
    if rule == DependencyMatchRule.SAME_OCCURRENCE_DATE:
        return order.occurrence_date
    return None


def _lookback(dep: BizDependency) -> timedelta:
    return timedelta(days=dep.lookback_days or 0)


def _upstream_targets(db: Session, dep: BizDependency, key: int | date) -> list[OpeOrder]:
    """待つ上流のオーダー（取消されていないもの全部 / 無ければ最新の取消済み1件 / それも無ければ空）"""
    return _live_or_last(ope_dependency_repo.get_orders_matching(db, biz_id=dep.upstream_biz_id, rule=dep.match_rule, key=key))


def _targets_for(db: Session, order: OpeOrder, dep: BizDependency) -> tuple[list[OpeOrder], int | date | None] | None:
    """定義のルールで待つ上流のオーダーと、行に残すキー。当てはまらない（キーが無い）なら None"""
    if dep.match_rule == DependencyMatchRule.LATEST_BEFORE:
        latest = ope_dependency_repo.get_latest_starting_before(
            db, biz_id=dep.upstream_biz_id, at=order.scheduled_start, since=order.scheduled_start - _lookback(dep),
        )
        return ([latest] if latest is not None else []), None
    key = _key(order, dep.match_rule)
    if key is None:
        return None
    return _upstream_targets(db, dep, key), key


def _remove(link: OpeDependency, reason: str, now: datetime) -> None:
    link.removed_at = now
    link.removed_reason = reason


def resolve(db: Session, order: OpeOrder, dep: BizDependency) -> None:
    """1つの定義について、下流のオーダーのつながりを今の状態に合わせる（開始前のオーダーのみ）"""
    if not is_not_started(order) or dep.downstream_biz_id != order.biz_master_id:
        return
    current = ope_dependency_repo.get_active(db, downstream_order_id=order.id, biz_dependency_id=dep.id)
    _apply_targets(db, order, dep, current, _targets_for(db, order, dep), _now())
    db.flush()


def _apply_targets(
    db: Session, order: OpeOrder, dep: BizDependency, current: list[OpeDependency],
    found: tuple[list[OpeOrder], int | date | None] | None, now: datetime,
) -> None:
    """
    下流のオーダーの今のつながり（current、古い順）を、定義のルールで見つけた上流（found）に合わせる（書き出さない）。
    resolve（1 件）と resolve_many（まとめて）の共通部分
    """
    if found is None:
        for link in current:
            _remove(link, REASON_RULE_NOT_APPLICABLE, now)
        return

    upstream_orders, key = found
    targets = {o.id for o in upstream_orders if o.id != order.id}
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


def resolve_many(db: Session, orders: list[OpeOrder], dep: BizDependency) -> None:
    """
    resolve を多くの下流のオーダーにまとめて行う（結果は 1 件ずつ resolve したのと同じ）。
    今のつながりと上流の候補を定義ごとに 1〜2 回の問い合わせで読み、最後に 1 回だけ書き出す
    （テンプレートの変更で数百のオーダーができる・消えるため。1 件ずつだと 1 件に数文かかる）
    """
    targets = [o for o in orders if is_not_started(o) and dep.downstream_biz_id == o.biz_master_id]
    if not targets:
        return
    now = _now()
    current: dict[int, list[OpeDependency]] = {}
    for link in ope_dependency_repo.get_active_for_definition(
        db, biz_dependency_id=dep.id, downstream_order_ids=[o.id for o in targets],
    ):
        current.setdefault(link.downstream_order_id, []).append(link)
    found = _targets_for_many(db, targets, dep)
    for order in targets:
        _apply_targets(db, order, dep, current.get(order.id, []), found[order.id], now)
    db.flush()


def _targets_for_many(
    db: Session, orders: list[OpeOrder], dep: BizDependency,
) -> dict[int, tuple[list[OpeOrder], int | date | None] | None]:
    """_targets_for を多くの下流のオーダーにまとめて行う（上流の候補は 1 回の問い合わせで読む）"""
    if dep.match_rule == DependencyMatchRule.LATEST_BEFORE:
        lookback = _lookback(dep)
        candidates = ope_dependency_repo.get_orders_starting_between(
            db, biz_id=dep.upstream_biz_id,
            since=min(o.scheduled_start for o in orders) - lookback, until=max(o.scheduled_start for o in orders),
        )  # 予定開始・id の順
        starts = [c.scheduled_start for c in candidates]
        result: dict[int, tuple[list[OpeOrder], int | date | None] | None] = {}
        for order in orders:
            # 予定開始 ≤ 下流の開始で最新（同じ時刻なら id の大きいもの）。lookback より前なら無し
            i = bisect_right(starts, order.scheduled_start) - 1
            latest = candidates[i] if i >= 0 and starts[i] >= order.scheduled_start - lookback else None
            result[order.id] = ([latest] if latest is not None else []), None
        return result
    keys = {o.id: _key(o, dep.match_rule) for o in orders}
    by_key: dict[int | date, list[OpeOrder]] = {}
    for candidate in ope_dependency_repo.get_orders_matching_keys(
        db, biz_id=dep.upstream_biz_id, rule=dep.match_rule, keys={k for k in keys.values() if k is not None},
    ):  # id の順
        by_key.setdefault(_key(candidate, dep.match_rule), []).append(candidate)  # type: ignore[arg-type]
    return {
        order.id: None if keys[order.id] is None else (_live_or_last(by_key.get(keys[order.id], [])), keys[order.id])  # type: ignore[arg-type]
        for order in orders
    }


def _live_or_last(candidates: list[OpeOrder]) -> list[OpeOrder]:
    """取消されていないもの全部 / 無ければ最新の取消済み1件 / それも無ければ空（候補は古い順）"""
    live = [o for o in candidates if o.status != OrderStatus.CANCELED]
    if live:
        return live
    return candidates[-1:]


def _link(order: OpeOrder, dep: BizDependency, key: int | date | None, upstream_order_id: int | None) -> OpeDependency:
    return OpeDependency(
        downstream_order_id=order.id, upstream_order_id=upstream_order_id, biz_dependency_id=dep.id,
        upstream_biz_id=dep.upstream_biz_id, kind=dep.kind, match_rule=dep.match_rule,
        match_sheet_event_id=key if isinstance(key, int) else None,
        match_date=key if isinstance(key, date) else None,
    )


# ==========================================
# きっかけごとの入口
# ==========================================
_DEFS_CACHE_KEY = "ope_dependency_defs_cache"


@contextmanager
def definitions_cached(db: Session) -> Iterator[None]:
    """
    この中では、業務ごとの依存関係の定義を 1 回だけ読む（オーダーの追従: テンプレートの変更で数百のオーダーが一度に動く）。
    定義はこの間に変わらない（定義の追加・削除は別の操作）。入れ子にしても外側のものを使う。
    """
    if _DEFS_CACHE_KEY in db.info:
        yield
        return
    db.info[_DEFS_CACHE_KEY] = {}
    try:
        yield
    finally:
        db.info.pop(_DEFS_CACHE_KEY, None)


def _definitions_for(db: Session, biz_id: int) -> list[BizDependency]:
    cache: dict[int, list[BizDependency]] | None = db.info.get(_DEFS_CACHE_KEY)
    if cache is None:
        return biz_dependency_repo.get_for_biz(db, biz_id=biz_id)
    if biz_id not in cache:
        cache[biz_id] = biz_dependency_repo.get_for_biz(db, biz_id=biz_id)
    return cache[biz_id]


def refresh_order(db: Session, order: OpeOrder, only_time_based: bool = False) -> None:
    """
    オーダーができた・動いた・取り消されたときに呼ぶ。
    このオーダー自身のつながり（下流として）と、このオーダーを待ちうる下流のオーダー（上流として）を直す。
    only_time_based: 時刻だけが動いた（発生枠のキー = ルール上の日付・シート日付は同じ）。同じシート日付・同じ日付の
    つながりはキーだけで決まるので変わらない。予定開始で突き合わせる「直前の上流」だけを直す
    （テンプレートの時刻の変更で数百のオーダーが動いても、つながりを 1 件ずつ読み直さない）。
    """
    # 定義の無い業務（ほとんど）は1回の参照で終える（予定の生成で大量のオーダーが作られるため）。
    # オーダーの追従の中（definitions_cached）では、業務ごとに 1 回だけ読む
    defs = _definitions_for(db, order.biz_master_id)
    if only_time_based:
        defs = [dep for dep in defs if dep.match_rule == DependencyMatchRule.LATEST_BEFORE]
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
        if dep.match_rule == DependencyMatchRule.LATEST_BEFORE:
            # このオーダーの開始から lookback_days 日の間に開始する下流は、このオーダーが「直前の上流」になりうる
            # （動く前の位置で待っていた下流は、上のつながりの一覧に入っている）
            for downstream in ope_dependency_repo.get_open_orders_of(
                db, biz_id=dep.downstream_biz_id,
                starting_from=order.scheduled_start, starting_until=order.scheduled_start + _lookback(dep),
            ):
                affected[downstream.id] = downstream
            continue
        key = _key(order, dep.match_rule)
        if key is not None:
            for downstream in ope_dependency_repo.get_orders_matching(db, biz_id=dep.downstream_biz_id, rule=dep.match_rule, key=key):
                affected[downstream.id] = downstream
    for downstream in affected.values():
        for dep in upstream_defs:
            if dep.downstream_biz_id == downstream.biz_master_id:
                resolve(db, downstream, dep)


def refresh_orders(db: Session, orders: list[OpeOrder]) -> None:
    """
    refresh_order を多くのオーダーにまとめて行う（オーダーの追従: テンプレートの変更で数百のオーダーができる・消える）。
    結果は 1 件ずつ refresh_order したのと同じ: 下流のつながりは今の状態だけで決まるので、関わる下流のオーダーを
    全部のオーダーを書き出したあとに 1 回ずつ直せば、1 件ごとに直したときの最後の状態と同じになる。
    問い合わせは定義ごとに数回（オーダーの数によらない）。
    """
    defs_by_biz = {biz_id: _definitions_for(db, biz_id) for biz_id in {o.biz_master_id for o in orders}}
    if not any(defs_by_biz.values()):
        return  # 定義の無い業務（ほとんど）
    db.flush()
    by_biz: dict[int, list[OpeOrder]] = {}
    for order in orders:
        by_biz.setdefault(order.biz_master_id, []).append(order)

    # このオーダー自身のつながり（下流として）
    for biz_id, group in by_biz.items():
        for dep in defs_by_biz[biz_id]:
            if dep.downstream_biz_id == biz_id:
                resolve_many(db, group, dep)

    # このオーダーを待ちうる下流のオーダー（上流として）。refresh_order と同じく、上流の業務の定義ごとに、
    # このオーダーを待っている下流 + 定義のルールで当てはまりうる下流を直す
    affected: dict[int, tuple[BizDependency, dict[int, OpeOrder]]] = {}
    for biz_id, group in by_biz.items():
        defs = [dep for dep in defs_by_biz[biz_id] if dep.upstream_biz_id == biz_id]
        if not defs:
            continue
        linked = ope_dependency_repo.get_active_downstream_orders(db, upstream_order_ids=[o.id for o in group])
        for dep in defs:
            orders_for_dep = affected.setdefault(dep.id, (dep, {}))[1]
            for downstream in [*linked, *_downstream_candidates(db, group, dep)]:
                orders_for_dep[downstream.id] = downstream
    for dep, downstreams in affected.values():
        resolve_many(db, list(downstreams.values()), dep)


def _downstream_candidates(db: Session, upstream_orders: list[OpeOrder], dep: BizDependency) -> list[OpeOrder]:
    """上流のオーダーができた・動いた・消えたときに、定義のルールで直す下流のオーダー（refresh_order と同じ範囲）"""
    if dep.match_rule == DependencyMatchRule.LATEST_BEFORE:
        # 上流の開始から lookback_days 日の間に開始する、開始前の下流
        lookback = _lookback(dep)
        windows = sorted(o.scheduled_start for o in upstream_orders)
        found = ope_dependency_repo.get_open_orders_of(
            db, biz_id=dep.downstream_biz_id, starting_from=windows[0], starting_until=windows[-1] + lookback,
        )
        result = []
        for downstream in found:
            i = bisect_right(windows, downstream.scheduled_start) - 1  # 下流の開始以前で最後の上流の開始
            if i >= 0 and downstream.scheduled_start <= windows[i] + lookback:
                result.append(downstream)
        return result
    keys = {k for k in (_key(o, dep.match_rule) for o in upstream_orders) if k is not None}
    return ope_dependency_repo.get_orders_matching_keys(db, biz_id=dep.downstream_biz_id, rule=dep.match_rule, keys=keys)


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
        if link.match_rule == DependencyMatchRule.LATEST_BEFORE:
            dep = db.get(BizDependency, link.biz_dependency_id) if link.biz_dependency_id else None
            days = f" in the {dep.lookback_days} days" if dep and dep.lookback_days else ""
            return f"no order of {code}{days} before it"
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
