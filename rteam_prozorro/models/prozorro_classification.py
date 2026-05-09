from odoo import api, fields, models


class ProzorroClassification(models.Model):
    """ДК021:2015 / CPV procurement classification codes.

    The Ukrainian variant of CPV-2008 is published as ДК021:2015 and is used
    by Prozorro to classify tender items. Codes are hierarchical
    (e.g. 42000000-6 -> 42600000-2 -> 42610000-5).
    """

    _name = "prozorro.classification"
    _description = "Prozorro CPV / DK021 Classification"
    _order = "code"
    _rec_name = "display_name"

    code = fields.Char(string="Code", required=True, index=True)
    name = fields.Char(string="Description", required=True, translate=True)
    scheme = fields.Char(string="Scheme", default="ДК021", index=True)
    parent_id = fields.Many2one(
        "prozorro.classification", string="Parent", index=True, ondelete="set null"
    )
    child_ids = fields.One2many("prozorro.classification", "parent_id", string="Children")
    active = fields.Boolean(default=True)
    display_name = fields.Char(compute="_compute_display_name", store=True)

    _code_uniq = models.Constraint(
        "UNIQUE(code, scheme)",
        "CPV code must be unique per scheme.",
    )

    @api.depends("code", "name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.code} {rec.name}" if rec.code and rec.name else rec.code or ""
