from odoo import api, fields, models


class ProzorroProcurementMethod(models.Model):
    """Prozorro procurement method types (procurementMethodType in the API).

    Codes are taken from https://prozorro-api-docs.readthedocs.io/.
    Records are seeded as master data and shown to operators with friendly,
    translatable names so they don't have to type API codes by hand.
    """

    _name = "prozorro.procurement.method"
    _description = "Prozorro Procurement Method Type"
    _order = "sequence, code"
    _rec_name = "name"

    code = fields.Char(string="API code", required=True, index=True)
    name = fields.Char(string="Name", required=True, translate=True)
    description = fields.Text(string="Description", translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "prozorro_procurement_method_code_uniq",
            "unique(code)",
            "Procurement method code must be unique.",
        ),
    ]

    @api.depends("code", "name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = rec.name or rec.code or ""
