# MEMORY.md — Session handoff

Handoff for the next working session. Read this first, then the design doc (§2), `frontend/README.md` (frontend
details), `src/domain_plan/`, `src/coordination/`, `src/domain_ope/`, `src/domain_biz/` and `src/domain_staff/`
(backend). Last updated: 2026-11-04 (evening), after Phase 7.

## 1. Current status

- **All seven phases of the design doc's delivery plan are built and merged into `main` (locally).** Plan MVP +
  Phase 1 daily dashboard · 2 generation job · 3 staff · 4 ope orders · 5 steps / maker-checker · 6 dependencies ·
  7 client events / signals. Per-phase detail ("as built" notes, decisions, step plans) is in §2.
- **Branches / commits (2026-11-04, nothing pushed by me):** I'm on **`main`** (`bf22125`, merge of Phase 7), **36
  commits ahead of `origin/main`**. Merges, all `--no-ff`, made locally at the user's request: Phases 1–3 `23767b8`
  (pushed long ago), Phases 4–5 `71de514`, Phase 6 `b3538aa`, Phase 7 `bf22125`. Feature branches:
  `feature/ope-orders` (Phases 4–5; 1 commit ahead of origin: `6a37fee`), `feature/ope-dependencies` (Phase 6, never
  pushed), `feature/ope-signals` (Phase 7, never pushed). **The user pushes; I never do.** The user sometimes commits
  a step themselves (e.g. 5.5 as `75cc109 phase5`) — check `git log` before committing.
- **Current work (2026-11-05): `feature/ux-shortcuts`** (from `main` `bf22125`), frontend shortcuts + an admin guard
  on the template page — see §2 "UX shortcuts". U.1 `9b7cb38`, U.2 `f002f9f`, U.3 `34914a8`; U.4 = candidate. Test fix (2026-11-05):
  `test_order_links.py::test_sheet_rule_…` failed when a mock 本締 upstream (+0, 09:00) was already past but its +1
  downstream was future (correct waiting row, KeyError in the test) → now accepts a waiting row for those.
- **Earlier "next" list (still valid):** Ask the user what to do next. Candidates: (a) the user's browser check of Phases 5–7
  screens (below) and fixes from it; (b) open questions in the doc: "latest before due" dependency matching, the
  client-limited role, other signal transports (file arrival / DB watcher scripts posting to `POST /ope/signals`);
  (c) small debts: `test_plan.py` client-code flake, pin / unpin UI (API only), staff names instead of 職員 #id in
  timelines, SOP "impact before approval" list, a real login replacing the `X-Staff-Id` header. Plan a new phase the
  same way as 5–7: questions first, a step plan approved by the user, one step at a time.
- **DB:** latest Alembic head **`3242f180527a`** (ope_signal + role signal_sender). Chain: `ceee1634d6a7` (squashed
  init) → `34bdbd214739` → `9ee3f3e0624e` → `9b2ec314368a` → `3493c39bdf43` → `5c1f7d2a9e04` → `568c3287c359`
  (staff) → `f9bbe9648f15` (biz release settings) → `9e1bcef6cbc7` (ope tables) → `5eede5808433` → `971958268657`
  (step template condition / is_blocking) → `966b78ff950f` (ope_step, ope_step_event) → `6c8cc87274c8`
  (ope_step.retired_at, RETIRED) → `2904467fd7b9` (biz_dependency) → `cb0ae371f402` (ope_dependency) →
  `58425e70286d` (order event DEPENDENCY_OVERRIDE_APPROVED) → `6a7849dbf2ec` (biz execution_type / completion_mode /
  signal_type) → `3242f180527a` (ope_signal, role signal_sender).
  **The user's dev DB** was at `6c8cc87274c8` (upgraded by the user 2026-11-04 during Phase 5); the later five
  (`2904467fd7b9` … `3242f180527a`) were handed over — ask whether they ran `alembic upgrade head`. The user runs it
  themselves; their uvicorn on 8000 auto-reloads, so a model change without the migration returns 500 until they
  upgrade. Warn them whenever a step adds a migration. Dev DB facts: staff id 1 login `system` (system_admin); biz 1
  has SOP version 1 `v1.1.0`; 890 orders backfilled 2026-10-30.
  **`plan_check`** (throwaway) is at `3242f180527a`. Seeded: client `MOCK`, biz 1 `MOCK0001` (SOP version 1 with 3
  step templates added via SQL: always / month_in [12] / first_in_week blocking), biz 2 `MOCK0002` (LIGHTWEIGHT +
  AUTOMATIC, signal type `data_received`, daily template 23), calendar `JP_BANK` id 1, the mock sheet. Staff: 1
  `check-admin` (system_admin), 2 `check-op` (suspended), 3 viewer+auditor, 4 operator, 5 releaser, 6 / 7 checkers,
  8 admin, 9 / 10 signal_sender. Many orders there were moved / released / finished by harness runs.
