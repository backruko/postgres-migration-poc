"""
業務の出来事の記録（アウトボックス。通知の仕組み N.1、2026-11-12）。

どのドメインも、起きたこと（例: オーダーが遅れた）を、その変更と同じトランザクションで domain_event に 1 行書きます。
書いた後に送らない・相手を呼ばない: 読むのは通知のドメイン（src/domain_notification）で、コミットの後にワーカーが読みます。
そのため、メールの送信が遅れても失敗しても、業務の変更が止まったり取り消されたりしない。プロセスが止まっても出来事は残る。

- 同じ事実は 2 回記録しない: dedupe_key が一意（期限の監視なら「オーダー 123 の遅れ、締切 …」。締切が動けば別のキー）
- 書いた後に変えられるのは処理の列（processed_at・attempts・last_error）だけ。削除もできない（データベースのトリガー）
- 処理済みの印は行そのものに付ける（読み手は今は通知だけ。2026-11-12 の決定）。未処理の行は部分インデックスで引く
"""
from datetime import datetime
from typing import Any

from sqlalchemy import CHAR, DDL, BigInteger, DateTime, Index, Integer, SmallInteger, String, Text, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.infrastructure.database import Base


class DomainEvent(Base):
    __tablename__ = "domain_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 種類の名前は formats.EventType（例: OrderLate）、キーは deadline_watch.dedupe_key（例: order:123:OrderLate:<締切>）。
    # 下の列のコメント（DB に入っている）の例は N.1 の時点の仮の書き方です
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, comment="出来事の種類（例: order.late）")
    schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1"), comment="payload の形の版（読み手が形を見分ける）"
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="起きた日時（例: 締切）")
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="記録した日時"
    )
    # 顧客の範囲で絞るため。信号のように未登録の顧客コードも残すことがあるので、外部キーにしない
    client_code: Mapped[str | None] = mapped_column(CHAR(4), comment="関わる顧客（顧客の範囲での絞り込み用）")
    subject_type: Mapped[str | None] = mapped_column(String(32), comment="何についての出来事か（例: order）")
    subject_id: Mapped[int | None] = mapped_column(BigInteger, comment="その ID（例: オーダーの ID）")
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), comment="読み手に渡す内容（schema_version の形）"
    )
    dedupe_key: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True, comment="同じ事実を 2 回記録しないためのキー（例: order:123:late:<締切>）"
    )

    # --- 処理（読み手が書く。ここだけ後から変えられる）---
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="読み手が処理を終えた日時")
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), comment="処理を試みた回数（失敗のたびに増える）"
    )
    last_error: Mapped[str | None] = mapped_column(Text, comment="最後の失敗の理由（処理できたら残しておく）")

    __table_args__ = (
        Index("ix_domain_event_unprocessed", "id", postgresql_where=text("processed_at IS NULL")),
        Index("ix_domain_event_subject", "subject_type", "subject_id"),
        Index("ix_domain_event_type_occurred", "event_type", "occurred_at"),
        Index("ix_domain_event_client", "client_code"),
    )


# 記録は変えられない: 処理の列（processed_at・attempts・last_error）だけ変えられる。削除はできない。
# マイグレーションにも同じSQLがあります。ここは create_all（テスト等）用です。
DOMAIN_EVENT_GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION domain_event_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING MESSAGE = 'domain_event is immutable (it cannot be deleted)';
    END IF;
    IF (NEW.id, NEW.event_type, NEW.schema_version, NEW.occurred_at, NEW.recorded_at, NEW.client_code, NEW.subject_type,
        NEW.subject_id, NEW.payload, NEW.dedupe_key)
       IS DISTINCT FROM
       (OLD.id, OLD.event_type, OLD.schema_version, OLD.occurred_at, OLD.recorded_at, OLD.client_code, OLD.subject_type,
        OLD.subject_id, OLD.payload, OLD.dedupe_key) THEN
        RAISE EXCEPTION USING MESSAGE = 'domain_event is immutable (only processed_at, attempts and last_error can change)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""
DOMAIN_EVENT_GUARD_TRIGGER = (
    "CREATE TRIGGER trg_domain_event_guard BEFORE UPDATE OR DELETE ON domain_event "
    "FOR EACH ROW EXECUTE FUNCTION domain_event_guard()"
)

event.listen(DomainEvent.__table__, "after_create", DDL(DOMAIN_EVENT_GUARD_FUNCTION).execute_if(dialect="postgresql"))
event.listen(DomainEvent.__table__, "after_create", DDL(DOMAIN_EVENT_GUARD_TRIGGER).execute_if(dialect="postgresql"))
