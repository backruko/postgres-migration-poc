"""
seed_data.json（scripts/export_seed.py が書き出したマスタデータ）を空の本番へ入れる。画面と同じ API で作るので、
API の確かめ（顧客が有効か・参照先があるか など）がそのまま効く。本番の使い始めの前日に行う（deploy/GO-LIVE.md）。

    python -m scripts.import_seed --target http://<サーバー>/api --login admin --seed seed_data.json [--effective-date 2026-12-01] [--dry-run]

順番（参照の向きに合わせる）:
  1. ログイン   2. 営業日カレンダーと休日   3. 顧客（すべて有効で作る）   4. 業務と設定   5. 業務の依存
  6. SOP（下書き → 手順 → 承認）   7. 年間計画シート（シート → 手で入れたマーカーと日付 → 生成マーカー）
  8. テンプレート   9. 無効だった顧客を無効にする（無効な顧客には業務・シートを作れないため最後）   10. 件数の確かめ

決まり:
- 本番に顧客・業務・シート・テンプレートが 1 つでもあれば何もしない。カレンダーはあってよい（GO-LIVE の B で
  setup_calendar が作る）: 同じコードなら使い、足りない休日だけ足す（週末の曜日が違えば止める）
- 今日より前の休日は足さない（API が断る。過去の日付は実際の運用の記録。予定は今日から先に作るので要らない）
- 途中で失敗したら、そこで止まる。取り込む前のバックアップに戻してからやり直す（deploy/README.md「6.3 データベースをバックアップから戻す」）
- SOP は承認の日（効く日）が明日以降でなければならない。現行の版は --effective-date（既定は明日）から効く。
  予定の版はその日のまま（その日が --effective-date 以前なら、使い始めの日に効いている最後の版だけを --effective-date で）
- テンプレートを作ると、本番でもすぐに予定とオーダーができる（ふつうの動き）。だから使い始めの前日に行う
- --dry-run: 読むだけ（確かめだけをして、何を作るかを出す）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.seed_api import SEED_VERSION, Api, ApiError, login  # noqa: E402
from src.core.clock import local_date_of, utc_now  # noqa: E402

KINDS = ("calendars", "clients", "businesses", "dependencies", "sops", "sheets", "templates")


class SeedError(Exception):
    """シードのファイルか取り込み先が、取り込める状態ではない"""


# ==========================================
# 取り込む前の確かめ（何も書かない）
# ==========================================
def load_seed(path: Path) -> dict:
    seed = json.loads(path.read_text(encoding="utf-8"))
    if seed.get("seed_version") != SEED_VERSION:
        raise SeedError(f"{path}: seed_version {seed.get('seed_version')!r} は読めない（この版は {SEED_VERSION}）")
    missing = [key for key in KINDS if key not in seed]
    if missing:
        raise SeedError(f"{path}: {missing} が無い")
    return seed


def check_references(seed: dict) -> None:
    """ファイルの中の参照（コード・名前）がすべてファイルの中にあること"""
    calendars = {c["code"] for c in seed["calendars"]}
    clients = {c["code"] for c in seed["clients"]}
    businesses = {b["business_code"] for b in seed["businesses"]}
    sheets = {(s["client_code"], s["name"]) for s in seed["sheets"]}
    problems = [f"business {b['business_code']}: client {b['client_code']}" for b in seed["businesses"] if b["client_code"] not in clients]
    problems += [f"dependency {d['upstream']} -> {d['downstream']}" for d in seed["dependencies"]
                 if not {d["upstream"], d["downstream"]} <= businesses]
    problems += [f"SOP {s['business_code']} {s['version_tag']}" for s in seed["sops"] if s["business_code"] not in businesses]
    problems += [f"sheet {s['client_code']} / {s['name']}" for s in seed["sheets"] if s["client_code"] not in clients]
    problems += [f"marker rule {s['name']} / {m['marker']}: calendar {m['rule']['business_calendar']}"
                 for s in seed["sheets"] for m in s["markers"]
                 if m["rule"] and m["rule"]["business_calendar"] is not None and m["rule"]["business_calendar"] not in calendars]
    for t in seed["templates"]:
        if t["business_code"] not in businesses:
            problems.append(f"template {t['name']}: business {t['business_code']}")
        if t["business_calendar"] is not None and t["business_calendar"] not in calendars:
            problems.append(f"template {t['name']}: calendar {t['business_calendar']}")
        sheet = t.get("reference_sheet")
        if sheet and (sheet["client_code"], sheet["name"]) not in sheets:
            problems.append(f"template {t['name']}: sheet {sheet['client_code']} / {sheet['name']}")
    if problems:
        raise SeedError("ファイルの中で見つからない参照: " + "; ".join(problems))


def check_target(api: Api, seed: dict) -> dict[str, dict]:
    """取り込み先が空であること（カレンダーは除く）。あるカレンダー（コード → 応答）を返す"""
    clients = api.get("/clients/", include_inactive="true")
    if clients:
        raise SeedError(f"取り込み先にもう顧客がある（{len(clients)} 件。例 {clients[0]['code']}）。空のデータベースにだけ入れる")
    existing = {c["code"]: c for c in api.get("/plans/calendars")}
    for c in seed["calendars"]:
        have = existing.get(c["code"])
        if have and sorted(have["weekend_days"]) != sorted(c["weekend_days"]):
            raise SeedError(f"カレンダー {c['code']} の週末の曜日が違う（取り込み先 {have['weekend_days']}、ファイル {c['weekend_days']}）")
    return existing


def sop_plan(seed: dict, effective: date) -> list[tuple[dict, date]]:
    """
    承認する版と効く日。業務ごとに、使い始めの日（effective）に効いている最後の版を effective から、
    それより後の予定の版はその日のまま。使い始めまでに入れ替わる版は持っていかない（出力で知らせる）
    """
    plan = []
    for code in dict.fromkeys(s["business_code"] for s in seed["sops"]):
        versions = sorted((s for s in seed["sops"] if s["business_code"] == code),
                          key=lambda s: (s["state"] != "current", s["effective_date"]))
        due = [v for v in versions if v["state"] == "current" or date.fromisoformat(v["effective_date"]) <= effective]
        if due:
            for skipped in due[:-1]:
                print(f"  SOP {code} {skipped['version_tag']}: {effective} までに次の版に替わるので持っていかない")
            plan.append((due[-1], effective))
        plan += [(v, date.fromisoformat(v["effective_date"])) for v in versions if v not in due]
    return plan


# ==========================================
# 取り込み
# ==========================================
def import_all(api: Api, seed: dict, existing_calendars: dict[str, dict], effective: date) -> None:
    calendar_id = _calendars(api, seed["calendars"], existing_calendars)
    client_id = {c["code"]: api.post("/clients/", {"code": c["code"], "name": c["name"]})["id"] for c in seed["clients"]}
    print(f"clients: {len(client_id)}")
    business_id = _businesses(api, seed["businesses"])
    for d in seed["dependencies"]:
        api.post(f"/biz/{business_id[d['downstream']]}/dependencies", {
            "upstream_biz_id": business_id[d["upstream"]], "match_rule": d["match_rule"], "kind": d["kind"],
            "lookback_days": d["lookback_days"],
        })
    print(f"dependencies: {len(seed['dependencies'])}")
    _sops(api, sop_plan(seed, effective), business_id)
    sheet_id = _sheets(api, seed["sheets"], calendar_id)
    _templates(api, seed["templates"], business_id, sheet_id, calendar_id)
    for c in seed["clients"]:
        if not c["is_active"]:
            api.post(f"/clients/{client_id[c['code']]}/deactivate")
    print(f"clients deactivated: {sum(not c['is_active'] for c in seed['clients'])}")


def holidays_to_add(calendar: dict, known: set[str], today: date) -> tuple[list[dict], int]:
    """
    足す休日と、足さない過去の休日の数。今日より前の休日は足せない（過去の日付は実際の運用の記録なので、
    API が断る）。予定は今日から先に作るので、新しい本番には要らない
    """
    future = [h for h in calendar["holidays"] if date.fromisoformat(h["holiday_date"]) >= today]
    return [h for h in future if h["holiday_date"] not in known], len(calendar["holidays"]) - len(future)


def _calendars(api: Api, calendars: list[dict], existing: dict[str, dict]) -> dict[str, int]:
    """カレンダー（あるものは使う）と、足りない今日以降の休日。コード → id"""
    ids = {}
    today = local_date_of(utc_now())
    for c in calendars:
        have = existing.get(c["code"])
        cid = have["id"] if have else api.post("/plans/calendars", {"code": c["code"], "name": c["name"], "weekend_days": c["weekend_days"]})["id"]
        known = {h["holiday_date"] for h in api.get(f"/plans/calendars/{cid}/holidays")} if have else set()
        missing, past = holidays_to_add(c, known, today)
        if missing:
            api.post(f"/plans/calendars/{cid}/holidays", {"holidays": missing})
        ids[c["code"]] = cid
        print(f"calendar {c['code']}: {'used' if have else 'created'}, holidays added {len(missing)}, past holidays skipped {past}")
    return ids


def _businesses(api: Api, businesses: list[dict]) -> dict[str, int]:
    """業務（同じ番号で作るので業務コードも同じ）と、作るときに渡せない設定。業務コード → id"""
    ids = {}
    for b in businesses:
        created = api.post("/biz/", {key: b[key] for key in ("client_code", "name", "manager_email", "description", "business_seq")})
        if created["business_code"] != b["business_code"]:
            raise SeedError(f"業務コードが変わった: {b['business_code']} -> {created['business_code']}")
        api.patch(f"/biz/{created['id']}", {key: b[key] for key in (
            "release_mode", "release_time", "release_days_before", "execution_type", "completion_mode", "signal_type")})
        ids[b["business_code"]] = created["id"]
    print(f"businesses: {len(ids)}")
    return ids


def _sops(api: Api, plan: list[tuple[dict, date]], business_id: dict[str, int]) -> None:
    """下書きを作り、手順を番号の順に足し、効く日を決めて承認する"""
    for sop, effective in plan:
        biz = business_id[sop["business_code"]]
        version = api.post(f"/biz/{biz}/versions", {"version_tag": sop["version_tag"]})
        for step in sop["steps"]:
            api.post(f"/biz/versions/{version['id']}/steps", step)
        api.post(f"/biz/{biz}/versions/{version['id']}/approve",
                 {"effective_date": effective.isoformat(), "note": "シードデータの取り込み（scripts/import_seed.py）"})
    print(f"SOP versions: {len(plan)}")


def _sheets(api: Api, sheets: list[dict], calendar_id: dict[str, int]) -> dict[tuple[str, str], int]:
    """シートを作り、手で入れたマーカーと日付を取り込み、生成マーカーをルールつきで足す。(顧客, 名前) → id"""
    ids = {}
    for s in sheets:
        sheet = api.post("/plans/sheets", {key: s[key] for key in ("client_code", "period_start", "period_end")} | {"name": s["name"]})
        plain = [m for m in s["markers"] if m["rule"] is None]
        if plain:
            api.post("/plans/sheets/import", {
                "client_code": s["client_code"], "sheet_name": s["name"], "period_start": s["period_start"], "period_end": s["period_end"],
                "markers": [{"marker": m["marker"], "description": m["description"]} for m in plain],
                "events": [{"event_date": d, "marker": m["marker"]} for m in plain for d in m["dates"]],
            })
        for m in s["markers"]:
            if m["rule"] is not None:
                rule = {key: m["rule"][key] for key in ("rrule", "anchor_date", "holiday_policy")}
                rule["business_calendar_id"] = calendar_id[m["rule"]["business_calendar"]] if m["rule"]["business_calendar"] else None
                api.post(f"/plans/sheets/{sheet['id']}/markers", {"marker": m["marker"], "description": m["description"], "rule": rule})
        ids[(s["client_code"], s["name"])] = sheet["id"]
    print(f"sheets: {len(ids)}")
    return ids


def _templates(api: Api, templates: list[dict], business_id: dict[str, int], sheet_id: dict[tuple[str, str], int],
               calendar_id: dict[str, int]) -> None:
    for t in templates:
        body = {key: value for key, value in t.items() if key not in ("business_code", "business_calendar", "reference_sheet")}
        body["biz_master_id"] = business_id[t["business_code"]]
        body["business_calendar_id"] = calendar_id[t["business_calendar"]] if t["business_calendar"] else None
        if t.get("reference_sheet"):
            body["reference_sheet_id"] = sheet_id[(t["reference_sheet"]["client_code"], t["reference_sheet"]["name"])]
        api.post("/plans/templates", body)
    print(f"templates: {len(templates)}")


def report_counts(api: Api, seed: dict) -> list[str]:
    """取り込み先の件数がファイルと合わないもの（合えば空）"""
    clients = api.get("/clients/", include_inactive="true")
    businesses = api.get_all("/biz/")
    counts = {
        "clients": len(clients),
        "businesses": len(businesses),
        "sheets": sum(len(api.get("/plans/sheets", client_code=c["code"])) for c in clients),
        "templates": sum(len(api.get("/plans/templates", biz_id=b["id"])) for b in businesses),
    }
    return [f"{key}: file {len(seed[key])}, target {n}" for key, n in counts.items() if n != len(seed[key])]


def main() -> int:
    parser = argparse.ArgumentParser(description="seed_data.json のマスタデータを空の本番へ入れる")
    parser.add_argument("--target", required=True, help="取り込み先の API の URL（例: http://10.0.0.5/api）")
    parser.add_argument("--login", required=True, help="業務管理者かシステム管理者のログイン ID")
    parser.add_argument("--seed", default="seed_data.json", help="export_seed.py が書き出したファイル")
    parser.add_argument("--effective-date", type=date.fromisoformat, help="現行の SOP が効く日（既定は明日。明日以降）")
    parser.add_argument("--dry-run", action="store_true", help="確かめるだけ（何も作らない）")
    args = parser.parse_args()

    tomorrow = local_date_of(utc_now()) + timedelta(days=1)
    effective = args.effective_date or tomorrow
    try:
        if effective < tomorrow:
            raise SeedError(f"--effective-date は明日（{tomorrow}）以降")
        seed = load_seed(Path(args.seed))
        check_references(seed)
        api = login(args.target, args.login)
        existing_calendars = check_target(api, seed)
        print("files: " + ", ".join(f"{key} {len(seed[key])}" for key in KINDS) + f" / SOP effective from {effective}")
        plan = sop_plan(seed, effective)
        today = local_date_of(utc_now())
        for c in seed["calendars"]:
            _, past = holidays_to_add(c, set(), today)
            if past:
                print(f"  calendar {c['code']}: 今日より前の休日 {past} 件は足さない（API が断るため。予定は今日から先に作る）")
        if args.dry_run:
            print(f"dry run: OK（作るもの: SOP の版 {len(plan)}。何も書いていない）")
            return 0
        import_all(api, seed, existing_calendars, effective)
        differences = report_counts(api, seed)
    except (SeedError, ApiError) as e:
        print(f"import_seed: {e}", file=sys.stderr)
        print("途中まで作ったものは残っている。取り込む前のバックアップに戻してからやり直す", file=sys.stderr)
        return 1
    if differences:
        print("件数が合わない: " + "; ".join(differences), file=sys.stderr)
        return 1
    print("done: 件数はファイルと同じ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
