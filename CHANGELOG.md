# Changelog

All notable changes to `rteam_prozorro` are documented here.

## [19.0.5.7.2] - 2026-04-30

### Fixed
- Force stop now also clears `is_running` immediately. v5.7.1 only
  set `cancel_requested=True`, so the user clicked Force stop, the
  cancellation was correctly recorded in chatter, but the form
  banner still showed "Sync running" because `is_running` was only
  cleared when the cron handler eventually finished. Verified live
  on test19 with Alex: clicked Force stop -> chatter shows
  "cancellation requested" but banner stuck on running. Now both
  flags clear on Force stop click; cron handler's finally block
  re-clears them idempotently if it was actually running.

## [19.0.5.7.1] - 2026-04-30

### Fixed (banner-vs-chatter contradiction)
- "Sync running" banner now appears immediately on click instead of
  30-60 seconds later when the cron worker picks up the trigger.
  `action_sync_now` flips `is_running=True` via isolated cursor
  BEFORE queuing the trigger; the cron handler sets it idempotently
  on entry. Banner and chatter no longer disagree.
- Removed the v5.6.8 `next: reload` chain on the action_sync_now
  toast (heavy, lost scroll). The bus.bus push from the new helper
  `_push_state_changed_to_managers` does the same job lighter.

### Added (Force stop = real stop)
- New field `prozorro.sync.cursor.cancel_requested`. Cron handler
  reads it via direct SQL (bypassing ORM cache) at start and after
  each tender; clean exit within ~1-3 seconds when set.
- `action_force_clear_running` rewired: instead of just clearing
  `is_running` (a UI-only lie), it now sets `cancel_requested=True`
  via isolated cursor + drops click-spammed immediate trigger rows
  + posts chatter "cancellation requested" + bus push. Cron handler
  picks it up and exits cleanly. Toast wording changed from "Sync
  state cleared" to "Stopping sync...".
- On clean cancel exit, handler posts "cancelled by user (pulled N,
  matched M)" chatter and records `last_error="Cancelled by user
  (pulled N, matched M)"`.
- `finally` block in `_cron_sync_feed` now resets BOTH `is_running`
  and `cancel_requested` so next run starts from clean state.

### Migration
- `migrations/19.0.5.7.1/post-migrate.py`:
  1. Drops ALL `ir_cron_trigger` rows for the sync cron, not just
     future ones. The 5.7.0 migration's `call_at > NOW()` filter
     left past-due rows that fired post-upgrade (observed live on
     test19: 3 stale triggers re-firing the cron with toggle OFF).
  2. Clears stuck `is_running` and `cancel_requested` flags.
  3. The `cancel_requested` column is auto-added by the ORM on
     field declaration; no explicit ALTER TABLE.

### Followups (deliberately out of scope)
- Memory profile: cron handler's full-tender-body in-memory list
  triggered SIGKILL on test19 trial container at 11:47 UTC. Real
  fix is processing in pages with explicit dropping of seen-tender
  payloads; tracked separately.
- Cursor-design rethink (per-run descending walk from head):
  unchanged from 5.6.x.

## [19.0.5.7.0] - 2026-04-30

### Architectural change: trigger-based scheduling

Replaces the entire `ir.cron.nextcall` mirroring pattern (5.5.x-5.6.x)
with permanent "never auto-fire" state plus on-demand
`ir_cron_trigger` rows. Eliminates the lock_for_update bug class
that surfaced in 5.6.5 (cron stuck for hours), 5.6.6 (its postcommit
fix), and the v5.6.9 follow-up (Force stop crashing settings save
mid-run, plus the auto-restart at 14:18 on test19).

### Why this fixes everything at once

The old design wrote to `ir.cron` from three different code paths:
`_inverse_*` methods on settings save, `_reschedule_cron_after_run`
in the cron handler, and the `_inverse_prozorro_sync_interval_hours`
when interval was changed. Any of these colliding with a running
cron raised
    UserError: This cron task is currently being executed and
    may not be modified
because Odoo 19's `ir.cron.write` does `lock_for_update` on the cron
row while the cron handler holds it. Workarounds (postcommit defer,
isolated cursors) helped for one path but not the others; settings
save still failed when a sync was in flight.

### What changed

- Cron row is now permanently parked in `data/prozorro_cron_data.xml`:
  `active=True, nextcall=2099-12-31, interval_number=10000,
  interval_type='days'`. Odoo's framework auto-reschedule after each
  run lands ~27 years out, so the cron will never fire on its own.
- All scheduling moved to `ir_cron_trigger` (a separate table, no
  lock conflict with the running cron):
    * `action_sync_now` -> `cron._trigger()` (immediate)
    * `_reschedule_cron_after_run` -> `cron._trigger(at=now()+interval)`
       if Schedule toggle is ON, else no-op. Uses an isolated cursor
       so the trigger survives a rollback of the cron transaction
       (uncaught exception, OOM, container kill). Auto-sync therefore
       no longer silently stalls after a bad run.
    * `_inverse_prozorro_auto_sync_active` -> queues first trigger on
       OFF -> ON, deletes pending triggers on ON -> OFF.
    * `_inverse_prozorro_sync_interval_hours` -> requeues with new
       interval if Schedule is ON.
- User intent moved fully to `ir.config_parameter`:
    * `prozorro.auto_sync_enabled` ("True" / "False")
    * `prozorro.sync_interval_hours` (int as string)
- `_inverse_*` methods NEVER write to `ir.cron`. Settings save while
  a sync is running no longer crashes (the original Force stop UX
  bug from this session).
- `_compute_prozorro_cron` reads interval from config_parameter, not
  from the cron row.
