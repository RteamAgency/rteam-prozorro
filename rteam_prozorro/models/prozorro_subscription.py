from odoo import _, api, fields, models


class ProzorroSubscription(models.Model):
    """User-defined matching rules against the Prozorro tender feed.

    A subscription is a rule set. Every tender from the feed is evaluated
    against every active subscription; matches are persisted in
    `prozorro.tender` and (if `create_lead=True`) result in a new `crm.lead`.
    """

    _name = "prozorro.subscription"
    _description = "Prozorro Subscription"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    user_id = fields.Many2one("res.users", default=lambda s: s.env.user, tracking=True)
    team_id = fields.Many2one("crm.team", string="Sales Team")
    company_id = fields.Many2one(
        "res.company", default=lambda s: s.env.company, required=True,
    )

    classification_ids = fields.Many2many(
        "prozorro.classification", string="CPV / DK021 codes",
        help="Match tenders whose items reference any of these codes. Empty = any.",
    )
    keyword_ids = fields.One2many("prozorro.subscription.keyword", "subscription_id", string="Keywords")

    region_filter = fields.Char(
        string="Region filter",
        help="Comma-separated region names to match against the procuring entity address. Empty = any.",
    )

    value_min = fields.Monetary(string="Min value", currency_field="value_currency_id")
    value_max = fields.Monetary(string="Max value", currency_field="value_currency_id")
    value_currency_id = fields.Many2one(
        "res.currency", default=lambda s: s.env.ref("base.UAH", raise_if_not_found=False),
    )

    status_filter = fields.Char(
        string="Status filter",
        default="active.tendering",
        help="Comma-separated Prozorro tender statuses. Empty = any.",
    )
    procurement_method_types = fields.Char(
        string="Procurement method types",
        help="Comma-separated, e.g. 'aboveThresholdUA,belowThreshold'. Empty = any.",
    )

    create_lead = fields.Boolean(default=True, tracking=True)
    assign_to_user_id = fields.Many2one("res.users", string="Assign lead to")
    tag_ids = fields.Many2many("crm.tag", string="Tags applied to lead")
    stage_id = fields.Many2one("crm.stage", string="Lead stage")

    matched_count = fields.Integer(compute="_compute_match_stats")
    last_match = fields.Datetime(compute="_compute_match_stats")

    def _compute_match_stats(self):
        Tender = self.env["prozorro.tender"]
        for sub in self:
            tenders = Tender.search([("matched_subscription_ids", "in", sub.id)])
            sub.matched_count = len(tenders)
            sub.last_match = max(tenders.mapped("date_imported")) if tenders else False

    @api.model
    def _get_active_subscriptions(self):
        return self.search([("active", "=", True)])

    def _status_filter_set(self):
        self.ensure_one()
        if not self.status_filter:
            return None
        return {s.strip() for s in self.status_filter.split(",") if s.strip()}

    def _method_types_set(self):
        self.ensure_one()
        if not self.procurement_method_types:
            return None
        return {s.strip() for s in self.procurement_method_types.split(",") if s.strip()}

    def _region_set(self):
        self.ensure_one()
        if not self.region_filter:
            return None
        return [r.strip().lower() for r in self.region_filter.split(",") if r.strip()]

    def _build_haystacks(self, tender):
        title = tender.get("title") or ""
        desc = tender.get("description") or ""
        items = "\n".join(
            (it.get("description") or "") for it in tender.get("items") or []
        )
        return {
            "title": title,
            "description": desc,
            "items": items,
            "any": "\n".join([title, desc, items]),
        }

    def _matches(self, tender):
        """Return True if this subscription matches the given tender dict.

        `tender` is the raw JSON body (dict) returned by Prozorro for one tender.
        """
        self.ensure_one()

        statuses = self._status_filter_set()
        if statuses and tender.get("status") not in statuses:
            return False

        methods = self._method_types_set()
        if methods and tender.get("procurementMethodType") not in methods:
            return False

        if self.classification_ids:
            sub_codes = set(self.classification_ids.mapped("code"))
            item_codes = {
                (it.get("classification") or {}).get("id")
                for it in tender.get("items") or []
                if (it.get("classification") or {}).get("id")
            }
            if not (sub_codes & item_codes):
                return False

        amount = (tender.get("value") or {}).get("amount")
        if amount is not None:
            if self.value_min and amount < self.value_min:
                return False
            if self.value_max and amount > self.value_max:
                return False

        regions = self._region_set()
        if regions:
            entity = tender.get("procuringEntity") or {}
            address = (entity.get("address") or {})
            tender_region = (address.get("region") or "").lower()
            if not any(r in tender_region for r in regions):
                return False

        if self.keyword_ids:
            haystacks = self._build_haystacks(tender)
            for kw in self.keyword_ids:
                if not kw._matches(haystacks):
                    return False

        return True

    def action_view_matched_tenders(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Matched tenders"),
            "res_model": "prozorro.tender",
            "view_mode": "list,form",
            "domain": [("matched_subscription_ids", "in", self.id)],
        }
