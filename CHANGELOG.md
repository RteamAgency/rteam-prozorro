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