- Removed dead `SYNC_CRON_NEVER_NEXTCALL` and `NEVER_NEXTCALL`
  constants (no callers after the rewrite).

### Migration

`migrations/19.0.5.7.0/post-migrate.py` runs on upgrade:

1. Seeds `prozorro.sync_interval_hours` from the existing cron's
   `interval_number` if not already in config_parameter.
2. Forces the cron row into the permanent never-auto shape.
3. Clears any pending future triggers from the legacy state.
4. If the user had auto-sync enabled, queues the first new-style
   trigger at the configured interval so the schedule is preserved
   end-to-end across the upgrade.
5. Clears any stuck `is_running=True` on the sync cursor singleton
   (the container restart that triggered the migration killed the
   in-flight sync).

### Tradeoffs / followups

- The old `_reschedule_cron_after_run` was idempotent in that it
  always wrote nextcall=2099 on OFF state. The new version simply
  does nothing when Schedule is OFF. If somehow a future trigger
  exists in `ir_cron_trigger` (manual SQL, partial migration, etc.)
  it will fire once after upgrade. The `_inverse_*` OFF-path's
  DELETE handles this when the user re-saves settings, but no
  automatic cleanup runs at module load. If this becomes a real
  problem we can add a defensive DELETE in the cron handler entry.
- Auto-sync continuity now depends on isolated-cursor commit being
  reliable. If the isolated cursor itself fails (DB connection lost
  mid-handler), auto-sync will stall until the user clicks Sync now
  or re-saves the toggle. Logged with full traceback so we can spot
  it.

## [19.0.5.6.9] - 2026-04-30

### Added
- New OWL service `rteam_prozorro.sync_reload`
  (`static/src/js/sync_reload_listener.js`) subscribes to bus
  notifications of type `prozorro.sync.done` and triggers
  `browser.location.reload()` so any open backend tab of a Prozorro
  manager refreshes after a background sync run finishes.
- `_notify_sync_result` now pushes a second bus message of type
  `prozorro.sync.done` alongside the existing `simple_notification`
  toast. Payload carries `pulled`, `matched`, `error` for future
  selective filtering (current listener reloads unconditionally).
- Manifest gains an `assets` block declaring the listener under
  `web.assets_backend`.

### Why
- v5.6.8 reloaded the form on click, which fixed the
  "queued -> in progress" banner transition. But the cron finishes
  30-60 seconds later in the background. `matched_count`,
  `last_match`, `sync_last_finished_at`, and the in-progress / OK
  banners were still stale from the click-time render until the
  user manually refreshed. The bus push closes that loop.
- Tradeoff: every Prozorro manager with a backend tab open
  reloads, regardless of which view they are on. Acceptable for
  the current debug cycle; gated to be replaced with a view-
  conditional `soft_reload` (Option C) before apps.odoo.com
  submission polish.

## [19.0.5.6.8] - 2026-04-30

### Changed
- `Sync now` toasts now chain a hard `reload` action so subscription
  and Settings status banners refresh immediately after click. The
  in-progress / success / error banners are driven by computed fields
  on `prozorro.sync.cursor`, which Odoo reads at form-load time only
  and does not auto-refresh on toast-only action results, leaving the
  user staring at a stale "last sync OK" banner while a fresh sync
  was already queued. Hard reload trades scroll/state preservation
  for correctness during the debug loop. To revisit with a bus.bus
  push pattern (option 3 in the design conversation) when polishing
  for apps.odoo.com submission.

## [19.0.5.6.7] - 2026-04-30

### Changed
- `Force stop` button on Settings -> Prozorro -> Sync status now shows
  whenever a sync is in progress (parent alert gates on `is_running`),
  instead of only after the 60-minute stale threshold. Removed the
  unused `prozorro_can_force_clear` computed field. Confirm dialog text
  softened to reflect that any in-flight HTTP request continues to
  completion regardless.
- Same `Force stop` button mirrored on the subscription form, inside
  the existing "Sync in progress..." alert. Calls the same underlying
  `prozorro.tender.action_force_clear_running` via a thin proxy on
  `prozorro.subscription`. Both buttons now use `btn btn-danger` for
  visual consistency and to signal the destructive nature of clearing
  system state.

## [19.0.5.6.6] - 2026-04-30

### Fixed (the stuck-is_running bug)
- v5.6.5's `_reschedule_cron_after_run` wrote to `ir.cron.nextcall`
  from inside the cron handler. Odoo 19 protects `ir.cron.write` with
  `lock_for_update(allow_referencing=True)` while the cron is running,
  so any write from inside the handler raises:
      UserError: Record cannot be modified right now: This cron task
      is currently being executed and may not be modified
  That UserError propagated through `_run_action_code_multi`, the cron
  framework rolled back the transaction, but the isolated-cursor
  `is_running=True` write had already committed. Result: `is_running`
  stuck at True for 16+ hours on test19 (build 31426538, observed
  2026-04-29 18:10 UTC -> 2026-04-30 08:17 UTC).
- Fix: `_reschedule_cron_after_run` now defers via
  `self.env.cr.postcommit.add(...)`, which fires after the cron
  transaction commits and the FOR-NO-KEY-UPDATE lock is released. The
  postcommit callback uses an independent cursor (the cron cursor is
  closed by then). Writing to `ir.cron.nextcall` succeeds.

### Fixed (defence-in-depth: stuck-flag self-heal)
- `_cron_sync_feed` is now wrapped in try/except/finally so the
  `is_running` flag is reset via isolated cursor even on uncaught
  exceptions (container kill, OOM, future Odoo internal API changes).
