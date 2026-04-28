from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    prozorro_api_url = fields.Char(
        string="Prozorro API URL",
        config_parameter="prozorro.api_url",
        default="https://public.api.openprocurement.org/api/2.5/tenders",
        help="Public read endpoint of the Prozorro / OpenProcurement API.",
    )
    prozorro_pages_per_run = fields.Integer(
        string="Pages per sync run",
        config_parameter="prozorro.pages_per_run",
        default=20,
        help="Safety cap. Each page returns up to 100 tenders.",
    )
    prozorro_retention_days = fields.Integer(
        string="Retention (days)",
        config_parameter="prozorro.retention_days",
        default=60,
        help="Drop matched tenders this many days after tender_period_end "
        "unless a CRM lead is linked.",
    )
