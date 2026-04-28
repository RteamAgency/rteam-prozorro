# Rteam Prozorro Connector

Free Odoo module that watches the Ukrainian Prozorro public-procurement feed, matches new tenders against your CPV / keyword / region rules, and auto-creates CRM opportunities.

- **Target**: Odoo 17 / 18 / 19 (Community + Enterprise)
- **License**: LGPL-3 (free on apps.odoo.com)
- **Module technical name**: `rteam_prozorro`
- **Author**: [Rteam](https://rteam.agency)
- **Audience**: Ukrainian SMBs participating as suppliers in Prozorro tenders

## Features (L0 / free)

1. Hourly sync of the Prozorro public API feed (`/api/2.5/tenders`)
2. Subscription rules: filter by CPV codes, keywords (with regex + negate), region, value range, status
3. Auto-creation of `crm.lead` for matched tenders, with team / user / tag / stage assignment
4. New-match Discuss notification + email alert
5. Daily digest email (one per subscription, grouped)
6. Tender form view with deep-link to prozorro.gov.ua, key dates, items, CPV chips

## Paid tier (`rteam_prozorro_sales`, OPL-1, separate module)

Tender ↔ Quotation ↔ Sale Order linkage, KP (commercial proposal) PDF generation, award status tracking, analytics dashboard. €299 one-time.

## Install

Clone the repo into your Odoo addons path (or install from apps.odoo.com once published), restart the server, update the apps list, install **"Prozorro Connector"** from the Apps menu.

## Configuration

1. Settings → Prozorro → set the API base URL (default: `https://public.api.openprocurement.org/api/2.5/tenders`)
2. CRM → Configuration → Prozorro Subscriptions → create at least one subscription (CPV codes / keywords / region / value range)
3. The hourly cron will start picking up matching tenders. Manually run `Settings → Technical → Scheduled Actions → Prozorro: sync tender feed → Run now` to verify.

## Architecture (one-liner)

Filter-at-sync. We do NOT mirror all UA tenders (~10K/day). Only tenders matching at least one active subscription are persisted in `prozorro.tender`. Retention cron prunes matched tenders 60 days after `tender_period_end` unless linked to a `crm.lead` or `sale.order`.

## Development

See [CHANGELOG.md](CHANGELOG.md) for version history. Issues and contributions: https://github.com/RteamAgency/rteam-prozorro

## License

LGPL-3. See [LICENSE](LICENSE).
