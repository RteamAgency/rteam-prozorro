# Changelog

All notable changes to `rteam_prozorro` are documented here.

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
