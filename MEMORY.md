# MEMORY.md — Session handoff

Handoff for the next working session. Read this first, then `frontend/README.md` (frontend details) and
`src/domain_plan/` (backend). Last updated: 2026-10-27.

## 1. Current status

- **Branch:** `feature/plan-holiday-calendar` (not merged to `main`, nothing pushed).
- **DB:** dev DB is at Alembic head `3493c39bdf43`. Migrations: `ceee1634d6a7` (squashed init) → `34bdbd214739` →
  `9ee3f3e0624e` → `9b2ec314368a` → `3493c39bdf43`.
- **Tests:** `pytest tests/test_plan.py` → 41 passed (runs against `test_db` via `tests/conftest.py`).
  Type check: `npx pyright@1.1.400 --pythonpath .venv/Scripts/python.exe src main.py tests scripts` → 0 errors.
  Frontend: `cd frontend && npm run build` (= `tsc --noEmit` + Vite build) passes.
- **Uncommitted right now: Step 3 (Create Template)** — implemented and verified, waiting for the user's review/commit:
  - new: `frontend/src/features/plan-settings/CreateTemplateModal.tsx`, `templateForm.ts`,
    `frontend/src/domain/plan/api/sheetApi.ts`, `domain/plan/hooks/useReferenceSheets.ts`, `frontend/src/shared/ui/color.ts`
  - modified: `PlanSettingsPage.tsx`, `domain/plan/api/{index,templateApi}.ts`, `domain/plan/types/index.ts`,
    `features/calendar/components/Icon.tsx` (exports `ICON_NAMES`), `features/schedule-actions/ScheduleFields.tsx`
    (uses shared color helper), `styles.css`

### Recent commits (newest first)
| Commit | Content |
|---|---|
| `9c7976b` | Step 2: business dropdown on the settings page (`GET /biz/`) |
| `86efa29` | Step 1: `/settings/templates` page, routing, read-only template list |
| `2517de5` | Calendar: past days / finished occurrences dimmed |
| `322f5fe` | Client calendar API + React calendar app |
| `b2349cd` | Holiday calendars, rule-generated sheet markers, sheet bulk import |

## 2. Working agreement with the user

- **Baby steps.** The user defines one small step at a time with strict limits (e.g. "create only, no edit/delete").
  Do exactly that step; do not pre-build later steps.
- **The user commits.** Leave each step uncommitted for review unless asked. Before starting a step, check `git status` —
  once a previous step turned out not to be committed; ask before mixing steps.
- **Don't touch the user's dev DB or their running uvicorn (port 8000).** For end-to-end checks use a throwaway DB
  (`plan_check`) + uvicorn on 8001 + Vite on 5174 (`VITE_PROXY_TARGET=http://127.0.0.1:8001 npm --prefix frontend run dev -- --port 5174`).
  Read-only GETs against the user's server are fine. Never run bare `npx vite` from the repo root (it once downloaded Vite 8).
- **Verify before claiming.** Build + type check every change; for API-facing frontend work, run the real domain modules
  through the Vite proxy (esbuild-bundled harness) against the throwaway backend. Say plainly what was *not* checked
  (so far: no in-browser click-through of any page).
- Code comments in the repo are Japanese; UI text is Japanese; replies to the user in English.
- Windows: preserve CRLF line endings in existing files; `.env`/`.env.*` are git-ignored.

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
  Common: `start_time`, `duration_minutes`, `timezone`, `holiday_policy`, `business_calendar_id`, `ui_metadata` (JSONB).
- **Engine** (`engine.py`, pure functions): local wall-clock + IANA tz → UTC (DST-safe). Holiday policies:
  `as_is`, `skip`, `following`, `preceding`, `modified_following`, `modified_preceding`. Rolled occurrences keep their
  original rule date as slot identity (`occurrence_date`); collisions on one day collapse to one.
- **Instances are immutable** (`plan_instance`, DB trigger `plan_reject_mutation` blocks UPDATE/DELETE on
  `plan_instance`, `plan_instance_void`, `plan_slot_release`). Changes are append-only:
  - `origin`: `GENERATED` / `OVERRIDE` (manual reschedule, `overrides_instance_id` chain) / `ONE_OFF`.
  - Void reasons: `TEMPLATE_REVISED`, `TEMPLATE_DELETED`, `SOURCE_DATE_CHANGED`, `MANUAL`, `REVERTED`, `CALENDAR_CHANGED`.
  - Regeneration = **reconcile** (desired vs live generated instances, keyed by start/end/source) over a rolling horizon
    (`materialized_until`, 90 days; extended lazily on calendar reads). Manually changed/cancelled slots are never
    regenerated (`_manual_slots`); `plan_slot_release` records "back under the rule".
  - Generation-affecting template edits bump `revision` and reconcile only the future; the past is never touched.
- **Manual operations:** override (reschedule), cancel, revert (back to rule), one-off (special case), restore cancelled.
  Rules: no scheduling into the past (override / one-off / restore); revert only for rule-generated roots whose original
  time is still in the future (`revertable` flag in calendar responses).
- **Occurrence cache** (`plan_occurrence_cache`): denormalized, GiST `tstzrange` index, effective `ui_metadata`
  (template merged with instance). Rebuildable from instances.
- **Reference sheets** (client annual plans): `reference_sheet` → `reference_sheet_marker` → `reference_sheet_event`.
  Markers can be **rule-generated** (rrule + calendar + policy, e.g. trading day = 27th, next business day);
  human edits/additions/cancellations on generated dates always win (`is_manual`, `is_cancelled` tombstones).
  Bulk import: `POST /plans/sheets/import` (mock file `mock_data/desensitized_mock_data.json`, client `MOCK`).
