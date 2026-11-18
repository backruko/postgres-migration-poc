"""
顧客の範囲（担当する顧客だけを扱う職員）。

職員に staff_client の行があれば、その顧客だけを扱えます（行が無ければ全顧客。今までどおり）。
ロールは「何ができるか」、範囲は「どの顧客か」を決めます。顧客での絞り込みは、すべてここを通します:

- 一覧: `stmt.where(scope.clause(Model.client_code))` で SQL の中で絞る（読み込んでから捨てない）
- 1件: `scope.require(obj.client_code, "Order (id=1) not found.")` で、範囲の外なら本当に無いときと同じ 404
- 全顧客に共通の設定（顧客マスタ・営業日カレンダーの変更・職員・API キー）: require_roles(..., all_clients=True) で 403

サービスの引数は `scope: ClientScope = ALL_CLIENTS` を既定にします（ワーカー・スクリプト・既存のテストは全顧客のまま）。
"""
from dataclasses import dataclass
from collections.abc import Iterable

from sqlalchemy import ColumnElement, true
from sqlalchemy.orm import InstrumentedAttribute

from src.core.exceptions import NotFoundError


@dataclass(frozen=True)
class ClientScope:
    """codes が None なら全顧客。そうでなければ、そのコードの顧客だけ"""
    codes: frozenset[str] | None = None

    @classmethod
    def of(cls, codes: Iterable[str]) -> ClientScope:
        """職員の担当顧客から作る（空 = 全顧客）"""
        wanted = frozenset(codes)
        return cls(wanted or None)

    @property
    def limited(self) -> bool:
        return self.codes is not None

    def allows(self, client_code: str | None) -> bool:
        if self.codes is None:
            return True
        return client_code is not None and client_code.strip().upper() in self.codes

    def require(self, client_code: str | None, not_found_message: str) -> None:
        """範囲の外なら、本当に無いときと同じ 404（ほかの顧客のものがあることも見せない）"""
        if not self.allows(client_code):
            raise NotFoundError(not_found_message)

    def clause(
        self, column: InstrumentedAttribute[str] | InstrumentedAttribute[str | None] | ColumnElement[str] | ColumnElement[str | None],
    ) -> ColumnElement[bool]:
        """一覧の絞り込み（SQL の WHERE に入れる）。全顧客なら常に真。顧客が空（NULL）の行は、範囲のある職員には出ない（IN）"""
        if self.codes is None:
            return true()
        return column.in_(sorted(self.codes))

    def visible(self, client_codes: Iterable[str]) -> list[str]:
        """コードの並びのうち、範囲の中のものだけ（順序はそのまま）"""
        return [c for c in client_codes if self.allows(c)]


ALL_CLIENTS = ClientScope()
