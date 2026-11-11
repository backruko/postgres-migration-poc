# MEMORY.md — Session handoff

Handoff for the next working session. Read this first, then the design doc (§2), `deploy/README.md` (server runbook),
`frontend/README.md` (frontend details), `src/domain_plan/`, `src/coordination/`, `src/domain_ope/`, `src/domain_biz/`,
`src/domain_client/` and `src/domain_staff/` (backend). Last updated: **2026-11-11 (late)** — **batch order creation
is built on `feature/batch-orders` (B.1–B.5 committed, not merged, not pushed)**; Level 2 is merged into `main`
(`5e1af2f`). See the first block of §1. The MVP pilot is postponed.

## 1. Current status

### Batch order creation (branch `feature/batch-orders` from `main` `2038e4b`, 2026-11-11) — start here

- **Status: B.1–B.5 committed on `feature/batch-orders`, not merged, not pushed** (`0ef890c` B.1 · `cee8ee2` B.2 ·
  `471f683` B.3 · `1cee5c5` B.4 · + the B.5 handoff commit). Next: the user reviews / merges ("commit and merge"
  merges into `main` like Level 2); then the next phase is open (finer permissions for SOP drafts + release
  settings; the pilot — rebuild the USB zip first, the tree is clean). No migration in this phase.

- **User chose it 2026-11-11** over finer permissions / the pilot (USB zip: rebuild later; the user's README is now
  committed as `2038e4b`, so the tree is clean). Design doc unchanged since rev 125, no new comments.
- **Decisions (all my recommendations):** creation **and** cancellation; a new batched `links.refresh_orders(db,
  orders)` for `order_sync` (single `refresh_order` stays for the ope services paths); proof = oracle tests (batched
  vs per-order links identical: same date / same sheet event / latest before, both sides, cancelled-upstream
  fallback) + statement counts; `create_missing_orders` reuses the batched path.
- **Steps:** B.1 batched creation without links · B.2 batched links for new orders · B.3 batched cancellation · B.4
  go-live script · B.5 checks (suite, probe before/after, HTTP on `plan_check` 8001) + MEMORY.
- **Probe before (test DB, 2-year daily, MO/WE/FR ↔ DAILY, 417 orders), create / cancel:** no dependency 1.25 s /
  1,270 stmts · 0.28 s / 22; downstream same-date 2.61 s / 2,521 · 0.68 s / 854; downstream latest-before 2.59 s /
  2,521 · 0.79 s / 854; upstream same-date 3.20 s / 3,355 · 2.61 s / 2,939. Probe = untracked
  `tests/test_zz_probe_creation.py` (never commit; delete in B.5).
- **B.1 committed:** cause = per order `db.get(BizMaster)` re-read (**the session's identity map is weak**: nothing
  held the business between orders, so it was collected and re-selected) + a flush per order (INSERT order, INSERT
  event). `order_sync.create_orders(db, items: list[NewOrder])`: template + business once per template (held), add
  all → one flush (multi-row INSERT … RETURNING), events → one flush; `_apply` appends to `new_orders`,
  `apply_slot_changes` creates after the loop; `create_order` = batch of one. Links still per order (B.2). No
  dependency: **1,270 → 22 statements, 1.25 → 0.50 s.** Test `test_a_rule_change_creates_hundreds_of_orders_…`
  (fails on old code: 706 statements for 228 orders). Suite 279 passed.
- **B.2 committed:** `links.refresh_orders(db, orders)` (downstream: `resolve_many` per definition of the order's
  business; upstream: per upstream definition, the downstreams already waiting on the new orders
  (`get_active_downstream_orders`) + the rule's candidates (`_downstream_candidates`: keys in one query, latest
  before = one range query then `bisect` on the windows), each definition's affected set resolved once).
  `resolve_many` = current links (`get_active_for_definition`) + upstream candidates (`_targets_for_many`: keys in one
  query / latest before: one range query + `bisect`, tie = latest start then highest id) → shared `_apply_targets`
  (also used by `resolve`), one flush. Oracle tests `tests/test_links_batched.py` (5 scenarios, two worlds, links incl.
  removed + reason normalised): a mutated cancelled fallback fails it. Count test both sides (B.1 code: 709 / 1,396).
  **Create with a dependency: 2,521–3,355 → 25–27 statements, ~1 s** (of which ~0.45 s SQL; same as no dependency —
  the rest is plan materialization + ORM objects). Test DB slower that day (unchanged cancel 0.28 → 0.43 s). Suite 286.
- **B.3 committed:** `_cancel` appends to `_Deferred.cancelled` (holder with `new` / `cancelled`, passed `_apply` →
  `_slot_removed` → `_cancel`) instead of `refresh_order` (its flush split UPDATE + event INSERT per order);
  `apply_slot_changes` runs `links.refresh_orders(cancelled)` once after `create_orders`. **Cancel 417 with a
  dependency: 854 / 2,939 → 22 / 26 statements, ~0.45 s.** 2 more oracle scenarios (downstream cancel, latest-before
  upstream cancel); count test none / downstream / upstream (B.2 code: 478 / 1,623). Suite 291. **Left as is (told the
  user):** moved orders with a LATEST_BEFORE definition still refresh per order (`_slot_moved`).
- **B.4 committed:** `create_missing_orders` groups future instances per template → `_find_orders` (one query) →
  `create_orders` (links via `refresh_orders`), inside `definitions_cached`, one commit; idempotent. Removed the
  unused `_find_order` / single `create_order` (`ope_order_repo.get_active_for_slot` kept: its docstring is the rule
  `get_active_for_slots` refers to). Oracle test `test_go_live_creation_in_batches_equals_one_by_one` (listeners off,
  old one-by-one loop copied into the test, limited to its world). **Probe: 3 two-year daily templates + a
  dependency, 2,190 orders: 22.5 s / 18,251 statements → 2.5 s / 29.** Suite 292.
- **B.5 (checks + handoff):** suite **292 passed**; `npx pyright@1.1.400 …` **0 errors**. HTTP on `plan_check`, admin 8
  `PATCH /plans/templates/23` (biz 2 daily, materialized to 2028-11, no dependency) DAILY ↔ MO/WE/FR, old code
  (`main` `2038e4b` in a temporary worktree, uvicorn 8002) vs new (8001), two rounds each: **create 1.85–2.82 s →
  0.45–0.79 s**, cancel 0.40–0.48 s → 0.32–0.52 s (no dependency → already batched before). Template 23 restored to
  `FREQ=DAILY`; worktree removed; probe deleted. Manual / GO-LIVE quote no timings → unchanged; design doc unchanged
  (performance only, same behaviour). Harness script: scratchpad `batch_http.py` (per session).

### Handoff 2026-11-11 — start here

- **`main` = merge `5e1af2f` of `feature/template-drafts` (2026-11-11) + this handoff commit; nothing pushed — the user
  pushes (`origin/main` = `6390c87`).** The branch started from `main` `306f34a`.
  Commits, oldest first: `67d0d81` D.1 · `bab603f` D.2 · `05a5acb` D.3 · `6dc1b15` D.4 · `34f37f8` D.5a · `98ad5d6`
  D.5b · `a7f2697` **perf: order-sync N+1** · `c8c370f` D.6a · `77afd4f` D.6b · + the D.7 docs commit (manual,
  GO-LIVE, MEMORY) `39c0250`. **Merged with "commit and merge" (user, 2026-11-11).** USB zip NOT rebuilt:
  `scripts/make_package.py` refuses while tracked files are dirty, and the user's `README.md` is uncommitted — rebuild
  after the user commits (or drops) it, before any server install. Next phase: not chosen yet (see "Open / offered").
- **Migration `e7f9acb3d34a`** (`plan_template_change`) — the user ran `alembic upgrade head` on the dev DB
  (2026-11-11); the server's `app` container migrates at start. Alembic head is now `e7f9acb3d34a`.
- **Checks at the end:** backend **278 tests** pass, pyright 0, `npm run build` passes. The user browser-tested the
  releaser flow, approval (after the N+1 fix: "lightning fast"), the stuck-dialog fix, the TimePicker and the
  no-change deadlock fix.
- **Docs:** `docs/OPERATION-MANUAL.md` (1.4 roles, 1.5 screens, 1.6 refresh, 3.7 「下書きと承認」 + 変更履歴 + API, 4.3
  error codes `TEMPLATE_DRAFT_PENDING` / `TEMPLATE_CHANGE_DECIDED` / `TEMPLATE_CHANGED_MEANWHILE`, 5 QA rows 17–19, 6
  limits); `deploy/GO-LIVE.md` D role table + checklist line "an admin to approve drafts". Every screen label quoted
  in the manual was grepped in the frontend. Design doc **rev 125** (Level 2 marked built and merged; finer-permissions item
  updated: template part done, SOP drafting + release settings still open).
- **Open / offered, not decided:** (1) ~~batching order creation~~ — **built on `feature/batch-orders`** (block
  above); (2) finer permissions for SOP drafts and release settings (doc item);
  (3) biz delete doesn't lock the biz row (narrow race with a new-template draft, same as the existing template
  check — the user said leave it).
- **`README.md` is the user's** (Japanese project description; the user committed it as `2038e4b`) — never include
  README edits in my commits; leave it to the user.
- **`plan_check` state:** template 23 (biz 2, daily) materialized ~2 years (for profiling); many withdrawn /
  rejected `HARNESS_*` change rows; all `HARNESS_*` templates deleted. jsdom test kit in the scratchpad
  `domtest/` (jsdom 24 + @testing-library/react 14, outside the project) — see the D.6 notes below for the bundling
  flags; scratchpads are per session, so re-install if needed.
- **Lessons from this phase (also in the step notes below):** `get_locked(... populate_existing)` discards unflushed
  changes → flush first; JSONB `None` needs `none_as_null=True` for `IS NULL` checks; antd doesn't re-render a
  closed modal's content → never nest a stateful modal unless the outer has `forceRender`; a flag set before an
  early `return` must be cleared on every path; order follow-up must stay batched (regression test counts
  statements); profile with a statement counter before guessing at locks.

### Level 2 step log (2026-11-11)

- **Branch `feature/template-drafts` from `main` `306f34a`.** Step plan approved (user, 2026-11-11), one step at a
  time, stop for review after each: **D.1** data model (migration + model + repo) · D.2 drafts in the backend
  (create / edit / withdraw, releaser + admin; checked like a real save; one pending draft; template writes refused
  while one is pending) · D.3 approve / reject (admin; applies through the existing `_apply_*`; no revision check;
  self-approval; admin direct saves write an `is_direct` row) · D.4 read endpoints (pending by business / all + count,
  history per template, preview of a stored draft; OpenAPI + `npm run gen:api`) · D.5 frontend for drafters
  (下書きとして保存, draft badge, blocked edit state) · D.6 frontend for admins (approval window with the current
  preview, 承認 / 差し戻し with a reason, 承認待ち list + menu badge, history drawer) · D.7 harness on `plan_check` +
  manual + design doc + MEMORY.
- **User's decisions 2026-11-11 (all my recommendations accepted):** Q1 data model **A** = a change-request table
  next to the live template (not SOP-style version rows); Q2 a releaser drafts **every** template change (create,
  any field incl. name / display, specified-date add / remove, delete; one draft holds fields + dates); Q3 admin
  direct saves are recorded too (`is_direct`, drafted = approved by the same admin) → full change history; Q4 a person
  holding admin counts as admin → may self-approve, history marks it self-approved (derived: approved, not direct,
  decided_by = drafted_by); Q5 a pending draft blocks admin direct edits, other drafts, date changes and delete of
  that template — not single-occurrence changes, sheets or calendars; drafter edits / withdraws, admin approves or
  rejects with a reason and **does not edit** the draft; Q6 the draft holds settings, not dates — the approval window
  re-runs the preview on current data, approval fails with the normal save errors if a referenced sheet / marker /
  calendar is gone; **no revision check at approval** (user chose A before D.2: `revision` also rises on holiday /
  calendar / sheet recalculation, which drafts do not block — so the blocking rule is the guard and `base_revision` is
  a record only; a separate settings counter was rejected as overengineering); Q7 承認待ち list on 予定の設定 + admin menu count badge, no
  notifications. **Releasers do not draft SOPs** in this phase (SOPs have their own versions). Design doc rev 110.
- **D.5 = the drafter screens, split D.5a (data + logic) / D.5b (screens).** User's decisions 2026-11-11 (all four
  as I recommended): **Q1 admins keep direct save only** (no draft button); **Q2 the releaser's draft flow mirrors the
  admin's save** (edit that changes dates → the existing 保存の前の確認 window, button この内容で下書きを保存;
  name / deadline / display edits save the draft directly; new template: optional 予定を確認 + 下書きとして保存;
  fields + dates in ONE draft call); **Q3 rows with a pending draft**: 承認待ち tag (who / when), 編集 / 削除 disabled
  for everyone with a tooltip (server would 409), 下書きを見る window (summary now → proposed, who / when; drafter
  also 直す (form prefilled with the proposed values, save = PUT) / 取り下げる; D.6 adds 承認 / 差し戻し there),
  releasers get 削除を依頼 on rows without a draft; **Q4 a 変更依頼 section above the table** for the selected
  business: its pending drafts of every kind + my own drafts decided in the last 30 days (with 差し戻し reasons).
