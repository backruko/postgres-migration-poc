# tests/client_codes.py
"""
テスト用の顧客コード（重複しない）。

顧客コードは CHAR(4) 固定なので、テストファイルごとの先頭 1 文字 + 16 進 3 桁（4096 通り）しかない。
乱数で選ぶと 1 回の実行の中で同じコードが出て、uq_client_id で失敗することがあった（test_plan.py で約 1/5、
ほかのファイルでもまれに）。先頭文字ごとにシャッフルした候補から、重複なしで取り出す。
test_db は実行の終わりに作り直される（conftest.py）ので、1 回の実行の中で重複しなければよい。
"""
import random
from collections.abc import Iterator

_pools: dict[str, Iterator[str]] = {}


def new_client_code(prefix: str) -> str:
    """prefix（テストファイルごとの英大文字 1 文字）+ 16 進 3 桁。1 回の実行の中では同じコードを返さない"""
    if len(prefix) != 1:
        raise ValueError("prefix must be one character (client codes are CHAR(4))")
    if prefix not in _pools:
        _pools[prefix] = iter(random.sample([f"{prefix}{n:03X}" for n in range(16**3)], 16**3))
    return next(_pools[prefix])
