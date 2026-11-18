# tests/openapi_auth.py
"""OpenAPI の認証の確認（LG.2: すべての API はログインのセッション Cookie が必要。信号の送信だけ API キーも可）"""


def needs_login(op: dict) -> bool:
    """その操作がログインのセッション（Cookie ops_session）を求めるか"""
    return {"APIKeyCookie": []} in op.get("security", [])
