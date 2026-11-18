"""
テスト環境のマスタデータを seed_data.json に書き出す（読むだけ。GET の API だけを使う）。本番の使い始めの前日に、
scripts/import_seed.py で空の本番へ入れる（deploy/GO-LIVE.md）。

    python scripts/export_seed.py --source http://localhost:8000 --login admin [--out seed_data.json]

書き出すもの: 営業日カレンダーと休日・顧客（有効・無効）・業務と設定・業務の依存・SOP（現行と予定の版と手順）・
年間計画シート（マーカーと今の日付・生成のルール）・テンプレート。
書き出さないもの: オーダー・予定（発生枠）と画面での変更・シグナル・出来事・通知・職員・通知の設定・API キー・
SOP とテンプレートの履歴・下書き・削除したもの。
ファイルにはデータベースの id を入れない（本番では id が変わる）。互いの参照は コード・名前 で書く。
人がシートの日付を動かした・取り消した記録は持っていかない（今の日付だけ）。その日付は最後に一覧で出す。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.seed_api import SEED_VERSION, Api, ApiError, login  # noqa: E402
from src.core.clock import local_date_of  # noqa: E402

TEMPLATE_COMMON = (
    "name", "template_type", "timezone", "start_time", "duration_minutes", "holiday_policy", "ui_metadata",
    "start_prompt", "deadline_mode", "deadline_offset_minutes", "deadline_time", "deadline_day_offset",
    "warn_before_minutes",
)


def export_all(api: Api) -> tuple[dict, list[str]]:
    """(seed_data.json の中身, 人が変えたシートの日付の一覧)"""
    calendars = api.get("/plans/calendars")
    calendar_code = {c["id"]: c["code"] for c in calendars}
    clients = api.get("/clients/", include_inactive="true")
    businesses = [b for b in api.get_all("/biz/") if not b["is_deleted"]]
    business_code = {b["id"]: b["business_code"] for b in businesses}

    seed: dict = {
        "seed_version": SEED_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "source": api.base_url,
        "calendars": [
            {"code": c["code"], "name": c["name"], "weekend_days": c["weekend_days"],
             "holidays": [{"holiday_date": h["holiday_date"], "name": h["name"]}
                          for h in api.get(f"/plans/calendars/{c['id']}/holidays")]}
            for c in calendars
        ],
        "clients": [{"code": c["code"], "name": c["name"], "is_active": c["is_active"]} for c in clients],
        "businesses": [
            {key: b[key] for key in ("business_code", "client_code", "name", "business_seq", "manager_email", "description",
                                     "release_mode", "release_time", "release_days_before", "execution_type",
                                     "completion_mode", "signal_type")}
            for b in businesses
        ],
        "dependencies": _dependencies(api, businesses),
        "sops": _sops(api, businesses),
    }
    sheets, sheet_key, edited = _sheets(api, clients, calendar_code)
    seed["sheets"] = sheets
    seed["templates"] = _templates(api, businesses, business_code, sheet_key, calendar_code)
    return seed, edited


def _dependencies(api: Api, businesses: list[dict]) -> list[dict]:
    """業務の依存（どちらの業務の一覧にも出るので 1 つにまとめる）"""
    found: dict[int, dict] = {}
    for b in businesses:
        for d in api.get(f"/biz/{b['id']}/dependencies"):
            found[d["id"]] = {"downstream": d["downstream"]["business_code"], "upstream": d["upstream"]["business_code"],
                              "match_rule": d["match_rule"], "kind": d["kind"], "lookback_days": d["lookback_days"]}
    return sorted(found.values(), key=lambda d: (d["downstream"], d["upstream"]))


def _sops(api: Api, businesses: list[dict]) -> list[dict]:
    """各業務の現行の版と、予定（承認済み・まだ効いていない）の版。手順は番号の順"""
    sops = []
    for b in businesses:
        for v in api.get(f"/biz/{b['id']}/versions"):
            if v["state"] not in ("current", "scheduled"):
                continue
            detail = api.get(f"/biz/versions/{v['id']}")
            sops.append({
                "business_code": b["business_code"], "version_tag": v["version_tag"], "state": v["state"],
                "effective_date": local_date_of(datetime.fromisoformat(v["effective_at"])).isoformat(),
                "steps": [{key: s[key] for key in ("step_num", "title", "content", "condition", "is_blocking")}
                          for s in sorted(detail["steps"], key=lambda s: s["step_num"])],
            })
    return sops


def _sheets(api: Api, clients: list[dict], calendar_code: dict[int, str]) -> tuple[list[dict], dict[int, dict], list[str]]:
    """(シート, シートの id → {client_code, name}, 人が変えた日付の一覧)。日付は取り消していない今の日付だけ"""
    sheets, sheet_key, edited = [], {}, []
    for client in clients:
        for s in api.get("/plans/sheets", client_code=client["code"]):
            sheet_key[s["id"]] = {"client_code": s["client_code"], "name": s["name"]}
            markers = []
            for m in s["markers"]:
                rule = None
                if m["is_generated"]:
                    rule = {"rrule": m["rrule"], "anchor_date": m["anchor_date"], "holiday_policy": m["holiday_policy"],
                            "business_calendar": calendar_code.get(m["business_calendar_id"])}
                for e in m["events"]:
                    if e.get("is_manual") or e.get("is_cancelled"):
                        what = "取り消し" if e.get("is_cancelled") else (f"{e['original_date']} から移動" if e.get("original_date") else "人が追加")
                        edited.append(f"{s['client_code']} / {s['name']} / {m['marker']} / {e['event_date']}: {what}")
                markers.append({
                    "marker": m["marker"], "description": m["description"], "rule": rule,
                    # 生成マーカーの日付は本番でルールから作り直すので持っていかない
                    "dates": [] if rule else sorted(e["event_date"] for e in m["events"] if not e.get("is_cancelled")),
                })
            sheets.append({"client_code": s["client_code"], "name": s["name"], "period_start": s["period_start"],
                           "period_end": s["period_end"], "markers": markers})
    return sheets, sheet_key, edited


def _templates(api: Api, businesses: list[dict], business_code: dict[int, str], sheet_key: dict[int, dict],
               calendar_code: dict[int, str]) -> list[dict]:
    """テンプレートの設定（種類ごとの項目。業務・シート・カレンダーはコードで）"""
    templates = []
    for b in businesses:
        for t in api.get("/plans/templates", biz_id=b["id"]):
            item = {key: t[key] for key in TEMPLATE_COMMON}
            item["business_code"] = business_code[t["biz_master_id"]]
            item["business_calendar"] = calendar_code.get(t["business_calendar_id"]) if t["business_calendar_id"] else None
            if t["template_type"] == "rrule":
                item.update(anchor_date=t["anchor_date"], rrule=t["rrule"])
            elif t["template_type"] == "explicit_dates":
                item["dates"] = sorted(d["local_date"] for d in t["explicit_dates"])
            elif t["template_type"] == "reference_sheet_based":
                item.update(reference_sheet=sheet_key[t["reference_sheet_id"]], reference_event_marker=t["reference_event_marker"],
                            offset_days=t["offset_days"], offset_day_unit=t["offset_day_unit"], offset_minutes=t["offset_minutes"])
            else:
                raise ApiError(f"unknown template type {t['template_type']!r} (template {t['name']!r})")
            templates.append(item)
    return templates


def main() -> int:
    parser = argparse.ArgumentParser(description="テスト環境のマスタデータを seed_data.json に書き出す（読むだけ）")
    parser.add_argument("--source", required=True, help="API の URL（例: http://localhost:8000）")
    parser.add_argument("--login", required=True, help="業務管理者かシステム管理者のログイン ID")
    parser.add_argument("--out", default="seed_data.json", help="書き出すファイル（既定 seed_data.json）")
    args = parser.parse_args()
    try:
        seed, edited = export_all(login(args.source, args.login))
    except ApiError as e:
        print(f"export_seed: {e}", file=sys.stderr)
        return 1
    Path(args.out).write_text(json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{args.out}: " + ", ".join(f"{key} {len(seed[key])}" for key in
                                      ("calendars", "clients", "businesses", "dependencies", "sops", "sheets", "templates")))
    if edited:
        print(f"人が変えたシートの日付 {len(edited)} 件（今の日付だけを持っていく。変えた記録は持っていかない）:")
        for line in edited:
            print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
