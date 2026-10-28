# MEMORY.md — Session handoff

Handoff for the next working session. Read this first, then `frontend/README.md` (frontend details) and
`src/domain_plan/` (backend). Last updated: 2026-10-28.

## 1. Current status

- **Branch:** `feature/plan-holiday-calendar` (not merged to `main`, nothing pushed). Working tree clean.
- **DB:** latest Alembic head is `5c1f7d2a9e04` (sheet event audit trail). Migrations: `ceee1634d6a7` (squashed init) →
  `34bdbd214739` → `9ee3f3e0624e` → `9b2ec314368a` → `3493c39bdf43` → `5c1f7d2a9e04`.
  The user runs `alembic upgrade head` on their dev DB themselves. Their uvicorn on 8000 auto-reloads, so a model change
  without the migration makes it return 500 until they upgrade. Warn them whenever a step adds a migration.
- **Tests:** `pytest tests/test_plan.py` → 46 passed (runs against `test_db` via `tests/conftest.py`, `create_all`, no migrations).
  Type check: `npx pyright@1.1.400 --pythonpath .venv/Scripts/python.exe src main.py tests scripts` → 0 errors.
  Migration check (on `plan_check`): `alembic upgrade head` / `downgrade -1` / `alembic check` → no drift.
  Frontend: `cd frontend && npm run build` (= `tsc --noEmit` + Vite build) passes.
- **Not verified yet:** in-browser click-through of the 年間計画シート screen (rule markers, per-date editing, the
  生成をやめる choice) and of the 休日カレンダー screen. The user is testing them.

### Recent commits (newest first)
| Commit | Content |
|---|---|
| `10d7ec0` | 休日カレンダー screen (`/settings/holidays`): list / add (rows or pasted lines) / rename / delete, past read-only in UI |
| `354f613` | 生成をやめる: choose 「日付を残す」 or 「今日以降の日付も取り消す」 (`cancel_future_dates`) |
| `6920a49` | MEMORY.md update (user) |
| `833190e` | Per-date editing UI on 年間計画シート (move / delete / revert / add, past read-only) |
| `42746b3` | Backend: audit trail of sheet dates, past read-only, `revertable` flag, migration `5c1f7d2a9e04` |
| `ad1dd99` | 年間計画シート screen + rule-generated markers (create / edit / stop generating) + duplicate warning |
| `0738f78` | Sticky top nav + sticky weekday row on the calendar |
| `7de97e6` | Template delete + template categories (定例 / 不定期 / スペシャル) with calendar filter |
| `679ff6c` | Template edit (Step 4) + nav keeps each page's query string (committed together) |
| `905f299` | Template create (Step 3) |
| `9c7976b` | Business dropdown on the settings page (`GET /biz/`) |
| `86efa29` | `/settings/templates` page, routing, read-only template list |

## 2. Working agreement with the user

- **Baby steps.** The user defines one step at a time with strict limits. Do exactly that step; do not pre-build later
  steps. When a step turns up a real design question, stop and present options with a recommendation; they decide.
- **Commits.** Leave each step uncommitted for review unless asked. The user usually commits; when they ask, I commit on
  the feature branch (conventional-commit style, `Co-Authored-By` line). Never push. Before starting a step, check
  `git status`; if the previous step is uncommitted, say so (they may ask me to split commits).
- **Don't touch the user's dev DB or their running uvicorn (port 8000).** Read-only GETs against 8000 are fine (useful for
  diagnosing their test data; `app.log` in the repo root logs SQL for both servers). For end-to-end checks use the
  throwaway DB `plan_check` (already exists and migrated; seeded with client `MOCK`, biz 1, calendar `JP_BANK` id 1,
  the mock sheet), uvicorn on 8001 with `DATABASE_URL` pointing at `plan_check`, and Vite on 5174:
  `VITE_PROXY_TARGET=http://127.0.0.1:8001 exec node node_modules/vite/bin/vite.js --port 5174 --strictPort` (from `frontend/`).
  Start Vite with `node` directly, not `npm run dev`: TaskStop only kills the npm wrapper and leaves Vite running.
  Vite binds to `localhost` (IPv6), so harnesses use `http://localhost:5174/api`. Never run bare `npx vite` from the repo root.
