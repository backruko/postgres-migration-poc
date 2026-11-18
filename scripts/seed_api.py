"""
シードデータ（マスタデータ）の書き出し・取り込みの共通部分（scripts/export_seed.py・scripts/import_seed.py が使う）。

REST API にログインし、画面と同じ API で読み書きする（データベースには直接触らない。API の確かめがそのまま効く）。
パスワードは実行のときに聞く（コマンドの引数や履歴に残さない）。開発 PC がプロキシを通っているときだけ、
環境変数 NO_PROXY にサーバーのアドレスを入れて、社内のサーバーへは直接つなぐ（requests は HTTP_PROXY / NO_PROXY を見る）。
"""
from __future__ import annotations

import getpass
from typing import Any

import requests

SEED_VERSION = 1  # seed_data.json の形の版。形を変えたら上げ、import_seed.py は知らない版を断る


class ApiError(Exception):
    """API が 4xx / 5xx を返した（どの呼び出しで、何と言われたか）"""


class Api:
    def __init__(self, base_url: str, timeout: float = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()  # ログインの Cookie を持ち回る

    def call(self, method: str, path: str, *, params: dict | None = None, body: Any = None) -> Any:
        response = self.session.request(method, self.base_url + path, params=params, json=body, timeout=self.timeout)
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text[:500]
            raise ApiError(f"{method} {path} -> {response.status_code}: {detail}")
        return response.json() if response.content else None

    def get(self, path: str, **params: Any) -> Any:
        return self.call("GET", path, params=params or None)

    def post(self, path: str, body: Any = None, **params: Any) -> Any:
        return self.call("POST", path, params=params or None, body=body)

    def patch(self, path: str, body: Any) -> Any:
        return self.call("PATCH", path, body=body)

    def get_all(self, path: str, page_size: int = 100, **params: Any) -> list:
        """skip / limit で区切られた一覧をすべて読む"""
        items: list = []
        while True:
            page = self.get(path, skip=len(items), limit=page_size, **params)
            items.extend(page)
            if len(page) < page_size:
                return items


def login(base_url: str, login_id: str) -> Api:
    """ログインした Api（パスワードはここで聞く）。最初のパスワードを変えていない職員は使えない（画面で変えてから）"""
    api = Api(base_url)
    password = getpass.getpass(f"{login_id} @ {base_url} のパスワード: ")
    me = api.post("/auth/login", {"login_id": login_id, "password": password})  # 応答はログインした職員
    if me.get("must_change_password"):
        raise ApiError(f"{login_id} は最初のパスワードを変えていない。画面でログインして変えてから使う")
    return api
