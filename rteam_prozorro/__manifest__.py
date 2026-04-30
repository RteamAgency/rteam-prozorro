{
    "name": "Prozorro Connector",
    "version": "19.0.5.6.8",
    "category": "Sales/CRM",
    "summary": "Monitor Ukrainian Prozorro tenders, match them to your CRM",
    "description": """
Prozorro Connector for Odoo
===========================

Free Odoo module that watches the Ukrainian Prozorro public-procurement feed,
matches new tenders against your CPV / keyword / region rules, and surfaces
them as Tenders ready for review (manual conversion to CRM lead). Targeted at
Ukrainian SMBs participating as suppliers.

Features (L0 free)
------------------
* On-demand sync of the Prozorro public API feed (`/api/2.5/tenders`),
  optional auto-sync schedule (off by default)
* Subscription rules: filter by CPV codes, keywords (contains / regex / negate),
  region, value range, status
* Optional auto-creation of `crm.lead` for matched tenders (off by default,
  always creates Leads not Opportunities so they go through manual triage)
* Live sync status panel in Settings + per-subscription chatter trail
* Tender form view with deep-link to prozorro.gov.ua, key dates, items, CPV chips

Paid tier `rteam_prozorro_sales` (separate module, OPL-1, EUR 299) adds tender
to Quotation linkage, KP PDF generation, award status tracking, analytics
dashboard.

Targeted at Odoo 17 / 18 / 19 (Community + Enterprise).
""",
    "author": "Rteam",
    "website": "https://rteam.agency",
    "license": "LGPL-3",
    "depends": ["base", "mail", "crm"],
    "external_dependencies": {"python": []},
    "data": [
        "security/prozorro_security.xml",
        "security/ir.model.access.csv",
        "data/prozorro_classification_data.xml",
        "data/prozorro_procurement_method_data.xml",
        "data/prozorro_tender_status_data.xml",
        "data/prozorro_region_data.xml",
        "data/prozorro_config_parameter_data.xml",
        "data/prozorro_cron_data.xml",
        "data/mail_template_data.xml",
        "views/prozorro_classification_views.xml",
        "views/prozorro_procurement_method_views.xml",
        "views/prozorro_tender_status_views.xml",
        "views/prozorro_region_views.xml",
        "wizards/prozorro_subscription_test_views.xml",
        "views/prozorro_subscription_views.xml",
        "views/prozorro_tender_views.xml",
        "views/crm_lead_views.xml",
        "views/res_config_settings_views.xml",
        "views/menus.xml",
    ],
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": False,
    "auto_install": False,
}
