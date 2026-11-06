# tests/test_plan_settings_guard.py
"""
事前設定の変更は業務管理者（admin）のみ（U.3: テンプレート・業務の設定、U.4: シート・休日カレンダー・残りの biz）。
予定 1 件の手動操作（変更・取消・臨時・ルールに戻す・取消の復元）は、オーダーの取消・予定日前の開始の承認と同じく
リリース担当・業務管理者（どれもオーダーを動かす・取り消す・作るため。作業者が予定日を今日に動かして承認なしで
開始する抜け道も塞ぐ。U.4）。参照は誰でも。
（ガードの動作そのもの = 401 / 403 は test_staff.py のデコレーターのテストで確認済み。ここでは付け忘れを防ぐ）
"""
from tests.openapi_auth import needs_login


WRITE_METHODS = ("post", "patch", "put", "delete")

# 有効な職員なら誰でもよい書き込み（今は無し）
ANY_ACTIVE_STAFF: set[tuple[str, str]] = set()
# リリース担当・業務管理者（予定 1 件の手動操作）。それ以外の /plans・/biz の書き込みは admin のみ
RELEASER_OR_ADMIN = {
    ("/plans/templates/{template_id}/instances", "post"),
    ("/plans/instances/{instance_id}/override", "post"),
    ("/plans/instances/{instance_id}/revert", "post"),
    ("/plans/instances/{instance_id}/cancel", "post"),
    ("/plans/instances/{instance_id}/restore", "post"),
}


def _operations(prefixes: tuple[str, ...]):
    from main import app

    for path, ops in app.openapi()["paths"].items():
        if path.startswith(prefixes):
            for method, op in ops.items():
                yield path, method, op


def test_every_plan_and_biz_write_needs_a_login():
    writes = [(p, m, op) for p, m, op in _operations(("/plans", "/biz")) if m in WRITE_METHODS]
    assert len(writes) >= 30  # 一覧が空になってテストが素通りしないように（2026-11-05 時点で 35）
    for path, method, op in writes:
        params = {p["name"] for p in op.get("parameters", [])}
        assert needs_login(op), (path, method)
        assert "_require_roles_current_staff" not in params, (path, method)
        assert {"401", "403"} <= set(op["responses"]), (path, method)


def test_settings_writes_need_admin_and_occurrence_changes_any_active_staff():
    """OpenAPI ではロールまで分からないので、ガードの目印（__required_roles__）で確かめる"""
    from fastapi.routing import APIRoute

    from src.domain_biz.router import router as biz_router
    from src.domain_plan.router import router as plan_router

    checked = 0
    # app.routes は include した router をまとめた形なので、各 router のルート（prefix 込みのパス）を直接見る
    for route in [*plan_router.routes, *biz_router.routes]:
        if not isinstance(route, APIRoute):
            continue
        for method in (m.lower() for m in route.methods or ()):
            if method not in WRITE_METHODS:
                continue
            assert hasattr(route.endpoint, "__required_roles__"), (route.path, method)  # ガードの付け忘れ
            roles = getattr(route.endpoint, "__required_roles__")
            if (route.path, method) in ANY_ACTIVE_STAFF:
                assert roles is None, (route.path, method, roles)
            elif (route.path, method) in RELEASER_OR_ADMIN:
                assert roles is not None and {r.value for r in roles} == {"releaser", "admin"}, (route.path, method, roles)
            else:
                assert roles is not None and {r.value for r in roles} == {"admin"}, (route.path, method, roles)
            checked += 1
    assert checked >= 30


def test_reads_need_a_login_but_no_role():
    """参照はロールを問わない（ログインは必要。LG.2）"""
    for path, method, op in _operations(("/plans", "/biz")):
        if method == "get":
            assert needs_login(op), (path, method)
    from fastapi.routing import APIRoute

    from src.domain_biz.router import router as biz_router
    from src.domain_plan.router import router as plan_router

    for route in [*plan_router.routes, *biz_router.routes]:
        if isinstance(route, APIRoute) and "GET" in (route.methods or ()):
            assert getattr(route.endpoint, "__required_roles__", None) is None, route.path
