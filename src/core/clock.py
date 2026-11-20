"""
時刻とタイムゾーン。アプリの「いま」と「業務の日付（日本時間）」はここから取る。

- utc_now(): いまの時刻（UTC、タイムゾーン付き）。データベースには UTC で保存する
- LOCAL_TIMEZONE: 業務のタイムゾーン（日本時間）。日付の区切り（「今日」・月・締切の日）はこれで決まる
- local_date_of(moment): その時刻が日本時間で何日か
- assume_local(value): タイムゾーンの無い日時を日本時間とみなす（画面や API から日時だけが来たとき）

業務のタイムゾーンは設定ではなく固定。画面（VITE_TIMEZONE）・コンテナ（TZ）・SQL の
timezone(...) も同じ日本時間で、バックエンドだけを変えると日の区切りが食い違うため。

テストで時刻を決めるとき: 各モジュールは `from src.core.clock import utc_now as _now` のように自分の名前で持ち、
テストはそのモジュールの `_now`（または `utc_now`）だけを差し替える（ほかのモジュールは本当の時刻のまま）。
"""
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

LOCAL_TIMEZONE_NAME = "Asia/Tokyo"
LOCAL_TIMEZONE = ZoneInfo(LOCAL_TIMEZONE_NAME)


def utc_now() -> datetime:
    """いまの時刻（UTC、タイムゾーン付き）"""
    return datetime.now(UTC)


def local_date_of(moment: datetime) -> date:
    """その時刻が日本時間で何日か（タイムゾーン付きの時刻を渡す）"""
    return moment.astimezone(LOCAL_TIMEZONE).date()


def assume_local(value: datetime | None) -> datetime | None:
    """タイムゾーンの無い日時を日本時間とみなす（付いていればそのまま。None は None）"""
    return value.replace(tzinfo=LOCAL_TIMEZONE) if value is not None and value.tzinfo is None else value
