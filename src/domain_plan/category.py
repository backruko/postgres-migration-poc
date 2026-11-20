"""
テンプレートの分類（定例・不定期・スペシャル）。開始時刻の知らせ（並行稼働の時刻表）で、どの作業を鳴らすかを決めるためにバックエンドも読む。

分類はテンプレートの ui_metadata.category に保存する（列ではない。画面のテンプレートの設定で選ぶ。frontend の
domain/plan/lib/category.ts と同じ決まり）。未設定・知らない値は定例として扱う。

mfa への開始の知らせ（GET /ops/schedule-feed）は、人が動く必要のある不定期・スペシャルの作業だけに出す（定例は自動化が進み、
鳴らすと雑音になる）。臨時に追加した予定（単発）は、テンプレートの分類によらずスペシャル。
"""
from typing import Any, Literal

TaskCategory = Literal["regular", "adhoc", "special"]
CATEGORIES: tuple[TaskCategory, ...] = ("regular", "adhoc", "special")
DEFAULT_CATEGORY: TaskCategory = "regular"
ONE_OFF_CATEGORY: TaskCategory = "special"


def category_of(ui_metadata: dict[str, Any] | None) -> TaskCategory:
    """テンプレートの分類（未設定・知らない値は定例）"""
    value = (ui_metadata or {}).get("category")
    return value if value in CATEGORIES else DEFAULT_CATEGORY