- **Tests:** `pytest tests/ --ignore=tests/client_biz_check.py --ignore=tests/client_biz_check2.py` → **147 passed**,
  ~95–225 s (run times vary a lot). Runs against `test_db` via `tests/conftest.py` (`create_all`, no migrations; the
  order-sync listener — and with it the biz dependency listener — is registered there, so every plan test also syncs
  orders). Each test file uses its own client-code prefix letter: T D B O S A R V P K M G L N Q E H J (pick an unused
  one for a new file). Known flake: `test_plan.py:69` client codes `"T"` + 3 hex digits collide ~1 run in 4–5 →
  `DuplicatedRecordError` / `uq_client_id` in a fixture; rerun. Not fixed yet (the user hasn't asked).
  Type check: `npx pyright@1.1.400 --pythonpath .venv/Scripts/python.exe src main.py tests scripts` → 0 errors.
  Migration check (on `plan_check`): `alembic upgrade head` / `downgrade -1` / `upgrade head` / `alembic check` → no
  drift. `.env` points at the user's `dev_db`: run alembic with `DATABASE_URL` = the same URL ending in `/plan_check`
  (`export DATABASE_URL=$(grep ^DATABASE_URL .env | cut -d= -f2- | sed 's#/dev_db$#/plan_check#')`).
  Frontend: `cd frontend && npm run build` (= `tsc --noEmit` + Vite build) passes. After backend schema changes:
  `python scripts/export_openapi.py` then `npm run gen:api` (frontend types).
- **Scheduled jobs (user's machine):** Task Scheduler `\postgres-migration-poc\PlanExtendJob` (daily 00:05,
  `scripts/extend_plans.py` → `extend_plans.log`) and `\postgres-migration-poc\OrderReleaseJob` (every 15 min,
  `scripts/release_orders.py` → `release_orders.log`), both registered by the user. Any new script that changes plan
  must call `order_sync.register()`.
- **Browser check:** the user clicked through 年間計画シート and 休日カレンダー (2026-10-28): no issues. **Not yet
  clicked through:** 日次ダッシュボード incl. the order drawer (status actions, ステップ, 依存関係, 受領 / 遅れ), the
  dashboard tags (3/5 progress, 待ち, 遅れ) and the 照合できなかった信号 panel, 予定テンプレート設定 (「次の予定」,
  the 業務の設定 panel), 業務の依存関係 (`/settings/dependencies`), 職員 + nav picker 「操作する職員」.

### Commits by phase (newest first; full detail per step in §2)
| Phase | Commits |
|---|---|
| 7 client events / signals (`feature/ope-signals`) | `22fec2a` 7.5 screens · `a7e1e03` 7.4 late + signal list · `0b5e762` 7.3 receive / match · `a9f8fc3` 7.2 ope_signal + role · `0ff76dd` 7.1 biz settings · `c1aade1` plan |
| 6 dependencies (`feature/ope-dependencies`) | `3669528` 6.5 screens · `916ebee` 6.4 start check + override · `1a364ca` 6.3 link resolution · `3eaede4` 6.2 ope_dependency · `8ab5f92` 6.1 biz_dependency |
| 5 steps (`feature/ope-orders`) | `6a37fee` docs · `75cc109` 5.5 (user) · `074840f` 5.4 · `abe6281` 5.3 · `11cd510` docs · `fc58dd0` 5.2 · `05850c1` / `5dbbe0f` 5.1 (user) |
| 4 orders (`feature/ope-orders`) | `d151fbf` 4.7 · `4790ac1` 4.6 · `09f7455` 4.5 · `0389de2` 4.4 · `7d94be7` 4.3 · `0552e9f` 4.2 · `d65af44` 4.1 |
| 1–3 (merged `23767b8`) | `702ebbb` / `bfe3deb` staff · `fcff659` generation job · `eb4e6b5` / `17f9bcc` / `9306e23` dashboard |

## 2. Target architecture — "Operations Orchestration Design" (draft for team review)

Design doc (Claude Docs): https://claude.ai/artifact/W4XgfnPTH4iJPstx362xpV — the source of truth for this section.
Decided in the doc's comments (2026-10-28): SOP version + steps **freeze at release**.
Before building any phase, re-read the doc: the team may have edited or commented on it.
**Last rev written by me: 69 (2026-11-04)** — read with `{"kind": "view", "sinceRev": 69}` to see only newer edits, and
query comments with `afterSeq: 24`. Doc ids: project `eb57297f-757b-4c1e-9116-9e545b1abd76`, body node `9e7b6bb0-1c80`.
The delivery-phase list in the doc carries the build status (phases 1–7 built and merged, rev 69).

### Domains (decided: plan and ope stay separate, no merge)
| Domain | Owns | Status |
|---|---|---|
| **biz** | What the work is: businesses (release + client-event settings), SOP versions (`effective_at` / `expired_at`), step templates (condition, is_blocking), dependency definitions | Built (`src/domain_biz/`) |
| **plan** | When it is due: templates, sheets, holidays, occurrences. Recomputable; regenerated freely, future only; knows nothing about ope | Built (`src/domain_plan/`) |
| **ope** | What happened: orders, steps, append-only events, dependency links, signals. Facts, never recomputed | Built (`src/domain_ope/`: services, steps, links, signals) |
| **staff** | Who: users and roles (operator, checker, releaser, admin, viewer, auditor, system_admin, signal_sender) | Built (`src/domain_staff/`) |
| **Coordination layer** | Cross-domain glue in one DB transaction (plan → order sync, biz definition listener) + read models (daily dashboard) | Built (`src/coordination/`) |

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
| 4.5 | **done** (`09f7455`) — Automatic release job (03:00 JST; `release_time` / `release_days_before`) + its own Task Scheduler entry (registered by the user) | — | |
| 4.6 | **done** (`4790ac1`) — SOP version resolution, pin / unpin (releaser/admin), freeze at release | none needed | |
| 4.7 | **done** (`d151fbf`) — Frontend: order status on the dashboard + actions | — | |

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

**4.6 as built — SOP version on orders** (no migration; the columns came with 4.2). User decision 2026-10-30:
**release goes ahead when no approved version is effective** (frozen = null; the release event detail says
`{"sop_version": null, "sop_note": "no SOP version effective on <day>"}`) — none of the user's businesses had an
approved SOP version on 2026-10-30.
- `ope.resolve_sop_version(db, order)` = pinned ?? `sop_version_repo.get_effective_at(biz, at=order.scheduled_start)`
  (non-draft, `effective_at ≤ at < expired_at`, newest `version_number`). Computed, so a plan move of a pending order
  changes it automatically. Biz's own `get_current_active_version` only works for "now" — don't use it for orders.
- `_release` freezes: `frozen_sop_version_id` + event detail `sop_version {id, version_tag, source pinned|effective}`.
  Manual and automatic release both go through it. A released order's version never changes (a later plan move
  keeps it).
- `POST /ope/orders/{id}/pin {sop_version_id, reason?}` / `…/unpin` (releaser, admin; pending only): same business,
  non-draft (older or newer); PINNED / UNPINNED events with `version_tag` / `previous_pin`.
- `OrderResponse.sop_version` = `{id, version_tag, source: frozen | pinned | effective}` or null.
- Not built from the doc's SOP section: the "impact before approval" list on the biz SOP approval screen
  ("which upcoming orders will run v4") — no step owns it yet; copying the step list at release is Phase 5.
- Biz quirk seen: only one FUTURE version may exist and nothing turns FUTURE into ACTIVE (status stays FUTURE;
  `dynamic_status` computes it), so tests create consecutive versions directly via the ORM.
- Tests: `tests/test_sop_resolution.py` (5; client prefix `"V"`). HTTP-checked on `plan_check` (biz 1 now has an
  approved version `v9.…` effective 2026-11-02, and order 191 is released with it frozen). Doc rev 39.
- The user's dev DB: biz 1 has SOP version id 1 `v1.1.0` (effective 2026-10-30 11:49; biz shows status "future") →
  biz 1's pending orders (e.g. 888–890) resolve to it.