- **D.6 = the admin screens, split D.6a / D.6b.** User's decisions 2026-11-11 (all as I recommended): **Q1 a new
  承認待ち page in the 設定 menu** (all businesses, oldest first, client filter, row → approval window, link to the
  business's template page); **Q2 a pending count badge for admins only** on the 設定 button and the 承認待ち entry,
  refreshed every 2 min while the tab is visible and after a decision (`useAutoRefresh`); **Q3 extend the
  下書きを見る window** (auto preview for pending drafts, admins + releasers; admin 承認 with an optional note and a
  self-approval line, 差し戻し with a required reason); **Q4 a 履歴 button per template row → side drawer** (newest
  first, approved changes compared with `before_values`, direct saves marked). D.6b then D.7 (manual + doc + MEMORY).
- **D.6b committed (frontend only; user browser-tested, incl. the two fixes below):**
  - `domain/plan/lib/templateChangeEvents.ts`: tiny store (`notifyTemplateChangesChanged`, `subscribe…`, `version`);
    `templateChangeApi` create / edit / withdraw / approve / reject notify after success (`notifying()`).
    `useTemplateChanges.ts`: `useTemplateChangesVersion()` (`useSyncExternalStore`), `usePendingDraftCount(enabled)`
    and `usePendingDrafts()` (both + `useAutoRefresh` 2 min); `useBizChangeRequests` now also reloads on the version.
  - `AppLayout`: 設定 menu entry **承認待ち** (`/settings/approvals`, after 予定テンプレート設定); for admins
    (`templateWriteMode === "direct"`) an antd `Badge` with the pending count on the 設定 button and on the menu entry
    (`.nav-badge`); the hook is called before the layout's early returns with `enabled` false until admin.
  - `features/plan-settings/ApprovalsPage.tsx`: all pending drafts oldest first, client filter `?client=`, columns
    書いた日時 / 業務 (link `/settings/templates?biz=&template=`) / テンプレート / 種類 / 書いた人 / 見る; 見る loads the
    live template (`templateApi.list(biz)`) then opens `DraftViewModal`; its 直す navigates to the template page.
  - `TemplateHistoryDrawer.tsx` + 履歴 button on every template row (everyone; actions column 320 px): history newest
    first in a `Collapse` (header: time, kind, 直接の保存 / status, self, drafter → decider; body: decision note +
    summary table with `summaryBaseline` → 変更前 for approved). **Bug found by the jsdom check and fixed:** the
    newest entry never opened — `defaultActiveKey` is read on the first render only, which happened with no rows
    while loading; now the Collapse renders once rows exist, keyed by the newest id.
  - **Also fixed in `DraftViewModal` (D.6a race, found by the jsdom check):** for one render after opening, no
    preview had started yet (`running` 0, `loading` false), so 承認する was briefly enabled before the effect.
    Now `previewPending` = showPreview && (loading || running > 0 || no data and no error).
  - Checked: `npm run build`; jsdom check `domtest/d6b_dom.tsx` with the real `AppRoutes` in a `MemoryRouter` on
    `plan_check` **13/13** (admin badge = count, page rows + link, 見る → window with preview, approve → row gone and
    badge −1 at once, no dialog left, client filter empty state, history 3 entries newest first + newest open with
    変更前, releaser: no badge + "admins decide" text); `approve_dom` / `reject_dom` still pass. Cleanup helper
    `cleanup_d6b.py` (withdraws HARNESS_D6B* drafts, deletes the templates) — a failed jsdom run leaves them behind.
  - **User's browser test, two more issues (fixed before committing D.6b):**
    1. **TimePicker needed an OK click** (users forgot it → the value did not change). All five TimePickers
       (`TemplateFormModal` ×2, `BizSettingsPanel`, `ScheduleFields` ×2) now have `needConfirm={false}`: picking applies at once.
    2. **Deadlock on "no changes" in the releaser form:** `onFinish` sets `submitting` before the preview request,
       then `save` → `saveDraft` returned early on a null body (「変更がありません」) **without clearing `submitting`** →
       form and キャンセル disabled, and reopening did not reset it. Fix: `saveDraft` clears `submitting` whenever the
       body is null; the open effect also resets `submitting` / `checking`. jsdom check `domtest/form_dom.tsx` (draft
       mode, releaser): **9/9** (no-change warning with usable キャンセル / 保存 / fields, X + reopen clean, an hour
       picked without OK applies, the saved draft carries it); **against the old form it fails exactly like the
       user's report** (キャンセル / fields disabled, still after reopen). Cleanup helper `domtest/cleanup_form.py`.
- **D.6a committed `c8c370f` (frontend; the N+1 fix is its own commit `a7f2697`):**
  - `templateChangeApi.approve(id, note?)` (trims; blank → null) and `reject(id, reason)`.
  - `templateDraft.summaryBaseline(change, current)` → `{template, label: "変更前" | "今"}`: approved changes use
    `before_values` (incl. `dates`, works when the template is gone), others the current template.
  - `DraftViewModal` rewritten: preview of pending drafts loaded on open for `canPreviewPlanSettings` (admin +
    releaser) via `templateChangeApi.preview` with `TemplatePreviewPanel` (create / update modes, 3 / 6 / 12 months);
    a preview error is shown as "このままでは承認もできません"; admins (`templateWriteMode === "direct"`) get 差し戻す
    (reason required, OK disabled while blank) and 承認する (optional note; warning line when it is their own draft;
    disabled while the preview loads) in a small inner modal; decisions reload via `onChanged`; summary column title
    = the baseline label. CSS `.draft-preview`.
  - **User's browser test: "loading is too long when approving with a comment".** Measured on `plan_check`: approve
    0.07–0.45 s ≈ a direct save, so approval itself is fine; **a running preview of the same template holds the
    template row lock (dry run, FOR UPDATE) and approve waits for it** (approve during a 12-month preview: 3.36 s vs
    0.45 s). Switching 3 / 6 / 12 months starts a new preview while the old one still runs on the server. Fix in
    `DraftViewModal`: count in-flight previews (`running`), 承認する disabled until all have returned (stale results
    ignored); while approving, an info line 「反映しています…」 and the dialog can't be cancelled / closed. (React
    StrictMode is on, but it double-runs effects only on mount, and this window is mounted before it opens — not a
    cause.) **The user retested: still ~10 s (MOCK0002, rrule) and asked for the backend root cause** → next bullet.
  - **Root cause found and fixed (backend, `order_sync` N+1):** a template change moves every slot in the generated
    range (the calendar extends `materialized_until` up to 2 years → a daily template = 730 slots), and
    `order_sync._apply` ran 3 queries per slot: `live_instance_for_slot`, `get_active_for_slot` (FOR UPDATE) and
    `links.refresh_order` → `biz_dependency_repo.get_for_biz` (same business every time). Profile on `plan_check`,
    template 23 materialized 730 days: **approve 5.48 s / 2,211 statements → 0.67 s / 24**; preview 5.58 s → 0.48 s.
    With a SAME_OCCURRENCE_DATE dependency on the downstream template the per-order link refresh (+ a `db.flush()`
    per order that broke the batched UPDATE / INSERT) cost 4.1 s / 2,942 → **0.82 s / 24**. Fix:
    `slot_events.live_instances_for_slots` (one query per template; same matching as the single version),
    `ope_order_repo.get_active_for_slots` (one FOR UPDATE query per template, ordered by id),
    `order_sync.apply_slot_changes` prefetches both and runs inside `links.definitions_cached(db)` (definitions read
    once per business per follow-up, kept in `db.info`), `_apply(db, change, now, live, order)`; `_find_order` kept
    for `create_missing_orders`; `links.refresh_order(..., only_time_based=True)` from `_slot_moved` when only times
    changed — re-resolves only LATEST_BEFORE definitions (same-date / same-sheet-event links depend only on the slot
    key, which a time change never alters). The preview holds row locks by design (it runs the real save and rolls
    back — Level 1 decision), now only ~0.5 s. HTTP on `plan_check`: approve 0.56 s, 12-month preview 0.81 s (was
    2.66 s). **Still per order (not fixed, told the user):** creating / cancelling hundreds of orders (a rule change,
    e.g. MO/WE/FR → DAILY creates 417 orders: 1.2 s, with a dependency 3.4 s) — one INSERT per order for its id + link
    resolution per order. Regression test `tests/test_order_sync_batching.py` (2; < 60 statements for ~400 moved
    orders, links unchanged): **fails on the old code (1,221 / 2,819 statements)**, passes now. **User confirmed on
    the dev DB: "lightning fast".**
  - **Bug found by the user after that: the approve dialog stayed on 「反映しています…」 after success and could not
    be closed.** Reproduced in jsdom (scratchpad `domtest/`: jsdom 24 + @testing-library/react 14 installed there, NOT
    in the project; `dom_setup.ts` + `approve_dom.tsx` / `reject_dom.tsx`, esbuild with react / react-dom / antd
    aliased to `frontend/node_modules`, `--external:jsdom`, run with `NODE_PATH=domtest/node_modules`). Cause:
    **antd / rc-dialog does not re-render a closed modal's content (`MemoChildren shouldUpdate = visible ||
    forceRender`)**; the decision modal was nested inside the 下書きを見る modal, both closed in one render, so the
    inner one froze with busy=true. Fix: render the decision modal as a sibling (fragment). `TemplateFormModal`'s
    nested preview modal is safe because the outer one has `forceRender`. **Lesson: never nest a modal whose state
    changes while the outer modal closes, unless the outer has `forceRender`.** jsdom check after the fix: approve
    and reject both close everything, server status approved / rejected. Full suite 278
    (276 + 2) — the suite itself got faster (2 m 54 s vs ~4 m). `plan_check` template 23 is now materialized ~2 years.
  - Checked: `npm run build`; harness `d6a_harness.ts` on `plan_check` **19/19** (releaser 403 on approve, releaser
    preview, approve with trimmed note, applied at once, approved baseline = 変更前 (today's values would show
    06:50→06:50), dates baseline + summary, blank reject 422, reject with reason, rejected baseline 今, self-approved,
    approved delete summarised from before values after the template is gone, history order). **The window itself
    (preview panel inside, inner modals, buttons) was not rendered — the user clicks through.**
- **D.5b committed `98ad5d6` (frontend only; user browser-tested the releaser flow on the dev DB):**
  - `TemplateFormModal`: prop `canSave` → **`mode: "direct" | "draft" | "preview"`** + optional `draft`
    (`TemplateChange`; opens prefilled via `formValuesOfDraft`, save = `templateChangeApi.edit`). Draft mode: titles
    「予定テンプレートの変更を下書き」 / 「新しい予定テンプレートを下書き」 / 「下書きを直す」, info `DRAFT_NOTE`, button
    下書きとして保存, the confirmation window's button この内容で下書きを保存; `saveDraft` = `updateDraftBody` /
    `createDraftBody` in ONE call, "変更がありません" when null, errors via `draftErrorText`, success
    `DRAFT_SAVED_MESSAGE`. Same preview rule as admins (window only when `generation_changed`). Admin path unchanged.
    **User's browser test (MOCK0002, dev DB):** a wrong time was drafted, then 直す set it back to the live value →
    「変更がありません」 and the useless draft stayed pending. Fix (user approved the wording): when 直す makes the
    draft equal to the live template, `saveDraft` asks 「今の設定と同じになりました。この下書きを取り下げますか？」
    (取り下げる / 戻って直す) and withdraws on OK. Checked on `plan_check`: a +1 h draft on all 19 templates saves,
    setting the time back gives `updateDraftBody` → null on all 19; the dialog itself only in the browser.
  - `PlanSettingsPage`: `writeMode = templateWriteMode(me)`; name cell gets a gold 承認待ち tag (tooltip: drafter +
    time from the loaded pending list); actions column always shown: 下書きを見る (anyone, when pending), direct →
    編集 / 削除, draft → 変更を下書き / 削除を依頼 (confirm → delete draft); all four disabled with the
    `pendingDraftReason` tooltip while pending. New button 新しいテンプレートを下書き for releasers. Header note
    「変更は下書きとして保存し、業務管理者が承認します」. Admin delete error maps TEMPLATE_DRAFT_PENDING to Japanese.
  - New `ChangeRequestsPanel.tsx` (the 変更依頼 section above the table; hidden when empty; pages of 10 when > 10
    rows), `DraftViewModal.tsx` (状態 / 業務 / 書いた人 / 決めた人 / 理由, summary table 項目・今・下書きの内容 with
    calendar + sheet names, drafter's 取り下げる (confirm) and 直す; "今 = the current value" note for decided ones;
    D.6 adds 承認 / 差し戻し here), hook `domain/plan/hooks/useTemplateChanges.ts` (`loadBizChangeRequests` +
    `useBizChangeRequests`: pending of the biz + my drafts decided within 30 days). CSS `.change-requests`,
    `.draft-tag`, `.draft-meta`, `.draft-after`. The sheet page keeps the releaser check-only mode (sheets are out of
    Level 2).
  - Checked: `npm run build`; harness `d5b_harness.tsx` (esbuild `--jsx=automatic` + react / react-dom aliases to
    `frontend/node_modules`, `--loader:.css=empty`) on `plan_check` **19/19**: each button's API path (変更を下書き with
    and without the window, 直す, 削除を依頼, 新しいテンプレートを下書き, 取り下げる), admin 409 texts, the panel data
    (pending, rejected reason, 30-day cut) and **the panel rendered with `react-dom/server`** (SSR inserts `<!-- -->`
    between text parts — strip before matching). D.5a harness still 33/33. **No jsdom here → the modals, tooltips
    and disabled buttons were not rendered; the user clicks through.** `plan_check` releaser 5 now has ~27 decided
    drafts on biz 1 from harness runs.
- **D.5a committed `34f37f8`.** As built (frontend only, no visible change):
  - `domain/plan/types`: `TemplateChange`, `TemplateChangeKind`, `TemplateChangeStatus`, `TemplateDraftInput`
    (create / update / delete union), `TemplateUpdateDraftInput`, `TemplateDraftPreview`.
  - `domain/plan/api/templateChangeApi.ts` (exported from `api/index.ts`): `list(filter)`, `count(filter)`, `get`,
    `history(templateId)`, `create`, `edit`, `withdraw`, `preview(changeId, months)`.
  - `domain/plan/lib/templateChanges.ts`: `CHANGE_STATUS_LABEL` (承認待ち / 承認済み / 差し戻し / 取り下げ) +
    colors, `CHANGE_KIND_LABEL` (新規作成 / 変更 / 削除), `templateWriteMode(staff)` → direct (admin, also admin +
    releaser) / draft (releaser without admin) / none, `canChangeOwnDraft`, `pendingDraftReason(template)`,
    `decidedWithin(change, now, 30)`, `draftErrorText(e)` (Japanese for TEMPLATE_DRAFT_PENDING / CHANGE_DECIDED /
    CHANGED_MEANWHILE / FORBIDDEN / "changes nothing").
  - `features/plan-settings/templateDraft.ts`: `changedFields(updateInput, template)` (keeps only differing fields),
    `updateDraftBody(form, template)` (null when nothing changes; dates via `diffDates`), `createDraftBody`,
    `deleteDraftBody`, `proposedTemplate(change, template)`, `templateOfCreateDraft(change)`,
    `formValuesOfDraft(change, template|null)` (prefill for 直す), `changeSummary(change, template, names?)` →
    `{label, before, after}` lines (deadline fields grouped into one 締切 line; dates 指定日を追加 / 外す; create =
    main fields with before "—"; delete = one line; sheet / calendar names via optional resolvers).
  - **Found and fixed while checking:** an untouched form looked like a display change on 10 of the 19 `plan_check`
    templates — the form always sends `ui_metadata.category`, older templates have none (or `ui_metadata` null).
    `comparable()` now compares display settings with `categoryOf()` (missing = 定例).
  - Checked: `npm run build`; esbuild harness `d5a_harness.ts` (scratchpad, imports `cookie_fetch.ts` first) against
    uvicorn 8001 on `plan_check`, releaser 5 / admin 8: **33/33** (write modes, only changed fields, draft written,
    pending_change_id + reason, summary lines, prefill + round trip, preview, edit, 409 + Japanese text, dates draft,
    create draft round trip + summary + preview, delete summary, list / count / get, withdraw + decided text,
    history); `d5a_all.ts`: unchanged form → no draft on **all 19** templates. Harness templates `HARNESS_D5A_*`
    deleted at the end; its drafts withdrawn.
  - **`README.md` is modified in the working tree by the user (project description in Japanese) — not mine; leave it
    out of my commits.**
- **D.4 committed `6dc1b15`.** As built (no migration). User's decisions before D.4 (both as I
  recommended): **reads open to any logged-in active staff** (like every `/plans` GET; `test_reads_need_a_login_but_no_role`
  covers them); **the approval preview covers every draft kind** (fields, specified dates, delete, create).
  - Reads (`src/domain_plan/router.py`): `GET /plans/template-changes?status=&biz_id=&template_id=&drafted_by=&limit=`
    (1–500, default 100; **status=draft → oldest first** = approval queue, otherwise newest first by
    coalesce(decided_at, drafted_at)) · `GET /plans/template-changes/count?status=draft&biz_id=&drafted_by=` →
    `{count}` (default status draft; declared before `/{change_id}`) · `GET /plans/template-changes/{id}` ·
    `GET /plans/templates/{id}/changes` (history newest first; deleted templates readable; unknown → 404).
  - `PlanTemplateChangeResponse` + `drafted_by_name`, `decided_by_name`, `client_code`, `business_code`,
    `business_name`, `template_name` (current name; for an unapproved create = the proposed `changes.name`), filled by
    `template_changes.describe(db, changes)` (bulk: one query each for staff, biz incl. deleted, templates incl.
    deleted). **Every draft endpoint (writes too) now returns described rows.** Repo `get_list` / `count` (shared
    `_filtered`).
  - `PlanTemplateResponse.pending_change_id` (null = none), set in `_with_next_occurrence` via
    `get_draft_template_ids` (one query per list).
  - Preview: `POST /ops/template-changes/{id}/preview?months=1..12` (releaser + admin = `PREVIEWERS`) →
    `TemplateDraftPreview {change_id, kind, create: TemplateCreatePreview | null, change: TemplateChangePreview | null}`;
    only DRAFT (else 409 `TEMPLATE_CHANGE_DECIDED`). `coordination/template_preview.py`: diff core extracted to
    `_preview_change(db, template_id, months, apply)` (`apply(since, until)`; one `now` for before and after);
    `preview_template_draft` applies like approval (fields → dates, or delete). Plan: `dry_run_update` gained
    `add_dates` / `remove_dates` (skips the field update when no field is set); new public `dry_run_delete`.
  - `test_template_preview.py` route count of `/ops/*/preview` 2 → 3. OpenAPI exported + `npm run gen:api` (also
    brings D.2's error codes into `schema.gen.ts`); `npm run build` passes. Avoid `Result.tuples()` (deprecated in
    SQLAlchemy 2.1 — it raised 12 warnings).
  - Checked: `tests/test_template_change_reads.py` 9 (list order / filters / count / names, history after delete,
    pending_change_id, **preview == approval result** for update, dates, delete, create; decided → 409; route roles);
    full suite **276 passed**; pyright 0. HTTP harness `reads_harness.py` on `plan_check` **21/21** (operator reads,
    401 without login, operator preview 403, dates / create / delete previews, approval matches the preview, own
    template `HARNESS_READS` deleted at the end via an approved delete draft; the create draft withdrawn).
- **D.3 committed `05a5acb`.** As built (no migration). User's decisions before D.3: **A = pending
  drafts block deleting their business**; all my calls approved (below).
  - `POST /plans/template-changes/{id}/approve` (admin; body `TemplateChangeApprove {note?}` optional — a POST with no
    body works) → `template_changes.approve_draft`: locks the draft, then (update / delete) the template; applies via
    `_apply_template_create` / `_apply_template_update` (if fields) + `_apply_explicit_dates` / `_apply_template_delete`
    in one transaction (orders follow like a direct save); then sets `template_id` (create), `before_values`,
    `applied_revision`, status, decided_by / at, note **all at the end** (setting `before_values` early made autoflush
    write a DRAFT row with before values → CHECK violation; the constraints caught it). A failing apply → nothing
    changes, draft stays DRAFT (tested by making the dates step fail after the field update). No revision check.
    Self-approval allowed (`self_approved` in the response).
  - `POST /plans/template-changes/{id}/reject` (admin; `TemplateChangeReject {reason}` required, stripped, 1–500) → REJECTED,
    template untouched. Decided drafts → 409 `TEMPLATE_CHANGE_DECIDED` for approve / reject too.
  - Direct saves: `create_template` / `update_template` / `delete_template` / `add_explicit_dates` /
    `remove_explicit_date` take `actor_id: int | None = None`; the API passes `current_staff.id` → `_record_direct_change`
    writes an APPROVED `is_direct` row (drafted = decided = admin, `base_revision` + `before_values` for update / delete,
    `applied_revision`). Rows only when something changed (`template_settings` before ≠ after; dates: only the new ones
    are listed). No actor (tests, service calls) → no row. Restore records nothing.
  - `coordination/master_guards.business_template_drafts_pending` → biz delete refused: "N template draft(s) waiting
    for approval; approve, reject or withdraw them first". (Existing narrow race, same as the template check: biz delete
    doesn't lock the biz row.)
  - **Bug found by the HTTP harness and fixed here (it was in committed D.2):** a draft changing only the name (or only
    display / deadline-warning fields) was refused as "changes nothing". Sessions run with `autoflush=False`; a
    name-only update writes nothing, and the following `_apply_explicit_dates([], [])` re-locked the template with
    `populate_existing`, which reloaded the row and dropped the unflushed rename. Fix: `_apply_template_update` ends with
    `db.flush()`; `_apply_explicit_dates` returns early when there is nothing to add / remove. **Lesson: any
    `get_locked(... populate_existing)` after unflushed changes silently discards them — flush first.**
  - Checked: `tests/test_template_approval.py` 14 (update / create / delete / dates approvals with orders following;
    name / display / warn-only drafts applied (regression); self-approval; failing approval atomic; reject; decided →
    409; direct rows only on change and only with an actor; dates rows list only new dates; biz delete refused while a
    draft waits; approve / reject admin-only); full suite **267 passed**; pyright 0. HTTP harness `approval_harness.py`
    on `plan_check` **16/16** (its own template `HARNESS_APPROVAL`, deleted at the end through an approved delete
    draft; the aborted first run's template 53 deleted by admin DELETE).
- **D.2 committed `bab603f`.** As built (no migration):
  - Endpoints (releaser + admin, login): `POST /plans/template-changes` (body switched by `kind`: `create` +
    `template` = POST body / `update` + `template_id`, `changes` = PATCH body, `add_dates`, `remove_dates` / `delete` +
    `template_id`) → 201 `PlanTemplateChangeResponse` (`self_approved` computed); `PUT /plans/template-changes/{id}`
    (drafter replaces the whole draft; kind + template fixed → 400; delete draft → 400 "nothing to edit");
    `POST /plans/template-changes/{id}/withdraw` (drafter). Not the drafter → 403 (admins too: they reject in D.3);
    decided draft → 409 `TEMPLATE_CHANGE_DECIDED`.
  - `src/domain_plan/template_changes.py`: `_check_draft` runs the real `_apply_template_create` /
    `_apply_template_update` + `_apply_explicit_dates` / `_apply_template_delete` inside `dry_run` (rolled back; the
    `after_rollback` hook of slot_events drops pending slot changes, so nothing reaches orders); "changes nothing"
    = `template_settings()` equal before / after → 400. The dry-run rollback releases the row lock, so before saving it
    re-locks the template, re-checks pending drafts and compares `template_settings` with the checked state → 409
    `TEMPLATE_CHANGED_MEANWHILE`. A unique-index race on commit → 409 `TEMPLATE_DRAFT_PENDING`. Stored `changes`:
    create = `model_dump(mode="json")`, update = `exclude_unset` (reads back with `model_validate`, tested).
  - `services.py`: `refuse_if_draft_pending(db, template_id, except_change_id)` (409 `TEMPLATE_DRAFT_PENDING`, names
    the change id + drafter) called under the template row lock by `update_template`, `delete_template`,
    `add_explicit_dates`, `remove_explicit_date`, `restore_template`. New non-committing `_apply_explicit_dates(add,
    remove)` (one regeneration) and `_apply_template_delete` (the public functions use them; behaviour unchanged).
    `template_settings(template)` = JSON snapshot of `TEMPLATE_SETTING_FIELDS` (biz, type, every PATCH field) + `dates`
    for specified-dates templates — D.3 uses it for `before_values`. Single-occurrence changes, one-offs, preview,
    sheets and calendars are not blocked.
  - New error codes `TEMPLATE_DRAFT_PENDING`, `TEMPLATE_CHANGE_DECIDED`, `TEMPLATE_CHANGED_MEANWHILE` (frontend
    `schema.gen.ts` not regenerated yet — OpenAPI export + `npm run gen:api` is planned for D.4).
  - Checked: `tests/test_template_drafts.py` 13 (nothing changes: settings, revision, instances, voids, orders, order
    events; errors equal the real save; one pending draft blocks drafts + PATCH / DELETE / dates; single-occurrence
    changes not blocked; edit / withdraw rules; route roles); `test_plan_settings_guard.py` allowlist gained the three
    draft routes; full suite **253 passed**; pyright 0. HTTP harness `drafts_harness.py` (scratchpad) on `plan_check`
    via uvicorn 8001, releaser 5 / admin 8 / operator 4: **29/29** (401 / 403 / 422s for body errors, 400 / 404 like
    PATCH, 409s, preview still works, withdraw frees PATCH; every draft it wrote is withdrawn, template settings as
    at the start — `plan_check` keeps the withdrawn change rows).
- **D.1 committed `67d0d81`:** migration **`e7f9acb3d34a`** (revises `b4e8d2a6c913`) →
  **the user runs `alembic upgrade head`**. Table `plan_template_change` (`src/domain_plan/models.py` §6):
  `biz_master_id`, `template_id` (NULL for a create until approved), `kind` CREATE / UPDATE / DELETE, `status` DRAFT /
  APPROVED / REJECTED / WITHDRAWN (enums stored as member names), `is_direct`, `base_revision`, `changes` JSONB
  (create = full body incl. `dates`; update = PATCH body with only set fields; delete = NULL), `add_dates` /
  `remove_dates` DATE[] (update only), `before_values` JSONB + `applied_revision` (set on approval), drafted_by /
  drafted_at / updated_at, decided_by / decided_at / `decision_note`. Partial unique index
  `uq_plan_template_change_one_draft` (template_id WHERE DRAFT); 10 CHECK constraints spell the state rules (see the
  model). **JSONB columns use `none_as_null=True`** — without it Python `None` is stored as JSON `null` and the
  `IS NULL` checks break. FKs RESTRICT (history must not vanish). No rows for existing templates: history starts with
  the first change after the migration. Repo `plan_template_change_repo` (`repositories/Plan.py`, flush only): `add`,
  `get`, `get_locked`, `get_draft_for_template`, `get_draft_template_ids`, `get_drafts(biz_master_id?)`,
  `count_drafts`, `get_history` (newest first by decided_at, else drafted_at).
  Checked: tests `tests/test_template_change_model.py` 23 (client prefix **`"I"`, shared** with
  `test_template_preview.py` — fine, the pool is per prefix per run); full suite **240 passed**; pyright 0;
  on `plan_check`: upgrade → `alembic check` no drift → downgrade (both enum types dropped, checked in `pg_type`) →
  upgrade; `plan_check` is at `e7f9acb3d34a`.

### Handoff 2026-11-10

- **`main` = merge `61f796f` + one handoff commit on top (local, NOT pushed; `origin/main` = `6390c87` — the user
  pushes).** Working tree clean, no open branch. **Tests 217**, pyright 0, frontend build passes, **no migration since
  `b4e8d2a6c913`** (nothing for the user to upgrade). Merged on 2026-11-10, oldest first: `d8a8f9e` sheet import ·
  `2395544` template preview (Level 1) · `1dff126` started work keeps its occurrence · `61f796f` releasers preview,
  admins save (`6b8f653`).
- **The user's decisions at the end of the day (2026-11-10):**
  - **The MVP pilot is postponed.** (The first server run was already postponed for IP configuration.) No go-live
    work is expected next session unless the user says so.
  - **Next phase, primary goal = "Level 2": a Draft → Approve → Active lifecycle for TEMPLATES ONLY, "just like
    SOPs".** The user wants to "spare no effort" on this architectural gap. Start the next session with questions
    (with a recommended option) and a step plan, as always.
  - **Corrected 2026-11-11 (user): sheet imports get NO draft state / version control** — sheets are updated very
    rarely; the user had said so before and my 2026-11-10 note ("templates AND sheet imports") was wrong. Admins
    import directly as today; releasers keep the check-only import window. Business settings are out of Level 2 too
    ("Templates ONLY"). Fixed in the design doc rev 109.
- **Level 2: what is already decided** (§2 "Safer changes"; design doc rev 107, open-questions item "Safer changes to
  businesses and templates"): releaser drafts, **only admin approves**; a non-admin drafter cannot approve their own
  draft; an admin may approve their own draft or apply directly; approval applies **immediately**; **one pending draft
  per live template, and it blocks other edits** to that template; record drafted-by / approved-by; design it together
  with the "finer permissions" question (same list in the doc).
  **Do NOT propose effective-dated / scheduled versions for templates** — the user dropped that for good on
  2026-11-10 ("superfluous"). "Like SOPs" means the draft → approve → active states, not the SOP effective date; if
  the user now wants a start date after all, they must say so — ask, do not assume.
- **Level 2 open questions:** all answered on 2026-11-11 (see the block above).
- **Building blocks Level 2 can reuse:** the dry run (`src/infrastructure/dry_run.py`, `plan_services.dry_run_update /
  dry_run_create`, `coordination/template_preview.py`) gives the approval dialog its "what will change" for free;
  `canPreviewPlanSettings` / the `canSave` and `canImport` check-only modes are where "save as draft" buttons go;
  the SOP draft / approve screens (M2) are the UI pattern; `SOPVersion` has no drafted-by / approved-by either.
- **Not verified in a browser by me (all merged):** the sheet import window, the releaser check-only windows, and the
  "started work" fix (merged on the user's word). The user browser-tested the template preview as admin on their dev
  DB. Their dev DB still has one orphan row from that test (template 8 不定期テスト, 11/26 11:00, no order; order 888
  finished on the retired occurrence) — I do not write to dev_db.
- **USB zip:** rebuilt after the last merge — the newest `packages/ops-20261110-<sha>.zip` (git-ignored). Not urgent
  now that the pilot is postponed; rebuild with `python scripts/make_package.py` before any server install.
- **No free client-code prefix letter is left for new test files** (`tests/client_codes.py`): share a letter or widen
  `new_client_code` first.

### Earlier status notes (kept for reference; the block above wins where they differ)

- **Before the last merge: `main` = merge `1dff126` + one MEMORY.md commit on top (`75d6755`).** `1dff126` =
  merge of `fix/started-slot-not-regenerated` (`97c7fdc`, 2026-11-10): a recalculation leaves an occurrence alone when
  its work has started — §2 "Safer changes" → "Started work keeps its occurrence". Found by the user's browser test on
  their dev DB; merged on the user's word ("I trust your changes") without a second browser check. **Tests 217**,
  pyright 0, no migration. **Everything of 2026-11-10 is merged: sheet import, template preview, this fix.**
- **USB zip rebuilt 2026-11-10 from the head of `main`** (`python scripts/make_package.py` → the newest
  `packages/ops-20261110-<sha>.zip`; `packages/` is git-ignored). It replaces `ops-20261106-4cfca4c.zip`, which has
  none of the 2026-11-10 work. The user still has to copy it to the USB drive. Rebuild again after any later commit.
- `feature/releaser-preview` (from `main` `75d6755`, 2026-11-10) = releasers can preview, admins save — §2
  "Safer changes" → "Releasers preview (Option B)"; merged as `61f796f`.
- (Superseded the same evening: "Level 2 only after the pilot" — the pilot is postponed and Level 2 is next.) Not confirmed as browser-checked: the sheet
  import window. The user's dev DB still has the orphan row from the test (template 8, 11/26 11:00, no order).
- Before that: `2395544` = merge of `feature/template-preview` ("Level 1" preview before saving a
  template: P.1 `595f5f8`, P.2 `707e1f8`, P.3 `d7539fe`, P.4 `8b2f62f`; §2 "Safer changes"). The user browser-tested the
  preview on their dev DB (Vite 5173) on 2026-11-10; the sheet import window is not confirmed as browser-checked.
- **`main` = `d8a8f9e` (local, not pushed; `origin/main` = `6390c87`)** = merge of `feature/sheet-import` (2026-11-10):
  sheet import screen S.1 `ec5c23f`, S.2–S.4 `f381c28` (checklist, manual, records); §2 "Sheet import". The USB zip
  `ops-20261106-4cfca4c.zip` is older than this — rebuild with `python scripts/make_package.py` before the install.
  After the template preview: Level 2 (drafts + approval, with finer permissions) only after the pilot. **Reference sheets are now IN the
  go-live scope** (user, 2026-11-10: "inseparable core feature"; reverses the 2026-11-06 decision). **The first server
  run is postponed (2026-11-10) because of IP configuration issues on the server.**
- Before that: `357c81a` = `c59aec4` + the open-question record; `c59aec4` = `acfedc3` (merge of `feature/go-live`: G.1
  `450f03e`, G.2 `3771751`, G.3 `4cfca4c`; §2 "Go-live") + the operation manual `docs/OPERATION-MANUAL.md`. **The user carries `packages/ops-20261106-4cfca4c.zip` (same files as
  `acfedc3`) to the Linux server by USB; next = their first run there (README §0, §1, then GO-LIVE.md A) — fix what it
  shows first.** Earlier: `origin/main` `6390c87` was pushed by the user 2026-11-06. After `895cbe5` the user committed `a00b2b1`
  "mvp" / `242dda6` "staging" (MEMORY.md + a `deploy_package.zip`) and `6390c87` "git" (zip removed again; it held no
  secrets). **The VM reaches only the office network (no GitHub): code goes as a zip on a USB
  drive** (user, 2026-11-06); it reaches the internet **only through a corporate HTTP/HTTPS proxy** (built for in G.3,
  see §2 "Go-live"). The user is installing Windows Server on the machine **for now only — the final server is Linux**. The user pushes; I never do. The user
  sometimes commits a step themselves — check `git log` before committing.
- **Built and merged into `main`:** all design-doc phases (1 daily dashboard · 2 generation job · 3 staff · 4 ope
  orders · 5 steps / maker-checker · 6 dependencies (+ L latest-before) · 7 client events / signals · 8 deadlines / due
  soon · 9 SLA report), the 2026-11-05 extras (UX shortcuts, admin guards, dashboard tabs, logging) and **the whole MVP
  roadmap of 2026-11-05/06** (goal: internal network, own team only; order M1 → M2 → login → deployment):
  | MVP step | Merge into `main` | What (detail in §2) |
  |---|---|---|
  | M1 client + business screens | `44c1737` | 顧客 / 業務 pages, 設定 dropdown, client deactivate, automatic business codes, in-use delete guard |
  | M2 SOP screens | `28b94b6` | SOP states from dates (migration `7a3c5e9b1d24`), draft editor, impact query, approve dialog — **user browser-tested OK** |
  | Login LG.1–LG.3 | `895cbe5` (with deployment) | passwords / sessions / API keys (migration `b4e8d2a6c913`), login for every API, X-Staff-Id removed, /login page, forced password change, 職員 page temp passwords + API keys |
  | Deployment | `895cbe5` | `deploy/` Docker Compose (db, app, worker, web, backup), `scripts/worker.py`, nightly pg_dump |
- **Now: waiting for the user's test report from the VM.** The Docker setup has **never been built or started**
  (no Docker on the office PC). **2026-11-06: the VM isn't set up yet**; the office PC has no Docker **and no WSL**
  either — offered WSL2 + Docker Engine (or Docker Desktop, license limits) for a local first run, **user chose to wait
  for the VM** (install nothing on the office PC). Static read-through of `deploy/` the same day found nothing (settings
  read env without a `.env` file, SQLAlchemy 2.1 `postgresql://` = psycopg 3, `GET /` open for the healthcheck, lockfile
  has the linux-musl rollup / esbuild packages, `.sh` files LF). Expect fixes in: image builds (python:3.14-slim + `psycopg-binary`, `npm ci` in
  node:24-alpine), nginx config, compose start-up order / healthchecks, the backup service in the postgres image.
  Ask for `docker compose ps` / `docker compose logs <service>` output. The login frontend (LG.3) and the 顧客 / 業務
  pages were **not** clicked through in a browser by me; the user tested M1 + M2 screens only.
- **Operation manual (2026-11-10, uncommitted on `main`): `docs/OPERATION-MANUAL.md`** — Japanese, for QA testers and
  future operations staff (my assumption; the user's request left the audience as a placeholder): overview / terms /
  order states / roles / screens, prerequisites (login rules, cookie vs API key), per-feature steps with the matching
  API calls, curl examples + error codes, a 16-row end-to-end QA scenario, MVP limits. Written from `openapi.json`
  (unchanged after re-export), the schemas and frontend labels; **not checked against a running server or browser**.
  Keep it in step when a screen label or rule changes.
- **After the VM test: go-live checklist** (not planned in detail yet): real master data (clients, businesses,
  templates, sheets, holidays, SOP versions, dependencies) on the server, staff accounts + temporary passwords,
  `scripts/create_orders.py` if orders are missing, browser checks, the user's NAS sync of `deploy/backups/`.
  **Go-live decisions (2026-11-06, all as I recommended):** master data **typed in the screens** on the server (dev DB
  not copied); the API-only gap (a fresh DB has **no business calendar**; calendar / sheet creation have no screen) is
  filled by a **one-time setup script**; ~~no reference sheets at go-live~~ **changed 2026-11-10: sheets are in scope**,
  the team converts the client tables to JSON themselves (confidentiality) and an admin imports the JSON on the sheet
  page (§2 "Sheet import"); **pilot one
  client first**, the rest after a few days. Python stays **3.14** (dev venv 3.14.2; change only if a wheel is missing).
  Step plan approved 2026-11-06, branch **`feature/go-live`** from `main` `6390c87`; detail in §2 "Go-live".
- **Pool (after go-live)**, smallest first: (1) notifications (mail / Teams / Slack) for まもなく / 遅れ — needs the
  user's tool + webhook; the worker loop is the natural place; (2) SFTP watcher "B" (§2 "Todo pool") — posts
  `POST /ope/signals` with an **API key** (built in LG.2); (3) client-limited role (§2 "Planned"); (4) SSO instead of
  passwords (behind the same session). Plan any of them like before: questions first (AskUserQuestion with a
  recommended option), a step plan the user approves, one step at a time, each left uncommitted until "go".
- **User's dev setup after login (important):** their uvicorn on 8000 + Vite now need a real login. They must have run
  `alembic upgrade head` (head `b4e8d2a6c913`) and `python scripts/set_password.py system` (staff 1, login `system`,
  has system_admin + operator / checker / releaser but **not admin** — grant 業務管理者 to edit settings). **Both
  confirmed by the user 2026-11-06.** The two Task Scheduler jobs on their PC stay for the dev DB; the server uses `worker`.
- **DB:** latest Alembic head **`e7f9acb3d34a`** (plan_template_change, Level 2 D.1, 2026-11-11; before it `b4e8d2a6c913`). Chain: `ceee1634d6a7` (squashed init) → `34bdbd214739` →
  `9ee3f3e0624e` → `9b2ec314368a` → `3493c39bdf43` → `5c1f7d2a9e04` → `568c3287c359` (staff) → `f9bbe9648f15` (biz
  release settings) → `9e1bcef6cbc7` (ope tables) → `5eede5808433` → `971958268657` (step template condition /
  is_blocking) → `966b78ff950f` (ope_step, ope_step_event) → `6c8cc87274c8` (ope_step.retired_at) → `2904467fd7b9`
  (biz_dependency) → `cb0ae371f402` (ope_dependency) → `58425e70286d` (DEPENDENCY_OVERRIDE_APPROVED) → `6a7849dbf2ec`
  (biz client-event settings) → `3242f180527a` (ope_signal, role signal_sender) → `2f7614f1cb02` (latest_before) →
  `d17d631f5ad2` (template deadline, N.1) → `c5bb40015e61` (order deadline columns, N.2) → `51d8d829a2d2` (attention
  indexes, N.3) → `7a3c5e9b1d24` (SOP version states from dates, M2.1) → `b4e8d2a6c913` (staff login, LG.1).
  **Never run alembic against `dev_db` myself.** The user runs `alembic upgrade head`; their uvicorn on 8000
  auto-reloads, so a model change without the migration returns 500 until they upgrade. **Warn them whenever a step
  adds a migration.** On the server the `app` container migrates at start.
  **`plan_check`** (throwaway, mine) is at `e7f9acb3d34a`. Seeded: client `MOCK` (+ test clients `Z8E4`, `YA64`), biz 1
  `MOCK0001` (approved SOP `v9.26817.0` current; draft id 6 `v9.1793927904.0-http` with 2 steps), biz 2 `MOCK0002`
  (LIGHTWEIGHT + AUTOMATIC `data_received`, daily template 23), calendar `JP_BANK` id 1, the mock sheet. Staff (every id
  N has password **`check-pass-N`**): 1 `check-admin` (system_admin), 2 suspended, 3 viewer+auditor, 4 operator
  `http-operator-1793324910`, 5 releaser `http-releaser-1793324911`, 6 / 7 checkers, 8 admin `http-admin-6eb1ee`, 9 / 10
  signal_sender. Many open orders past their deadline (good for 遅れ / attention / SLA checks).
- **Tests:** `pytest tests/ --ignore=tests/client_biz_check.py --ignore=tests/client_biz_check2.py` → **278 passed** (2026-11-11),
  ~3–8 min. Runs against `test_db` via `tests/conftest.py` (`create_all`, no migrations; the order-sync listener —
  and with it the biz dependency / template listeners and the master guards — is registered there). **Every new test
  file uses `from tests.client_codes import new_client_code` with its own prefix letter**; used: T D B O S A R V P K M
  G L N Q E H J W F Y U C X Z I (none free: the next test file shares a letter or `new_client_code` is widened). Auth in OpenAPI tests: `tests/openapi_auth.py` `needs_login(op)`.
  Type check: `npx pyright@1.1.400 --pythonpath .venv/Scripts/python.exe src main.py tests scripts` → 0 errors.
  Migration check (on `plan_check`): `alembic upgrade head` / `downgrade -1` / `upgrade head` / `alembic check` → no
  drift; run alembic with `export DATABASE_URL=$(grep ^DATABASE_URL .env | cut -d= -f2- | sed 's#/dev_db$#/plan_check#')`
  (`.env` points at the user's `dev_db`). Frontend: `cd frontend && npm run build` (tsc + vite) passes. After backend
  schema changes: `python scripts/export_openapi.py` then `npm run gen:api`.
  **Harness pattern (since login):** start uvicorn on 8001 against `plan_check` with **absolute paths** (a backgrounded
  command whose cwd drifted ran without its `cd`): `export DATABASE_URL=$(grep ^DATABASE_URL /c/p/postgres-migration-poc/.env
  | cut -d= -f2- | sed 's#/dev_db$#/plan_check#') && export LOG_FILE=/c/p/postgres-migration-poc/app-8001.log && exec
  /c/p/postgres-migration-poc/.venv/Scripts/python.exe -m uvicorn main:app --app-dir /c/p/postgres-migration-poc --port
  8001`. **Log in first** (`POST /auth/login`): Python with `http.cookiejar`; node harnesses import a cookie-keeping
  `fetch` first (pattern in §2 "Login" LG.3) and bundle the real modules with esbuild
  (`--define:import.meta.env='{"VITE_API_BASE_URL":"http://127.0.0.1:8001","VITE_TIMEZONE":"Asia/Tokyo"}'`). Stop
  uvicorn with TaskStop when done.
- **Scheduled jobs (user's PC, dev DB only):** Task Scheduler `\postgres-migration-poc\PlanExtendJob` (daily 00:05,
  `scripts/extend_plans.py`) and `\postgres-migration-poc\OrderReleaseJob` (every 15 min, `scripts/release_orders.py`).
  On the server: the `worker` service. Any new script that changes plan must call `order_sync.register()`.

### Commits by phase (newest first; full detail per step in §2)
| Phase | Commits |
|---|---|
| Deployment (`feature/deployment` → `895cbe5`) | `7a2e3c6` deploy/ + worker |
| Login (`feature/login`, merged with deployment) | `311c38e` LG.3 frontend · `c644b4d` LG.2 backend switch · `3a93ffd` LG.1 building blocks |
| M2 SOP (`feature/sop-screens` → `28b94b6`) | `17aa788` M2.6 · `800061c` M2.5 · `f549312` M2.4 · `2582eaa` M2.3 · `ce85e39` M2.2 · `5b054ea` M2.1 |
| M1 masters (`feature/master-screens` → `44c1737`) | `13a7238` M1.2c · `2cfb5fa` M1.2b · `5a619b6` M1.2a · `2729ea7` M1.1 |
| SLA report (`feature/sla-report`) | `16045c6` S.2 page + CSV · `aae5c50` S.1 endpoint |
| Dashboard tabs | `36ce2e9` |
| N deadlines (`feature/deadlines`) | `7ab2cc3` N.4 frontend · `95c94fe` N.3 re-stamp + read side + attention · `503f3c6` N.2 order columns · `2e5228f` N.1 template fields |
| L latest before due (`feature/latest-before`) | `e3541c3` L.3 · `c653251` L.2 · `bfaab32` L.1 |
| U.4 admin guard / D.1–D.2 small debts | `60ce2cb` U.4 · `f4e6dbf` D.2 · `9036c8f` D.1 |
| UX shortcuts U.1–U.3 | `34914a8` U.3 · `f002f9f` U.2 · `9b7cb38` U.1 |
| 7 client events / signals (`feature/ope-signals`) | `22fec2a` 7.5 screens · `a7e1e03` 7.4 late + signal list · `0b5e762` 7.3 receive / match · `a9f8fc3` 7.2 ope_signal + role · `0ff76dd` 7.1 biz settings · `c1aade1` plan |
| 6 dependencies (`feature/ope-dependencies`) | `3669528` 6.5 screens · `916ebee` 6.4 start check + override · `1a364ca` 6.3 link resolution · `3eaede4` 6.2 ope_dependency · `8ab5f92` 6.1 biz_dependency |
| 5 steps (`feature/ope-orders`) | `6a37fee` docs · `75cc109` 5.5 (user) · `074840f` 5.4 · `abe6281` 5.3 · `11cd510` docs · `fc58dd0` 5.2 · `05850c1` / `5dbbe0f` 5.1 (user) |
| 4 orders (`feature/ope-orders`) | `d151fbf` 4.7 · `4790ac1` 4.6 · `09f7455` 4.5 · `0389de2` 4.4 · `7d94be7` 4.3 · `0552e9f` 4.2 · `d65af44` 4.1 |
| 1–3 (merged `23767b8`) | `702ebbb` / `bfe3deb` staff · `fcff659` generation job · `eb4e6b5` / `17f9bcc` / `9306e23` dashboard |

## 2. Target architecture — "Operations Orchestration Design" (draft for team review)

Design doc (Claude Docs): https://claude.ai/artifact/W4XgfnPTH4iJPstx362xpV — the source of truth for this section.
Decided in the doc's comments (2026-10-28): SOP version + steps **freeze at release**.
Before building any phase, re-read the doc: the team may have edited or commented on it.
**Last rev written by me: 125 (2026-11-11: revs 108–125 = Level 2 in the "Safer changes" item — scope corrected to templates only, the decisions, D.1–D.6 built, the N+1 note; status paragraph notes Level 2 is built on its branch; finer-permissions item updated). Before that 107 (2026-11-10: revs 105–107 add the milestone + "pilot postponed, next phase = level 2" to the status paragraph, retitle Level 2 as the primary goal of the next phase and widen its scope to sheet imports with four open points; rev 104 adds "Who can use it" (releasers preview, admins save; import summary for releasers) to the Level 1 item; revs 102–103 add "Work that has started keeps its occurrence" after the table in "Propagating plan changes to orders" and replace the "two occurrences today" sentence in the Level 1 item; revs 100–101 mark Level 1 as built in the "Safer changes" item, with how it works and the accepted "two occurrences today" behaviour; rev 96 open-questions item "Safer changes to businesses and templates" (level 1 preview, level 2 drafts + its rules); revs 97–99 removed the "Level 3" scheduled-versions sentence from it at the user's request; rev 93 open question "Finer permissions for settings" at the end of the open-questions list; revs 94–95 in the "Go-live" paragraph "Reference sheets are not used at go-live" → sheets are part of go-live, JSON converted by the team, import screen with two modes, and the status paragraph notes the postponed first run; before that 2026-11-06: "Go-live" paragraph after "MVP deployment" — rev 91 wrote it: pilot client, screens for master data, setup_calendar.py, no sheets, USB zip + fresh folder, GO-LIVE.md; rev 92 replaced "internet still being checked" with the proxy build, optional company certificate, package + install scripts)** — read with `{"kind": "view", "sinceRev": 125}` to see only newer edits, and
query comments with `afterSeq: 24`. Doc ids: project `eb57297f-757b-4c1e-9116-9e545b1abd76`, body node `9e7b6bb0-1c80`.
The delivery-phase list in the doc carries the build status (phases 1–7 plus 8 deadlines and 9 SLA report, built and merged; rev 77). Rev 77 also added: SLA report and dashboard tabs bullets, latest-before note under phase 6, ope_order deadline columns in the data model.

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
| U.2 | Dashboard rows: ⚙ link (all users, also rows without an order; not voided rows) → `/settings/templates?biz=&template=` (row highlighted + scrolled into view); 「← 戻る」 links via router state (`shared/lib/returnTo.ts`, `shared/ui/ReturnLink.tsx`): dashboard → calendar (same client / month, day re-selected via `state.selectDate`), template page → dashboard day; state kept across day / business changes, chains (calendar → dashboard → template) | **done** (`f002f9f`) |
| U.3 | Template page edits need `admin` (業務管理者; not system_admin): `@require_roles([ADMIN])` on plan template writes (create / patch / delete / restore / dates / cache rebuild) and `PATCH /biz/{id}`; UI hides 新規作成 / 編集 / 削除 and the 業務の設定 編集 for non-admins (panel stays visible). The user's staff 1 has no admin role → must grant it | **done** (`34914a8`) |
| U.4 | Branch `feature/admin-guard` (from `main` `d6ad461`). User's choices as I recommended: sheet dates, holidays / calendars, remaining biz writes (biz create / delete / restore, SOP version clone / approve / cancel / create, step templates) → `admin`; **calendar occurrence changes (変更 / 取消 / 臨時 / ルールに戻す / 取消の復元) → releaser or admin** (they move / cancel / create orders; closes the loophole of an operator moving an order to today and starting it without the early-start approval). No `require_active` left in plan. `auth._guard` sets `__required_roles__` on the wrapper (test marker). `tests/test_plan_settings_guard.py` checks every `/plans` + `/biz` route (writes: header + exact roles; reads open; walks `plan_router.routes` / `biz_router.routes` — `app.routes` holds `_IncludedRouter`s in this FastAPI). UI: sheet / holiday pages hide edits for non-admins (「変更は業務管理者だけができます」); calendar shows no actions / 臨時 for non releaser-admin (`domain/plan/lib/permissions.ts` `canChangeOccurrences`); 取消済みの予定 list without 元に戻す. Operators: dashboard work only | **done** |

### Phase L — "latest before due" (branch `feature/latest-before`, 2026-11-05)
User's decisions: compare by **scheduled start** (not rule date); a **canceled latest upstream stays awaited**;
**look-back limit in days on the definition** (my alternative recommendation was no limit). Doc rev 72 (paragraph
after "Link resolution" + ticked open question).
| # | Step | Status |
|---|---|---|
| L.1 | `DependencyMatchRule.LATEST_BEFORE`; `biz_dependency.lookback_days` (1–366, required ⇔ latest_before; CHECKs use `match_rule::text` because a new enum value can't be used in the same transaction); create API / response; migration `2f7614f1cb02` (downgrade rebuilds the shared enum type and the ope CHECKs) | **done** (`bfaab32`) |
| L.2 | `links._targets_for`: latest upstream order with `scheduled_start` in [own start − lookback, own start] (canceled included; tie → higher id), no match key stored, else a waiting row (`describe`: "no order of CODE in the N days before it"); `refresh_order` re-resolves not-started downstreams starting within lookback after an upstream order; `order_sync._slot_moved` refreshes on any time change (not only rule-date change); repo `get_latest_starting_before`, `get_open_orders_of(starting_from/until)`; `tests/test_latest_before.py` (client prefix `W`, weekly → daily) | **done** (`c653251`) |
| L.3 | 業務の依存関係: option 直前の上流 + required 日数 input (default 35, `DEFAULT_LOOKBACK_DAYS`), table text 「直前の上流（35日前まで）」 (`matchRuleText`), body via `dependencyBody`; drawer hint 「（直前の上流）」 | **done** |

### Phase N — deadlines and "due soon" (branch `feature/deadlines`, 2026-11-05, merged into `main`)
Why: the person checking client files in WinSCP needs a warning before the deadline, not only 遅れ after it.
User's decisions: **deadline per template, 3 modes** — `END` (scheduled end, default), `OFFSET` (minutes after the
*scheduled* start; internal steps), `FIXED_TIME` (local time on the scheduled day + 0–7 days; external hard
cut-offs); **warning lead on the template, default 0 = off** (avoid alert fatigue); dashboard first (まもなく tag +
panel + auto-refresh), no messages yet; all not-finished orders. **The user insisted (blocker): `deadline_at` is
stored on the order** (not computed on read) for SLA + polling performance; **the rule is fixed at release** (template
edits re-stamp only 未リリース orders; released / running / finished never change; moves recompute from the order's
own rule snapshot); **`finished_at` stored on the order** for SLA. Doc rev 73 (bullet after "Late").
| # | Step | Status |
|---|---|---|
| N.1 (`2e5228f`) | plan_template `deadline_mode` (`plan_deadline_mode_enum`), `deadline_offset_minutes` (1–10080), `deadline_time` + `deadline_day_offset` (0–7), `warn_before_minutes` (0–1440, default 0); CHECKs; schemas (`deadline_problem` shared); service: deadline fields don't bump revision / regenerate, mode switch clears unused fields, fixed time ≤ start on day 0 → 400; migration `d17d631f5ad2`; `tests/test_template_deadline.py` (prefix `F`); frontend create body sends defaults until N.4 | **done** |
| N.2 (`503f3c6`) | ope_order: rule snapshot (`deadline_mode` reuses `plan_deadline_mode_enum`, offset, time, day offset, `deadline_timezone`, `warn_before_minutes`) + `deadline_at` (NOT NULL) + `warn_at` + `finished_at`; CHECKs (rule, warn_at ⇔ warn, finished_at ⇔ FINISHED); `domain_ope/deadlines.py` (`copy_rule`, `compute`, `restamp`; fixed time = local date of scheduled_start + days); stamped in `order_sync.create_order`, `_slot_moved`, `resolve_conflict` accept_plan; `finished_at` set in `ope_order_repo.add_event` when to_status FINISHED; migration `c5bb40015e61` with backfill; `tests/test_order_deadlines.py` (prefix `Y`); direct-ORM tests pass END defaults | **done** |
| N.3 (`95c94fe`) | `domain_plan/template_events.py` (listener; `update_template` notifies when deadline fields or timezone change) → `order_sync.apply_template_deadline_change` re-stamps orders with `released_at IS NULL` (not canceled; `ope_order_repo.get_unreleased_of_template`); `links.is_late` = `deadline_at` < now, `is_due_soon` = `warn_at` ≤ now < `deadline_at`; order detail + dashboard rows carry `deadline_at` / `due_soon` (+ detail: `deadline_mode`, `warn_at`, `finished_at`); **`GET /ops/attention?limit=`** (open orders late or due soon, any day, earliest deadline first, `truncated`; `ope_order_repo.get_needing_attention`); migration `51d8d829a2d2` (partial indexes `ix_ope_order_open_deadline`, `ix_ope_order_open_warn`). Timings on plan_check: `/ops/daily` ~24 ms median, `/ops/attention` ~11 ms | **done** |
| N.4 (`7ab2cc3`) | frontend: `domain/plan/lib/deadline.ts` (labels, `describeDeadline`); `templateForm.deadlineFields` (unused fields null, fixed day default 0) + template modal section 締切 (mode radio, 分後 / 予定日の N日後 + 時刻, 何分前 default 0) + list column 締切; `domain/ops` `getAttention` + `useAttention`; `features/daily-dashboard/AttentionPanel.tsx` (shown only when items; row → drawer); rows: まもなく (gold) + 「締切 HH:mm」 when it differs from the end (`deadlineLabel`, `showsOwnDeadline`); drawer: 締切 line, まもなく tag, 完了 line + 締切後 tag; `shared/hooks/useAutoRefresh` — dashboard + attention every **2 min** while the tab is visible (and on return) | **done** |
**Dashboard tabs (2026-11-05, user feedback "the late box makes it dirty", branch `feature/dashboard-tabs`):** 日次ダッシュボード = Tabs 予定 (default) / 締切・遅れ (count badge: red if any late, else gold) / 信号 (orange); `?tab=attention|signals` in the URL (kept across day changes and the back-link state); no big alert — one summary line + Empty states; open signals fetched in the page (`useOpenSignals`) for the badge, refreshed with the 2-min auto-refresh.
**SLA report (2026-11-05, branch `feature/sla-report`):** user's figures — 完了 / 遅れ / 期限内率 / 平均遅れ（分）, grouped by client × month; decisions (all as I recommended): **month of the deadline (JST)**, **average over late orders only**, extra column **未完了の遅れ** (open past deadline, not in the other figures), **any active staff**. S.1 `aae5c50`: `GET /ops/sla?from=YYYY-MM&to=YYYY-MM&client_code=` (`coordination.services.get_sla_report`, one GROUP BY over `ope_order` stored `deadline_at` / `finished_at`; canceled excluded; default last 12 months, max 36; `tests/test_sla_report.py` prefix `U`). S.2: nav 「SLA」 → `/reports/sla?from=&to=&client=` (`features/sla-report`: month range + client filter, table with 合計 rows for months with 2+ clients — late-weighted average, CSV of the shown rows with BOM, `sla_<from>_<to>[_<client>].csv`).
Random client-code flake seen twice in Phase N (once unidentified, once `YCFD` in `test_order_deadlines.py`) → **fixed
2026-11-05: `tests/client_codes.py` `new_client_code(prefix)`** (per-prefix shuffled pool, no repeats within a run) used by all
21 test files (branch `fix/test-client-codes-all`). Use it in every new test file. Before: the other
test files still draw `prefix + 3 hex` at random → proposed: move `test_plan.py`'s unique pool into a shared test module.

### M1 — client + business master data (branch `feature/master-screens`, 2026-11-06; doc rev 79)
User's decisions (all as I recommended): **business delete refused while in use** (dependency definitions, live plan
templates, orders not finished / canceled — past-dated open orders count too); **inactive client = hidden from
`GET /clients/` (unless `include_inactive=true`) + no new businesses / sheets / business restores**, history stays;
**`GET /biz/?client_code=&include_deleted=`**. Earlier: clients never deleted; business code automatic and fixed.
**M1.1 as built** (no migration — `client_master.is_active` already existed):
- Client: `ClientCreate.code` pattern `^[A-Za-z0-9]{4}$` (upper-cased), name 1–100; `ClientUpdate` = name only,
  `extra="forbid"` (sending `code` → 422); `ClientResponse` + `is_active`, `created_at`, `updated_at`. Endpoints:
  `GET /clients/?include_inactive=` (open, code order) · `POST /clients/` 201 · `PATCH /clients/{id}` ·
  `POST /clients/{id}/deactivate` · `POST /clients/{id}/activate` (all writes `@require_roles([ADMIN])`);
  **`DELETE /clients/{id}` removed** (405). `client_services.require_active_client(db, code, lock=)` (404 / 400 inactive)
  used by biz create / restore and plan `create_sheet` / `import_sheet`.
- Business: `BizMasterCreate.business_seq` optional, 1–9999 (LPAD 4 would truncate 10000); omitted → client's max seq
  **including deleted** + 1, client row locked `FOR UPDATE`; explicit number used by a live / deleted business → 409
  (DB already had a global unique `biz_master_business_code_key`). `delete_biz` → 400 listing reasons; `restore_biz`
  needs an active client.
- Hooks (client / biz must not import biz / plan / ope): `domain_client/deactivation_checks.py`,
  `domain_biz/delete_checks.py`; checks registered by `src/coordination/master_guards.py` (`register()` called from
  `order_sync.register()`): alive businesses of the client; live templates (`plan_template_repo.get_by_biz`);
  unfinished orders (`ope_order_repo.count_unfinished_of_biz`). Repos: `client_repo.get_list` / `get_locked` /
  `get_locked_by_code`, `biz_master_repo.get_list` / `max_business_seq` / `count_alive_of_client`.
- Tests `tests/test_master_data.py` (6; client prefix **`C`**). HTTP-checked on `plan_check` (24/24): **new client
  `Z8E4` (id 2) with business `Z8E40001` live, `Z8E40002` / `0003` deleted**; MOCK0001 delete refused ("18 plan
  template(s); 186 order(s) …"). Frontend types regenerated; no UI yet (M1.2). Committed `2729ea7`.

**M1.2 step plan (approved 2026-11-06).** User's decisions (all as I recommended): business number **automatic with a
preview** in the 登録 modal (no number field; API still accepts one for a load script); nav = **「設定」 dropdown**;
業務 page edits **basics (名前 / 管理者メール / 説明) + a link** 「予定テンプレート・業務の設定」 → `/settings/templates?biz=`
(release / client-event settings stay in that page's 業務の設定 panel).
| # | Step | Status |
|---|---|---|
| M1.2a | `AppLayout`: カレンダー / 日次ダッシュボード / SLA flat; antd `Dropdown` 「設定」 (hover + click) with the 5 settings pages (`SETTINGS_PAGES`), button shows 「設定：<page>」 + active style when one is open (`currentSettingsPage`); links keep each page's last query string; CSS `.nav-menu*`. 顧客 / 業務 entries are added in M1.2b / c | **done** (`5a619b6`) |
| M1.2b | 顧客 page `/settings/clients` (`features/client-settings`: `ClientSettingsPage` + `ClientFormModal`; `clientApi` create / update / deactivate / activate, `list(signal, includeInactive)`; `useAllClients`; `domain/client/lib/clients.ts` `canManageClients` (active admin), `normalizeClientCode`, `clientCodeError` (4 alnum, not used — inactive included), `deactivateBlockedReason`). Table コード / 顧客名 / 状態 / 業務 (count of live businesses; becomes a link in M1.2c) / 登録日 / 操作 (admin: 名前を変更, 無効にする — disabled with the reason while businesses remain — / 有効にする, confirm; backend message shown on refusal); inactive rows greyed (`is-past-row`); first entry of the 設定 menu. Harness 15/15 on plan_check (client `YA64` id 3 left active, its business deleted) | **done** (`2cfb5fa`) |
| M1.2c | 業務 page `/settings/businesses?client=` (`features/business-settings`: `BusinessSettingsPage` + `BizFormModal`; `bizApi.listForClient(code, includeDeleted)` / `create` / `remove` / `restore`; `domain/biz/lib/businesses.ts` `canManageBusinesses` (active admin), `nextBusinessCode` (max seq incl. deleted + 1, null past 9999), `bizBasics` (blank → null), `bizUpdateBody` (changed fields only)). Client field like 業務の依存関係 (active clients; warning when `?client=` is inactive / unknown → no 登録 / 元に戻す). Table 業務コード / 業務名 (+ description) / 管理者メール / 状態 有効 / 削除済み / 操作: 「予定テンプレート・業務の設定」 (all users, live rows; navigate to `/settings/templates?biz=`), admin: 編集, 削除 (confirm; backend's in-use reasons shown 10 s), 元に戻す. 登録 modal shows the code it will get. 顧客 page 業務 column = link to this page; 業務 = 2nd entry of the 設定 menu; CSS `.biz-description`. Harness 16/16 on plan_check (YA64 now has YA640002 live, YA640001 deleted) | **built, uncommitted** |

### M2 — SOP screens (branch `feature/sop-screens` from `main` `44c1737`, 2026-11-06; doc rev 80)
Found first: the stored SOP status never moved FUTURE → ACTIVE (nothing promoted it), so a second approval was always
refused ("既に別の予約…") and 予約の取消 could turn an in-use version back into an editable draft. User's decisions (all
as I recommended): **states from dates**; **新しい版 copies the newest approved version** (scheduled one if any, else
current; empty draft for the first SOP); **effective = a date, from 00:00 JST**. My calls (user didn't object):
approving a draft with **no steps is refused**; **no four-eyes** on SOP approval (editors aren't recorded).
| # | Step | Status |
|---|---|---|
| M2.1 | Version states from dates + approve / cancel rules; migration `7a3c5e9b1d24` | **done** (`5b054ea`) |
| M2.2 | Draft editing API (as built below) | **done** (`ce85e39`) |
| M2.3 | `GET /ope/sop-impact` (as built below) | **done** (`2582eaa`) |
| M2.4 | SOP page (as built below) | **done** (`f549312`) |
| M2.5 | Draft step editor (as built below) | **done** (`800061c`) |
| M2.6 | Approve dialog + 予定を取り消す (as built below) | **built, uncommitted** |

**M2.1 as built** (migration `7a3c5e9b1d24`: rebuilds `sop_status_enum` as DRAFT / APPROVED — FUTURE / ACTIVE / ARCHIVED →
APPROVED; data fix-up first: drafts lose their dates, approved without `effective_at` get now, open-ended approved
versions are chained to the next one's `effective_at` (LEAD by effective_at); CHECKs `ck_sop_version_effective_iff_approved`
(draft ⇔ no effective_at), `ck_sop_version_expired_after`; downgrade maps back by dates).
- `SOPStatus` DRAFT | APPROVED; `SopState` draft | scheduled | current | past via `SOPVersion.state_at(now)` / `.state`;
  `SOPVersionResponse` + `state` (and `model_config`); `SOPVersionScheduleIn.effective_date` (was `effective_at`).
- `approve_and_schedule_version(db, biz_id, draft_id, effective_date)`: locks the biz row; date must be > today JST;
  draft of this biz with ≥ 1 step; refused while another version is scheduled; every approved version with
  `expired_at` NULL gets `expired_at = effective_at` (exact, no −1 s); draft → APPROVED. `cancel_future_version`: approved
  + scheduled only, else 400 "already taken effect"; `version_checks.reasons_not_to_cancel` (new hook
  `src/domain_biz/version_checks.py`, registered by `master_guards.version_used_by_orders` →
  `ope_order_repo.count_using_sop_version`: pinned or frozen, not canceled); versions ending at its start reopen; → DRAFT.
  Removed dead `create_sop_version` / `get_latest_active_version`. `LOCAL_TIMEZONE` in biz services.
- **Fixed a latent bug:** git stored `src/infrastructure/repositories/client.py` while `__init__` imports `.Client`; it
  only worked because the working-tree file was `Client.py`. A branch switch rewrote it lowercase →
  `ModuleNotFoundError`. Renamed in git to `Client.py` (a fresh clone / Linux would have failed).
- Tests: `tests/test_sop_versions.py` (4; client prefix **`X`**); six test files switched `SOPStatus.ACTIVE` /
  `FUTURE` → `APPROVED`. Round trip on plan_check (also with synthetic old-style rows: chain + draft cleanup OK, rows
  removed). HTTP 10/10 on plan_check: v9 = current; draft id 6 (`v9.1793927904.0`, 1 step) left on biz 1 after
  approve → cancel.

**M2.2 as built** (no migration). Endpoints (writes `@require_roles([ADMIN])`, reads open):
`GET /biz/{id}/versions?include_drafts=` (default approved only = pin choices; newest first by version_number) ·
`GET /biz/versions/{id}` → `SOPVersionDetailResponse` (version + `steps` by number) · `POST /biz/{id}/versions/clone`
= **新しい版**: copies the newest approved version by `effective_at` (scheduled one if any), empty when none ·
`POST /biz/{id}/versions` (empty draft, kept) · `PATCH /biz/versions/{id}` (`SOPVersionUpdate` = tag, subclass of
`SOPVersionCreate`) · `DELETE /biz/versions/{id}` (draft + steps, hard) · `POST /biz/versions/{id}/steps`
(`step_num` optional → max + 1; title stripped, 1–100) · `PUT /biz/versions/{id}/steps/order` (`SOPStepOrderIn.step_ids`
= every step once → 1..n, two-phase via a high offset because of `uq_sop_version_step_num`) · `PATCH /biz/steps/{id}`
(`SOPStepTemplateUpdate`: title / content (null clears) / condition / is_blocking; explicit null refused for the
others) · `DELETE /biz/steps/{id}` (numbers kept; reorder to close gaps). All edits: approved version → 400
"…is approved and can no longer be changed"; the version row is locked `FOR UPDATE`. Services `list_versions`,
`get_version`, `_get_draft`, `rename_draft`, `delete_draft`, `update_step_template`, `delete_step_template`,
`reorder_steps`; `create_new_sop_version` now rolls back on a duplicate tag. Tests +3 in `tests/test_sop_versions.py`.
HTTP 14/14 on plan_check: draft id 6 renamed **`v9.1793927904.0-http`** (1 step) left on biz 1.

**M2.3 as built** (no migration; ope side, since biz must not read orders). `GET /ope/sop-impact?biz_id=&effective_date=&limit=`
(`@require_active()`, limit 1–500, default 50) → `SopImpactResponse {biz_id, effective_date, effective_at (00:00 JST),
will_switch, pinned, released}`, each `SopImpactGroup {total (exact), orders (earliest first, ≤ limit)}` of
`SopImpactOrder {id, template_id, template_name, scheduled_start, status, pinned_sop_version_tag,
frozen_sop_version_tag}`. Groups (orders of the biz scheduled ≥ effective_at, not finished / canceled):
**will_switch** = `released_at` null + not pinned; **pinned** = not released + pinned; **released** = released (ready /
in progress / on hold). `src/domain_ope/sop_impact.py` (`sop_impact(db, biz_id, effective_date, limit)`; joins
PlanTemplate with `include_deleted`). Tests `tests/test_sop_impact.py` (2; client prefix **`Z`**). HTTP 6/6 on
plan_check (biz 1, a week ahead: 158 / 0 / 1). Gotcha: a backgrounded command whose cwd drifted to `frontend/` ran
without its leading `cd` → start uvicorn with absolute paths (`/c/p/postgres-migration-poc/.env`, `.venv`, `LOG_FILE`).

**M2.4 as built** (frontend only). `/settings/sop?biz=&version=` (「SOP」 = 3rd entry of the 設定 menu; 「SOP」 button on
業務 rows for live SOP businesses — not LIGHTWEIGHT; page shows an info alert for LIGHTWEIGHT). Business select =
`bizOptions` (moved from PlanSettingsPage to `domain/biz/lib/businesses.ts`). Layout `.sop-layout` (grid: versions
left 280–360 px, steps right; one column under 900 px): versions table タグ / 状態 (草稿 / 予定 gold / 現行 green / 過去,
past rows greyed) / 有効期間, row click → `?version=`; default selection `defaultVersion` = current → scheduled →
newest. Right: tag + state + period, steps table 番号 / 手順 (title + 承認で次へ tag + content pre-wrap) / 含める条件
(`conditionText`: 毎回 / 3月・9月のみ / 週の最初の予定のみ). Admin: 「新しい版」 modal (`NewDraftModal`: says what it copies
via `newDraftSource`, tag prefilled by `suggestNextTag` = highest vX.Y.Z with Y+1, `VERSION_TAG_PATTERN`, duplicate check)
and 「草稿を削除」 (confirm). New: `domain/biz/api/sopApi.ts` (listVersions(bizId, includeDrafts), getVersion, newDraft,
deleteDraft), `domain/biz/lib/sop.ts` (`canManageSop` active admin, labels / colors, helpers above), types `SopState`,
`SopVersionDetail`, `SopStep`, `SopStepCreate`, `SopStepUpdate`. **`periodText`** in `sopVersions.ts` shows the last day
(expired_at − 1 ms, since expired_at = next version's start since M2.1); `pinOptions` in the order drawer uses it too.
CSS `.sop-layout`, `.sop-version-selected` (`--select`), `.sop-step-content`. Harness 17/17 on plan_check (draft created
and deleted again).

**M2.5 as built** (frontend only). On a **draft** (admin): header buttons タグを変更 (`RenameDraftModal`) / ステップを追加 /
草稿を削除; steps table gets 操作: ↑ / ↓ (swap via `movedStepIds` → `reorderSteps` = renumber 1..n), 編集, 削除 (confirm;
numbers kept); info alert + 「番号を振り直す」 when `hasNumberGaps`. `StepFormModal` (add / edit): 手順 (1–100), 内容,
含める条件 radio 毎回 / 指定した月だけ / 週の最初の予定だけ with help text (`CONDITION_LABEL` / `CONDITION_HELP`), month
checkboxes 1–12 (`.sop-months` grid of 6) required ≥ 1 (`conditionError`), 承認で次へ checkbox with explanation.
`sop.ts`: `conditionBody` (months deduped + sorted), `stepFormValues`, `stepCreateBody` (blank content → null),
`stepUpdateBody` (changed fields only, null when unchanged), `movedStepIds`, `hasNumberGaps`. `sopApi`: renameDraft,
addStep, updateStep, deleteStep, reorderSteps. Harness 16/16 on plan_check (draft id 6 `v9.1793927904.0-http` now has 2
steps).

**M2.6 as built** (frontend only; doc rev 81). Draft (admin): 「承認…」 button, disabled with the reason
(`approveBlockedReason`: draft only, ≥ 1 step, no other scheduled version) also shown as a line under the header.
`ApproveModal` (`destroyOnClose`): DatePicker from tomorrow (`firstEffectiveDate(todayIn(TIMEZONE))`), text "<date> 以降
… が <tag> を使います。現行の <tag> は <前日> までになります", impact via `sopImpactApi.get(bizId, date, 20)`
(`domain/ope/api/sopImpactApi.ts`, types `SopImpact*`) in three sections 新しい版を使うオーダー / 指定した版のまま /
リリース済み（固定した版のまま） with total, up to 20 orders (time, template, status tag, kept tag, #id) and 「ほか N 件」;
approve → `sopApi.approve(bizId, id, date)`. Scheduled version (admin): 「予定を取り消す」 (confirm; backend refusal e.g.
pinned orders shown 8 s) → `sopApi.cancelApproval`. CSS `.sop-impact*`. Harness 12/12 on plan_check (draft id 6 approved
for tomorrow, impact read, second approval 400, cancel → draft again; current v9 open-ended again). **Not checked in a
browser.**

### Login (branch `feature/login`, 2026-11-06; doc rev 82)
User's decisions (all as I recommended): **server-side DB sessions in an HttpOnly cookie, 12 h idle, max 7 days**
(logout / suspend / password change end them); **a system_admin sets a temporary password, the person must change it
at the next login**, everyone can change their own, 5 wrong tries → 15 min lock; **API key per signal_sender** for
`POST /ope/signals` (shown once, hashed, revocable); **X-Staff-Id removed entirely** (harnesses log in). My calls:
stdlib `hashlib.scrypt` (no new dependency), cookie `Secure` from a setting (off on plain-HTTP intranet), SameSite=Lax
as CSRF protection (same-origin app). Step names **LG.x** (L.x is the latest-before phase).
| # | Step | Status |
|---|---|---|
| LG.1 | Model + building blocks (no endpoint uses them yet); migration `b4e8d2a6c913` | **done** (`3a93ffd`) |
| LG.2 | Backend switch: `/auth/login` / `logout` / `password`, cookie in `get_current_staff`, X-Staff-Id removed, Bearer key on `POST /ope/signals`, must_change_password → 403 elsewhere, temporary password + API key endpoints (system_admin), suspend / password ends sessions, `create_staff.py` password prompt + `scripts/set_password.py`, guard tests; update the doc's X-Staff-Id paragraph | **done** (`c644b4d`) |
| LG.3 | Frontend (as built below) | **done** (`311c38e`) |

**LG.1 as built** (migration `b4e8d2a6c913`). `staff_user` + `password_hash` (String 255, null = cannot log in),
`must_change_password`, `failed_logins`, `locked_until`, `password_changed_at`. New `staff_session` (`token_hash`
unique SHA-256 hex, `last_seen_at`, `expires_at` = created + 7 d, `revoked_at`) and `staff_api_key` (`label`,
`key_prefix` = first 10 chars, `key_hash` unique, `created_by_id` / `revoked_by_id`, `last_used_at`, `revoked_at`); FKs to
staff RESTRICT (staff are never deleted). `src/domain_staff/credentials.py` (no commits; callers commit):
`hash_password` / `verify_password_hash` (format `scrypt$n$r$p$salt$hash`, n=2^14 r=8 p=1, `hmac.compare_digest`),
`password_problem` (8–128 chars, no edge spaces, ≠ login ID), `set_password(temporary, keep_session_id)` (clears lock,
ends the staff's other sessions), `authenticate` (row locked; unknown login still hashes once; 401
`INVALID_CREDENTIALS` "Login ID or password is wrong."; 5th failure → `locked_until` = now + 15 min, 403
`ACCOUNT_LOCKED` "…after HH:MM JST"; non-active → 403 `STAFF_NOT_ACTIVE` only after a correct password),
`create_session` (token `secrets.token_urlsafe(32)`), `resolve_session` (revoked / idle 12 h / max age → None; touches
`last_seen_at` at most once a minute), `revoke_session`, `revoke_all_sessions(except_session_id)`, `issue_api_key`
(signal_sender only, label required, key `ops_` + 32 random bytes), `resolve_api_key` (records `last_used_at`),
`revoke_api_key`. New `ErrorCode`s `PASSWORD_CHANGE_REQUIRED`, `ACCOUNT_LOCKED` (`INVALID_CREDENTIALS` existed).
Messages are English like the rest of the API; the login page maps codes to Japanese (LG.3). Tests
`tests/test_credentials.py` (6, no client). Round trip + `alembic check` on plan_check OK.

**LG.2 as built** (no migration). User decision 2026-11-06 (as I recommended): **login for everything** — every API
call needs a session (or the signal API key); open: `/auth/login`, `/auth/logout`, `/openapi.json` + `/docs`, `/`
(and the old `/test-error` in main.py).
- `src/domain_staff/auth.py`: `SESSION_COOKIE = "ops_session"`; `get_current_staff(request, db, session_token =
  APIKeyCookie, bearer = HTTPBearer)` (OpenAPI shows `security: APIKeyCookie / HTTPBearer`): session → staff and
  `request.state.session_id`; Bearer key only on **`API_KEY_ROUTES = {("POST", "/ope/signals")}`** (route path from
  `request.scope["route"]`); commits the touch; cached on `request.state.staff`; else 401 "Login required.".
  `require_login` (added to every router in `main.py`: `include_router(..., dependencies=[Depends(require_login)])`):
  non-active → 403 `STAFF_NOT_ACTIVE` except `GET /staff/me`; `must_change_password` → 403 `PASSWORD_CHANGE_REQUIRED`
  except **`PASSWORD_CHANGE_ROUTES`** (`GET /staff/me`, `POST /auth/password`, `POST /auth/logout`).
  `current_session_id(request)`. `require_roles` / `require_active` unchanged. **X-Staff-Id is gone.**
- `src/domain_staff/auth_router.py` (`/auth`, no global dependency): `POST /login` (`LoginIn`) → `StaffResponse` +
  cookie (HttpOnly, SameSite=Lax, Path=/, max-age 7 d, `Secure` if **`COOKIE_SECURE=1`** — new `settings.cookie_secure`,
  default false); `POST /logout` → 204, revokes + deletes the cookie (works without a session); `POST /password`
  (`PasswordChangeIn`; `Depends(require_login)`; wrong current → 401, same as current → 400; keeps this session).
- Staff router (system_admin): `PUT /staff/{id}/password` (`TemporaryPasswordIn`; not yourself → 400) · `GET /staff/{id}/api-keys`
  · `POST /staff/{id}/api-keys` (`ApiKeyCreate {label}` → 201 `ApiKeyIssuedResponse` with `key` once) ·
  `POST /staff/api-keys/{key_id}/revoke`. `set_staff_status` to non-active revokes the staff's sessions.
  `StaffResponse` + `has_password`, `must_change_password`, `locked_until` (model property `has_password`).
- Services `login` (commits failure counts even when it raises), `logout`, `change_own_password`,
  `set_temporary_password`, `list_api_keys`, `issue_api_key`, `revoke_api_key`.
- Scripts: `create_staff.py` asks for the password twice (getpass) and sets it (not temporary);
  **new `scripts/set_password.py <login_id> [--temporary]`** (getpass twice; clears lock, ends sessions).
- Tests: new `tests/openapi_auth.py` `needs_login(op)`; nine files' x-staff-id checks → `needs_login`; "reads stay
  open" → `test_reads_need_a_login_but_no_role`; `test_staff.py` `test_get_current_staff_reads_the_session_or_an_api_key`
  (fake starlette `Request` with `route`); new `tests/test_login.py` (4).
- **plan_check passwords** (set by my scratchpad `seed_passwords.py`, plan_check only): every staff id N has
  **`check-pass-N`** (e.g. 8 admin `http-admin-6eb1ee` / `check-pass-8`, 1 `check-admin` system_admin, 4 operator
  `http-operator-1793324910`, 5 releaser `http-releaser-1793324911`, 9 signal_sender `signal-bot-cc9a19`). HTTP 26/26
  (cookie flags, logout, temporary password flow, lock 5 → unlock via temporary, suspend ends session, API key issue /
  list / signal 201 / refused elsewhere / revoked). Left: one more unmatched signal `http_check`, a revoked key on staff 9.
- **Harness pattern from now on:** log in first (`POST /auth/login`, keep the cookie: Python `http.cookiejar`; node:
  read `set-cookie` and send `Cookie`).

**LG.3 as built** (frontend; backend only the 401 description text in `src/core/schemas/error.py`).
- **Removed:** `app/ActingStaffPicker.tsx`, `shared/api/actingStaff.ts`, `shared/hooks/useActingStaffId.ts`,
  `DEFAULT_STAFF_ID` / `VITE_DEFAULT_STAFF_ID` (config, vite-env.d.ts, .env.example); http.ts no longer sets a header.
- `shared/api/session.ts` (store `{version, signedOut}`; `markSignedIn` / `markSignedOut`) + `shared/hooks/useSession.ts`;
  http.ts `onResponse`: any 401 except `/auth/login` → `markSignedOut()`. `useCurrentStaff` / `useStaffList` /
  `useRoles` / `useSlaReport` re-fetch on `version` (no more `enabled: actingId !== null`).
- `AppLayout` = the login gate: `signedOut` or me 401 → `<Navigate to="/login" state={{from: location}} />`; loading
  spinner; other error → alert + reload; **`must_change_password` → full-page 「パスワードを変更してください」 card
  (`PasswordChangeForm`)** instead of nav + pages; else nav with **`UserMenu`** (name pill reusing `.acting-staff`
  CSS; menu: ログインID, パスワードを変更 (modal), ログアウト → `authApi.logout` + `markSignedOut` + `/login`).
- Route **`/login`** outside the layout (`features/auth/LoginPage.tsx`: ログインID / パスワード, error text by code via
  `domain/staff/lib/login.ts` `loginErrorText` (INVALID_CREDENTIALS / ACCOUNT_LOCKED with the HH:MM / STAFF_NOT_ACTIVE),
  success → `markSignedIn` + back to `state.from`; already logged in → redirect). `PasswordChangeForm` (current / new /
  confirm; `newPasswordProblem` mirrors the backend rules; `passwordChangeErrorText`).
- `domain/staff/api/authApi.ts` (login, logout, changePassword); `staffApi` + setTemporaryPassword, listApiKeys,
  issueApiKey, revokeApiKey; types `LoginInput`, `PasswordChangeInput`, `ApiKey`, `ApiKeyIssued`.
- 職員 page: 「この職員として操作」 and the acting-staff alerts removed; 状態 column adds tags パスワード未設定 (gold) /
  仮パスワード (blue) / ロック中 (red, until HH:mm); system_admin actions **仮パスワード** (`TemporaryPasswordModal`, not
  yourself) and, for signal_sender staff, **API キー** (`ApiKeysModal`: list with prefix / issued / last used / 取消済み,
  発行 with a label → green alert with the full key + copy button, shown only until the modal closes; 取り消す confirm).
- Texts "ナビの「操作する職員」で…" in OrderDrawer / SignalsPanel / SLA → "このアカウントでは…（利用開始前・停止中）".
- CSS `.login-wrap`, `.login-card`, `.api-key-value`; removed the picker-only `.acting-staff-panel` / `-note` /
  `.is-warning` / `.ant-tag` rules.
- Checked: build; node harness 18/18 against 8001 with a cookie-keeping `fetch` (scratchpad `cookie_fetch.ts`, imported
  first): 401 → signedOut, login failure doesn't sign out, cookie set / cleared, temporary password → 403
  PASSWORD_CHANGE_REQUIRED → change → unblocked, API key issue / list / revoke / non-sender 400; **curl through Vite 5174**:
  401 → login Set-Cookie passes the proxy → 200 → logout 204 → 401; `/login` served. plan_check staff 6 password is
  `check-pass-6` again; API key on staff 10 revoked. **Not checked: the browser.**

### Deployment (branch `feature/deployment`, 2026-11-06; doc rev 89)
User's decisions: **intranet, plain HTTP, no domain, no HTTPS**; **daily `pg_dump` to a local folder** (`./backups`),
the user syncs it to the NAS at OS level; (asked, as I recommended) **01:00 JST, keep 14 days**; **migrations run
automatically at app start**. Earlier: Linux VM + Docker Compose, worker loop. My calls: a separate **`web` (nginx)**
service serves the built frontend and proxies `/api/` → `app:8000/` (same origin, like the Vite proxy) instead of
FastAPI serving static files; compose files live in **`deploy/`** (so the dev `.env` at the root is never read by
Compose; build context `..`); **uvicorn 1 process** (one writer for the rotating `app.log`); worker checks releases at
**second 1 of every minute** (release times are minute-granular ⇒ ≤ 1 min late, same outcome as "sleep until the next
moment, cap 60 s", much simpler).
- `scripts/worker.py`: `order_sync.register()`, loop: plan extension once a day after `WORKER_EXTEND_AT` (00:05 JST;
  also right after start if that day's run hasn't happened), then `release_due_orders`, then
  `stop.wait(seconds_until_next_run)`; errors logged, loop continues; SIGTERM/SIGINT stop after the current pass;
  logs to stdout (releases logged only when something was released). Pure helpers `seconds_until_next_run`,
  `extension_due`, `parse_time` → `tests/test_worker.py` (2, no DB). Ran 75 s against plan_check OK.
- `deploy/app.Dockerfile` (python:3.14-slim + tzdata, TZ=Asia/Tokyo, requirements, copies alembic / main.py / src /
  scripts; `deploy/app-entrypoint.sh`: mkdir logs, `alembic upgrade head`, `exec uvicorn main:app --host 0.0.0.0
  --port 8000 --workers 1 --proxy-headers`), CRLF stripped in the image. `deploy/web.Dockerfile` (node:24-alpine
  `npm ci` + `npm run build` → nginx:1.27-alpine) + `deploy/nginx.conf` (SPA fallback `try_files $uri /index.html`,
  `/assets/` cached 1 y immutable, index no-cache).
- `deploy/docker-compose.yml` (project `ops`): `db` postgres:16 (volume `pgdata`, pg_isready healthcheck), `app`
  (image `ops-app`, `./logs:/app/logs`, `LOG_FILE=/app/logs/app.log`, healthcheck GET `/`), `worker` (same image,
  `python scripts/worker.py`, waits for app healthy = migrations done), `web` (image `ops-web`, port
  `${HTTP_PORT:-80}:80`), `backup` (postgres:16 running `deploy/backup.sh` mounted read-only, `${BACKUP_DIR:-./backups}`);
  shared env `DATABASE_URL` built from `POSTGRES_*`, `ENVIRONMENT=production`, `COOKIE_SECURE=false`, `TZ`; json-file
  logs 10 MB × 5. DB port not published. `deploy/.env.example` (POSTGRES_DB / USER / PASSWORD — letters + digits only,
  HTTP_PORT, BACKUP_DIR / TIME / KEEP_DAYS).
- `deploy/backup.sh`: daily loop (GNU `date -d "today/tomorrow HH:MM"`), `pg_dump --format=custom --compress=6` to
  `<db>_YYYYMMDD_HHMMSS.dump` via a `.partial` name, then `find -mtime +(KEEP-1) -delete` (KEEP 0 = keep all); `once`
  argument for an immediate dump; `BACKUP_DEST` overrides `/backups` (for testing). Tested on the office PC with the local
  PostgreSQL 16 `pg_dump` against plan_check: dump 212 KB, `pg_restore --list` OK, 20-day file removed / 5-day kept, the
  loop fired at the set minute.
- `deploy/README.md` (Japanese runbook): first start, first admin via `docker compose exec app python
  scripts/create_staff.py admin "…" --roles system_admin admin`, update (`git pull` + `docker compose up -d --build`),
  logs, backup now / restore (`pg_restore --clean --if-exists --no-owner` with app + worker stopped), common commands,
  notes (COOKIE_SECURE, `/api/openapi.json`, no Task Scheduler on the server). **Superseded by G.3** (§2 "Go-live"):
  no git on the server — USB zip, `deploy/install.sh`, `/srv/ops` layout, proxies.
- Repo: `.dockerignore` (root), `.gitattributes` `*.sh text eol=lf`, `.gitignore` + `deploy/backups/`, `deploy/logs/`
  (`deploy/.env` already ignored by `.env`).
- **Not verified (no Docker here):** image builds (python:3.14-slim + `psycopg-binary` wheel, `npm ci` in node:24),
  nginx config, compose start-up order / healthchecks, the backup service inside the postgres image. First real run =
  on the VM; check `docker compose ps` / `logs`.

### Go-live (branch `feature/go-live` from `main` `6390c87`, 2026-11-06)
User's decisions: see §1 "Go-live decisions". Then (2026-11-06): **the VM can't reach GitHub → code by zip on a USB
drive**; **VM layout = a fresh folder per zip, data outside** (`.env`, backups, logs in one fixed folder such as
`/srv/ops`, so a file removed in a new version — e.g. a renamed migration — can't linger and break alembic). Then
(user): **internet only through a corporate HTTP/HTTPS proxy**; Windows Server is temporary, **the final server is
Linux**. Decisions (all as I recommended): **build on the server through the proxy** (not `docker save` tars — no
machine with Docker + internet exists); **optional company CA** for proxies that inspect HTTPS. Checking it on the server
is still to do: `docker pull hello-world`, `curl -sI https://pypi.org/simple/`, `curl -sI https://registry.npmjs.org/`.
| # | Step | Status |
|---|---|---|
| G.1 | `scripts/setup_calendar.py [--code JP_BANK] [--name 銀行営業日] [--weekend 6 7]`: creates the business calendar via `create_calendar`; existing code → prints it, changes nothing, exit 0 (note on stderr when `--weekend` differs); bad input exit 1. `tests/test_setup_calendar.py` (2, no client) | **done** (`450f03e`) |
| G.2 | `deploy/GO-LIVE.md` (Japanese checklist: A pre-check + **restore drill into a scratch DB `ops_restore_check`** with `deploy/restore-check.sql` = alembic version + 6 table counts, B first admin / setup_calendar / holidays incl. bank 12/31 1/2 1/3, C pilot client incl. "SOP effective tomorrow at the earliest → approve the day before the first order", D staff + role table + four-eyes, E orders + `create_orders.py` → `Created 0 orders`, F browser checks, G first days: worker / backup / NAS / plan extension log lines, H other clients); README §1 links it; doc rev 91. Checked: drill commands with local PG 16 tools on plan_check (restore exit 0, counts identical, scratch DB dropped), `create_orders.py` output, UI labels / messages grepped from the frontend. Docker-only commands not run | **done** (`3771751`) |
| G.3 | USB zip + proxy (as built below) | **built, uncommitted** |

**G.3 as built** (no migration, no app code). Layout on the server: `/srv/ops/{.env, certs/, backups/, logs/,
releases/ops-YYYYMMDD-<sha>/, current -> releases/…}`; all `docker compose` commands in `/srv/ops/current/deploy`.
- `scripts/make_package.py [--out packages] [--allow-dirty]`: refuses with uncommitted tracked changes; `git -c
  core.autocrlf=false archive --format=zip --prefix=ops-<commit date>-<sha>/` of HEAD → `packages/` (git-ignored);
  prints the two server commands. **The `-c core.autocrlf=false` matters:** the office PC has `autocrlf=true`, and a
  plain `git archive` turned 281 of 285 files CRLF; with it, 0.
- `deploy/install.sh` (same for first install and updates; `OPS_HOME` default `/srv/ops`): needs `/srv/ops/.env`
  (else prints the cp command, exit 1) → `ln -sfn` it as the release's `deploy/.env` → copies `/srv/ops/certs/*.crt` into
  `deploy/certs/` → if the `backup` service is running (`docker compose ps -q backup`; project name fixed `ops`, so the
  old release's containers are visible) `backup.sh once` → `docker compose up -d --build` (blocks until app is healthy
  because worker depends on it) → only then `ln -sfn` release as `/srv/ops/current` → `ps`. Rollback = run an older
  release's install.sh (README warns: not across a migration; restore the pre-update backup instead).
- Proxy: compose `x-build-args` (HTTP_PROXY / HTTPS_PROXY / NO_PROXY + lowercase, from `.env`, empty = direct) on app,
  worker and web builds — Docker's predefined build args, no `ARG`, not kept in the image; **not** passed to running
  containers, and `x-app-env` sets `NO_PROXY=localhost,127.0.0.1,db,app,web` in case Docker injects proxies (the app
  healthcheck uses urllib, which honors it). `docker pull` proxy = `/etc/docker/daemon.json` `proxies` (README §0;
  Docker Engine 23+), apt proxy `/etc/apt/apt.conf.d/95proxy`.
- Company CA: `deploy/certs/` (README.md only in git; `*.crt` / `*.pem` git-ignored). app image: copied to
  `/usr/local/share/ca-certificates/company/` + `update-ca-certificates` + `PIP_CERT=/etc/ssl/certs/ca-certificates.crt`;
  web build: `*.crt` concatenated → `NODE_EXTRA_CA_CERTS` for `npm ci` (only when a file exists). Host trust for `docker
  pull`: README §0.5.
- `.env.example`: `BACKUP_DIR=/srv/ops/backups`, new `LOG_DIR=/srv/ops/logs` (compose `${LOG_DIR:-./logs}`), proxy
  block. README rewritten (folder layout, §0 server prep with proxies, §1 first start via zip, §2 update / rollback,
  `sudo` + `/srv/ops/current/deploy` everywhere); GO-LIVE.md paths updated. `.dockerignore` + `packages`.
- Checked: compose parsed with js-yaml (anchors expand on app / worker / web, NO_PROXY in app / worker env, volumes);
  `sh -n` install.sh / backup.sh; pyright 0; make_package refused dirty, built a 731 KB zip (285 files, one top
  folder, no `.env` / logs / node_modules, 0 CR); install.sh with a fake `docker` in Git Bash: no .env → exit 1, first
  install, update with backup first, failed build → exit 1 and `current` untouched. **Not checked (no Docker):** the
  builds through a real proxy, `update-ca-certificates` in python:3.14-slim, NODE_EXTRA_CA_CERTS with npm, daemon.json.

### Todo pool: signal transport "B" — SFTP watcher (parked 2026-11-05, user's choice: start with A)
**A (in use now, nothing built):** the client-file business = LIGHTWEIGHT + MANUAL completion (手で受領), automatic
release, a template at the expected arrival time (its end = the 遅れ deadline); a person checks the client's SFTP in
WinSCP and presses 受領 in the drawer. Downstream jobs wait via 業務の依存関係.
**B (todo, not planned):** a scheduled script (Task Scheduler, every few minutes) that lists the SFTP folders and posts
`POST /ope/signals` for new files → the business switches to AUTOMATIC. Facts from the user (2026-11-05): source is
**SFTP**; today **one user monitors it by hand in WinSCP**; the client's server has **several folders at different
paths**; **some file names carry a timestamp, some don't**; "received" = **the file exists in the right path** (no
marker / size check for now); the final signal of a client-side job = the file appearing in the right path.
Still unknown: server account / access from the scheduler host (read-only?), the date to use when a name has no
timestamp (arrival day JST?), whether files stay after download, how to avoid resends (state file recommended).
Possible first step for both A and B: store the expected SFTP path + file pattern on the business and show it in the
drawer next to 受領 (the person knows where to look; B's watcher reads the same config).

### Sheet import (branch `feature/sheet-import` from `main` `357c81a`, 2026-11-10; doc rev 95)
User's decisions 2026-11-10: annual plan sheets are a core feature from day one; **the system never parses the client's
raw files** (confidentiality) — the team converts Excel → JSON outside and the system only accepts the final JSON;
import path = **an import screen** (chosen over a server script / the converter calling the API); in "add only" the
screen **widens the period** so a file with only the new year can be added (user's scenario: one file per year,
non-overlapping). Irregular tables (JCB year-end with × days): the team computes "rule dates minus × plus ○" outside
and imports the result as a plain marker — the file has no "×", a date is removed by being absent in match mode.
A "patch mode" for the import (remove entries applied to rule markers as cancel records) was discussed, NOT built.
- **S.1 `ec5c23f` — frontend only, no backend change, no migration.** 年間計画シート page: 「シートを取り込む」 (admin +
  client selected) → `SheetImportModal.tsx`: `Upload.Dragger` (not sent; `file.text()`), summary, modes 追加だけ /
  ファイルに合わせる (`?replace_events=true`, red button), preview alert, result counts. Pure logic in
  `features/plan-settings/sheetImport.ts`: `parseImportFile` (BOM stripped, shape problems all listed, trims, drops
  extra keys) and `previewImport` (same rules as backend `import_sheet`: adds skip dates a person moved / cancelled;
  match mode deletes non-manual dates incl. every date of a non-rule marker absent from the file → `emptied` warning;
  blockers: file client ≠ selected client (screen-only rule), rule marker with dates in the file; `effective` = the
  input actually sent, period = union with the existing sheet in add-only). `sheetApi.importSheet`. 4 MB limit in the
  screen (nginx 5m; 20,000 dates ≈ 1 MB).
- **Backend facts confirmed by the harness:** the file's period REPLACES the sheet's; add-only with existing dates
  outside the new period → 400 "outside the new period" (so API callers must keep `period_start` and only extend
  `period_end` — the widening is in the screen, not the API); rule markers regenerate over a changed period (extended
  to the new end); a bad file → 422 with all problems, nothing written. No API deletes a sheet.
- Checked: `npm run build`; esbuild harness of the real modules against uvicorn 8001 on `plan_check`, admin staff 8
  logged in — **97/97** (preview counts = server counts in every case, mock file twice = no change, yearly 2026 → 2027
  file, match mode, person edits kept, rule marker untouched). Left test sheets `HARNESS_IMPORT_…` / `HARNESS_YEARLY_…`
  on client `MOCK` in `plan_check`. **The modal was not opened in a browser by me**; the user said "go" after saying
  they would check it.
- **S.2** `deploy/GO-LIVE.md` C: sheet step before the templates (import, compare with the source table, rule markers,
  next year's file with 追加だけ, match-mode warning) + the 年間計画シート template type. **S.3**
  `docs/OPERATION-MANUAL.md` 3.8 rewritten (import procedure, mode table, rules, JSON format + limits, API difference
  about the period, editing on screen); limits table: raw tables are not read, no sheet delete.

### Safer changes to businesses and templates (strategy agreed with the user 2026-11-10; doc rev 96; nothing built)
Goal: less burden and risk on the admin. Today `update_template` applies at once (`_regenerate` → revision + future
reconcile, orders follow); templates have no status and no who-changed-what; `src/domain_plan/engine.py` is pure
(expand_rrule / expand_explicit_dates / expand_sheet_events), so a dry run is cheap.
- **Level 1 — NEXT:** preview before saving in the template create / edit forms: the calendar the template would
  produce; for an edit the diff (dates added / removed / moved) and the affected orders (how many released / started).
  No data model change. Same idea as the SOP impact list.
- **Level 2 — after the pilot, one phase with "finer permissions":** saved drafts + approval. User's rules: releaser
  drafts, **only admin approves**; a non-admin drafter cannot approve their own draft; an admin may approve their own
  draft or apply directly; approval applies **immediately** (no effective date); **a pending draft blocks other edits
  to the same live template** (one draft per template); record drafted-by / approved-by. No draft state on the
  business itself (a business with only draft templates generates nothing; release settings → affected-orders preview
  + change history). **Open:** what four-eyes means for a person holding both releaser and admin (flat team, most
  people have several roles) — I raised it, not answered yet.
- **Dropped for good (user, 2026-11-10): effective-dated / scheduled template versions ("Level 3").** The user called
  it superfluous and asked to remove it from plans and the design doc (removed in rev 99). **Do not propose it again.**
- **Step plan approved 2026-11-10, branch `feature/template-preview` from `main` `d8a8f9e`.** User's answers: templates
  only (business release settings later); preview **required when dates change** on an edit (name / deadline edits save
  as today; for a new template the preview is a button); default **3 months, extendable to 12**; the preview runs the
  **real recalculation and discards it** (user agreed explicitly). P.1 backend preview of an edit · P.2 backend preview
  of a new template · P.3 frontend · P.4 manual + doc + MEMORY.
- **P.1 as built (no migration):**
  - `src/infrastructure/dry_run.py`: `with dry_run(db):` → rollback on exit; a `before_commit` listener raises
    `RuntimeError` while the flag is set, so a committing function called by mistake saves nothing.
  - `slot_events.publish_pending(session)` (extracted from the `before_commit` hook) lets a dry run hand the pending
    slot changes to the listeners (order sync) without committing.
  - `plan services`: `update_template` = `_apply_template_update` (no commit; returns `(template, generation_changed)`)
    + commit. New public, non-committing `dry_run_materialize(db, id, since, until)` → `(occurrences, effective_until)`
    (effective end = later of `until` and `materialized_until`, because a real change reconciles the whole generated
    range) and `dry_run_update(db, id, body, since, until)` → `(occurrences, generation_changed)`; both return
    `DryRunOccurrence` values (slot kind/ref, occurrence_date, start, end, manual).
  - `src/coordination/template_preview.py` `preview_template_update(db, id, body, months=3)`: baseline (materialize to
    the range end, publish, snapshot orders) → apply the change (publish, snapshot orders) → rollback → diff by slot:
    `added` / `removed` / `moved`; `manual_unchanged`; `orders_total`, `orders_created`, `order_effects`
    (`rescheduled` / `canceled` / `conflict`, with the status before). A rule change (e.g. BYDAY MO → TU) is
    removed + added, not moved (different slots — the real system cancels and creates orders the same way).
  - **`POST /ops/templates/{template_id}/preview?months=1..12`**, body = `PlanTemplateUpdate`, admin only; errors are
    the same as the real PATCH. Explicit-date add / remove (`/dates` endpoints) is not previewed.
  - Tests `tests/test_template_preview.py` (7; client prefix **`"I"`** — no free prefix letter is left; next file must
    share one or widen `new_client_code`). Harness `preview_harness.py` on `plan_check`: 114/114 over all 19 templates
    (preview 200, template + instances unchanged after, name-only change touches nothing, 401 / 403 / 404 / 422, a real
    save still works). OpenAPI re-exported + `npm run gen:api`.
  - **Found while testing (existing behaviour, NOT changed, told the user):** moving a template's time later on a day
    whose old time has already passed leaves **two live occurrences in the same slot today** (the past one is never
    touched, a new one is generated at the new time); the order stays on the old one. The preview shows the new one
    under `added` for today.
  - Side effect to know: a dry run consumes sequence values (ids of instances / orders skip numbers).
- **P.1 committed `595f5f8`. P.2 as built (no migration):** `create_template` = `_apply_template_create` (no commit)
  + commit; `plan_services.dry_run_create(db, body, since, until)` → `(temporary template id, occurrences, effective
  until)`; `preview_template_create(db, body, months=3)` → `TemplateCreatePreview` (range, occurrences,
  `orders_created`); **`POST /ops/templates/preview?months=1..12`**, body = `PlanTemplateCreate` (all three types),
  admin only, same errors as the real POST. The range reaches what creation generates if that is longer (90 days, or
  the next occurrence of a yearly job). Tests: `tests/test_template_preview.py` now 10. Harness
  `preview_create_harness.py` on `plan_check` 21/21 (a copy of an existing rrule / explicit-dates / sheet template
  previews the same dates as the original; nothing created; 401 / 403 / 404 / 422; a real create still works).
  P.2 committed `707e1f8`.
- **P.3 as built (frontend only):** `templateApi.previewCreate / previewUpdate`; pure logic
  `features/plan-settings/templatePreview.ts` (`marksOfCreate` / `marksOfChange` → day marks same / added / removed /
  moved / moved_from / manual; `monthsToShow`, `monthCells` Sunday-first, `orderImpact` with released counts);
  `TemplatePreviewPanel.tsx` (range + 3 / 6 / 12 か月 switch, summary alert, legend, mini month grids `.pv-*` in
  `styles.css`, Collapse with date lists and the affected-orders table — opened by default when a non-pending order is
  touched). `TemplateFormModal.tsx`: **edit** → 保存する first calls `previewUpdate`; `generation_changed` false (name /
  deadline / display) saves as before; true opens a second modal 「保存の前の確認」 with 戻って直す / この内容で保存する
  (saves the values captured at preview time). **Create** → new footer button 「予定を確認」 (optional) opens the same
  modal with この内容で作成する. A preview error blocks the save and is shown as the save error. Explicit-date add /
  remove is not in the preview (the panel says so when dates are pending). Checked: `npm run build`; esbuild harness
  `preview_ui_harness.ts` of the real modules on `plan_check` — 659/659 over all 19 templates (**an unchanged form
  never triggers the confirmation**, name-only neither, a time change does; marks and order counts equal the API
  result; month grids; 12-month range; nothing saved). **Not opened in a browser by me.**
  P.3 committed `d7539fe`. **P.4:** `docs/OPERATION-MANUAL.md` 3.7 got 「保存の前の確認」 (steps, a table of what each
  summary line means, the things to know, both preview API calls); design doc rev 101.
- **Releasers preview (Option B, user's decision 2026-11-10; `6b8f653`, merged `61f796f`).** The user expected
  releasers to do the "pre-editing"; both features were admin-only. Offered A (template preview only) / B (A + see the
  import summary, no import) / C (A + import directly, no approval); **chosen: B** — releasers are involved in
  preparation "without bypassing the admin approval principle"; save / create / import stay admin-only until Level 2
  brings saved drafts + approval.
  - Backend: `/ops/templates/preview` and `/ops/templates/{id}/preview` → `PREVIEWERS = [RELEASER, ADMIN]`
    (`src/coordination/router.py`). Nothing else changed; template writes and `POST /plans/sheets/import` stay ADMIN
    (asserted in `test_preview_endpoints_need_a_login_and_releaser_or_admin_while_saving_stays_admin_only`).
  - Frontend: `canPreviewPlanSettings` (active + admin or releaser), `PLAN_SETTINGS_PREVIEW_ONLY`, `PREVIEW_ONLY_NOTE`
    in `domain/biz/lib/settings.ts`. `TemplateFormModal` got `canSave`: false → titles say 保存はできません, footer is
    閉じる + 変更を確認 / 予定を確認, an info note, the preview window has only 戻る, an edit always opens the preview
    (even when no date changes), `save()` returns early. `PlanSettingsPage`: releasers see 「変更を確認」 per row and
    「新しいテンプレートの予定を確認」. `SheetImportModal` got `canImport`: false → title 取り込みを確認（取り込みは
    できません）, only 閉じる, info note; the summary / mode radio / preview are browser-side, nothing is sent.
    `SheetSettingsPage`: releasers see 「取り込みの内容を確認」.
  - Limit told to the user: no saved drafts, so the releaser passes the content to the admin outside the system.
  - Checked: build, pyright, harness `releaser_harness.py` on `plan_check` 16/16 (releaser staff 5: both previews 200,
    sheets GET 200; PATCH / POST / DELETE template and both import modes 403; operator previews 403; admin 200; nothing
    changed). **Not opened in a browser by me.**
- **Started work keeps its occurrence (2026-11-10, branch `fix/started-slot-not-regenerated`).** The user's browser
  test: template 8 不定期テスト (sheet-based, dev DB) 9:00 → 11:00; the 11/26 row then showed the new time, no order
  ("—"), no release / cancel. Cause (read-only SELECTs on dev_db, with the user's OK for that one template): order 888
  was FINISHED on 11/05; the edit voided instance 948 and created 1162; the finished order stayed on 948 and the
  dashboard joins rows to orders by `current_instance_id`. **User's decisions:** an occurrence whose order is finished
  **stays** (not "move and keep the link"); an occurrence whose order is in progress **stays with no 矛盾 flag** on a
  template edit; and (this reverses the earlier "leave it" decision, the user said "go" for it) a slot that already
  started today is not generated a second time.
  - `slot_events.register_slot_guard` / `protected_slots(db, template_id, lower, upper, reason)`; plan asks, coordination
    answers: `order_sync.work_started_slots` → slots of orders FINISHED (every reason) or IN_PROGRESS (only
    `VoidReason.TEMPLATE_REVISED`), `scheduled_start` in the window, no one-offs. **My judgment, told to the user:**
    finished is protected for holiday / sheet-date recalculations too (same orphan otherwise); in progress keeps the old
    rule there (occurrence moves, order gets 矛盾).
  - `_reconcile`: a protected slot that has a live generated instance in the window is neither voided nor re-created
    (`kept_slots`); a slot with a live instance that started before the window is not created again
    (`_started_slots`, repo `get_live_started`). A protected slot with no live generated instance (e.g. after a revert)
    is treated as before.
  - Tests: `test_template_preview.py` now 13 (finished keeps its occurrence — the user's case with a monthly-26th
    template; started work stays on a template edit but a holiday still flags work in progress and leaves finished
    alone; today's started slot not generated again); `test_order_sync.py`
    `test_system_move_follows_or_flags_in_progress` now expects no conflict for in progress on a template edit.
    Screen texts (form note, preview summary) and manual 3.7 updated; design doc rev 103.
  - **The user's dev row is not repaired by the fix:** instance 1162 (11/26 11:00, live, no order) and order 888 on the
    voided 948 stay as they are; I do not write to dev_db.
  - Also seen, NOT changed: (1) moving a time earlier than "now" on a day whose old time is still ahead removes
    today's occurrence (the new time is past, the old one is voided) and cancels its pending order; (2) at the end of
    the generated range a slot can drop out when its new time falls past the range end, and comes back when the
    daily job extends the range (its pending order is cancelled and a new one is created later).
  **Superseded:** the "two occurrences today" behaviour was first left as it is (user, 2026-11-10), then fixed the
  same day (above) after the user saw a row that could not be released or cancelled. Original note: the behaviour stays as it is (no logic to skip today;
  the preview shows it and a person can cancel one of the two; simple and predictable for the MVP).

### Open questions (in the doc)
**Still open:** client-limited role details
(name, one or several client codes per user, read-only or also its own client events) · signal transports beyond HTTP
(file arrival, another database) — the matching key is decided (type + client + rule date) · **finer permissions for
settings** (raised by the operations team 2026-11-10, NOT for the MVP; doc rev 93, last item of the open-questions
list): flat team, frequent small changes to release times and SOP steps would pile up on a few admins. Idea 1:
releasers create / edit SOP drafts, admin only approves — draft and approve are already separate endpoints in
`src/domain_biz/router.py` (all `@require_roles([RoleCode.ADMIN])`), so mostly decorators + showing the SOP editor to
releasers without the approve button; but `SOPVersion` has no drafted-by / approved-by columns (migration if approval
should be a real control). Idea 2: clients + calendars stay admin; release settings (`RELEASE_FIELDS` = release_mode,
release_time, release_days_before) are part of the single `PATCH /biz/{id}` → recommended a separate endpoint for
those three fields + a change history for biz settings (none today). To decide with the team: is admin for 2–3 leads
enough; are the release-time changes permanent or one-day (releasers already override / cancel / restore one day);
may the drafter approve their own draft; new role ("operational lead") or wider releaser. My proposal: go live as is
with 2–3 admins, count real changes during the pilot, then build both as one phase.
**Decided** (ticked in the doc): early start only with a releaser's approval; releaser + admin release / pin /
force-start (now: approve a dependency override) / resolve conflicts; automatic release default 03:00 JST; rejecting
a step resets only that step (2026-10-30); dependencies same client only (2026-11-04); client event = per business
(2026-11-04); settings = admin, calendar occurrence changes = releaser / admin (2026-11-05, doc rev 70); latest
before due = by scheduled start with a look-back in days (2026-11-05, doc rev 72).

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
- **Logging (2026-11-05):** `app.log` rotates by size (`RotatingFileHandler` in `main.py`: 10 MB × 5 backups; env vars `LOG_FILE` / `LOG_MAX_MB` / `LOG_BACKUPS` — real env vars, `.env` is not exported). **Start my uvicorn on 8001 with `LOG_FILE=app-8001.log`** (Windows can't rename a file another process holds open). The user's old app.log was 1.3 GB (SQL echo); SQL logging is **off by default** since 2026-11-05 (`settings.sql_echo`, `SQL_ECHO=1` in `.env` or the env to turn it on); the scripts still force it off for themselves.
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
- **Markers:** imported (`POST /plans/sheets/import`, or the import screen since 2026-11-10 — §2 "Sheet import"; JSON format of `mock_data/desensitized_mock_data.json`; import cannot
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
- **Login (LG.3, replaced the acting-staff picker):** `/login` page; session in the HttpOnly `ops_session` cookie;
  `shared/api/session.ts` + `useSession`; http.ts marks signed out on 401; `AppLayout` redirects to `/login` and forces
  a password change for temporary passwords; nav `UserMenu` (パスワードを変更, ログアウト). Detail in §2 "Login".
- **職員** (`/settings/staff`, `features/staff-settings`): list, 職員を登録, 編集 (name, email, role checkboxes; own
  system_admin locked), 有効化 / 利用停止 / 再開 (confirm; own row disabled), 仮パスワード / API キー (system_admin, LG.3). UI permissions in
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