- `action_sync_now` self-heals if `is_running=True` AND
  `last_started_at` is older than `STALE_RUN_MINUTES` (60 min). The
  user can click Sync now and it auto-clears the stuck flag.

### Added (Force stop button on Settings)
- New `prozorro_can_force_clear` computed field + `Force stop`
  button on the Settings status panel, visible only when a sync has
  been "running" for more than 60 minutes (so the button does not
  show during a healthy in-flight sync). Clicking it writes
  `is_running=False` + a "force-cleared by user" note in `last_error`.
  Manager-only.

### Notes
- Lesson codified for future Rteam Apps: NEVER write to `ir.cron`
  from inside a cron handler. The cron's own row is locked. Use
  `cr.postcommit.add(...)` to defer or write via an independent
  cursor AFTER the cron's transaction commits.
- Stuck-state hygiene: any flag set via `_mark_cursor_isolated` (or
  any independent-cursor write) MUST have a corresponding reset path
  in a `finally` block, NOT just in the success/error branches.
  Otherwise an uncaught exception bypasses the reset and the flag
  sticks.

## [19.0.5.6.5] - 2026-04-29

### Fixed (the silence-after-Sync-now bug)
- **Manual `Sync now` actually triggers the cron now.** v5.5.0 made
  `ir_cron_prozorro_sync_feed` `active=False` by default, on the
  premise that `cron._trigger()` would still fire it on manual click.
  Wrong premise: in Odoo 19 `ir.cron._trigger_list` filters out
  triggers on inactive crons (`if not active: at_list = [at for at
  in at_list if at > now]`). So `_trigger()` was a silent no-op when
  the cron was inactive. Confirmed live on test19 v5.6.4 (build
  31426538) - click at 15:33 UTC produced no in-progress / done
  chatter; `ir_cron_trigger` empty; cron worker only ran retention.
- Cron is now ALWAYS `active=True`. The "Auto-sync on schedule"
  toggle in Settings drives `ir.cron.nextcall` instead:
    toggle ON  -> nextcall = now() + interval (recurring auto-sync)
    toggle OFF -> nextcall = 2099-12-31 (effectively no auto-run)
  Either way `_trigger()` works because active=True.
- Added `prozorro.auto_sync_enabled` ir.config_parameter as the
  canonical "did the user want auto-sync?" boolean. The Settings
  toggle reads/writes it AND mirrors to `cron.nextcall`. After every
  cron run, `_reschedule_cron_after_run()` re-parks `nextcall` to 2099
  if the parameter is False (Odoo would otherwise set
  `nextcall = now + interval` automatically and start auto-running).
- Migration `19.0.5.6.5/post-migrate.py` for existing installs:
  preserves prior `cron.active` state into the new
  `prozorro.auto_sync_enabled` config_parameter, then forces
  `active=True` and rewrites `nextcall` accordingly.

### Fixed (the silent-completion bug)
- `_notify_sync_result` no longer silently swallows runs with 0
  matches. v5.6.4's `if error is None and not matched: return`
  guard meant a sync that finished successfully but with 0 new
  matches produced NOTHING visible to the user - no toast, only a
  chatter line they had to scroll for. Now every completion sends a
  `bus.bus` toast (success/info/danger by outcome), so the user
  knows the run finished even if it found nothing.

### Added (sync-status visibility on subscription form)
- Three live alert banners on the subscription form, driven by
  computed `sync_is_running` / `sync_last_started_at` /
  `sync_last_finished_at` / `sync_last_pulled` /
  `sync_last_matched` / `sync_last_error` fields that mirror the
  `prozorro.sync.cursor` singleton:
    `<i fa-spinner fa-spin/>` Sync in progress... (info, while running)
    `<i fa-times-circle/>` Last sync failed (danger, when last_error set)
    `<i fa-check-circle/>` Last sync OK (success, when last finish was clean)
  Auto-recompute on every form load so the user gets immediate
  feedback right where they click Sync now, without having to scroll
  through chatter or hop to Settings -> Prozorro -> Status.
- Stats tab now shows the global sync run details (started /
  finished / pulled / matched / last_error) alongside the per-
  subscription matched_count / last_match counters.

### Notes for future Apps
- Do NOT rely on `cron._trigger()` for inactive crons. Either keep
  the cron active and gate auto-sync via a different mechanism
  (config_parameter checked in handler, or nextcall sentinel) OR run
  manual sync inline in the user's RPC transaction (with the usual
  browser-freeze caveat).
- Always verify a "manual click should fire" path against
  `ir_cron_trigger` table before trusting it: if no row is created,
  the worker won't pick it up regardless of how clean the toast looks.

## [19.0.5.6.4] - 2026-04-29

### Fixed
- **Banner translations actually render now.** v5.6.3 added UK/RU
  translations as separate fragments per `<strong>`/`<em>` chunk inside
  the `<div class="alert">` warning banners (Broad filter, Auto-create
  lead disabled by CRM, Leads disabled in Settings). Odoo's
  `xml_translate` extracts each alert div as ONE msgid containing the
  full inline HTML (`<i/>`, `<strong>`, `<em>`, `<code>`) plus the
  source-XML indentation between tags. The fragments never matched, so
  banners stayed in English on test19. Replaced with single multi-line
  msgids per alert that preserve the exact HTML structure and
  whitespace; `xml_term_adapter` now applies the translation since the
  tag positions match.