- **Verify before claiming.** Build + type check every change; backend changes need pytest + pyright (+ `alembic check`
  for migrations). For API-facing frontend work, bundle the real domain/feature modules with esbuild
  (`--define:import.meta.env='{"VITE_API_BASE_URL":"http://localhost:5174/api","VITE_TIMEZONE":"Asia/Tokyo"}'`) and run
  them in node against the throwaway backend. Say plainly what was *not* checked (in-browser clicking so far).
- Code comments in the repo are Japanese; UI text is Japanese; replies to the user in English.
- Windows line endings: frontend files are CRLF in the working tree; backend Python files and migrations are LF.
  Match the file you edit. Japanese in shell arguments gets mangled (cp932): pass JSON via files and run Python with
  `PYTHONUTF8=1`. `.env`/`.env.*` are git-ignored.

## 3. Backend architecture (FastAPI + SQLAlchemy 2 + PostgreSQL)

Layering per domain: `models.py` / `schemas.py` / `services.py` / `router.py`, repositories in
`src/infrastructure/repositories/` (`CRUDBase`, `SoftCRUDBase`). Errors: `BusinessException` subclasses
(`BadRequestError` 400, `NotFoundError` 404, `DuplicatedRecordError` 409) → `{status_code, error_code, message}`.
Global soft-delete filter (`do_orm_execute` + `with_loader_criteria(SoftDeleteMixin)`) hides deleted rows;
use `.execution_options(include_deleted=True)` to bypass.

### Plan domain (`src/domain_plan/`)
- **Templates** (`plan_template`, soft-deletable) — 3 types:
  `RRULE` (anchor_date + rrule), `EXPLICIT_DATES` (`plan_explicit_date`),
  `REFERENCE_SHEET_BASED` (sheet + marker + `offset_days`/`offset_day_unit` calendar|business + `offset_minutes`).
  Common: `start_time`, `duration_minutes`, `timezone`, `holiday_policy`, `business_calendar_id`, `ui_metadata` (JSONB,
  extra keys allowed; replaced as a whole on PATCH).
- **Template update (PATCH)** compares values: unchanged fields don't bump `revision`; fields of another template type → 400.
  Name / `ui_metadata` changes never bump revision. Delete = soft delete (future instances voided `TEMPLATE_DELETED`,
  past kept); template restore exists in the API but the user decided not to build it.
- **Engine** (`engine.py`, pure functions): local wall-clock + IANA tz → UTC (DST-safe). Holiday policies:
  `as_is`, `skip`, `following`, `preceding`, `modified_following`, `modified_preceding`. Rolled occurrences keep their
  original rule date as slot identity (`occurrence_date`); collisions on one day collapse to one.
- **Instances are immutable** (`plan_instance`, DB trigger `plan_reject_mutation` blocks UPDATE/DELETE on
  `plan_instance`, `plan_instance_void`, `plan_slot_release`). Changes are append-only:
  - `origin`: `GENERATED` / `OVERRIDE` (manual reschedule, `overrides_instance_id` chain) / `ONE_OFF`.
  - Void reasons: `TEMPLATE_REVISED`, `TEMPLATE_DELETED`, `SOURCE_DATE_CHANGED`, `MANUAL`, `REVERTED`, `CALENDAR_CHANGED`.
  - Regeneration = **reconcile** (desired vs live generated instances) over a rolling horizon (`materialized_until`,
    90 days; extended lazily on calendar reads). Manually changed/cancelled slots are never regenerated.
  - Generation-affecting template edits bump `revision` and reconcile only the future; the past is never touched.
- **Manual operations on instances:** override, cancel, revert (back to rule), one-off, restore cancelled
  (`POST /plans/instances/{id}/restore`, used by the 取消済みの予定 drawer; not the same as template restore).
- **Occurrence cache** (`plan_occurrence_cache`): denormalized, GiST `tstzrange` index, effective `ui_metadata`.
- **Business calendars:** `business_calendar` (weekend days) + `business_calendar_holiday`; changes cascade to templates
  and generated markers.