- **Business calendars:** `business_calendar` (weekend days) + `business_calendar_holiday`; changes cascade to templates
  and generated markers.

### Key endpoints used by the frontend
| Purpose | Endpoint |
|---|---|
| Client month calendar | `GET /plans/clients/{code}/calendar?month=&timezone=&holiday_calendar=` |
| Cancelled list / restore | `GET /plans/clients/{code}/cancelled?start=&end=`, `POST /plans/instances/{id}/restore` |
| Override / cancel / revert | `POST /plans/instances/{id}/override` · `/cancel` · `/revert` |
| One-off | `POST /plans/templates/{id}/instances` |
| Template list / create | `GET /plans/templates?biz_id=`, `POST /plans/templates` |
| Lookups | `GET /clients/`, `GET /biz/` (limit 1000 requested), `GET /plans/calendars`, `GET /plans/sheets?client_code=` |

Optional CORS via `CORS_ORIGINS` in `.env` (dev uses the Vite proxy instead).

## 4. Frontend architecture (`frontend/`)

Vite 5.4 + React 18 + TypeScript + **Ant Design 5.22** (ja_JP locale, theme tokens match `styles.css`) + dayjs +
react-router-dom 6.28. Exact-pinned versions.

- **OpenAPI-generated types:** `python scripts/export_openapi.py` → `frontend/openapi.json`;
  `npm run gen:api` → `src/shared/api/schema.gen.ts` (never edit by hand). Typed client: `openapi-fetch`
  (`src/shared/api/http.ts`, `unwrap()` → `ApiError{status, code, message}`).
- **Layering (one direction): `features → domain → shared`.** UI never calls HTTP directly.
  - `domain/{plan,biz,client}/{types,api,hooks,lib}` — types are aliases of generated schemas only.
  - `shared/` — http client, `useApiQuery` (abort + reload + keep data), `datetime.ts` (dayjs + utc/timezone/
    customParseFormat; `zonedISO()` sends form times as `Asia/Tokyo` wall-clock), `useNow`, `ui/color.ts`.
  - `features/calendar` (daily usage), `features/schedule-actions` (override/cancel/one-off modals, cancelled drawer),
    `features/plan-settings` (pre-configuration).
- **Routes:** `/` calendar · `/settings/templates?biz=<id>` template management. Production needs SPA fallback to
  `index.html`.
- Dev: `npm run dev` (proxy `/api` → `http://127.0.0.1:8000`; IPv4 on purpose).
- Gotchas already fixed — don't regress: ColorPicker `onChange` 2nd arg is `rgb(...)`, use `hexFromColorPicker`;
  dayjs custom formats need `customParseFormat`; antd Modal + `forceRender` without `destroyOnClose`.

## 5. Refactor plan: separate daily usage from pre-configuration (template CRUD)

| Step | Scope | State |
|---|---|---|
| 1 | Settings page + routing + read-only template list | committed `86efa29` |
| 2 | Business dropdown (`GET /biz/`) synced to `?biz=` | committed `9c7976b` |
| 3 | **Create template** (modal form from the OpenAPI union schema) | **implemented, uncommitted — awaiting review** |
| 4 | Edit template | next (not started) |
| 5 | Delete / restore template | later |

### Step 3 details (for review)
- `POST /plans/templates`; body = `RRuleTemplateCreate | ExplicitDatesTemplateCreate | ReferenceSheetTemplateCreate`
  discriminated by `template_type` (`CreatePlanTemplateInput`).
- `templateForm.ts` holds `FormValues`, `initialValues()`, `toCreateInput()` (pure; tested via harness).
  `CreateTemplateModal.tsx` is UI only: type switch, schema-based validation (name ≤100, duration >0,
  RRULE `FREQ=`…, offset −366…366, marker required), calendar required when policy ≠ `as_is` or business-day offset
  (mirrors backend validators), sheet/marker dropdowns limited to the selected business's client, optional
  `ui_metadata` (color/icon/bold). biz_id comes from the page dropdown.
- Success: message → close → reset → table reload. Failure: Alert inside the modal with the backend message.
- Verified: build/typecheck; harness 13/13 (all 3 types created, 422/400/404 errors surfaced, no partial writes).
  Not verified: in-browser clicking.

### Step 4 notes (Edit) — for planning only
- Endpoint: `PATCH /plans/templates/{id}` with `PlanTemplateUpdate` (all optional). `template_type` and `biz_master_id`
  cannot change. Fields not applicable to the type → 400; `null` rejected except `business_calendar_id` (detach) and
  `ui_metadata` (clear, replaces whole object).
- Generation-affecting changes bump `revision` and regenerate future instances (manual overrides are kept);
  name / `ui_metadata` changes do not bump revision (cache updated in place).
- Explicit dates are edited via separate endpoints: `POST /plans/templates/{id}/dates`, `DELETE /plans/templates/{id}/dates/{date}`.
- Likely reuse: `templateForm.ts` (add a `toUpdateInput` that sends only changed fields), `CreateTemplateModal` patterns.

### Step 5 notes (Delete) — for planning only
- `DELETE /plans/templates/{id}` = soft delete: future instances voided (`TEMPLATE_DELETED`), past kept as history.
  `POST /plans/templates/{id}/restore` regenerates from now with a new revision.

## 6. Known data quirks (user's dev DB)
- Client `MOCK`, 15 businesses; only 6 have templates (`MOCK0011`–`MOCK0016`, e.g. biz id 10 → template `会員登録`).
  Template names vs business names look shifted by one (user's test data).
- Calendar `JP_BANK` (holidays 2026-11-03, 2026-11-23 …).
- Instance 910 (10/20) was moved into the past and cancelled; it shows in the cancelled list as not restorable.
