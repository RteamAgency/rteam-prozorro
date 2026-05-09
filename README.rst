Prozorro Connector for Odoo
===========================

Mirror the Ukrainian Prozorro public-procurement feed into Odoo, match new tenders against your CPV / keywords / region rules, and turn matches into CRM leads automatically.

Free LGPL-3 module targeted at Ukrainian SMBs participating in public procurement as suppliers.

Features
--------

* **Subscription rules** with five filter dimensions: CPV codes (DK021:2015), keywords (contains / regex / negate), region, value range, target status, procurement method
* **Filter-at-sync**: only matched tenders are persisted. The rest are dropped immediately so the database stays small
* **CRM leads created automatically** per subscription (opt-in), always as ``type='lead'`` so they go through manual triage
* **Email alert** with NEW MATCH severity badge, tender header, items collapsed in ``<details>``, and one-click "View in Odoo" deep-link
* **Test wizard**: paste a tender UUID, get a per-filter verdict for any active subscription
* **Settings page** with live sync status, schedule controls, and Manager-only Force stop for stale runs
* **8 languages**: English, Russian, Ukrainian, German, Spanish, Romanian, Polish, Arabic - all 286 entries 100% translated

Installation
------------

1. Install the module from Apps
2. Open ``Prozorro -> Settings``
3. Switch on **Enable scheduled sync**, optionally raise the **Pages per run** cap, and configure retention
4. Create one or more **Subscriptions** under the Prozorro menu - add CPV codes and / or keywords, optionally restrict by region / value / status
5. Optionally enable **Auto-create CRM lead** on each subscription so matches become leads automatically

Configuration
-------------

================== ===============================================================================
Setting            Default
================== ===============================================================================
API URL            ``https://public.api.openprocurement.org/api/2.5/tenders``
Pages per run      20 (safety cap on the cron walker)
Retention (days)   60 (matched tenders dropped this many days after ``tender_period_end``)
Enable CRM Leads   Off (master toggle - off means no leads ever created)
Sync schedule      Off (cron is shipped inactive so you can sync manually first)
================== ===============================================================================

Technical details
-----------------

* **Targets**: Odoo 19 Community and Enterprise
* **Dependencies**: ``base``, ``mail``, ``crm`` only
* **HTTP client**: Python stdlib ``urllib.request`` (no ``requests`` dependency)
* **Security**: operational menus require ``base.group_user``; settings and force-stop require ``base.group_system``. No public HTTP endpoints
* **Storage**: seven small tables, all retention-bounded
* **Cron resilience**: stale ``is_running`` self-heal after 60 min; postcommit-deferred reschedule writes; isolated chatter cursor so a cron rollback never erases the audit trail
* **Test coverage**: 20 unit and integration tests

What this module does *not* do
------------------------------

* It does not submit bids on Prozorro - this is a read-only mirror plus alerting
* It does not authenticate to the Prozorro API - the public feed is unauthenticated and that is what we read
* It does not download tender attachments - only the structured metadata exposed by the API
* It does not generate quotations or sale orders from tenders - that is the scope of the paid sibling ``rteam_prozorro_sales`` module

Need quotations from tenders, full DK021:2015 dictionary, or daily HTML digests? The paid extension ``rteam_prozorro_sales`` adds those. Get in touch.

Support
-------

* Source code, issues, feature requests: https://github.com/RteamAgency/rteam-prozorro
* Built and maintained by Rteam, an Odoo Enterprise consulting agency: https://rteam.agency

License
-------

LGPL-3. See the ``LICENSE`` file at the module root for the full text.
