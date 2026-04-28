# Changelog

All notable changes to `rteam_prozorro` are documented here.

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
