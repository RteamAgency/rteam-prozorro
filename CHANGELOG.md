# Changelog

All notable changes to `rteam_prozorro` are documented here.

## [19.0.1.0.2] - 2026-05-10

### Fixed
- `prozorro_start_date` setting was a `fields.Date`, which Odoo 19's
  `res.config.settings` validator rejects (only boolean/integer/float/char/
  selection/many2one/datetime are accepted). The whole Settings page failed
  to render with `Field res.config.settings.prozorro_start_date must have
  type ...`. Changed to `fields.Datetime`. The watermark builder now takes
  the first 10 characters and anchors at midnight Kyiv time, so legacy
  "YYYY-MM-DD" values stored in `ir.config_parameter` keep working without
  a migration.

## [19.0.1.0.1] - 2026-05-09

### Changed
- Replace placeholder banner.png with branded Light Glass cover (P+ monogram in signature gradient + 4 feature tiles: TENDERS / CPV / LEADS / ALERTS) so the apps.odoo.com listing card stops showing an empty navy rectangle

## [19.0.1.0.0] - 2026-05-09

Initial public release on apps.odoo.com.

### Features
- Mirror of the Ukrainian Prozorro public-procurement feed (`https://public.api.openprocurement.org/api/2.5/tenders`) into Odoo, hourly auto-sync (off by default) plus on-demand "Sync now" with bus.bus completion toast
- Subscription rules with CPV codes (DK021:2015), keywords (contains / regex / negate), region, value range, status, and procurement method filters
- Optional auto-creation of `crm.lead` from matched tenders (always as Leads, never Opportunities, so they go through manual triage)
- Email alert template with severity badge, "View in Odoo" deep-link, and tender items collapsed inside `<details>`
- Tender form with deep-link to `prozorro.gov.ua`, key dates, items, CPV chips, and one-click "Convert to lead"
- Test wizard: paste a tender UUID, get a per-filter verdict for any active subscription
- Settings page with live sync status (currently syncing / last finished / last error), schedule controls, and Manager-only "Force stop" for stale runs
- Master data shipped: 28-code CPV sample, 11 Prozorro tender statuses, 15 procurement method types, 27 Ukrainian regions
- Cron self-heals stale `is_running` flag, postcommit-defers reschedule writes to avoid `lock_for_update` UserError, isolates chatter writes through a side cursor so a cron rollback never erases the audit trail
- 8 languages: English, Russian, Ukrainian, German, Spanish, Romanian, Polish, Arabic (286 entries each, 100% translated)
- 20 unit and integration tests covering subscription matching, feed sync with mocked HTTP, lead-creation gating, and cursor lifecycle