- Same fix for the inline-wrapped strings in the Settings panel that
  were previously translated as plain text but extracted as
  `<span>Run every</span>`, `<span>hour(s)</span>`,
  `<i.../><strong>Sync running now</strong>`, `<strong>Last error:</strong>`,
  `<strong>Last finished</strong>`, `<strong>Pulled (last run)</strong>`,
  `<strong>Matched (last run)</strong>`. Each now has both a plain msgid
  (for the field_description) AND a wrapped msgid (for the view).
- Added two missing entries: `Ends:` (tender form key dates section) and
  `<span class="o_stat_text">Prozorro</span>` (CRM lead smart button),
  plus the `sum="Total"` attribute in the tender list view.

### Notes
- Lesson codified for future Rteam Apps: any `<div>` containing
  `<strong>`/`<em>`/`<i>`/`<span>` inline children gets extracted as
  one inline msgid by Odoo's `xml_translate` (per `TRANSLATED_ELEMENTS`
  set in `odoo/tools/translate.py`). Translations must preserve the same
  tag structure or `xml_term_adapter` rejects them and Odoo falls back
  to source. Use `python3 /tmp/extract_msgids.py <view.xml>` (simulates
  Odoo's extraction) to verify msgid format BEFORE writing translations
  for any HTML-wrapping view content.

## [19.0.5.6.3] - 2026-04-29

### Added
- High-quality UK and RU translations for the v5.6.x additions:
  Settings panel blocks (CRM integration, Schedule, Status, Feed),
  the "Auto-create lead is disabled by CRM settings" and "Broad filter"
  warning banners on the subscription form, the keyword filter selectors
  (Field, Match Mode, Exclude when matched, Title only / Description only
  / Items only / Any text, Contains / Regex), and all sync-flow chatter
  / toast messages ("Prozorro sync queued by ...", "Prozorro sync in
  progress...", "Prozorro sync done: pulled X, matched Y", "Sync already
  running", "A background sync was scheduled. Open any active
  subscription...", and the "Leads are disabled" Settings banner with
  the explanation body).

### Fixed
- `i18n/uk.po` and `i18n/ru.po` no longer carry stale partial-string
  msgids left behind by older Odoo extractor runs ("Pulled %(pulled)s
  tenders, %(matched)s matched. " / "A background sync was scheduled.
  Refresh the Tenders list "). Reference for the regex error message
  switched from `code:rteam_prozorro/...:52` to the canonical
  `code:addons/rteam_prozorro/...:0` form.

## [19.0.5.6.2] - 2026-04-29

### Changed (behaviour)
- **The "Enable CRM Leads" toggle in Prozorro Settings is now a real
  gate, not just a visibility hint.** Previously, subscriptions with
  `create_lead=True` created `crm.lead` records regardless of the
  global `crm.group_use_lead` state - those records existed in the DB
  but were invisible because the Leads pool/menu was hidden, leaking
  silently. With 5.6.2: when `crm.group_use_lead` is OFF, the sync
  loop skips lead creation entirely and logs a warning per skipped
  subscription. Tenders are still mirrored and visible under
  `Prozorro > Tenders`. Operators must turn the toggle ON in
  `Settings -> Prozorro -> CRM integration` for auto-creation to
  actually run.
- Subscription form shows an inline warning when `create_lead=True`
  AND CRM Leads are globally disabled, so the gate is visible at the
  point of intent (not only on the Settings page).

### Added
- New computed `crm_use_leads_enabled` field on `prozorro.subscription`
  (mirrors `env.user.has_group('crm.group_use_lead')`); used by the
  form's invisible/visible expressions.

## [19.0.5.6.1] - 2026-04-29

### Fixed
- 19.0.5.6.0 referenced `crm_use_leads` in res_config_settings_views.xml,
  which does not exist on `res.config.settings` in Odoo 19's `crm`
  module - the field is named `group_use_lead`. Build 31562449 failed
  with `Field "crm_use_leads" does not exist in model
  "res.config.settings"` during view validation. Renamed all references.

## [19.0.5.6.0] - 2026-04-29

### Added
- Settings -> Prozorro now opens with a "CRM integration" block exposing
  `crm_use_leads` (the standard CRM "Leads" toggle that gates the Leads
  pool / Generation menu). Fresh Odoo databases ship with this OFF.
  Without it, type='lead' records still get created by 5.5.0's
  auto-creation flow but are invisible in the UI; the new block warns
  loudly and lets the user flip the toggle without leaving the Prozorro
  app.
- Subscription form shows an inline warning "Broad filter - no CPV
  codes and no keywords, will match almost every tender" when both
  `classification_ids` and `keyword_ids` are empty. The Test wizard
  remains the authoritative per-filter explainer; the warning prevents
  users from creating a subscription whose name implies a narrow scope
  but whose filters do not enforce it.

## [19.0.5.5.0] - 2026-04-29

### Fixed
- `prozorro.tender.status` Selection was missing `active.pre-qualification`
  and `active.pre-qualification.stand-still`, so any tender in those
  states aborted the entire sync run with `ValueError: Wrong value for
  prozorro.tender.status`. Selection now mirrors the master data XML
  one-to-one (11 codes). Unknown future codes now log a warning and
  store empty status instead of crashing.
- Per-tender upsert is wrapped in a try/except so a single bad payload
  no longer aborts the whole run; the sync logs and continues.

### Changed (behaviour)
- **Auto-created CRM records are now Leads, not Opportunities.**
  `_create_lead` had `type='opportunity'`, which dumped matched tenders
  straight into the Pipeline / Kanban (3000+ records on a broad rule).
  They now land in the Leads pool (Generation / Triage) where operators
  promote them manually. Existing opportunities created by previous
  versions stay where they are; cleanup is a one-off operator decision.
- **Hourly cron disabled by default.** The sync cron ships with
  `active=False`. Manual `Sync now` still works (cron `_trigger()`
  processes pending triggers regardless of `active`). Enable auto-sync
  from Settings -> Prozorro -> Schedule; default cadence when enabled
  is 6 hours, configurable in the same panel.

### Added
- Settings -> Prozorro now has **Schedule** (toggle + interval hours)
  and **Status** (live `Sync running` banner, last finished, pulled,
  matched, last error, Sync now / Reset cursor buttons) blocks.
- `prozorro.sync.cursor` gained `is_running` and `last_started_at`,
  written via an isolated cursor so the Settings status reflects state
  in real time even mid-sync.
- `action_sync_now` guards against double-clicks: returns a warning
  toast if a sync is already in flight.

## [19.0.5.4.2] - 2026-04-29

### Changed
- `action_sync_now` now posts `Prozorro sync queued by <user>...` to
  each active subscription's chatter **immediately on click**. Previous
  versions only posted "Sync in progress..." once the cron worker
  actually picked up the `_trigger()` queue, which on Odoo.sh can take
  30 seconds to ~2 minutes (webhook poll cadence). Operators were
  seeing nothing for that whole window. Now the timeline is:
  - click: `queued by <user>` (instant, in user's RPC transaction)
  - cron starts: `sync in progress...` (isolated cursor)
  - cron ends: `sync done: pulled N, matched M this subscription` (isolated cursor)

## [19.0.5.4.1] - 2026-04-29

### Fixed
- Chatter `Sync in progress...` and `Sync done: ...` messages from
  19.0.5.4.0 never showed up in subscription chatter. The
  `subs.message_post(...)` calls happened **inside the cron's main
  transaction**, which wraps the long HTTP loop walking the Prozorro
  feed (often 2000+ tenders, 30+ minutes per run). Any timeout,
  network error, or container restart during that loop rolls back the
  whole transaction including the start-message_post, so operators
  see nothing despite clicking Sync now.
- Replaced direct `message_post` calls with `_post_chatter_isolated(sub_ids, body)`
  helper that opens an independent cursor (`self.pool.cursor()`) and
  commits the chatter line on its own transaction. Same pattern as
  `odoo_health_check`'s ir.cron.history logger. The chatter post now
  survives any subsequent rollback in the sync loop.

## [19.0.5.4.0] - 2026-04-29

### Changed (simplification)
- Sync chatter is now posted on each **active subscription's** chatter
  instead of a separate Sync Log page. Subscriptions already have
  `mail.thread`; operators tail the subscription form they are tuning
  and see real-time activity. Two messages per run (per active sub):
  - Start: `Prozorro sync in progress...`
  - End: `Prozorro sync done: pulled N tenders, M matched this subscription.`
  - Failure: `Prozorro sync failed: <reason>`
- Both messages use `mail.mt_note` so they are silent in chatter (no
  email or Discuss ping). Hourly cron + 3 active subs = 6 silent notes
  per hour, only visible when someone opens the subscription form.

### Removed (overengineering rollback from 19.0.5.3.x)
- Dropped `views/prozorro_sync_cursor_views.xml` (sync-log form view).
- Dropped the `Sync Log` menu item.
- Dropped the `Sync log` button in the Tender list header.
- Dropped the `action_prozorro_sync_now_global` server action.
- Dropped `is_running` and `last_started_at` fields on `prozorro.sync.cursor`.
- Dropped `mail.thread` inheritance on `prozorro.sync.cursor`.
- `action_sync_now` reverted to its original toast-only behaviour.

### Why
- 19.0.5.3.0 added an entire new page just to display chatter, which
  read as new-model overhead for what was a one-line ask ("post start
  and end of sync to chatter"). Subscriptions are the natural place
  since users already look at them when tuning rules; no new UI surface.
- Also fixes the lingering `column prozorro_sync_cursor.is_running does
  not exist` 500 error from the half-applied 5.3.0 schema.

## [19.0.5.3.1] - 2026-04-29

### Fixed
- Build 31524779 went red with `External ID not found in the system: rteam_prozorro.action_prozorro_sync_now_global` while parsing `prozorro_sync_cursor_views.xml`. The form view's header button references `%(action_prozorro_sync_now_global)d`, but the matching `<record>` was defined LATER in the same file. Forward references via `%()d` only resolve against `ir_model_data` rows that already exist, so definition must precede usage **even within a single XML file** (the manifest-ordering rule we already had for cross-file references). Reordered: server action first, form view second, act_window last. Fresh installs roll back without this fix; in-place upgrades hide it.

## [19.0.5.3.0] - 2026-04-29

### Added
- **Sync Log via chatter**: `prozorro.sync.cursor` now inherits `mail.thread`. Every sync run posts to the chatter:
  - Start: `Sync in progress...`
  - End: `Sync completed: pulled N tenders, M matched.`
  - Empty: `Sync skipped: no active subscriptions`
  - Failure: `Sync failed: <reason>`
- New menu item **Prozorro -> Sync Log** opens the singleton cursor's form view with full chatter history. Manager-only "Sync now" button on the form header re-runs the global sync.
- Tender list header gets a new "Sync log" button alongside the existing "Sync now" / "Reset cursor" buttons. Clicking "Sync now" now redirects to the Sync Log so operators see progress in chatter instead of relying on the ephemeral toast.
- New fields on `prozorro.sync.cursor`: `last_started_at`, `is_running` (lights up between start and end of a run).

### Changed (Constitution alignment, App #2 retro)
- `_get_int_param(key, default)` helper collapses the duplicated try/except float-parse logic in `_get_pages_per_run` and `_get_retention_days`.
- `mail_template_prozorro_new_match`: repeated inline styles extracted into `t-set` vars (`badge_style`, `primary_button`, `secondary_button`, `muted`). QWeb still renders fully inlined HTML so Gmail / Outlook / mobile clients see the same output - CSS classes would have been stripped, hence we keep inline.
- `_notify_sync_result` logs a warning when `group_prozorro_manager` xmlid is missing instead of silently skipping. Same for `ir_cron_prozorro_sync_feed` in `action_sync_now`.

## [19.0.5.2.4] - 2026-04-28

### Fixed
- The 3 Python `_()` references added in 19.0.5.2.3 were missing the required `code:` prefix on the `#:` reference line. Odoo's `TranslationFileReader` logged `malformed po file: unknown occurrence: rteam_prozorro/models/prozorro_*.py` at ERROR level, which Odoo.sh interprets as a failed build (build 31522903 went red even though the registry loaded fine). Added the `code:` prefix to all three references.

## [19.0.5.2.3] - 2026-04-28

### Added
- 40 missing UK / RU translations for strings introduced after the initial 19.0.4.0.0 i18n drop. Covers Settings UI (Feed / API base URL / Pages per sync run / Retention (days) / help texts), subscription form filters (All regions / All statuses / All procedure types / Any CPV / Region·Status·Method / Stats / Value range / placeholder / Filters), classification / region / procurement-method / tender-status master-data forms, tender form (CRM, Key dates, Procurement, Tender, search filters, Reset cursor confirm), test wizard (Close, Test match against tender, placeholder), and three Python notification strings (`Invalid regex …`, `Pulled … matched`, `A background sync was scheduled …`). Each entry has a real `#:` reference (`model_terms:ir.ui.view,arch_db:…` or `code:…`) so Odoo's translation loader actually picks it up.

## [19.0.5.2.2] - 2026-04-28

### Fixed
- Installing Ukrainian (or Russian) language in Settings crashed with `AttributeError: 'NoneType' object has no attribute 'groups'` while loading `i18n/uk.po`. Odoo 19's `tools/translate.py` parses each entry's `#.` auto-comment with `re.match(r"(module[s]?): (\w+)", entry.comment)` and naively calls `.groups()` without a None check. Our `.po` files contained decorative section-header comments like `#. -------------------- Menus --------------------` which polib glued onto the next msgid's comment block, breaking the regex. Stripped all such headers; also deduplicated 11 msgids that pointed at multiple Odoo metadata sources (e.g. `Regions` referenced from both the menu/action and the subscription field) by merging their `#:` reference lines per gettext convention. 178 unique entries, polib `check_for_duplicates=True` clean.

## [19.0.5.2.1] - 2026-04-28

### Fixed
- Fresh install / Odoo.sh CI failure: `External ID not found in the system: rteam_prozorro.action_prozorro_subscription_test`. Root cause was a manifest-data ordering bug. `views/prozorro_subscription_views.xml` references the action via `%(...action_prozorro_subscription_test)d`, but `wizards/prozorro_subscription_test_views.xml` (which defines the action) was loaded AFTER it. On an existing DB, an in-place upgrade hides this because the action is already in `ir_model_data` from a previous install attempt; a fresh install rolls back. Moved the wizard XML to load before the subscription view.

## [19.0.5.2.0] - 2026-04-28

### Changed
- Module icon iterated from "flower only" to **hybrid: 6-petal flower + Rteam P+ monogram in the centre disc**. The flower silhouette keeps the visual hook ("this is the Prozorro thing") while the P+ inside the teal disc anchors it to the Rteam product family alongside Health Check and AI Assistant. Same trademark-safe palette (violet -> teal gradient), no copy of Prozorro's pink/violet mark.

## [19.0.5.1.0] - 2026-04-28

### Changed
- Replaced the placeholder 1px navy `icon.png` with a proper 512x512 module icon. Six-petal flower silhouette over the Rteam signature gradient (violet `#7C5CFC` -> teal `#00D4AA`) with a teal centre disc. Trademark-safe by design: the petal proportions and palette differ from Prozorro's official pink/violet mark, so the icon reads as "tender / Prozorro connector by Rteam" without imitating the government brand.

### Why
The Apps grid was rendering an empty navy square because `icon.png` was a 258-byte solid-colour stub. Reusing the actual Prozorro logo would be a trademark violation and apps.odoo.com policy reject. The flower-inspired Rteam-palette icon keeps the visual hook ("oh, this is the Prozorro thing") without the legal risk.

## [19.0.5.0.0] - 2026-04-28

### Added
- "Test against tender" wizard reachable from the subscription form header. Operator pastes a Prozorro tender UUID (or full prozorro.gov.ua URL) and the wizard fetches the tender via the live API, runs the subscription against it, and prints a per-filter verdict table: which filter passed, which failed, and *why* (e.g. `tender region 'Київська область' matched token(s) ['київська']` or `value 4400 UAH is BELOW min 5000`). Nothing is persisted - the tender is not saved, no lead is created.
- New helper `prozorro.subscription._match_with_reasons(tender)` returns a list of `(filter_name, passed, reason)` tuples. The hot-path `_matches` now delegates to this helper but short-circuits as before.
- UA + RU translations for all wizard strings appended to the existing `.po` files.

### Why
Tuning subscription rules used to depend on luck: you had to hope the live feed contained a tender that exercised the rule you were testing. The wizard breaks that dependency entirely - operators can paste any tender they're curious about and get an immediate, deterministic verdict without polluting `prozorro.tender` or the CRM pipeline. Field-by-field reasoning makes "why didn't this match" answerable in one screen.

## [19.0.4.0.0] - 2026-04-28

### Added
- Ukrainian (`i18n/uk.po`) and Russian (`i18n/ru.po`) translations covering all UI surfaces an operator interacts with: field labels, help texts, button strings, action and menu names, notification messages, model `_description` strings, status / region / procurement-method master data names, and the most-trafficked CPV / DK021 codes (medical equipment branch + electrical machinery + lasers + IT services + a few others). 171 msgid entries per language.
- Help texts for `region_ids`, `status_ids`, `procurement_method_ids`, `classification_ids`, `create_lead` are translated; the long auto-create-lead help is verbatim-translated.

### Why
With Ukrainian-language users testing on Odoo 17/18/19 (per ARAMIS dry runs) the previously English-only UI made the form opaque. The .po files apply the moment the user installs the Ukrainian language pack and re-upgrades the module - no schema migration, no view changes.

### Notes
- CPV codes auto-created from the live feed already arrive in Ukrainian (Prozorro's `classification.description` field is UA), so this PR only translates the seeded subset.
- Subsequent feature PRs that add or rename strings should append entries to both `.po` files; there is no auto-extraction step in CI yet.

## [19.0.3.1.0] - 2026-04-28

### Added
- Real-time bus.bus toast pushed to all Prozorro Managers when an async sync finishes. Replaces the diagnostic feedback we lost when `action_sync_now` was made non-blocking. Toast renders "Pulled X, Y matched. Refresh Tenders to see new results." on success; failures appear as sticky `danger`-style toasts with the error head. 0-match runs stay silent so the hourly cron doesn't spam every UI session.

### Why
After flipping Sync now to async, operators had no way to know when the background fetch completed without tailing the server log. Toast closes the loop.

## [19.0.3.0.0] - 2026-04-28

Two intertwined fixes for the lead-spam and sync-hang problems surfaced during ARAMIS dry runs.

### Changed (BREAKING)
- `prozorro.subscription.create_lead` default flipped from `True` to `False`. The previous behaviour spammed the CRM pipeline with a separate lead per matched tender (459 leads from one Sync now is real). Existing subscriptions keep their stored value; only the default for new subscriptions changes.
- Field renamed in UI: `Create Lead` -> `Auto-create lead`, with a help line clarifying that the off-state expects manual promotion via the new tender-form button.

### Added
- `action_convert_to_lead` on `prozorro.tender`: idempotent button that promotes one tender (or a multi-selection from the list) to a `crm.lead`. Uses the first matched subscription as context for team / user / tags / stage; falls back to a bare lead when no subscription is on record.
- `action_open_lead` helper that opens the linked lead form. Tender form header now shows "Convert to lead" when no lead, "Open lead" otherwise.
- Tender list header gains a multi-select "Convert to lead" button so an operator can triage 50 matches into 5 leads in one click.

### Fixed
- Sync now froze the browser for up to 7.5 minutes because it ran the feed pull (up to 2000 HTTP fetches) inside the HTTP request. `action_sync_now` now hands off to the existing `prozorro_sync_feed` cron via `_trigger()` and returns a "Sync queued" toast immediately. The cron worker picks it up within ~60s. Operators refresh the Tenders list to see new matches.

### Migration note
Subscriptions created before this version that had `create_lead=True` continue to auto-create leads. To switch them to manual workflow, untick `Auto-create lead` on the subscription form. No SQL migration required.

## [19.0.2.3.0] - 2026-04-28

### Added
- Medical equipment CPV / DK021 subset seeded under `33000000-0`. 15 new codes covering the level-2 branches operators most commonly subscribe to:
  - `33100000-1` Medical equipment (parent of all medical specifics)
  - `33110000-4` Imaging, `33120000-7` Recording systems, `33130000-0` Dental, `33140000-3` Consumables (+ `33141000-0` non-chemical consumables), `33150000-6` Radiotherapy / physical therapy, `33160000-9` Operating techniques (+ `33169000-2` Surgical instruments), `33170000-2` Anaesthesia and resuscitation, `33180000-5` Functional support, `33190000-8` Miscellaneous medical devices
  - `33600000-6` Pharmaceutical products, `33700000-7` Personal care, `33900000-9` Post-mortem and mortuary
- Driven by ARAMIS-style use case: a hospital-supplier subscription needs to be reachable through CPV dropdown rather than typing free-form codes. Full DK021 dictionary load is still pending and remains a tracked follow-up.

## [19.0.2.2.0] - 2026-04-28

### Fixed
- Subscription form: CPV / DK021 section was a column of an inner two-column group and reserved a wide empty area when no codes were selected. Moved CPV out into its own full-width section below Region/Status/Method (and same for Keywords); empty subscription forms are now compact.
- Feed sync direction bug: subsequent pages within one cron run dropped the `descending=1` query param, so the API silently switched to ascending and walked away from the latest tenders. URL builder now preserves `descending=1` across all pages of a run.

### Added
- "Reset cursor" header button on the Tenders list (manager-only, with confirm prompt). Clears `prozorro.sync.cursor.offset` so the next sync starts from the head of the feed; useful while tuning subscription rules so the cron doesn't have to walk all the way back through history.
- Sync now notification is now diagnostic. Three branches: 0 pulled -> hint to reset cursor; >0 pulled but 0 matched -> hint to broaden filters; otherwise -> success. Sticky for warnings so the operator can read the hint.

## [19.0.2.1.0] - 2026-04-28

### Added
- "Sync now" header button on Tender list and on Subscription form. Triggers `_cron_sync_feed()` synchronously and renders a toast with `pulled / matched` counts (or the error / no-active-subscription case). Restricted to Prozorro Manager so non-managers don't accidentally hammer the public API.
- `_cron_sync_feed()` now returns a result dict (`pulled`, `matched`, `error`, `skipped`) so the manual UI path can render meaningful feedback. Cron callers ignore the return value.

### Why
Hourly cron is right for production but a 60-minute wait kills the dev / tuning loop. Operators tuning rules need a one-click way to trigger a fresh pull.

## [19.0.2.0.1] - 2026-04-28

### Fixed
- Empty Prozorro Settings pane: `<app name="prozorro">` did not match the action context `module=rteam_prozorro`, so Odoo's settings filter rendered the page blank. Renamed the block to `name="rteam_prozorro"` to match the module technical name.

## [19.0.2.0.0] - 2026-04-28

UX refactor: replace API-code Char filters on `prozorro.subscription` with translatable Many2many master data so operators see human-readable names instead of `aboveThresholdUA,belowThreshold` in the form.

### Added
- `prozorro.procurement.method` master data model with 15 standard Prozorro procedure types (open bidding, simplified, ESCO, framework agreement, negotiation, etc.).
- `prozorro.tender.status` master data model with 11 lifecycle statuses (draft, tendering, qualification, awarded, complete, cancelled, ...).
- `prozorro.region` master data model with 27 Ukrainian regions (24 oblasts + Kyiv city + AR Crimea + Sevastopol). Each record carries `match_tokens` for case-insensitive substring match against the feed's free-form region string.
- Configuration menus for the three new master-data models under `Prozorro > Configuration`.
- ACL entries: read for users, full CRUD for managers (parity with `prozorro.classification`).

### Changed
- `prozorro.subscription.procurement_method_types` (Char) -> `procurement_method_ids` (M2M).
- `prozorro.subscription.status_filter` (Char, default `active.tendering`) -> `status_ids` (M2M, default = the `active.tendering` master record).
- `prozorro.subscription.region_filter` (Char) -> `region_ids` (M2M).
- Subscription form view shows three `many2many_tags` widgets with `no_create` instead of free-form text inputs.
- All test fixtures updated to use M2M references via `env.ref`.

### Why
The previous Char fields exposed raw Prozorro API codes to operators, who had no way to know that `aboveThresholdUA` means "Open bidding". Switching to master data makes the names translatable (UA + RU) via standard Odoo i18n, enables typo-free dropdowns, and surfaces the canonical procedure list as configuration the customer can audit.

### Notes
- This is a breaking schema change but the module has not been published to apps.odoo.com yet, so no migration script is provided. Re-installing or upgrading on Odoo.sh test19 will drop the obsolete columns.
- Translation files (`i18n/uk.po`, `i18n/ru.po`) are intentionally postponed to the next minor release once the data models stabilise.

## [19.0.1.0.0] - 2026-04-28

Initial scaffold (L0 free tier).

### Added
- `prozorro.tender` model: mirrored tender record (only matched tenders are persisted).
- `prozorro.classification` model: ДК021 / CPV codes master data with parent tree.
- `prozorro.subscription` model: user-defined matching rules (CPV, keywords, region, value range, status).
- `prozorro.subscription.keyword` model: regex / contains keyword filter with negate support.
- `prozorro.sync.cursor` singleton: tracks Prozorro feed offset between cron runs.
- `crm.lead.prozorro_tender_id` extension: link CRM opportunity back to the originating tender.
- Hourly `ir.cron` `prozorro_sync_feed`: pulls feed via `urllib.request`, evaluates each tender against active subscriptions, persists matches, creates leads.
- `res_config_settings`: API URL, default retention days, alert recipients.
- Tree / kanban / form views for tenders + subscriptions; CRM lead extension with Prozorro group.
- Sample CPV master data (30 representative codes including `42610000-5` Lasers - relevant for the ARAMIS founding case).
- Unit tests for subscription matching (CPV, value bounds, region, keywords, negate).
- Integration test for sync cron with mocked HTTP.

### Notes
- Full ДК021:2015 dictionary (~9700 codes) is loaded as scaffold sample only. Follow-up commit will preload the full dataset from official Defra-equivalent CSV.
- Banner / icon are placeholders. P+ monogram per Rteam Brand Kit will replace before apps.odoo.com submission.

### Odoo 19 schema migrations applied (during L0 smoke test on alex-odoo-test19)
- `res.groups`: switched from `category_id` to `privilege_id` referencing new `res.groups.privilege` model. Renamed `users` -> `user_ids`.
- `ir.cron`: dropped deprecated `numbercall` field.
- Search views: removed `expand` and `string` attributes from group-by `<group>` element.
- `res.config.settings` inherit: switched anchor from `//block[hasclass('o_setting_box')]` to `//form` with `position="inside"`.
- `ir.actions.act_window`: switched `target=inline` (removed in 19) to `target=current`.
- Test fix: `test_keyword_in_items_only` now asserts both directions (positive + negative match) using a keyword that actually exists in the item description.

### Validation
- 20/20 unit + integration tests pass on alex-odoo-test19 (Odoo 19, build 31426538).
- Module installs cleanly on fresh DB; 5 prozorro.* models, 2 crons, 28 CPV codes, 2 security groups, crm.lead extension all confirmed.

### Known follow-ups
- `_sql_constraints` is deprecated in Odoo 19 (warnings logged) - migrate to `models.Constraint` declarative syntax.
- Backport to 18.0 + 17.0 branches.
- Replace placeholder icon/banner with P+ monogram.
- Preload full ДК021:2015 dictionary.