**4.7 as built — order screen** (no migration). Backend: `/ops/daily` rows carry `order: {id, status, conflict,
conflict_reason}` (`ope_order_repo.get_active_by_instances`, one query; null for occurrences without an order).
Frontend: `domain/ope/{types,api,hooks,lib}` (`orderApi` = every `/ope/orders` action; `lib/orderActions.ts` = status /
event labels and `availableActions(order, staff, today, day)` mirroring the backend roles + rules, display only);
the 日次ダッシュボード rows show an order status tag (未リリース / リリース済 / 作業中 / 保留中 / 完了 / 取消) + 矛盾,
and clicking a row opens `OrderDrawer` (status, schedule, SOP version + source, release time, the actions the acting
staff may use with a reason / memo modal — reason required for hold, cancel, resolve-conflict cancel — and the event
timeline, newest first; actors shown as 職員 #id). **Not in the UI:** pin / unpin (biz has no endpoint listing a
business's SOP versions to pick from; API only), staff names in the timeline, the SOP "impact before approval" list.
Harness-checked through Vite on `plan_check` (10 checks). Frontend types regenerated (includes 4.3–4.6 changes).
5. Steps: conditions, maker-checker, `is_blocking`. **built** (5.1–5.5, branch `feature/ope-orders`)

#### Phase 5 decisions (2026-10-30, doc rev 41)
Rejecting a non-blocking step returns **only that step** to todo (later steps keep their state; the order can't
finish until all are approved). Steps are created at release but stay **locked until the order starts**; starting
opens step 1. **Only the checker role** approves / rejects, never the step's submitter (four-eyes). Already in the
doc: conditions `always` / `month_in` / `first_in_week` evaluated on the scheduled date (`first_in_week` = the
template's first occurrence in that ISO week, read from plan); live preview while pending, frozen at release; next
step opens on submit, or on approve when `is_blocking` (versioned on the step template); order finishes automatically
when the last step is approved; a ready order whose step conditions change after a system move gets a conflict flag.

#### Phase 5 step plan (approved 2026-10-30)
| # | Step | Status |
|---|---|---|
| 5.1 | biz `sop_step_template.condition` (JSONB, default `{"type": "always"}`) + `is_blocking` (default false); migration `971958268657`; schemas `StepCondition` = discriminated union `AlwaysCondition` / `MonthInCondition(months 1–12, deduped + sorted)` / `FirstInWeekCondition`; `clone_active_to_draft` copies both; tests in `tests/test_biz.py` | **done** (`05850c1`, pushed by the user) |
| 5.2 | ope tables `ope_step` (order_id, step_template_id, step_num, snapshot title / content / is_blocking, state) + append-only `ope_step_event`; migration `966b78ff950f` | **done** (`fc58dd0`, not pushed) |
| 5.3 | Build steps: live preview while pending (in the order detail), create + freeze at release, conflict flag when a system move changes a ready order's step set; migration `6c8cc87274c8` | **done** (`abe6281`, not pushed) |
| 5.4 | Step actions: submit (operator) / approve / reject (checker, four-eyes, reason for reject), unlock rules, start opens step 1, auto-finish on the last approval; manual finish only for zero-step orders | **done** (`074840f`, not pushed) |
| 5.5 | Frontend: steps + actions in the order drawer, step progress ("3/5") on dashboard rows | **done** (`75cc109`, committed + pushed by the user) |

**5.2 as built** (`src/domain_ope/models.py`, migration `966b78ff950f`). User decisions 2026-11-04 (all four as I
recommended): **events shaped like order events** (`StepEventType` CREATED | STATE_CHANGED, from / to required for a
change; reject = SUBMITTED → TODO, CHECK `ck_ope_step_event_reject_reason` requires `reason`); **actors only in the
events** (no submitted_by / approved_by on `ope_step`; 5.4's four-eyes check reads the latest → SUBMITTED event);
**non-matching steps get no row** (5.3 lists the skipped step templates in the release event detail); **`step_num` =
the template's number** (gaps allowed).
- `ope_step`: `order_id` (FK RESTRICT), `step_template_id` (FK sop_step_template RESTRICT — so an SOP version /
  step template used by an order can't be hard-deleted), `step_num` (> 0), snapshot `title` / `content` /
  `is_blocking`, `state` (`StepState` LOCKED default | TODO | SUBMITTED | APPROVED; cache of the latest event),
  timestamps. Unique `uq_ope_step_order_num`, `uq_ope_step_order_template`. The snapshot matters: biz's
  `cancel_future_version` turns a FUTURE version (pinnable) back into an editable DRAFT.
- `ope_step_event`: `step_id` (FK RESTRICT), `event_type`, `from_state` / `to_state`, `source` (reuses
  `ope_event_source_enum`, `create_type=False` in the migration), `actor_staff_id` (CHECK HUMAN ⇔ actor), `reason`,
  `detail`, `occurred_at`; trigger `trg_ope_step_event_immutable` → existing `ope_reject_mutation()` (models.py also
  re-creates the function for `create_all`, since table order isn't fixed). Downgrade drops the two new enum types.
- Tests `tests/test_ope_step.py` (4; client prefix `"P"`; listener switched off like `test_ope.py`).
- Doc rows for `ope_step` / `ope_step_event` updated (rev 48, after the user allowed the doc edit).

**5.3 as built** (`src/domain_ope/steps.py`, migration `6c8cc87274c8`). User decisions 2026-11-04 (all as I
recommended): **accept_plan rebuilds** the steps of a released, unstarted order whose step set changed (old rows get
`retired_at` + a RETIRED step event; the unique indexes ignore retired rows); **same-week orders are re-checked**
(first_in_week); **person moves flag too** (one rule for all plan changes). Doc rev 49 (paragraph after the
first_in_week note in Steps).
- Conditions on the scheduled day (JST): `month_in` = its month; `first_in_week` = no other live occurrence of the
  template (one-offs included) starts earlier in that ISO week (Mon–Sun JST), via new
  `plan_cache_repo.starts_for_template`. Same start time counts as first.
- `plan_steps(db, order, version) -> StepPlan(included, skipped)`; `create_steps` at release (in `_release`, so manual
  and automatic): LOCKED rows + CREATED step events with the release's source / actor; release event
  `detail.steps = {created: [nums], skipped: [{step_num, step_template_id, condition}]}` (no version → both empty).
- `GET /ope/orders/{id}` → `steps` (`OrderStepResponse`: id / state null in a preview) + `steps_preview` (true while
  not released; canceled unreleased → empty). Frontend types regenerated (no UI yet — 5.5).
- `order_sync.apply_slot_changes`: `_apply` returns the days touched (old + new scheduled day); afterwards
  `_recheck_steps` flushes, then for each template checks `ope_order_repo.get_released_unstarted` (ready, or on_hold
  from ready; FOR UPDATE) in the touched weeks → `step_set_change` → CONFLICT_RAISED, reason
  `ステップの条件が変わりました（追加: 2、除外: 3）`, detail `{cause: "step conditions changed", added, removed}`. Not raised
  twice; appended to an existing conflict reason with ` / `. `_raise_conflict` lost its unused `change` param.
- `resolve_conflict(accept_plan)`: after adopting the plan times, if `is_unstarted_release` and the set differs →
  `rebuild_steps` (retires **all** active rows, recreates from the frozen version; `detail.steps = {added, removed,
  created, skipped}`). keep = frozen steps stay. Started orders (in_progress / on_hold from in_progress) are never
  rechecked or rebuilt.
- Migration gotchas: autogenerate misses partial-index WHERE changes and enum values; `ALTER TYPE … ADD VALUE` works
  in the migration transaction (PG 16); the downgrade recreates the enum type and must drop / recreate the CHECKs
  that compare `event_type` with literals.
- Tests `tests/test_order_steps.py` (6; client prefix `"K"`; dates computed from today: `base_tuesday()` ≥ 14 days
  ahead, `next_month_tuesday`); release-detail assertions in `test_release_job.py` / `test_sop_resolution.py` now
  include `steps`. HTTP-checked on `plan_check` (uvicorn 8001): biz 1's version 1 got 3 step templates via SQL
  (always / month_in [12] / first_in_week blocking); order 18 (11/17) previewed [1, 3], released, moved by staff 4 to
  12/08 → conflict 「追加: 2」, start → 400, accept_plan → steps [1, 2, 3].

**5.4 as built** (no migration). User decisions 2026-11-04 (both as I recommended): **a conflict doesn't stop step
work, but the order can't finish while it's open** (the last approval leaves it in_progress; `resolve_conflict` accept_plan /
keep then finishes it); **steps are frozen while the order is on hold** (step actions only while in_progress).
My calls (doc rev 50): opening a step and the automatic finish are SYSTEM events (`detail.cause`: "order started" /
"previous step submitted" / "previous step approved" + `after_step`; order: "all steps approved"); four-eyes =
approver / rejecter ≠ the **latest** submitter of that step (`ope_step_repo.latest_submitter`), else 403 `FOUR_EYES`
(new `ErrorCode`); a resubmission by someone else lets the first submitter approve.
- `POST /ope/steps/{step_id}/submit` (operator, `OrderCommentIn`) · `/approve` (checker, comment) · `/reject` (checker,
  `OrderReasonIn`, reason required) → `OrderDetailResponse`. Each locks the **order** row first (`_get_step_for_action`,
  then `db.refresh(step)`), requires order in_progress and the step state (todo / submitted), else 400.
- Unlock (`steps.open_next_step`): only the immediate next active step, only if still LOCKED; non-blocking → on
  submit, `is_blocking` → on approve. Reject → that step back to todo, nothing else changes.
- `start_order` → `steps.open_first_step`. `finish_order` → 400 when the order has active steps (manual finish = zero
  steps only). `_finish_if_complete` after approve and after a non-cancel `resolve_conflict`.
- `OrderStepResponse.events` (`StepEventResponse`, oldest first; empty in a preview) — one query for all steps.
- Tests `tests/test_step_actions.py` (7; client prefix `"M"`; `monkeypatch.setattr(ope, "_now", …)` to start).
  HTTP-checked on `plan_check` (8001): new active checkers 6, 7; order 18 (12/08, steps 1, 2, 3 blocking) early-start
  approved by 5, started by 4 → step 1 todo; manual finish 400; operator approve / checker submit 403; empty reject
  reason 422; reject → resubmit; all approved → finished (SYSTEM "all steps approved"). Four-eyes over HTTP not checked
  (no plan_check staff is operator + checker); covered by the unit test.
- Frontend gap closed by 5.5 (完了 only for zero-step orders, steps in the drawer).

**5.5 as built** (no migration; no user decisions needed — my UI calls, listed here).
- Backend: `/ops/daily` `DailyOrderSummary.steps_total` / `steps_approved` (`ope_step_repo.progress_by_orders`, one
  grouped query, retired steps excluded); `OrderDetailResponse.steps` / `steps_preview` and
  `OrderStepResponse.events` are now **required** in the schema (openapi-typescript made `default_factory` fields
  optional). Test `test_dashboard_rows_carry_the_step_progress` in `tests/test_step_actions.py`.
- Frontend: `domain/ope/types` (StepState, StepEventType, OrderStep, StepEvent); `orderApi.submitStep / approveStep /
  rejectStep`; new `domain/ope/lib/stepActions.ts` (labels 待機 / 未提出 / 確認待ち / 承認済 — not 作業中, which is
  the order's; `describeStepEvent`, `latestSubmitter`, `rejectionReason`, `availableStepActions` mirroring the
  backend: in_progress only, submit = operator on todo, approve / reject = checker on submitted and not the latest
  submitter; `stepBlockedReason` explains the four-eyes case; `stepProgress`). `availableActions` offers 完了 only
  when `order.steps` is empty.
- `OrderDrawer`: section 「ステップ」 (承認 x/y; preview note before release) with `StepRow` per step (number, title,
  state tag, 承認で次へ tag for blocking, content pre-wrap, 差し戻し alert with the reason, last human action line,
  small 提出 / 承認 / 差し戻し buttons); the existing modal handles step actions too (`PendingAction` union; reason
  required for 差し戻し). Dashboard rows show `x/y` (mono, muted) after the title when the order has steps.
  CSS `.order-steps`, `.order-step*`, `.order-progress` (uses `--rule` / `--sheet`; there is no `--border`).
- Checked: `npm run build`; esbuild harness of the real modules (orderApi, stepActions, orderActions, dashboardApi,
  staffApi, actingStaff) in node **directly against uvicorn 8001** (`VITE_API_BASE_URL=http://127.0.0.1:8001`; the
  Vite proxy only strips `/api`, so Vite isn't needed for API checks) on `plan_check`: orders 74 (1 step) and 104
  (12/07, 3 steps incl. blocking) — preview, release, start, operator / checker actions, four-eyes hiding, reject
  reason, finish, dashboard "3/3": all 14 checks passed. **Not checked: the browser** (layout, the drawer, buttons) —
  the user clicks through.
6. Dependencies (hard + override, informational). **built** (6.1–6.5, branch `feature/ope-dependencies`)

#### Phase 6 decisions (2026-11-04, doc rev 54)
Matching rules built: **same sheet event** and **same occurrence date** ("latest before due" stays an open question;
add later as a new `match_rule` value). **A hard link whose upstream order can't be found = not met** (start refused;
releaser / admin force-start with reason). **Same client only** (doc open question ticked). Concrete links stored in
ope (`ope_dependency`), as the doc says. My call: definition writes need **admin** (業務管理者 = 業務の設定); reads open
like the rest of biz.

#### Phase 6 step plan (approved 2026-11-04)
| # | Step | Status |
|---|---|---|
| 6.1 | biz `biz_dependency` (upstream biz, downstream biz, match_rule, kind HARD / INFORMATIONAL) + API, cycle check on save; migration `2904467fd7b9` | **done** (`8ab5f92`, not pushed) |
| 6.2 | ope tables `ope_dependency` (upstream order, downstream order, definition, kind) — tables only; migration `cb0ae371f402` | **done** (`3eaede4`, not pushed) |
| 6.3 | Link resolution: create / refresh concrete links when orders are created or moved (order_sync) and when a definition is added or removed | **done** (`1a364ca`, not pushed) |
| 6.4 | Start check: hard links need the upstream order finished, else start → 400 listing blockers; force-start (releaser / admin, reason) as an order event; upstream canceled / on hold stays blocked; migration `58425e70286d` | **done** (`916ebee`, not pushed) |
| 6.5 | Frontend: dependency settings for a business, upstream / downstream in the order drawer, "blocked" tag + force-start | **done** (`3669528`, not pushed) |

**6.1 as built** (`src/domain_biz/models.py` end, migration `2904467fd7b9`). `BizDependency`: `upstream_biz_id` /
`downstream_biz_id` (FK biz_master RESTRICT), `match_rule` (`DependencyMatchRule` SAME_SHEET_EVENT |
SAME_OCCURRENCE_DATE), `kind` (`DependencyKind` HARD default | INFORMATIONAL), `created_at`; CHECK
`ck_biz_dependency_not_self`, unique `uq_biz_dependency_pair` (one definition per pair, whatever the rule).
- API: `GET /biz/{biz_id}/dependencies` (both directions, open) · `POST /biz/{biz_id}/dependencies`
  `{upstream_biz_id, match_rule, kind?}` (this biz = downstream; `@require_roles([ADMIN])`) · `DELETE
  /biz/dependencies/{id}` (admin; hard delete). Response `BizDependencyResponse {id, upstream, downstream: BizRef{id,
  business_code, name}, match_rule, kind, created_at}` (refs read with `include_deleted`).
- Service checks: self 400, other client 400, deleted / unknown biz 404 (`get_biz`), duplicate pair 409, cycle 400
  (`_creates_cycle`: DFS from downstream over all definitions, hard and informational). Created via the ORM (not
  `CRUDBase.create`: enums). No PATCH (change = delete + create) — mention if the UI needs it.
- `biz_dependency_repo` (`BizMaster.py`): `get_for_biz`, `get_pair`, `get_edges`. Migration drops the two enum types
  on downgrade.
- Tests `tests/test_biz_dependency.py` (4; client prefix `"G"`; guard checked via the OpenAPI x-staff-id param like
  `test_staff.py`). HTTP-checked on `plan_check`: new admin staff 8, new biz 2 `MOCK0002`; no staff 401, operator 403,
  admin 201, duplicate 409, cycle 400, delete 200.

**6.2 as built** (`OpeDependency` at the end of `src/domain_ope/models.py`, migration `cb0ae371f402`). User decisions
2026-11-04 (both as I recommended): **a deleted definition's links stay, marked removed** (`removed_at` +
`removed_reason`; FK `biz_dependency_id` ON DELETE SET NULL); **a missing upstream = a waiting row** with
`upstream_order_id` NULL (6.3 fills it when the upstream order appears). My call: `kind` / `match_rule` copied onto
the link (reuses the biz enum types `biz_dependency_kind_enum` / `biz_dependency_match_rule_enum`; the migration
uses `postgresql.ENUM(..., create_type=False)` and its downgrade leaves them to 6.1).
- Columns: `downstream_order_id` (FK RESTRICT), `upstream_order_id` (FK RESTRICT, nullable), `biz_dependency_id`,
  `upstream_biz_id` (FK biz_master RESTRICT: what is awaited even without an order), `kind`, `match_rule`,
  `match_sheet_event_id` / `match_date` (CHECK per rule), `removed_at` / `removed_reason` (CHECK both or neither),
  timestamps. CHECK not self. Partial unique (active rows only): `uq_ope_dependency_pair` (down, up) where up not
  null; `uq_ope_dependency_waiting` (down, upstream_biz) where up null. Not append-only (6.3 fills / removes rows).
- **For 6.3:** biz must not import ope, so marking links removed on a definition delete needs the coordination
  layer (e.g. the delete endpoint calling a coordination use case, or a hook like plan's slot listener).
- Tests `tests/test_ope_dependency.py` (3; client prefix `"L"`; listener off like `test_ope.py`).

**6.3 as built** (`src/domain_ope/links.py`, `src/domain_biz/dependency_events.py`; no migration). User decisions
2026-11-04 (all as I recommended, doc rev 57): **definition changes reach ope through a biz listener**
(`dependency_events.register_dependency_listener`, registered in `order_sync.register()` → `apply_dependency_change`;
`create_dependency` notifies "created" after flush, `delete_dependency` notifies "deleted" before the delete);
**only not-started downstream orders are (re)resolved** (pending / ready / on_hold from those); **a canceled upstream
stays linked** (live matches preferred, else the latest canceled; a returning slot's new order takes over — old row
removed with `REASON_UPSTREAM_CHANGED`); **no key → no link** (e.g. sheet rule on a non-sheet order).
- `links.resolve(db, order, dep)`: targets = `_upstream_targets` (upstream biz's orders with the same key via
  `ope_dependency_repo.get_orders_matching`); stale links removed, waiting rows filled with a found upstream (same
  row) or removed when others cover it, new rows for the rest, one waiting row when nothing matches.
- `links.refresh_order(db, order)`: one `biz_dependency_repo.get_for_biz` lookup first (returns at once for businesses
  without definitions — order creation is hot), then resolves the order as downstream and re-resolves downstream
  orders linked to it or matching its key. Called from `order_sync.create_order`, `_cancel`, `_slot_moved` (only when
  `occurrence_date` changes — one-offs), `ope.cancel_order`, `ope.resolve_conflict`.
- `links.definition_created` → all open orders of the downstream biz (`ope_dependency_repo.get_open_orders_of`);
  `definition_deleted` → every active link of it gets `REASON_DEFINITION_DELETED` (started orders' links too).
- Reasons (Japanese, stored): `依存関係の定義が削除されました` / `待つ上流のオーダーが変わりました` /
  `定義の突き合わせの条件に当てはまらなくなりました`.
- Tests `tests/test_order_links.py` (5; client prefix `"N"`; sheet case uses the mock sheet, marker 本締, offsets 0 / 1).
  HTTP-checked on `plan_check`: definition biz 2 → biz 1 (same date) gave 184 waiting rows (biz 2 has no orders);
  delete → 184 removed with the reason. Links aren't in any API response yet (6.4 / 6.5).
- Known flake seen again: `test_plan.py:69` client code collision (`uq_client_id`) ~1 run in 4.

**6.4 as built** (migration `58425e70286d`: `ALTER TYPE ope_order_event_type_enum ADD VALUE
'DEPENDENCY_OVERRIDE_APPROVED'`; downgrade recreates the type and drops / recreates `ck_ope_order_event_status_change`
and `ck_ope_order_event_created`). User decisions 2026-11-04 (both as I recommended, doc rev 58): **force-start =
approve, then the operator starts** (like early start); **the override doesn't lift the scheduled-day rule**. My
calls: met = upstream FINISHED only (waiting row, canceled, on hold, ready, in progress all block); informational
never blocks; the approval covers the links unmet **at approval time** (`detail.links` ids, `detail.blockers` text) —
a re-pointed / new link needs a new approval.
- `POST /ope/orders/{id}/approve-dependency-override` (releaser / admin, `OrderReasonIn`; pending or ready): 400 when
  nothing is unmet, 409 when all unmet links are already covered.
- `start_order`: after the day check, `links.unmet_hard_links` minus covered ids → 400 `Order N waits for: order M of
  CODE is pending; no order of CODE for 2026-11-08. A releaser or admin can approve starting without them.`; the
  start event `detail.dependency_override` lists the overridden link ids (and `early_start` as before).
- `OrderDetailResponse.dependencies` (`OrderDependencyResponse {id, kind, match_rule, upstream_biz {id,
  business_code, name}, upstream_order_id, upstream_status, upstream_scheduled_start, met, overridden}`) — active links
  where this order is downstream (downstream view for 6.5 still to add).
- Frontend: only `ORDER_EVENT_LABEL.dependency_override_approved` (依存関係を待たない開始を承認) so the build passes;
  types regenerated. **Until 6.5 the drawer offers 開始 even when dependencies block (backend 400s)** and has no
  override button.
- Tests `tests/test_dependency_start.py` (5; client prefix `"Q"`); `test_order_links.py`'s started-order test now
  approves an override first. HTTP-checked on `plan_check`: definition 3 (biz 2 → biz 1, deleted afterwards), order
  75: start 400 with the blocker, operator override 403, empty reason 422, releaser override 200, start 200
  (`detail {"early_start": true, "dependency_override": [254]}`).

**6.5 as built** (no migration). User decision 2026-11-04: **a new settings page** `/settings/dependencies?client=`
(nav 「業務の依存関係」), not a section on the template page.
- Backend additions: `GET /biz/clients/{client_code}/dependencies` (`biz_dependency_repo.get_for_client`; path chosen
  because `/biz/dependencies` would hit `GET /biz/{biz_id}`); `OrderDetailResponse.dependents`
  (`OrderDependentResponse {id, kind, downstream_biz, downstream_order_id, downstream_status,
  downstream_scheduled_start}`, via `ope_dependency_repo.get_active_by_upstream`); `DailyOrderSummary.blocked`
  (`links.blocked_order_ids`: not-started orders with an unmet, un-overridden hard link — a few queries for the whole
  day via `get_active_for_orders` + `ope_order_repo.get_events_of_type`); `links.overridden_link_ids` (services
  aliases it). Test `test_dashboard_blocked_flag_dependents_and_client_list` in `tests/test_dependency_start.py`.
- Frontend: `domain/biz/{types (BizDependency…), api/dependencyApi.ts (listForClient / create / remove),
  lib/dependencies.ts (MATCH_RULE_LABEL 同じシート日付 / 同じ日付 + help, KIND_LABEL 待つ / 参考 + help,
  canManageDependencies = active admin)}`; `features/dependency-settings/DependencySettingsPage.tsx` (client select in
  `?client=`, table 上流 → 下流 / 突き合わせ / 種類 / 登録日 / 削除 (admin), 依存関係を追加 modal with upstream /
  downstream selects of that client's businesses, rule and kind radios with help text, delete confirm).
  `domain/ope`: types `OrderDependency` / `OrderDependent`, `orderApi.approveDependencyOverride`,
  `orderActions`: action `approveDependencyOverride` (releaser / admin, reason required; pending / ready while
  `blockingDependencies(order)` is non-empty), 開始 hidden while blocked, `startBlockedReason` explains it.
  `OrderDrawer`: section 「依存関係」 (待つ上流のオーダー: biz, #id, status tag, 完了 / 待ち / 承認済み / 未完了, 参考
  tag; このオーダーを待つ下流のオーダー), override note in the modal. Dashboard rows: orange 「待ち」 tag when `blocked`.
  CSS `.order-deps`, `.order-dep`.
- Checked: `npm run build`; esbuild harness `deps_harness.ts` of the real modules against uvicorn 8001 on
  `plan_check` (order 76): operator create 403 + UI hides, admin create + listForClient, blocking waiting row, override
  offered to the releaser, 開始 hidden for the operator, blocked reason, dashboard 待ち true → override → false, start,
  delete — 14/14 passed. **Not checked: the browser** (the new page, the drawer section, the tag) — the user clicks
  through.
7. Client events with automatic completion (signals). **built** (7.1–7.5, branch `feature/ope-signals`)

#### Phase 7 decisions (2026-11-04, doc rev 62)
All four as I recommended: Phase 6 merged first, Phase 7 on a new branch; **a client event = a LIGHTWEIGHT business**
with its own template / orders (a file feeding several jobs = they depend on that business via Phase 6 links);
**transport: HTTP `POST /ope/signals` only**, sender = a staff account with a new role **`signal_sender`** (X-Staff-Id
until real login; watchers later = scripts posting the same signal); **match = signal type + client + rule date**
(`key` = the order's `occurrence_date` YYYY-MM-DD); none or several matches → stored unmatched for a person.

#### Phase 7 step plan (approved 2026-11-04)
| # | Step | Status |
|---|---|---|
| 7.1 | biz `execution_type` (SOP / LIGHTWEIGHT), `completion_mode` (MANUAL / AUTOMATIC), `signal_type` (required for AUTOMATIC); biz PATCH; migration; LIGHTWEIGHT orders get no steps at release; migration `6a7849dbf2ec` | **done** (`0ff76dd`, not pushed) |
| 7.2 | `ope_signal` table (append-only: type, client_code, key, received_at, sender, match result + order) + role `signal_sender` — tables / role only; migration `3242f180527a` | **done** (`a9f8fc3`, not pushed) |
| 7.3 | `POST /ope/signals` (signal_sender): store every signal, match, finish the order (system event); unmatched / ambiguous kept; manual way out (finish with reason) for AUTOMATIC | **done** (`0b5e762`, not pushed) |
| 7.4 | Read side: "late" computed on the dashboard, unmatched signals listed (`GET /ope/signals`) | **done** (`a7e1e03`, not pushed) |
| 7.5 | Frontend: client-event settings on a business, 受領 (mark received) in the drawer, late tag, unmatched signals panel | **done** (merged into `main`, not pushed) |

**7.1 as built** (migration `6a7849dbf2ec`). User decisions 2026-11-04 (both as I recommended, doc rev 63): **signal
type unique per client** (partial unique index `uq_biz_client_signal_type_alive` on (client_code, signal_type) where
not null and `deleted_at IS NULL`); **AUTOMATIC only for LIGHTWEIGHT**.
- `biz_master`: `execution_type` (`ExecutionType` SOP default | LIGHTWEIGHT; `biz_execution_type_enum`),
  `completion_mode` (`CompletionMode` MANUAL default | AUTOMATIC; `biz_completion_mode_enum`), `signal_type`
  (String 50). CHECKs `chk_biz_automatic_completion_lightweight`, `chk_biz_signal_type` (AUTOMATIC ⇔ signal type set).
  Migration creates the enum types first and the CHECKs by hand (both autogenerate gaps again).
- `BizMasterUpdate`: the three fields (explicit null refused for the two enums via `NOT_NULL_FIELDS`; `signal_type`
  stripped, null clears it); `BizMasterResponse` carries them. `update_biz` → `_check_client_event_settings` on the
  merged values: 400 (AUTOMATIC on SOP / AUTOMATIC without signal type / signal type with MANUAL), 409 (signal type used
  by another business of the client, message names it). `PATCH /biz/{id}` stays unguarded like before.
- `steps.plan_steps` returns no steps for LIGHTWEIGHT businesses (preview, release, step-set checks).
- Tests `tests/test_client_events.py` (4; client prefix `"E"`). HTTP-checked on `plan_check` (biz 2 left back at SOP /
  MANUAL). Frontend types regenerated (no UI yet — 7.5).
- Shell gotcha seen: `sed -n … $f` with an empty `$f` reads stdin and hangs the command — guard with `[ -n "$f" ]` /
  `< /dev/null`. And never combine `python - <<'EOF'` with `< /dev/null` (the redirect replaces the heredoc → an
  interactive REPL that hangs). Edit scripts go into the scratchpad via the Write tool.

**7.2 as built** (`OpeSignal` at the end of `src/domain_ope/models.py`, migration `3242f180527a`). User decisions
2026-11-04 (all as I recommended, doc rev 64): **store every signal; a resend whose order is already finished =
`DUPLICATE`** (no idempotency key); **optional JSON `payload`** (never used for matching); **`handled_*` settable once**
(trigger, no separate table).
- Columns: `signal_type` (50), `client_code` (String 10, **no FK** so unknown clients are kept), `key` (100),
  `received_at` (sender's), `recorded_at` (server), `sender_staff_id` (FK staff), `payload` JSONB, `result`
  (`SignalResult` MATCHED | DUPLICATE | UNMATCHED | AMBIGUOUS; `ope_signal_result_enum`), `matched_order_id` (FK
  ope_order RESTRICT), `result_note`, `handled_at` / `handled_by_staff_id` / `handled_note`. CHECKs:
  `ck_ope_signal_matched_order` (matched / duplicate ⇔ order), `ck_ope_signal_handled` (all three or none),
  `ck_ope_signal_handled_unmatched` (handled only for unmatched / ambiguous). Indexes `ix_ope_signal_open` (result,
  handled_at), `ix_ope_signal_client_type`.
- Trigger `trg_ope_signal_guard` → `ope_signal_guard()`: DELETE refused; UPDATE refused once handled, or when any
  non-handled column changes (row compare with IS DISTINCT FROM). Also installed by `create_all` (models.py DDL).
- Role `signal_sender` (信号の送信): `RoleCode.SIGNAL_SENDER`, `ROLE_SEED`, migration INSERT … ON CONFLICT DO NOTHING
  (downgrade deletes it; fails if a staff still holds it). `test_staff.py::test_roles_are_seeded` updated.
- Tests `tests/test_ope_signal.py` (3; client prefix `"H"`). Frontend types regenerated.

**7.3 as built** (`src/domain_ope/signals.py`; no migration). User decisions 2026-11-04 (all as I recommended, doc rev
65): **a signal finishes only ready / in-progress orders** (pending → unmatched "not released yet"); **on hold /
conflict → unmatched** with the reason; **handle endpoint** for people. My call: **LIGHTWEIGHT orders can be finished
from ready** (受領 = mark received; no start, so no day / dependency check for them).
- `POST /ope/signals` (`@require_roles([SIGNAL_SENDER])`, `SignalIn {type, client_code, key, received_at (tz
  required), payload? (≤ 10 000 bytes JSON)}`, 201 → `SignalResponse`): client code upper-cased; `_match` = AUTOMATIC
  business with (client, signal_type) → `date.fromisoformat(key)` → non-canceled orders of that business on that
  `occurrence_date` (FOR UPDATE): none → unmatched; all finished → DUPLICATE (latest finished order); >1 open →
  AMBIGUOUS; the one open order: conflict / on hold / pending → unmatched, else MATCHED → `finish_by_signal`
  (STATUS_CHANGED → FINISHED, SYSTEM, `detail {"cause": "signal", "signal_id"}`). The signal row is flushed first so
  the event can carry its id.
- `POST /ope/signals/{id}/handle` (operator / releaser / admin, `SignalHandleIn {note, order_id?}`): only unmatched /
  ambiguous, once (400 otherwise); with `order_id` the order must be the signal's client's (404) and ready / in
  progress (400) → finished as HUMAN with `detail {"cause": "signal handled", "signal_id"}` and `reason` = note.
- `finish_order`: LIGHTWEIGHT allowed from READY or IN_PROGRESS; AUTOMATIC businesses need a non-empty comment (400).
- Tests `tests/test_signals.py` (6; client prefix `"J"`). HTTP-checked on `plan_check`: new signal_sender staff 9 / 10,
  **biz 2 `MOCK0002` is now LIGHTWEIGHT + AUTOMATIC `data_received` with daily template 23** (left that way for 7.4 /
  7.5); order 194 (11/07): signal before release unmatched, operator 403, after release matched → finished (SYSTEM,
  signal 2), resend duplicate, handle once 200 / again 400.
- Gotcha: plan template API wants `template_type: "rrule"` (lowercase value).

**7.4 as built** (no migration). User decisions 2026-11-04 (both as I recommended, doc rev 66): **late = past
`scheduled_end` and not finished / canceled**; **all orders** (not only client events).
- `links.is_late(order, now)` (one rule); `OrderResponse.late` (detail, via `ope._now`), `DailyOrderSummary.late`
  (dashboard, via `ops_services._now`; `now` computed once per request).
- `GET /ope/signals?scope=open|all&client_code=&since=&limit=` (`@require_active()`; open = unmatched / ambiguous and
  not handled; newest first; client code upper-cased) → `signals.list_signals`.
- Tests added to `tests/test_signals.py` (late with `_now` monkeypatched past the end; list scope / client filter /
  handled drops out). HTTP-checked on `plan_check`: **unmatched signal 4 (bad key) left open for 7.5's panel**; 11/02
  orders show `late: true`.

**7.5 as built** (no migration). User decision 2026-11-04: **a 「業務の設定」 panel on the template settings page**
(`/settings/templates?biz=`) holding the release settings (Phase 4, had no screen) and the client-event settings.
- Backend: `OrderDetailResponse.execution_type` / `completion_mode` (from the business).
- Frontend: `domain/biz`: types `BizMasterUpdate` / `ReleaseMode` / `ExecutionType` / `CompletionMode`,
  `bizApi.update` (PATCH), `lib/settings.ts` (labels, `clientEventError` = backend rules, `clientEventBody` drops the
  signal type unless LIGHTWEIGHT + automatic); `features/plan-settings/BizSettingsPanel.tsx` (Descriptions + 編集
  modal: release mode / days before / time, execution type, completion mode + signal type only when LIGHTWEIGHT).
  `domain/ope`: types `Signal` / `SignalResult`, `api/signalApi.ts` (listOpen, handle), `hooks/useOpenSignals.ts`,
  `lib/signals.ts` (labels, `canHandleSignals` = operator / releaser / admin); `orderActions`: 完了 offered from ready
  for LIGHTWEIGHT, `finishLabel` (受領 for LIGHTWEIGHT), `finishNeedsReason` (automatic). `OrderDrawer`: 遅れ tag,
  顧客側の出来事 tag, 受領 label, reason required + note for automatic. Dashboard rows: red 「遅れ」 tag.
  `features/daily-dashboard/SignalsPanel.tsx` above the client cards (only when there are open signals and the acting
  staff is active): table 受信 / 顧客 / 信号の種類 / キー / 結果 + note, 対応 modal (order number optional, note required).
  CSS `.biz-settings`, `.signals-panel`.
- Checked: `npm run build`; esbuild harness `ce_harness.ts` against uvicorn 8001 on `plan_check` (order 196 of biz 2,
  signal 4): settings rules, save manual / automatic, open list, 受領 offered from ready with a reason, handle with
  the order → finished and gone from the list, late on 11/02 — 14/14 passed. **Not checked: the browser.**
- Incident: a typo (`newline="\\n"`) in an edit script raised after `open(..., "w")` had already **emptied
  `frontend/src/styles.css`**; restored from HEAD with `git checkout` (no uncommitted CSS changes existed) and
  re-applied the 2 new lines. Lesson: build the full text first, then write.

### UX shortcuts (branch `feature/ux-shortcuts`, 2026-11-05; not in the design doc — UI only)
User's ideas; all choices as I recommended.
| # | Step | Status |
|---|---|---|
| U.1 | Calendar day: double-click → `/daily?date=`; 「日次ダッシュボードで開く」 link in the selected-day panel | **done** (`9b7cb38`) |
| U.2 | Dashboard rows: ⚙ link (all users, also rows without an order; not voided rows) → `/settings/templates?biz=&template=` (row highlighted + scrolled into view); 「← 戻る」 links via router state (`shared/lib/returnTo.ts`, `shared/ui/ReturnLink.tsx`): dashboard → calendar (same client / month, day re-selected via `state.selectDate`), template page → dashboard day; state kept across day / business changes, chains (calendar → dashboard → template) | **done** |
| U.3 | Template page edits need `admin` (業務管理者; not system_admin): `@require_roles([ADMIN])` on plan template writes (create / patch / delete / restore / dates / cache rebuild) and `PATCH /biz/{id}`; UI hides 新規作成 / 編集 / 削除 and the 業務の設定 編集 for non-admins (panel stays visible). The user's staff 1 has no admin role → must grant it | **done** |
| U.4 | Candidate (not approved): same guard for sheets / markers / sheet dates, holidays / calendars, remaining biz writes (create / delete / SOP versions / step templates) | — |

### Open questions (in the doc)
**Still open:** "latest before due" matching for dependencies with different cadences · client-limited role details
(name, one or several client codes per user, read-only or also its own client events) · signal transports beyond HTTP
(file arrival, another database) — the matching key is decided (type + client + rule date).
**Decided** (ticked in the doc): early start only with a releaser's approval; releaser + admin release / pin /
force-start (now: approve a dependency override) / resolve conflicts; automatic release default 03:00 JST; rejecting
a step resets only that step (2026-10-30); dependencies same client only (2026-11-04); client event = per business
(2026-11-04).

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
- **Design work** (cross-domain, for the team) goes into a Claude Docs document, not chat. Record each step's
  decisions in the doc (a short "Decided …, built in X.Y" paragraph or a data-model row) and in §2 here.
- **"go" from the user** (also typed "GO" or "ご" with the Japanese IME) after a finished step = commit it; after a
  commit = start the next step (questions first). "commit and merge" = commit, then merge the feature branch into
  `main` locally with `--no-ff` (check `main` hasn't diverged first); they push.
- **Shell / editing lessons (2026-11-04):** write edit scripts to the scratchpad with the Write tool and run them
  with `python <file> < /dev/null` — never `python - <<'EOF' < /dev/null` (the redirect replaces the heredoc →
  interactive REPL that hangs), never `sed … $f` with a possibly empty `$f` (reads stdin, hangs). Build the whole new
  text before `open(path, "w")` (a failing call after opening empties the file — happened to `styles.css`, restored
  from git). Anchors in edit scripts must be unique (`# --- System Audit Trail ---` repeats in biz models). The shell
  cwd drifts between calls: use absolute paths (`/c/p/postgres-migration-poc/...`). Backgrounded uvicorn:
  `--app-dir /c/p/postgres-migration-poc --port 8001`, stop it with TaskStop when done. Harnesses can call uvicorn
  8001 directly (`VITE_API_BASE_URL=http://127.0.0.1:8001`; the Vite proxy only strips `/api`).
- Code comments in the repo are Japanese; UI text is Japanese; replies to the user in English.
- Line endings: the ope / dashboard frontend files and `styles.css` are LF in the working tree (checked 2026-11-04;
  older notes said CRLF); backend Python files, migrations and MEMORY.md are LF. Match the file you edit. Japanese in shell arguments gets mangled (cp932): pass JSON via files and run Python with
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
content, `condition` JSONB, `is_blocking` — 5.1). Also `biz_dependency` (6.1) and the client-event settings
`execution_type` / `completion_mode` / `signal_type` (7.1); execution itself lives in ope.
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
| Business settings | `PATCH /biz/{id}` (release + client-event settings; unguarded like the rest of biz) |
| Dashboard | `GET /ops/daily?date=&timezone=` (rows carry order `{id, status, conflict, steps_total, steps_approved, blocked, late}`) |
| Orders | `GET /ope/orders/{id}` (detail: steps, dependencies, dependents, events, late, execution_type, completion_mode); `POST /ope/orders/{id}/release · approve-early-start · approve-dependency-override · start · hold · resume · finish · cancel · resolve-conflict · pin · unpin` |
| Steps | `POST /ope/steps/{id}/submit` (operator) · `/approve` · `/reject` (checker, four-eyes) |
| Dependencies | `GET /biz/clients/{code}/dependencies`, `GET /biz/{id}/dependencies`, `POST /biz/{id}/dependencies` + `DELETE /biz/dependencies/{id}` (admin) |
| Signals | `POST /ope/signals` (signal_sender), `GET /ope/signals?scope=open\|all&client_code=&since=`, `POST /ope/signals/{id}/handle` (operator / releaser / admin) |

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
- **Routes / nav:** `/` calendar (`?client=&month=&holidays=`) · `/daily?date=` 日次ダッシュボード (order drawer,
  signals panel) · `/settings/templates?biz=` (+ 業務の設定 panel) · `/settings/sheets?client=` ·
  `/settings/holidays?calendar=<id>&year=` · `/settings/dependencies?client=` 業務の依存関係 · `/settings/staff` 職員.
  Domain modules for Phases 4–7: `domain/ope/{types,api (orderApi, signalApi),hooks,lib (orderActions, stepActions,
  signals)}`, `domain/biz/{api (bizApi, dependencyApi),lib (dependencies, settings)}`.
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