### Reference sheets (client annual plans) — audit rules, decided with the user
`reference_sheet` → `reference_sheet_marker` → `reference_sheet_event`. Real workflow: one baseline marker per client
(e.g. 振込日 = 27th, next business day) with several jobs offset from it (3 business days before, …). Every date change
cascades to all templates referencing the marker (`_propagate_sheet_change`).
- **Markers:** imported (`POST /plans/sheets/import`, JSON format of `mock_data/desensitized_mock_data.json`; import cannot
  create rule markers) or **rule-generated** (`POST /plans/sheets/{id}/markers` with `rule`; `PATCH …/markers/{marker}`
  changes the rule, `rule: null` stops generating).
- **Stopping a rule** = freeze by default: dates and dependent jobs stay, but holidays no longer move them. With
  `cancel_future_dates: true` (only allowed with `rule: null`; the routine has ended) every date from today on is
  cancelled in one transaction, by the same rules as a single delete (`_cancel_sheet_event`); past dates untouched.
- **Event provenance** (never erased, including after stopping a rule): `rule_date` (rule slot), `original_date`
  (imported/rule date before a person moved or cancelled it), `is_manual` (a person changed it), `is_cancelled`
  (取消 record). UI labels: ルール / 人が変更 (元: original) / 人が追加 / 取消 / 取り込み.
