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
        "res.company",
        default=lambda s: s.env.company,
        required=True,
    )

    classification_ids = fields.Many2many(
        "prozorro.classification",
        string="CPV / DK021 codes",
        help="Match tenders whose items reference any of these codes. Empty = any.",
    )
    keyword_ids = fields.One2many(
        "prozorro.subscription.keyword", "subscription_id", string="Keywords"
    )

    region_ids = fields.Many2many(
        "prozorro.region",
        string="Regions",
        help="Match tenders whose procuring entity is registered in any of "
        "these regions. Empty = any region.",
    )

    value_min = fields.Monetary(string="Min value", currency_field="value_currency_id")
    value_max = fields.Monetary(string="Max value", currency_field="value_currency_id")
    value_currency_id = fields.Many2one(
        "res.currency",
        default=lambda s: s.env.ref("base.UAH", raise_if_not_found=False),
    )

    status_ids = fields.Many2many(
        "prozorro.tender.status",
        string="Statuses",
        default=lambda s: s._default_status_ids(),
        help="Match tenders in any of these lifecycle statuses. Empty = any.",
    )
    procurement_method_ids = fields.Many2many(
        "prozorro.procurement.method",
        string="Procurement methods",
        help="Match tenders run with any of these procurement procedures. Empty = any procedure.",
    )

    create_lead = fields.Boolean(
        string="Auto-create lead",
        default=False,
        tracking=True,
        help="If on, every matched tender automatically becomes a CRM lead. "
        "Leave OFF for high-volume subscriptions: matches are persisted as "
        "Prozorro Tenders and operators promote them to leads via the "
        "'Convert to lead' button on the tender form.",
    )
    assign_to_user_id = fields.Many2one("res.users", string="Assign lead to")
    tag_ids = fields.Many2many("crm.tag", string="Tags applied to lead")
    stage_id = fields.Many2one("crm.stage", string="Lead stage")

    matched_count = fields.Integer(compute="_compute_match_stats")
    last_match = fields.Datetime(compute="_compute_match_stats")

    @api.model
    def _default_status_ids(self):
        active_tendering = self.env.ref(
            "rteam_prozorro.status_active_tendering", raise_if_not_found=False
        )
        return [(6, 0, active_tendering.ids)] if active_tendering else False

    def _compute_match_stats(self):
        Tender = self.env["prozorro.tender"]
        for sub in self:
            tenders = Tender.search([("matched_subscription_ids", "in", sub.id)])
            sub.matched_count = len(tenders)
            sub.last_match = max(tenders.mapped("date_imported")) if tenders else False

    @api.model
    def _get_active_subscriptions(self):
        return self.search([("active", "=", True)])

    def _build_haystacks(self, tender):
        title = tender.get("title") or ""
        desc = tender.get("description") or ""
        items = "\n".join((it.get("description") or "") for it in tender.get("items") or [])
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

        if self.status_ids:
            allowed_statuses = set(self.status_ids.mapped("code"))
            if tender.get("status") not in allowed_statuses:
                return False

        if self.procurement_method_ids:
            allowed_methods = set(self.procurement_method_ids.mapped("code"))
            if tender.get("procurementMethodType") not in allowed_methods:
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

        if self.region_ids:
            entity = tender.get("procuringEntity") or {}
            address = entity.get("address") or {}
            tender_region = (address.get("region") or "").lower()
            tokens = self.region_ids._token_set()
            if not any(tok in tender_region for tok in tokens):
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

    def action_sync_now(self):
        """Run the global feed sync and return its UI notification.

        The sync evaluates EVERY active subscription, not just `self`. Exposed
        on the subscription form as a convenience so operators can trigger
        a fresh pull while they are tuning rules.
        """
        return self.env["prozorro.tender"].action_sync_now()
