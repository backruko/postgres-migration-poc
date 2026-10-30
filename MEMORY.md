# MEMORY.md — Session handoff

Handoff for the next working session. Read this first, then the design doc (§2), `frontend/README.md` (frontend
details), `src/domain_plan/`, `src/coordination/` and `src/domain_staff/` (backend). Last updated: 2026-10-28 (evening).

## 1. Current status

- **Branches:** `main` = Plan MVP + Phases 1–3 (merged 2026-10-28, merge `23767b8`, pushed by the user).
  **Current work: `feature/ope-orders`** (from `main`), Phase 4. The user pushes; I never do.
- **Where we are:** Plan domain MVP done. Operations design (§2): **Phases 1 (daily dashboard), 2 (generation job +
  next-occurrence guarantee) and 3 (staff: backend + screen) are done. Phase 4 (ope orders): step 4.1 (release
  settings on businesses), 4.2 (ope tables, `0552e9f`, pushed), 4.3 (order sync, `7d94be7`) and 4.4 (status API,
  `0389de2`) committed (4.3 / 4.4 not pushed); step 4.5 (automatic release job) built 2026-10-30, awaiting review —
  **its Task Scheduler entry was not created** (the permission check refused it; the user was given the command).
  Next: 4.6, SOP version resolution / pinning / freeze at release**
  (plan in §2 "Phase 4 step plan"). The user's dev DB is migrated to `5eede5808433` and backfilled (890 orders,
  2026-10-30); orders have no screen until 4.7 (the user knows and chose to keep the original step order).
- **DB:** latest Alembic head is `5eede5808433` (slot uniqueness ignores canceled). Migrations: `ceee1634d6a7`
  (squashed init) → `34bdbd214739` → `9ee3f3e0624e` → `9b2ec314368a` → `3493c39bdf43` → `5c1f7d2a9e04` →
  `568c3287c359` (staff) → `f9bbe9648f15` (biz release settings) → `9e1bcef6cbc7` (ope tables) → `5eede5808433`.
  The user's dev DB was at `f9bbe9648f15` on 2026-10-30 (staff id 1, login `system`, system_admin); `9e1bcef6cbc7` and
  `5eede5808433` handed over, then `python scripts/create_orders.py` once (go-live backfill).
  `plan_check` is at `5eede5808433`, backfilled (190 orders + HTTP-check changes), staff 1 `check-admin`
  (system_admin), 2 `check-op` (suspended) + harness users.
  The user runs `alembic upgrade head` on their dev DB themselves. Their uvicorn on 8000 auto-reloads, so a model change
  without the migration makes it return 500 until they upgrade. Warn them whenever a step adds a migration.
- **Tests:** `pytest tests/test_plan.py tests/test_ops.py tests/test_staff.py tests/test_biz.py tests/test_ope.py tests/test_order_sync.py tests/test_order_actions.py tests/test_release_job.py` → 89 passed, ~80 s (the order-sync listener is registered in `conftest.py`, so every plan test also syncs orders) (runs against `test_db` via `tests/conftest.py`,
  `create_all`, no migrations). Known flake: `test_plan.py` client codes are `"T"` + 3 hex digits and collide now and
  then (~1 run in 5) → `DuplicatedRecordError` in a fixture; rerun. `test_ops.py` uses `"D"`, `test_biz.py` `"B"`.
  The user hasn't asked for the fix yet (retry or counter in the fixture).
  Type check: `npx pyright@1.1.400 --pythonpath .venv/Scripts/python.exe src main.py tests scripts` → 0 errors.
  Migration check (on `plan_check`): `alembic upgrade head` / `downgrade -1` / `alembic check` → no drift.
  Frontend: `cd frontend && npm run build` (= `tsc --noEmit` + Vite build) passes.