- **People always win:** manual edits are kept on rule change, calendar change, re-import and `replace_events`.
  Option 1 (user's decision): a manual future date can coexist with a new rule date in the same month; the system
  does not auto-delete it, the UI warns (edit form, after save, orange 「今後: …」 tag) and the user cleans up.
- **Deleting:** rule/imported dates become 取消 records; hand-added dates (no rule/original date) are hard-deleted
  (a record would block that day for future rule dates via the unique `(marker_id, event_date)` constraint).
- **Past is read-only (backend-enforced):** add / move / delete / revert of dates before today → 400. "Today" is JST
  (`SHEET_TIMEZONE`, `_sheet_today()` in services). Rule and holiday changes don't rewrite past dates; only a new
  marker's first generation fills the past. Import is still allowed to add past dates (initial loads need it), and
  `replace_events` can still delete past imported dates that nobody edited.
- **`revertable`** on each event (computed, not stored; `_annotate_revertable`): true only if the current rule produces
  that slot on a free date ≥ today. Same conditions as `POST /plans/sheets/events/{id}/revert`.

### Key endpoints used by the frontend
| Purpose | Endpoint |
|---|---|
| Client month calendar | `GET /plans/clients/{code}/calendar?month=&timezone=&holiday_calendar=` |
| Cancelled list / restore | `GET /plans/clients/{code}/cancelled?start=&end=`, `POST /plans/instances/{id}/restore` |
| Override / cancel / revert | `POST /plans/instances/{id}/override` · `/cancel` · `/revert` |
| One-off | `POST /plans/templates/{id}/instances` |
| Templates | `GET /plans/templates?biz_id=`, `POST /plans/templates`, `PATCH` / `DELETE /plans/templates/{id}`, `POST` / `DELETE …/dates` |
| Sheets / markers | `GET /plans/sheets?client_code=`, `POST /plans/sheets/{id}/markers`, `PATCH /plans/sheets/{id}/markers/{marker}` |
| Sheet dates | `POST …/markers/{marker}/events`, `PATCH` / `DELETE /plans/sheets/events/{id}`, `POST …/events/{id}/revert` |
| Holidays | `GET /plans/calendars/{id}/holidays` (all years; the page filters by year), `POST …/holidays` (re-posting a date only renames it, no regeneration), `DELETE …/holidays/{date}` |
| Lookups | `GET /clients/`, `GET /biz/` (limit 1000 requested), `GET /plans/calendars` |

Optional CORS via `CORS_ORIGINS` in `.env` (dev uses the Vite proxy instead).

## 4. Frontend architecture (`frontend/`)

Vite 5.4 + React 18 + TypeScript + **Ant Design 5.22** (ja_JP locale, theme tokens match `styles.css`) + dayjs +
react-router-dom 6.28. Exact-pinned versions. Plain CSS with color variables (light/dark), no Tailwind.

- **OpenAPI-generated types:** `python scripts/export_openapi.py` → `frontend/openapi.json`;
  `npm run gen:api` → `src/shared/api/schema.gen.ts` (never edit by hand; regenerate after backend schema changes).
  Typed client: `openapi-fetch` (`src/shared/api/http.ts`, `unwrap()` → `ApiError{status, code, message}`).
- **Layering (one direction): `features → domain → shared`.** UI never calls HTTP directly.
  - `domain/{plan,biz,client}/{types,api,hooks,lib}` — types are aliases of generated schemas only.
    `domain/plan/lib/category.ts`: template category stored in `ui_metadata.category` (missing → 定例).
  - `shared/` — http client, `useApiQuery`, `datetime.ts`, `useNow`, `ui/color.ts`, `config.ts` (`TIMEZONE`).
  - `features/calendar`, `features/schedule-actions`, `features/plan-settings`.
    Pure form logic lives in `templateForm.ts` / `markerForm.ts` / `holidayForm.ts` (testable in node); modals are UI only.
- **Routes / nav:** `/` calendar (`?client=&month=&holidays=`) · `/settings/templates?biz=` · `/settings/sheets?client=` ·
  `/settings/holidays?calendar=<id>&year=`.
  The layout remembers each page's last query string, so switching pages keeps selections. The calendar's
  `useUrlState` goes through react-router (`useSearchParams`), not `history.replaceState`.
- **Calendar:** category chips (定例 hidden by default, stored in localStorage) + per-template legend toggles; sticky nav
  (`--nav-h: 44px`) and sticky weekday row (`.month` uses `overflow: clip` so sticky works).
- Dev: `npm run dev` (proxy `/api` → `http://127.0.0.1:8000`; IPv4 on purpose). Production needs SPA fallback to `index.html`.
- Gotchas already fixed — don't regress: ColorPicker `onChange` 2nd arg is `rgb(...)`, use `hexFromColorPicker`;
  dayjs custom formats need `customParseFormat`; antd Modal + `forceRender` without `destroyOnClose` → reset the form in
  a `useEffect` on `open` and keep the target in state while closing; `ui_metadata` PATCH replaces the whole object, so
  forms carry over unknown keys (`text_color` …). openapi-typescript marks every field with a default as required, so
  request bodies send those fields explicitly (e.g. `cancel_future_dates: false`, `offset_days`).

## 5. Plan endpoint audit (2026-10-28) — what is still unused, and the agreed MVP
Done since the audit: rule-marker create/edit, per-date editing, stop-rule choice, holiday maintenance screen.
Remaining MVP candidates (recommended, not started):
1. **Sheet import UI** (`POST /plans/sheets/import`) + read-only sheet view. Open question for the user: clients may send
   Excel, not this JSON format.
2. A way to create a sheet for a client with none (`POST /plans/sheets`) — without it, rule markers have no sheet.

Open decision offered to the user: enforce "past holidays are read-only" in the backend too (today UI-only; harmless
for history because past occurrences and past sheet dates no longer change, only past calendar labels would).

Later / not planned: template audit history (`GET /plans/templates/{id}/instances`, the only way to see a deleted
template's voided occurrences), calendar create / weekend change, flat occurrence lists, cache rebuild (backend-only),
template restore (decided not needed). Next domains after Plan: Biz and Client.

## 6. Known data quirks (user's dev DB)
- Client `MOCK`, 15 businesses; only some have templates. Template names vs business names look shifted by one.
- Sheets: `Annual_Schedule` (id 2, imported) and `MOCK_Schedule` (id 1). Marker `引落日` (id 10) was a rule marker; its
  history was wiped by the old 生成をやめる before the audit-trail fix (unrecoverable, accepted by the user).
- Calendar `JP_BANK` id 1 (holidays 2026-11-03, 2026-11-23 …).
- Instance 910 (10/20) was moved into the past and cancelled; it shows in the cancelled list as not restorable.
- Dates moved before migration `5c1f7d2a9e04` have no `original_date`; dates added via the UI on imported markers before
  it look like 取り込み.
