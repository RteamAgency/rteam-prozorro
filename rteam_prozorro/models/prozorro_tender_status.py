from odoo import api, fields, models


class ProzorroTenderStatus(models.Model):
    """Prozorro tender lifecycle statuses (the `status` field in the API).

    Seeded with the canonical Prozorro statuses; codes such as
    `active.tendering`, `complete`, `cancelled` are stored verbatim and
    displayed via translatable `name`.
    """

    _name = "prozorro.tender.status"
    _description = "Prozorro Tender Status"
    _order = "sequence, code"
    _rec_name = "name"

    code = fields.Char(string="API code", required=True, index=True)
    name = fields.Char(string="Name", required=True, translate=True)
    description = fields.Text(string="Description", translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint(
        "UNIQUE(code)",
        "Tender status code must be unique.",
    )

    @api.depends("code", "name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = rec.name or rec.code or ""
