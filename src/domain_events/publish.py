"""
出来事を記録する入口（N.2、2026-11-12）。どのドメインもこの publish() だけで domain_event に書きます。

- 呼び出し側のトランザクションの中で書く。コミットはしない（業務の変更と出来事が一緒に残るか、一緒に消える）
- 同じ dedupe_key がもうあれば何もしない（INSERT … ON CONFLICT DO NOTHING）。ワーカーが 2 つ動いても 1 件だけ
- 種類と形は formats.CURRENT_FORMATS のものだけ（版はここで付ける）
"""
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.domain_events.formats import CURRENT_FORMATS, EventFormat
from src.domain_events.models import DomainEvent


def publish(
    db: Session,
    *,
    event_type: str,
    payload: EventFormat,
    dedupe_key: str,
    occurred_at: datetime,
    client_code: str | None = None,
    subject_type: str | None = None,
    subject_id: int | None = None,
) -> int | None:
    """出来事を 1 件書き、その ID を返す。同じ dedupe_key がもうあれば書かずに None を返す"""
    if event_type not in CURRENT_FORMATS:
        raise ValueError(f"Unknown event type: {event_type}")
    version, format_class = CURRENT_FORMATS[event_type]
    if type(payload) is not format_class:
        raise TypeError(f"{event_type} v{version} expects {format_class.__name__}, got {type(payload).__name__}")
    if occurred_at.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware")
    stmt = (
        insert(DomainEvent)
        .values(
            event_type=event_type,
            schema_version=version,
            occurred_at=occurred_at,
            client_code=client_code,
            subject_type=subject_type,
            subject_id=subject_id,
            payload=payload.model_dump(mode="json"),
            dedupe_key=dedupe_key,
        )
        .on_conflict_do_nothing(index_elements=[DomainEvent.dedupe_key])
        .returning(DomainEvent.id)
    )
    return db.execute(stmt).scalar_one_or_none()