- **Scheduled job (user's machine):** Task Scheduler `\postgres-migration-poc\PlanExtendJob`, daily 00:05, runs
  `scripts/extend_plans.py` in the repo root (so `.env` → dev DB) as the user (interactive logon, runs on next logon if
  missed), output appended to `extend_plans.log` (git-ignored). First run 2026-10-28: exit 0, 7 templates, 1 extended.
  Planned second task `\postgres-migration-poc\OrderReleaseJob` (every 15 min, `scripts/release_orders.py` →
  `release_orders.log`): **not registered yet** — creating it was refused by the permission check on 2026-10-30; the
  user got the PowerShell command to run themselves. Check `Get-ScheduledTask -TaskPath '\postgres-migration-poc\'`.
- **Browser check:** the user clicked through the 年間計画シート and 休日カレンダー screens (2026-10-28): no issues found.
  Not yet clicked through: 日次ダッシュボード (`/daily`), the 「次の予定」 column on the template settings page, and the
  職員 screen (`/settings/staff`) + nav picker 「操作する職員」.

### Recent commits (newest first)
| Commit | Content |
|---|---|
| `91cde48` | `.gitignore`: `frontend/.vite/` (Vite dep cache; two committed cache files untracked) |
| `d65af44` | Phase 4.1: biz release settings, migration `f9bbe9648f15`, `tests/test_biz.py` |
| `0d4ff4c` | MEMORY.md: Phase 4 decisions (first commit on `feature/ope-orders`) |
| `23767b8` | `main`: merge of `feature/plan-holiday-calendar` (Phases 1–3) |
| `b13fe73` / `7d01bff` / `8ec4b8d` | MEMORY.md: release 03:00, client-limited role, roles decisions, Phase 3 done |
| `702ebbb` | Phase 3 frontend: 職員 screen `/settings/staff`, nav picker 「操作する職員」 (X-Staff-Id middleware) |
| `bfe3deb` | Phase 3 backend: staff domain, migration `568c3287c359`, `require_roles`, `/staff` API, `scripts/create_staff.py` |
| `fcff659` | Phase 2: `extend_all_templates` + `scripts/extend_plans.py`, next-occurrence guarantee, `next_occurrence_at`, 「次の予定」 column |
| `eb4e6b5` | Phase 1 frontend: 日次ダッシュボード page `/daily` (committed by the user as "dashboard") |
| `17f9bcc` | Phase 1 service: `GET /ops/daily` implementation + `tests/test_ops.py` (committed by the user as "fix") |
| `9306e23` | Phase 1: coordination package, dashboard schemas + router skeleton |
| `165866d` / `b410e5c` | MEMORY.md rewrite around the operations design |
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
Decided in the doc's comments (2026-10-28): SOP version + steps **freeze at release**.
Before building any phase, re-read the doc: the team may have edited or commented on it.

### Domains (decided: plan and ope stay separate, no merge)
| Domain | Owns | Status |
|---|---|---|
| **biz** | What the work is: businesses, SOP versions (`effective_at` / `expired_at`), step templates, execution settings, dependency definitions | Exists (definitions only, no execution) |
| **plan** | When it is due: templates, sheets, holidays, occurrences. Recomputable; regenerated freely, future only; knows nothing about ope | Exists (this branch) |
| **ope** | What happened: orders, steps, append-only status events, signals. Facts, never recomputed | Planned |
| **staff** | Who: users and roles (operator, checker, releaser, admin) | Planned |
| **Coordination layer** | Cross-domain use cases (reschedule, release) in one DB transaction + read models (daily dashboard) | Daily dashboard built (`src/coordination/`); use cases planned |

Dependency direction: ope → plan (reads slots), ope → biz (pins SOP version), ope → staff (actor), plan → biz
(template's business). plan never imports ope.

### Occurrence ≠ order
- Occurrence (plan) = "by the rule, this work is due in this slot". Order (ope) = "we do this work under this SOP version".
- **One order per slot `(template_id, occurrence_date)`**; plan voids and recreates instances, the order survives.
- **Eager creation:** every generated occurrence gets a `pending` order immediately; pending/ready orders follow plan changes.

### Visibility beyond 90 days (built in Phase 2)
- Per-template high-water mark `materialized_until`; reads still extend it on demand (`_materialize_templates`, cap
  now + 2 years, `MAX_LOOKAHEAD`).
- `_ensure_next_occurrence` extends to max(now + 90 days rounded up to the next UTC midnight, the **next occurrence**),
  cap 2 years; skips manually cancelled slots and keeps looking. Runs on template create / restore, at the end of every
  `_regenerate` (edits, explicit dates, holiday / sheet cascades) and in the daily job `extend_all_templates`
  (`scripts/extend_plans.py`, commits per template, idempotent within a day).
- Templates with nothing upcoming within 2 years: `next_occurrence_at = null` → warning on the settings page.
- Phase 4 still to do: orders created for the same set.
- Known edge (pre-existing): a template whose mark is in the past gets the gap filled from the old mark (past dates
  included) on its next extension. The daily job prevents new gaps.

### Order lifecycle
- Statuses: `pending → ready → in_progress → finished`; `on_hold` (保留, no date; resume returns to the previous status);
  `canceled`. `finished` / `canceled` are final.
- **Who may do what (decided 2026-10-28):** **releaser and admin** release, pin a version, force-start and resolve
  conflicts → `@require_roles(["releaser", "admin"])`.
- **Release** (pending → ready), per business `release_mode`: `MANUAL_APPROVAL` (releaser or admin) or `AUTOMATIC`
  (at `release_lead`; **default 03:00 JST on the scheduled day**, decided 2026-10-28: after the nightly batch
  (夜間バッチ), which often runs until about 02:30). Release freezes SOP version + steps.
- **Start** (ready → in_progress): operator, **on or after the scheduled day (JST) only** (decided 2026-10-28);
  earlier needs a **releaser's approval** (an order event). Hard dependencies must be met, or a releaser / admin
  force-starts with reason.
- **Reschedule** (延期) is not a status: it is a plan reschedule; the order follows while pending / ready / on hold.
  A person cannot reschedule an order in progress.
- Cancel: a person with reason; the system only when a pending/ready order's slot disappears.
- Every transition = an order event (who or `system`, when, why).

### SOP version resolution
- `resolved = pinned ?? version effective on the scheduled date` (biz `effective_at` ≤ date < `expired_at`).
- The SOP approval screen shows the impact list ("upcoming orders that will run v4").
- A releaser or admin can pin a pending order to any approved version, and unpin; recorded as events.
- Frozen at release (decided).

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

Mechanism (decided 2026-10-28): **slot-change listener** — plan's reconcile / manual changes hand a per-slot change set
to listeners registered at startup; the coordination layer's listener applies it to orders in the same transaction.
Plan never imports ope.

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
1. ~~Cross-client daily dashboard~~ **done** (§4 Coordination, §5 `/daily`).
2. ~~Plan generation job + next-occurrence guarantee + "no upcoming occurrence" warning~~ **done** (user chose: CLI
   script + OS scheduler; warning from a template field).
3. ~~staff (users, roles)~~ **done** (§4 Staff, §5 職員). User decisions: no login yet (trusted `X-Staff-Id` header);
   fixed global roles; backend first, screen second.
4. ope orders: eager creation, propagation, statuses, release modes, SOP resolution / pinning, conflict flags. ← **next**
   (branch `feature/ope-orders`). Decided 2026-10-28: slot-change listener; orders only for live occurrences from
   go-live on (no past backfill); interim finish = operator marks in_progress → finished (Phase 5 replaces it for
   orders with steps); release config on biz_master = `release_mode`, `release_time` (default 03:00 JST),
   `release_days_before` (default 0).

#### Phase 4 step plan (approved by the user 2026-10-28; one step at a time, each reviewed + committed)
| # | Step | Migration | Status |
|---|---|---|---|
| 4.1 | Release settings on `biz_master` + biz API | `f9bbe9648f15` | **done** (`d65af44`) |
| 4.2 | ope tables: `ope_order`, append-only `ope_order_event` (immutability trigger like plan's `plan_reject_mutation`). Models + migration + tests, **no behavior** | `9e1bcef6cbc7` | **done** (`0552e9f`) |
| 4.3 | **done** (`7d94be7`) — Slot-change listener in plan (reconcile + manual override / cancel / revert / one-off / restore emit per-slot changes) + coordination listener (create / move / cancel / conflict-flag orders per the propagation table) + script creating orders for existing future live occurrences | — | |
| 4.4 | **done** (`0389de2`) — Status API: release (releaser/admin), start (operator, on/after the scheduled day JST, earlier only with a releaser's approval event), hold / resume, cancel (reason), finish (operator, interim), resolve conflict (releaser/admin); every transition an order event; `@require_roles` | — | |
| 4.5 | **built 2026-10-30, awaiting review** — Automatic release job (03:00 JST; `release_time` / `release_days_before`) + its own Task Scheduler entry (entry not registered yet, see §1) | — | |
| 4.6 | SOP version resolution, pin / unpin (releaser/admin), freeze at release | maybe | |
| 4.7 | Frontend: order status on the dashboard + actions | — | |

**4.2 as built (`src/domain_ope/models.py`, migration `9e1bcef6cbc7`) — order slot identity = plan's slot.**
Plan has three slot kinds: rule-generated `("date", occurrence_date)`, sheet-based `("sheet_event",
source_sheet_event_id)` (survives a sheet date move), and **one-offs, which have no slot in plan** (`add_one_off` sets
only its own date; overrides inherit their original's slot). User's decision 2026-10-30: **typed slot columns** —
`ope_order` keeps `template_id` + `occurrence_date` always, plus `source_sheet_event_id` (snapshot, no FK, like plan)
**or** `one_off_instance_id` (FK plan_instance: the one-off's *first* instance; later moves keep it).
Partial unique indexes `uq_ope_order_sheet_slot`, `uq_ope_order_one_off_slot`, `uq_ope_order_date_slot` (neither set) +
CHECK `ck_ope_order_one_slot_kind`. So 4.3's listener must find a one-off's root instance (walk
`overrides_instance_id`) to locate its order.
Other `ope_order` columns: `current_instance_id` (FK, last one after the slot disappears), `biz_master_id`,
`client_code` (FK client_master; for the future client-limited role), `scheduled_start` / `scheduled_end`, `status`
(`OrderStatus`, member names stored, default PENDING; cache of the latest event), `status_before_hold` (only while
ON_HOLD; PENDING / READY / IN_PROGRESS), `pinned_sop_version_id` / `frozen_sop_version_id` (FK sop_version),
`released_at` (required for READY / IN_PROGRESS / FINISHED), `conflict` + `conflict_reason` (required when conflict).
`ope_order_event` (append-only; trigger `trg_ope_order_event_immutable` → `ope_reject_mutation()`, also installed by
`create_all` for tests): `event_type` (`OrderEventType`: CREATED, STATUS_CHANGED, RESCHEDULED, PINNED, UNPINNED,
EARLY_START_APPROVED, CONFLICT_RAISED, CONFLICT_RESOLVED), `from_status` / `to_status`, `source` (HUMAN | SYSTEM;
CHECK: HUMAN ⇔ `actor_staff_id` set), `reason`, `detail` (JSONB), `occurred_at`. FK order_id RESTRICT (orders with
events can't be deleted). Adding an event type later = `ALTER TYPE … ADD VALUE` migration.
Tests: `tests/test_ope.py` (6, constraints only — they switch the 4.3 listener off with
`monkeypatch.setattr(slot_events, "_listeners", [])`; client prefix `"O"`). The doc's data-model rows were updated (rev 33).

**4.3 as built — plan → order sync** (decisions 2026-10-30: a returning slot gets a **new** order, the canceled one
stays as history → migration `5eede5808433` adds `status <> 'CANCELED'` to the three slot unique indexes; plan's
manual endpoints **require X-Staff-Id**).
- `src/domain_plan/slot_events.py` (plan side, knows nothing of ope): `SlotKey(template_id, kind date|sheet_event|
  one_off, ref, occurrence_date[compare=False])`, `slot_key_of(db, instance)` (one-offs: walk to the root),
  `live_instance_for_slot`, `record_created` / `record_voided` (called from the repo chokepoints `add_many`,
  `add_override`, `add_one_off`, `void_many`), `mark_person_change(db, actor_staff_id, note)` (called by the 5 manual
  services), `register_slot_listener`. A global `Session` `before_commit` hook groups the recorded slots and calls the
  listeners **in the same transaction**; `after_rollback` discards them. A listener exception aborts the commit.
- Plan manual services (`override_instance`, `cancel_instance`, `revert_instance`, `add_one_off_instance`,
  `restore_cancelled_instance`) take `actor_staff_id: int | None = None`; their endpoints have `@require_active()` +
  `current_staff` (→ the calendar's 変更 / 取消 / 臨時 / 元に戻す buttons get 401 until an acting staff is picked in
  the nav). Service calls without an actor (scripts, old tests) are recorded as SYSTEM events with
  `detail.cause = "manual plan change"`.
- `src/coordination/order_sync.py`: `register()` (called in `main.py`, `tests/conftest.py`, `scripts/extend_plans.py`,
  `scripts/create_orders.py` — **any new script that changes plan must call it too**), `apply_slot_changes` = the
  doc's propagation table: no order + live future instance → CREATED pending; moved → pending / ready / on_hold
  follow (RESCHEDULED, detail before / after), in_progress: person → `DuplicatedRecordError` code
  `ORDER_IN_PROGRESS` (409, rolls back the plan change), system → CONFLICT_RAISED + `conflict_reason`; removed →
  person: cancel (in_progress refused), system: pending / ready canceled with the plan void reason as `reason`,
  on_hold / in_progress conflict; finished untouched. No orders for past instances. `create_missing_orders(db)` =
  go-live backfill (`scripts/create_orders.py`, idempotent).
- Repo `src/infrastructure/repositories/Ope.py` (`ope_order_repo.get_active_for_slot` with `FOR UPDATE`,
  `add_event`, `get_events`).
- "Ready order whose step conditions change gets a flag" (doc table) waits for Phase 5 (steps).
- Tests: `tests/test_order_sync.py` (9; client prefix `"S"`). Doc rev 34 records both decisions.

**4.4 as built — order status API** (`src/domain_ope/{schemas,services,router}.py`, prefix `/ope`; no migration).
User decisions 2026-10-30: **cancel = releaser / admin** (reason required); **hold / resume = operator, releaser,
admin** (reason required to hold); start + finish = operator; release + resolve conflict = releaser / admin;
approve early start = **releaser only**; a releaser / admin **may release an AUTOMATIC business's order by hand**;
**a conflict flag blocks release and start** until resolved.
- `GET /ope/orders/{id}` (active staff) → `OrderDetailResponse` (order + `early_start_approved` + events oldest first).
- `POST /ope/orders/{id}/release | approve-early-start | start | hold | resume | finish | cancel | resolve-conflict`;
  every action locks the order (`ope_order_repo.get_locked`, FOR UPDATE), checks the status, appends one event
  (HUMAN, actor) and returns the detail. Wrong status → 400 (message lists allowed statuses).
- Start: scheduled day = `scheduled_start` in JST vs `_local_today()` (JST); before it only if an
  EARLY_START_APPROVED event exists (start event then has `detail.early_start = true`). Approving twice → 409;
  approving on/after the day → 400.
- Hold stores `status_before_hold`; resume returns to it. Finish = interim (Phase 5 replaces it for orders with steps).
- **Cancel also cancels the plan occurrence**: the order is marked canceled first, then
  `plan_services.cancel_instance(..., actor_staff_id)` voids the live instance (MANUAL, reason as note), so the
  listener finds no active order and creates nothing; the calendar drops it. Cancel works from in_progress too
  (unlike a plan-side cancel, which the listener refuses for in-progress orders).
- Resolve conflict: `accept_plan` (adopt the slot's live instance times; if the slot is gone → cancel), `keep` (clear
  the flag only), `cancel` (reason required; also cancels the plan occurrence if still live). One CONFLICT_RESOLVED
  event (with from / to status when it cancels).
- Force-start is **not** built (it belongs to dependencies, Phase 6). SOP freeze at release is 4.6.
- Tests: `tests/test_order_actions.py` (7; client prefix `"A"`; `monkeypatch.setattr(ope, "_now", …)` to pick
  "today"). HTTP-checked on `plan_check` with staff 4 (operator) and 5 (releaser). Doc rev 38 records the decisions.

**4.5 as built — automatic release** (no migration). `ope.release_due_orders(db) -> AutoReleaseResult{checked,
released}`: pending, non-conflicted orders of `AUTOMATIC` businesses (`ope_order_repo.get_pending_automatic`, FOR UPDATE
SKIP LOCKED, scheduled within 31 days) whose `release_moment` = (scheduled day JST − `release_days_before`) at
`release_time` JST has passed → ready, SYSTEM event `detail {"cause": "automatic release", "release_at": …}`.
Overdue ones (job was down) are released on the next run; idempotent. Manual and automatic release share
`_release()` (4.6 adds the SOP freeze there). Script `scripts/release_orders.py` (no listener needed: it doesn't touch
plan). **Runs every 15 minutes** (my call: release times differ per business, e.g. 21:30 the day before, so a single
03:00 run isn't enough; releases happen up to 15 min after the set time). Tests `tests/test_release_job.py` (3;
client prefix `"R"`). On `plan_check`: run with biz 1 manual → 0 checked; automatic → 71 checked, 0 released
(earliest pending is 11/1, due 11/1 03:00); set back to manual.
5. Steps: conditions, maker-checker, `is_blocking`.
6. Dependencies (hard + override, informational).
7. Client events with automatic completion (signals).

### Open questions (in the doc)
Client event scope (per business or per client) · reset later steps on rejection (proposed no) · "latest before due"
matching · cross-client dependencies (proposed no) · signal transport and matching key · client-limited role details
(name, one or several client codes per user, read-only or also its own client events).
Decided 2026-10-28 (ticked in the doc, rev 26): early start only with a releaser's approval; releaser + admin
release / pin / force-start / resolve conflicts; automatic release default 03:00 JST on the scheduled day.

### Planned: client-limited role (decided direction 2026-10-28, not built)
A future role that sees only its own client code(s). Needs a per-user client scope (e.g. `staff_client(staff_user_id,
client_code)`) and every read model / API (calendar, dashboard, orders …) filtering by it — so new endpoints should
take the acting staff into account and keep client filtering in one place. Details are an open question in the doc.

## 2b. Working away from the office (web session or home PC)

The user sometimes continues away from the office PC: in a Claude Code web session (cloud sandbox, Linux) or on a
home PC. Their office `.env` (dev DB) is not available there.
- It clones from GitHub: only **pushed** commits exist there. Push nothing yourself; when done, commit on the branch
  and tell the user to push / pull.
- Not available there: the user's dev DB, their uvicorn on 8000, `plan_check`, Task Scheduler, the Windows paths and
  PowerShell. `.env` / `.env.test` are git-ignored and absent.
- **PostgreSQL runs in Docker (the user's decision, 2026-10-28)** — away from the office (web session or home PC),
  never a native install:
  `docker run -d --name pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16`, then create `test_db` and a
  throwaway DB `plan_check` (`docker exec pg createdb -U postgres test_db`, same for `plan_check`), and write
  `.env.test` (`ENVIRONMENT=test`, `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/test_db`) and `.env`
  (`DATABASE_URL=postgresql://postgres:postgres@localhost:5432/plan_check`), then `alembic upgrade head` on
  `plan_check`. Never commit them. If Docker isn't available, say so before writing code and list what can't be
  verified — don't silently switch to another way of running PostgreSQL.
- Python: create a venv and `pip install -r requirements.txt` — commands are `.venv/bin/python` there
  instead of `.venv/Scripts/python.exe` (pyright: `--pythonpath .venv/bin/python`). Frontend: `cd frontend && npm ci`.
- Line endings: the repository stores LF; the Linux checkout is LF everywhere — keep LF (Windows converts on checkout).
- Migrations for the user's dev DB: still only hand them over; the user runs `alembic upgrade head` at home/office.

## 3. Working agreement with the user

- **Baby steps.** The user defines one step at a time with strict limits. Do exactly that step; do not pre-build later
  steps. When a step turns up a real design question, stop and present options with a recommendation; they decide.
- **Commits.** Leave each step uncommitted for review unless asked. When they ask (or approve a finished step with
  "excellent" / "go" / "go ahead"), I commit on the feature branch (conventional-commit style, `Co-Authored-By` line).
  Never push. The user sometimes commits and pushes a step themselves (short messages like "fix") — check `git log`
  before committing, and never amend or rewrite their pushed commits.
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
  them in node against the throwaway backend. Say plainly what was *not* checked (I can't click through the browser; the
  user does that).
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

### Coordination layer (`src/coordination/`) as built
Not a domain: a flat package (`schemas.py` / `services.py` / `router.py`, prefix `/ops`) that reads plan / biz / client.
It calls only public plan functions (`plan_services.materialize_all_templates`), never plan's private helpers.
- **`GET /ops/daily?date=&timezone=Asia/Tokyo`** (`date` optional → today in `timezone`; alias of `target_date`):
  live occurrences **starting** that day (same day split as the calendar), grouped client → business (both by code);
  sheet dates (`DailyMilestone`, with provenance) under the client; `calendars[]` = every business calendar with
  `is_business_day` / `holiday_name`. **Past dates only:** `voided[]` per business (reason, note, voided_at; includes
  deleted templates via `include_deleted`, excludes deleted businesses) and cancelled sheet dates.
  Clients with only sheet dates appear with `businesses: []`. Reads through generation up to 2 years.
- Repos added for it: `plan_cache_repo.query_starting_in`, `plan_instance_repo.get_voided_starting_in`,
  `reference_sheet_repo.get_events_on`, `plan_template_repo.get_all`, `client_repo.get_by_codes`.
- Its own `_now()` (tests monkeypatch `ops_services._now` to view a day as past).

### Plan generation job (Phase 2)
`plan_services.extend_all_templates(db) -> TemplateExtensionResult {templates_checked, templates_extended,
without_upcoming}`; script `scripts/extend_plans.py` (sets `engine.echo = False` for itself, one log line, exit 1 on
failure). `PlanTemplateResponse.next_occurrence_at` (computed per response by `_with_next_occurrence` from the cache via
`plan_cache_repo.next_starts`; null on delete responses).

### Staff domain (`src/domain_staff/`) as built (Phase 3)
- Tables `staff_user` (login_id unique, name, email, `status`), `role` (id, code unique, name, description; seeded by
  the migration and, for `create_all` in tests, by an `after_create` DDL in models.py), `staff_role` (composite PK,
  `granted_at`, `granted_by_id`). Staff are never deleted (future order / step events reference them); suspend instead.
- **Roles (user's list):** operator, checker, releaser, admin (business), viewer, auditor, system_admin (account admin,
  not business work). Global, a user can hold several. `RoleCode` enum in models.py.
- **Status, not roles (my call, user didn't object):** `pending → active ⇄ suspended`; no way back to pending. Only
  `active` staff can act; suspended keep their roles.
- **Guards (user asked for decorators):** `@require_roles(["checker", "releaser"])` / `@require_active()` in
  `domain_staff/auth.py`, placed **under** `@router.<method>`. They add a hidden keyword-only param
  (`_require_roles_current_staff = Depends(get_current_staff)`) to the signature and pop it in the wrapper; sync and
  async; role typos fail at import. Errors: 401 `UNAUTHORIZED` (no / unknown header), 403 `STAFF_NOT_ACTIVE`,
  403 `FORBIDDEN` (missing role). `ForbiddenError` in core/exceptions; `AUTH_ERRORS` in core/schemas/error.py.
- `get_current_staff` reads the trusted `X-Staff-Id` header — the one place to replace when real login comes.
  Endpoints that need the actor also declare `current_staff: StaffUser = Depends(get_current_staff)` (cached per request).
- `/staff`: `GET /me` (any status) · `GET /roles` (active) · `GET /`, `GET /{id}` (system_admin, admin, auditor) ·
  `POST /`, `PATCH /{id}`, `PUT /{id}/roles`, `PUT /{id}/status` (system_admin). Lockout guards: can't change own
  status, can't remove own system_admin. Plan / ops endpoints are **not** guarded yet.
- `scripts/create_staff.py <login_id> <name> --roles system_admin [--email]` creates an **active** user (bootstrap).

### Biz domain (`src/domain_biz/`) as built
`biz_master` (client_code, business_seq → business_code, name, …) → `sop_version` (version_number, version_tag,
status, `effective_at`, `expired_at`; clone / approve / cancel endpoints) → `sop_step_template` (step_num, title,
content). Definitions only — no execution, no conditions, no `is_blocking` yet (§2 adds them).
**Release settings (4.1):** `release_mode` (`ReleaseMode`: MANUAL_APPROVAL default | AUTOMATIC; stored as member
names), `release_time` (Time, default 03:00 JST), `release_days_before` (0–30, default 0, CHECK
`chk_biz_release_days_before`). New businesses get the defaults (the shared `CRUDBase.create` runs
`jsonable_encoder`, which turns enums into value strings the Enum column rejects — so create doesn't take them);
change them with `PATCH /biz/{id}` (`model_dump`, explicit nulls refused for these three only).
Migration gotchas seen: `op.add_column` with an Enum does not create the PG type (create it first, drop it on
downgrade); autogenerate never emits CHECK constraints.

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
  - `domain/{plan,biz,client,ops,staff}/{types,api,hooks,lib}` — types are aliases of generated schemas only.
    `domain/plan/lib/category.ts`: template category stored in `ui_metadata.category` (missing → 定例; display-only —
    "who acts" for client events must be a real biz field, not this).
    `domain/plan/lib/jpHolidays.ts`: Japanese national holidays calculated from the current law (equinox formula,
    happy Mondays, 振替休日, 国民の休日), 2022–2099 only; matches the official lists for 2024–2027.
  - `shared/` — http client, `useApiQuery`, `datetime.ts`, `useNow`, `ui/color.ts`, `config.ts` (`TIMEZONE`).
  - `features/calendar`, `features/schedule-actions`, `features/plan-settings`.
    Pure logic lives in `templateForm.ts` / `markerForm.ts` / `holidayForm.ts` (testable in node); modals are UI only.
- **Routes / nav:** `/` calendar (`?client=&month=&holidays=`) · `/daily?date=` 日次ダッシュボード · `/settings/staff` 職員 ·
  `/settings/templates?biz=` · `/settings/sheets?client=` · `/settings/holidays?calendar=<id>&year=`.
- **日次ダッシュボード** (`features/daily-dashboard`, data via `domain/ops/{types,api,hooks}`): prev / next day, date
  picker, 今日; calendar tags (営業日 / 休日（name） / 休業日（週末）); one card per client (code links to its calendar
  month), sheet-date tags with provenance labels, occurrences per business (time, chipStyle swatch + Icon, 人が変更 /
  臨時 tags, 元の日付 when the slot date differs); past days add struck-through voided rows with the reason
  (`VOID_REASON_LABEL`) and note. Pure logic in `dailyDashboard.ts`.
- **Acting staff (until login):** `shared/api/actingStaff.ts` keeps the id (localStorage, else `VITE_DEFAULT_STAFF_ID`);
  an openapi-fetch middleware in `http.ts` sends it as `X-Staff-Id` on every request; `useActingStaffId`
  (useSyncExternalStore). Nav picker `app/ActingStaffPicker.tsx` (enter id, shows name / status / roles).
- **職員** (`/settings/staff`, `features/staff-settings`): list, 職員を登録, 編集 (name, email, role checkboxes; own
  system_admin locked), 有効化 / 利用停止 / 再開 (confirm; own row disabled), 「この職員として操作」. UI permissions in
  `domain/staff/lib/permissions.ts` mirror the backend (display only).
- **Template settings page:** 「次の予定」 column (template timezone) and a warning tag + banner for templates with no
  upcoming occurrence. The layout remembers each page's last query string, so switching pages keeps
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
