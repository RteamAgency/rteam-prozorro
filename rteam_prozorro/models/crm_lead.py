from odoo import fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    prozorro_tender_id = fields.Many2one(
        "prozorro.tender", string="Prozorro tender", ondelete="set null", index=True,
    )
