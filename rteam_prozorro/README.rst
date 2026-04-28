Prozorro Connector
==================

.. |license| image:: https://img.shields.io/badge/license-LGPL--3-blue.svg
   :target: https://www.gnu.org/licenses/lgpl-3.0-standalone.html
.. |version| image:: https://img.shields.io/badge/version-19.0.1.0.0-brightgreen.svg

|version| |license|

Free Odoo module that watches the Ukrainian **Prozorro** public-procurement
feed, matches new tenders against your CPV / keyword / region rules, and
auto-creates CRM opportunities.

Features (L0 free)
------------------

* Hourly sync of the Prozorro public API feed (``/api/2.5/tenders``)
* Subscription rules: CPV, keywords (contains / regex / negate), region, value
  range, status
* Auto-creation of ``crm.lead`` for matched tenders
* New-match Discuss + email alert
* Daily digest email (one per subscription)
* Tender form view with deep-link to prozorro.gov.ua, key dates, items, CPV chips

Configuration
-------------

1. Settings -> Prozorro -> set the API base URL
   (default: ``https://public.api.openprocurement.org/api/2.5/tenders``)
2. CRM -> Configuration -> Prozorro Subscriptions -> create at least one
   subscription
3. Wait for the hourly cron, or run *Settings -> Technical -> Scheduled Actions
   -> Prozorro: sync tender feed -> Run now*

Architecture
------------

Filter-at-sync. We do NOT mirror all UA tenders (~10K per day). Only tenders
matching at least one active subscription are persisted in ``prozorro.tender``.
Retention cron prunes matched tenders 60 days after ``tender_period_end``
unless linked to a ``crm.lead`` or ``sale.order``.

Paid tier
---------

``rteam_prozorro_sales`` (OPL-1, EUR 299 one-time) adds tender to Quotation
linkage, KP PDF generation, award status tracking, analytics dashboard.

License
-------

LGPL-3. See ``LICENSE``.

Author
------

`Rteam <https://rteam.agency>`_ - Odoo Enterprise consulting.
