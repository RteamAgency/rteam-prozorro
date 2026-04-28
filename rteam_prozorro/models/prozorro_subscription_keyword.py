import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProzorroSubscriptionKeyword(models.Model):
    """Single keyword filter inside a subscription.

    A subscription matches a tender only if every active keyword passes
    (logical AND across keyword rows; `negate=True` flips the row's result).
    Match modes: contains (case-insensitive) or regex.
    """

    _name = "prozorro.subscription.keyword"
    _description = "Prozorro Subscription Keyword"

    subscription_id = fields.Many2one(
        "prozorro.subscription",
        required=True,
        ondelete="cascade",
        index=True,
    )
    keyword = fields.Char(required=True)
    field = fields.Selection(
        [
            ("title", "Title only"),
            ("description", "Description only"),
            ("items", "Items only"),
            ("any", "Any text"),
        ],
        default="any",
        required=True,
    )
    match_mode = fields.Selection(
        [
            ("contains", "Contains (case-insensitive)"),
            ("regex", "Regex"),
        ],
        default="contains",
        required=True,
    )
    negate = fields.Boolean(string="Exclude when matched")

    @api.constrains("match_mode", "keyword")
    def _check_regex_compiles(self):
        for rec in self:
            if rec.match_mode == "regex" and rec.keyword:
                try:
                    re.compile(rec.keyword)
                except re.error as e:
                    raise ValidationError(_("Invalid regex %r: %s", rec.keyword, str(e))) from e

    def _matches(self, haystacks):
        """Return True if this keyword row passes against `haystacks` dict.

        haystacks keys: 'title', 'description', 'items', 'any' (concatenated).
        """
        self.ensure_one()
        text = haystacks.get(self.field) or haystacks.get("any") or ""
        if self.match_mode == "contains":
            hit = self.keyword.lower() in text.lower()
        else:
            try:
                hit = bool(re.search(self.keyword, text, flags=re.IGNORECASE))
            except re.error:
                hit = False
        return (not hit) if self.negate else hit
