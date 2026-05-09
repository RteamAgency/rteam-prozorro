from odoo import api, fields, models


class ProzorroRegion(models.Model):
    """Ukrainian regions (oblasts + cities of special status).

    The Prozorro feed exposes the procuring entity region as a free-form
    Ukrainian-language string in `procuringEntity.address.region` (e.g.
    'Київська область', 'м. Київ'). We compare the tender's region against
    every `match_token` of every selected region (case-insensitive substring),
    so admins can extend tokens to cover spelling variants.
    """

    _name = "prozorro.region"
    _description = "Prozorro Region"
    _order = "sequence, name"
    _rec_name = "name"

    code = fields.Char(string="Code", required=True, index=True)
    name = fields.Char(string="Name", required=True, translate=True)
    match_tokens = fields.Char(
        string="Match tokens",
        required=True,
        help="Comma-separated lowercase substrings matched against the tender "
        "region string. E.g. 'київська' will match 'Київська область'.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint(
        "UNIQUE(code)",
        "Region code must be unique.",
    )

    @api.depends("name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = rec.name or rec.code or ""

    def _token_set(self):
        """Return the union of match tokens across this recordset, lowercased."""
        tokens = []
        for rec in self:
            for tok in (rec.match_tokens or "").split(","):
                tok = tok.strip().lower()
                if tok:
                    tokens.append(tok)
        return tokens
