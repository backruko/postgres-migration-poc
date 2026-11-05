# tests/test_plan_settings_guard.py
"""
予定テンプレートの画面（U.3）: テンプレートと業務の設定の変更は業務管理者（admin）のみ。参照は誰でも。
（ガードの動作そのもの = 401 / 403 は test_staff.py のデコレーターのテストで確認済み。ここでは付け忘れを防ぐ）
"""

GUARDED = (
    ("/plans/templates", "post"),
    ("/plans/templates/{template_id}", "patch"),
    ("/plans/templates/{template_id}", "delete"),
    ("/plans/templates/{template_id}/restore", "post"),
    ("/plans/templates/{template_id}/dates", "post"),
    ("/plans/templates/{template_id}/dates/{local_date}", "delete"),
    ("/plans/templates/{template_id}/cache/rebuild", "post"),
    ("/biz/{biz_id}", "patch"),
)

OPEN = (
    ("/plans/templates", "get"),
    ("/plans/templates/{template_id}", "get"),
    ("/biz/{biz_id}", "get"),
    ("/biz/", "get"),
)


def test_template_and_business_setting_changes_need_the_staff_header():
    from main import app

    paths = app.openapi()["paths"]
    for path, method in GUARDED:
        op = paths[path][method]
        params = {p["name"] for p in op.get("parameters", [])}
        assert "x-staff-id" in params, (path, method)
        assert "_require_roles_current_staff" not in params, (path, method)
        assert {"401", "403"} <= set(op["responses"]), (path, method)
    for path, method in OPEN:
        assert "x-staff-id" not in {p["name"] for p in paths[path][method].get("parameters", [])}, (path, method)
