# tests/frozen_clock.py
"""
止めた時計（テストの「いま」を決める）。fixture `frozen_clock`（conftest.py）から使う。

なぜ: 本当の時刻で動くテストは、実行する時刻で結果が変わる（今日の 9:00 の枠が、9:00 を過ぎると作られない・
UTC の 0 時 = 日本時間 9:00 で日付の境目をまたぐ・月末など）。実際に、8:00〜10:00 の間だけ失敗したテストがあった。

どう止めるか: アプリの「いま」はすべて src.core.clock.utc_now から来る（ほかに本当の時刻を読む所は無い）。
ただし各モジュールは `from src.core.clock import utc_now as _now` のように自分の名前で持つので、clock だけを
差し替えても届かない。そこで、読み込み済みのこのリポジトリのモジュール（src・scripts・adapters・tests。ファイルの
場所で読み込んだ worker なども）のうち、本物の utc_now をそのまま持っている名前を、すべてこの時計に差し替える。
テストのファイル自身が import した utc_now も止まるので、テストの中の utc_now() も同じ「いま」になる。

届かないもの:
- 引数の既定値として持った時計（例: HealthRecorder(now=...)）。関数を作った時点の本物を持つので、テストから渡す
- データベースが付ける時刻（created_at などの server_default）。比べるのには使っていない
- datetime.now() / date.today() をテストに直接書いたもの。frozen_clock.now / frozen_clock.today に書き換える
"""
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.core import clock as _clock
from src.core.clock import LOCAL_TIMEZONE, local_date_of

_REAL_UTC_NOW = _clock.utc_now
_REPO = Path(__file__).resolve().parent.parent
_VENV = _REPO / ".venv"

# 既定の「いま」: 2026-11-18（水）12:00 日本時間 = 03:00 UTC。平日・月の半ば・日の真ん中で、
# 日本時間の 0 時・UTC の 0 時（日本時間 9:00）・業務の時刻（9:00 など）のどれからも離れている。
# 動かすときは mock_data の年間計画シートの期間（2023-04-01〜2028-03-31）の中に置く: シートを使うテストは、
# 「いま」より後のシートの日付があることを前提にしている
FROZEN_AT = datetime(2026, 11, 18, 12, 0, tzinfo=LOCAL_TIMEZONE)


class FrozenClock:
    """呼ぶと「いま」（UTC、タイムゾーン付き）を返す。advance / set で動かすまで動かない"""

    def __init__(self, at: datetime = FROZEN_AT):
        self.set(at)

    def __call__(self) -> datetime:
        return self.now

    def set(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FrozenClock needs a timezone-aware datetime")
        self.now = at.astimezone(UTC)

    def advance(self, **delta: float) -> datetime:
        self.now += timedelta(**delta)
        return self.now

    @property
    def today(self):
        """日本時間の今日"""
        return local_date_of(self.now)


_REPO_PREFIX = os.path.normcase(str(_REPO)) + os.sep
_VENV_PREFIX = os.path.normcase(str(_VENV)) + os.sep
_is_repo: dict[str, bool] = {}  # モジュールの名前 → このリポジトリのものか（テストごとに 800 以上を調べ直さない）


def _repo_modules():
    """このリポジトリのファイルから読み込んだモジュール（.venv の中は除く）"""
    for name, module in list(sys.modules.items()):
        mine = _is_repo.get(name)
        if mine is None:
            path = os.path.normcase(os.path.abspath(getattr(module, "__file__", None) or ""))
            mine = path.startswith(_REPO_PREFIX) and not path.startswith(_VENV_PREFIX) and name != __name__
            _is_repo[name] = mine
        if mine:
            yield module


def freeze(monkeypatch, at: datetime = FROZEN_AT) -> FrozenClock:
    """本物の utc_now を持つ名前をすべて止めた時計に差し替える（monkeypatch がテストの終わりに戻す）"""
    frozen = FrozenClock(at)
    for module in _repo_modules():
        for name, value in list(vars(module).items()):
            if value is _REAL_UTC_NOW:
                monkeypatch.setattr(module, name, frozen)
    return frozen
