# MEMORY.md — Session handoff

Handoff for the next working session. Read this first, then the design doc (§2), `frontend/README.md` (frontend
details) and `src/domain_plan/` (backend). Last updated: 2026-10-28.

## 1. Current status

- **Branch:** `feature/plan-holiday-calendar` (not merged to `main`, nothing pushed). Working tree clean.
- **Where we are:** the Plan domain MVP is done (calendar, template CRUD, sheets with rule markers and per-date editing,
  holiday maintenance). The cross-domain design for operations is drafted (§2) and awaits team review.
  **Next step: Phase 1, the cross-client daily dashboard** (read-only, no new domain).
- **DB:** latest Alembic head is `5c1f7d2a9e04` (sheet event audit trail). Migrations: `ceee1634d6a7` (squashed init) →
  `34bdbd214739` → `9ee3f3e0624e` → `9b2ec314368a` → `3493c39bdf43` → `5c1f7d2a9e04`.
  The user runs `alembic upgrade head` on their dev DB themselves. Their uvicorn on 8000 auto-reloads, so a model change
  without the migration makes it return 500 until they upgrade. Warn them whenever a step adds a migration.
- **Tests:** `pytest tests/test_plan.py` → 47 passed (runs against `test_db` via `tests/conftest.py`, `create_all`, no migrations).
  Type check: `npx pyright@1.1.400 --pythonpath .venv/Scripts/python.exe src main.py tests scripts` → 0 errors.
  Migration check (on `plan_check`): `alembic upgrade head` / `downgrade -1` / `alembic check` → no drift.
  Frontend: `cd frontend && npm run build` (= `tsc --noEmit` + Vite build) passes.
- **Not verified yet:** in-browser click-through of the 年間計画シート screen (rule markers, per-date editing, the
  生成をやめる choice) and of the 休日カレンダー screen (including 日本の祝日を取り込む). The user is testing them.

### Recent commits (newest first)
| Commit | Content |
|---|---|
| `ae9ac2e` | MEMORY.md update |
| `fa52e42` | Past holidays read-only in the backend; 「日本の祝日を取り込む」 (calculated, 2022–2099) |
| `1fc8606` | MEMORY.md update |
| `10d7ec0` | 休日カレンダー screen (`/settings/holidays`): list / add (rows or pasted lines) / rename / delete |
| `354f613` | 生成をやめる: choose 「日付を残す」 or 「今日以降の日付も取り消す」 (`cancel_future_dates`) |
| `6920a49` | MEMORY.md update (user) |
| `833190e` | Per-date editing UI on 年間計画シート (move / delete / revert / add, past read-only) |
| `42746b3` | Backend: audit trail of sheet dates, past read-only, `revertable` flag, migration `5c1f7d2a9e04` |
| `ad1dd99` | 年間計画シート screen + rule-generated markers (create / edit / stop generating) + duplicate warning |
| `0738f78` | Sticky top nav + sticky weekday row on the calendar |
| `7de97e6` | Template delete + template categories (定例 / 不定期 / スペシャル) with calendar filter |
| `679ff6c` | Template edit + nav keeps each page's query string (committed together) |
| `905f299` | Template create |
| `9c7976b` | Business dropdown on the settings page (`GET /biz/`) |
| `86efa29` | `/settings/templates` page, routing, read-only template list |

## 2. Target architecture — "Operations Orchestration Design" (draft for team review)

Design doc (Claude Docs): https://claude.ai/artifact/W4XgfnPTH4iJPstx362xpV — the source of truth for this section.
An open comment on it asks whether SOP version + steps freeze at **release** (proposed) or at **start**.
Before building any phase, re-read the doc: the team may have edited or commented on it.

### Domains (decided: plan and ope stay separate, no merge)
| Domain | Owns | Status |
|---|---|---|
| **biz** | What the work is: businesses, SOP versions (`effective_at` / `expired_at`), step templates, execution settings, dependency definitions | Exists (definitions only, no execution) |
| **plan** | When it is due: templates, sheets, holidays, occurrences. Recomputable; regenerated freely, future only; knows nothing about ope | Exists (this branch) |
| **ope** | What happened: orders, steps, append-only status events, signals. Facts, never recomputed | Planned |
| **staff** | Who: users and roles (operator, checker, releaser, admin) | Planned |
| **Coordination layer** | Cross-domain use cases (reschedule, release) in one DB transaction + read models (daily dashboard) | Planned |

Dependency direction: ope → plan (reads slots), ope → biz (pins SOP version), ope → staff (actor), plan → biz
(template's business). plan never imports ope.

### Occurrence ≠ order
- Occurrence (plan) = "by the rule, this work is due in this slot". Order (ope) = "we do this work under this SOP version".
- **One order per slot `(template_id, occurrence_date)`**; plan voids and recreates instances, the order survives.
- **Eager creation:** every generated occurrence gets a `pending` order immediately; pending/ready orders follow plan changes.

### Visibility beyond 90 days (to build in Phase 2)
- Today: per-template high-water mark `materialized_until` (now + 90 days at creation); **no background job**; it only
  extends when a read reaches further (`_materialize_templates`, cap now + 2 years, `MAX_LOOKAHEAD`).
- Proposal: a daily job extends each active template to max(now + 90 days, its **next occurrence**), cap 2 years;
  orders are created for the same set (annual work visible all year). Templates with no future occurrence get a
  "no upcoming occurrence" warning on the settings page.

### Order lifecycle
- Statuses: `pending → ready → in_progress → finished`; `on_hold` (保留, no date; resume returns to the previous status);
  `canceled`. `finished` / `canceled` are final.
- **Release** (pending → ready), per business `release_mode`: `MANUAL_APPROVAL` (releaser role) or `AUTOMATIC`
  (at `release_lead`, proposed default 00:00 JST on the scheduled day). Release freezes SOP version + steps.
- **Start** (ready → in_progress): operator; hard dependencies must be met, or a force-start with reason.
- **Reschedule** (延期) is not a status: it is a plan reschedule; the order follows while pending / ready / on hold.
  A person cannot reschedule an order in progress.
- Cancel: a person with reason; the system only when a pending/ready order's slot disappears.
- Every transition = an order event (who or `system`, when, why).

### SOP version resolution
- `resolved = pinned ?? version effective on the scheduled date` (biz `effective_at` ≤ date < `expired_at`).
- The SOP approval screen shows the impact list ("upcoming orders that will run v4").
- An authorized user can pin a pending order to any approved version, and unpin; recorded as events.
- Frozen at release (open question in the doc: release vs start).

### Steps
- Built at release from the frozen version; step template `condition`: `always` | `month_in` | `first_in_week`
  (read from plan's occurrences); live preview while pending.
- States `locked → todo → submitted (operator) → approved (checker)`; reject (reason required) → `todo`.
- Next step unlocks on **submit**; if the step's `is_blocking` (on the step template, versioned) → on **approve**.
- Rejecting a non-blocking step does not reset later steps; the order finishes when all steps are approved.
- Four-eyes: checker ≠ operator (needs staff).

### Dependencies (recommended: hard by default)
- Hard blocking + audited human force-start (with reason); `informational` links never block. Reason: an automated
  executor needs a yes/no precondition, and links that start hard are already correct for automation.
- Checked at ready → in_progress, not at release.
- Matching: same `source_sheet_event_id` (both offset from the same baseline date, e.g. 受領 and 本処理 from one 振込日),
  same `occurrence_date`, or "latest upstream before due" (to confirm).
- Upstream canceled or on hold → downstream stays blocked; a person decides (force-start or cancel).
- Definitions in biz (`biz_dependency`, cycle-checked on save); concrete links in ope.

### Plan changes → orders (coordination layer)
| Plan change | Pending / ready | On hold | In progress | Finished / canceled |
|---|---|---|---|---|
| A person reschedules / cancels | Follows / canceled | Follows / canceled | **Refused** | Not affected |
| The system moves the slot (holiday, revision, sheet date) | Follows (flag if step conditions change) | Follows | Stays + conflict flag | Not affected |
| The slot disappears | Canceled by system | Conflict flag | Conflict flag | Not affected |
| New slot | New pending order | — | — | — |

Mechanism: reconcile returns its change set per slot; the coordination layer applies it in the same transaction.

### Client-side events
- A business with `execution_type = LIGHTWEIGHT` → an order with zero steps, same lifecycle.
- `completion_mode`: `MANUAL` (operator marks received) or `AUTOMATIC` via `POST /ope/signals {type, client_code, key,
  received_at}` (append-only; unmatched signals shown, never dropped); manual way out with reason. "Late" is computed.

### Data model sketch (from the doc)
- biz: `biz_master` + `execution_type`, `release_mode`, `release_lead`, `completion_mode`, `signal_type`;
  `sop_step_template` + `condition` (JSON), `is_blocking`; new `biz_dependency`.
- staff: `staff_user`, `role`, `staff_role`.
- ope: `ope_order` (unique slot, current instance, status cache, pinned / frozen SOP version, `released_at`, `conflict`),
  `ope_order_event`, `ope_step`, `ope_step_event` (all events append-only), `ope_dependency`, `ope_signal`.
- plan: tables unchanged; + the daily generation job; reconcile returns a per-slot change set.

### Delivery phases
1. **Cross-client daily dashboard** (read-only: occurrences + sheet milestones + holidays per calendar), e.g.
   `GET /ops/daily?date=&timezone=Asia/Tokyo` grouped by client → business; reads through on-demand generation (up
   to 2 years ahead); past dates show voided/canceled items with reasons. ← **next**
2. Plan generation job + next-occurrence guarantee + "no upcoming occurrence" warning.
3. staff (users, roles).
4. ope orders: eager creation, propagation, statuses, release modes, SOP resolution / pinning, conflict flags.
5. Steps: conditions, maker-checker, `is_blocking`.
6. Dependencies (hard + override, informational).
7. Client events with automatic completion (signals).

### Open questions (in the doc)
Client event scope (per business or per client) · automatic release lead time · may operators start before the
scheduled day · reset later steps on rejection (proposed no) · "latest before due" matching · cross-client
dependencies (proposed no) · signal transport and matching key · which roles release / pin / force-start / resolve.

## 3. Working agreement with the user

- **Baby steps.** The user defines one step at a time with strict limits. Do exactly that step; do not pre-build later
  steps. When a step turns up a real design question, stop and present options with a recommendation; they decide.
- **Commits.** Leave each step uncommitted for review unless asked. When they ask (or approve a finished step with
  "excellent" / "go"), I commit on the feature branch (conventional-commit style, `Co-Authored-By` line). Never push.
  Before starting a step, check `git status`; if the previous step is uncommitted, say so.
- **Don't touch the user's dev DB or their running uvicorn (port 8000).** Read-only GETs against 8000 are fine (useful for
  diagnosing their test data; `app.log` in the repo root logs SQL for both servers). For end-to-end checks use the
  throwaway DB `plan_check` (already exists and migrated; seeded with client `MOCK`, biz 1, calendar `JP_BANK` id 1,
  the mock sheet), uvicorn on 8001 with `DATABASE_URL` pointing at `plan_check`, and Vite on 5174:
  `VITE_PROXY_TARGET=http://127.0.0.1:8001 exec node node_modules/vite/bin/vite.js --port 5174 --strictPort` (from `frontend/`).
  Start Vite with `node` directly, not `npm run dev`: TaskStop only kills the npm wrapper and leaves Vite running.
  Wait until `http://localhost:5174/api/plans/calendars` returns 200 before running a harness.
  Vite binds to `localhost` (IPv6), so harnesses use `http://localhost:5174/api`. Never run bare `npx vite` from the repo root.
- **Verify before claiming.** Build + type check every change; backend changes need pytest + pyright (+ `alembic check`
  for migrations). For API-facing frontend work, bundle the real domain/feature modules with esbuild
  (`--define:import.meta.env='{"VITE_API_BASE_URL":"http://localhost:5174/api","VITE_TIMEZONE":"Asia/Tokyo"}'`) and run
  them in node against the throwaway backend. Say plainly what was *not* checked (in-browser clicking so far).
- **Design work** (cross-domain, for the team) goes into a Claude Docs document, not chat.
- Code comments in the repo are Japanese; UI text is Japanese; replies to the user in English.
- Windows line endings: frontend files are CRLF in the working tree; backend Python files, migrations and MEMORY.md are
  LF. Match the file you edit. Japanese in shell arguments gets mangled (cp932): pass JSON via files and run Python with
  `PYTHONUTF8=1`; long inline Python heredocs can break bash parsing → write the script to the scratchpad and run it.
  `.env`/`.env.*` are git-ignored.

## 4. Backend as built (FastAPI + SQLAlchemy 2 + PostgreSQL)

Layering per domain: `models.py` / `schemas.py` / `services.py` / `router.py`, repositories in
`src/infrastructure/repositories/` (`CRUDBase`, `SoftCRUDBase`). Errors: `BusinessException` subclasses
(`BadRequestError` 400, `NotFoundError` 404, `DuplicatedRecordError` 409) → `{status_code, error_code, message}`.
Global soft-delete filter (`do_orm_execute` + `with_loader_criteria(SoftDeleteMixin)`) hides deleted rows;
use `.execution_options(include_deleted=True)` to bypass. Domains in `src/`: `domain_biz`, `domain_client`, `domain_plan`.

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
  - Regeneration = **reconcile** (desired vs live generated instances) over `materialized_until` (see §2 visibility).
    Manually changed/cancelled slots are never regenerated.
  - Generation-affecting template edits bump `revision` and reconcile only the future; the past is never touched.
- **Manual operations on instances:** override, cancel, revert (back to rule), one-off, restore cancelled
  (`POST /plans/instances/{id}/restore`, used by the 取消済みの予定 drawer; not the same as template restore).
- **Occurrence cache** (`plan_occurrence_cache`): denormalized across all clients, GiST `tstzrange` index, effective
  `ui_metadata` — the natural source for the daily dashboard.
- **Business calendars:** `business_calendar` (weekend days) + `business_calendar_holiday`; changes cascade to templates
  and generated markers. **Past holidays are read-only (backend-enforced):** add / rename / delete before today → 400;
  a request containing any past date is rejected as a whole.
- **"Today"** for sheet dates and holidays is JST: `LOCAL_TIMEZONE`, `_local_today()`; `_check_not_past(dates, action)`
  raises the 400.

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
- **Past is read-only (backend-enforced):** add / move / delete / revert of dates before today → 400. Rule and holiday
  changes don't rewrite past dates; only a new marker's first generation fills the past. Import is still allowed to add
  past dates (initial loads need it), and `replace_events` can still delete past imported dates that nobody edited.
- **`revertable`** on each event (computed, not stored; `_annotate_revertable`): true only if the current rule produces
  that slot on a free date ≥ today. Same conditions as `POST /plans/sheets/events/{id}/revert`.

### Biz domain (`src/domain_biz/`) as built
`biz_master` (client_code, business_seq → business_code, name, …) → `sop_version` (version_number, version_tag,
status, `effective_at`, `expired_at`; clone / approve / cancel endpoints) → `sop_step_template` (step_num, title,
content). Definitions only — no execution, no conditions, no `is_blocking` yet (§2 adds them).

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
| Holidays | `GET /plans/calendars/{id}/holidays` (all years; the page filters by year), `POST …/holidays` (re-posting a date only renames it, no regeneration), `DELETE …/holidays/{date}`; past dates → 400 |
| Lookups | `GET /clients/`, `GET /biz/` (limit 1000 requested), `GET /plans/calendars` |

Unused Plan endpoints, kept on purpose: sheet import UI + `POST /plans/sheets` (sheet creation) — still open, waiting
on the Excel-vs-JSON question; template audit history `GET /plans/templates/{id}/instances`; calendar create / weekend
change; flat occurrence lists `GET /plans/occurrences`, `GET /plans/clients/{code}/occurrences`; cache rebuild
(backend-only); template restore (decided not needed).

Optional CORS via `CORS_ORIGINS` in `.env` (dev uses the Vite proxy instead).

## 5. Frontend as built (`frontend/`)

Vite 5.4 + React 18 + TypeScript + **Ant Design 5.22** (ja_JP locale, theme tokens match `styles.css`) + dayjs +
react-router-dom 6.28. Exact-pinned versions. Plain CSS with color variables (light/dark), no Tailwind.

- **OpenAPI-generated types:** `python scripts/export_openapi.py` → `frontend/openapi.json`;
  `npm run gen:api` → `src/shared/api/schema.gen.ts` (never edit by hand; regenerate after backend schema changes).
  Typed client: `openapi-fetch` (`src/shared/api/http.ts`, `unwrap()` → `ApiError{status, code, message}`).
- **Layering (one direction): `features → domain → shared`.** UI never calls HTTP directly.
  - `domain/{plan,biz,client}/{types,api,hooks,lib}` — types are aliases of generated schemas only.
    `domain/plan/lib/category.ts`: template category stored in `ui_metadata.category` (missing → 定例; display-only —
    "who acts" for client events must be a real biz field, not this).
    `domain/plan/lib/jpHolidays.ts`: Japanese national holidays calculated from the current law (equinox formula,
    happy Mondays, 振替休日, 国民の休日), 2022–2099 only; matches the official lists for 2024–2027.
  - `shared/` — http client, `useApiQuery`, `datetime.ts`, `useNow`, `ui/color.ts`, `config.ts` (`TIMEZONE`).
  - `features/calendar`, `features/schedule-actions`, `features/plan-settings`.
    Pure logic lives in `templateForm.ts` / `markerForm.ts` / `holidayForm.ts` (testable in node); modals are UI only.
- **Routes / nav:** `/` calendar (`?client=&month=&holidays=`) · `/settings/templates?biz=` · `/settings/sheets?client=` ·
  `/settings/holidays?calendar=<id>&year=`. The layout remembers each page's last query string, so switching pages keeps
  selections. The calendar's `useUrlState` goes through react-router (`useSearchParams`), not `history.replaceState`.
- **Calendar:** category chips (定例 hidden by default, stored in localStorage) + per-template legend toggles; sticky nav
  (`--nav-h: 44px`) and sticky weekday row (`.month` uses `overflow: clip` so sticky works).
- Dev: `npm run dev` (proxy `/api` → `http://127.0.0.1:8000`; IPv4 on purpose). Production needs SPA fallback to `index.html`.
- Gotchas already fixed — don't regress: ColorPicker `onChange` 2nd arg is `rgb(...)`, use `hexFromColorPicker`;
  dayjs custom formats need `customParseFormat`; antd Modal + `forceRender` without `destroyOnClose` → reset the form in
  a `useEffect` on `open` and keep the target in state while closing; `ui_metadata` PATCH replaces the whole object, so
  forms carry over unknown keys (`text_color` …). openapi-typescript marks every field with a default as required, so
  request bodies send those fields explicitly (e.g. `cancel_future_dates: false`, `offset_days`).

## 6. Known data quirks (user's dev DB)
- Client `MOCK`, 15 businesses; only some have templates. Template names vs business names look shifted by one.
- Sheets: `Annual_Schedule` (id 2, imported) and `MOCK_Schedule` (id 1). Marker `引落日` (id 10) was a rule marker; its
  history was wiped by the old 生成をやめる before the audit-trail fix (unrecoverable, accepted by the user).
- Calendar `JP_BANK` id 1 (holidays 2026-11-03, 2026-11-23 …).
- Instance 910 (10/20) was moved into the past and cancelled; it shows in the cancelled list as not restorable.
- Dates moved before migration `5c1f7d2a9e04` have no `original_date`; dates added via the UI on imported markers before
  it look like 取り込み.
