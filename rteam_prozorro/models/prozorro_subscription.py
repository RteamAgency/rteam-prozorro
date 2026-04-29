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

    crm_use_leads_enabled = fields.Boolean(
        string="CRM Leads enabled",
        compute="_compute_crm_use_leads_enabled",
        help="Reflects the global crm.group_use_lead toggle. Read-only "
        "indicator used to warn on the form when create_lead is on but "
        "the Leads pool is hidden.",
    )

    # Sync-status mirror of `prozorro.sync.cursor` singleton, exposed on
    # every subscription so the user gets immediate feedback right where
    # they click Sync now (without scrolling chatter or hopping over to
    # Settings -> Prozorro -> Status). All fields are computed and
    # non-stored: they recompute on every form load against the current
    # cursor state.
    sync_is_running = fields.Boolean(
        string="Sync running",
        compute="_compute_sync_status",
        help="True while the global Prozorro feed sync is in flight (for "
        "any subscription, not just this one).",
    )
    sync_last_started_at = fields.Datetime(
        string="Sync started at",
        compute="_compute_sync_status",
    )
    sync_last_finished_at = fields.Datetime(
        string="Sync finished at",
        compute="_compute_sync_status",
    )
    sync_last_pulled = fields.Integer(
        string="Sync last pulled",
        compute="_compute_sync_status",
    )
    sync_last_matched = fields.Integer(
        string="Sync last matched",
        compute="_compute_sync_status",
    )
    sync_last_error = fields.Text(
        string="Sync last error",
        compute="_compute_sync_status",
    )

    @api.depends_context("uid")
    def _compute_crm_use_leads_enabled(self):
        enabled = self.env.user.has_group("crm.group_use_lead")
        for rec in self:
            rec.crm_use_leads_enabled = enabled

    @api.depends_context("uid")
    def _compute_sync_status(self):
        Cursor = self.env["prozorro.sync.cursor"].sudo()
        cursor = Cursor.search([("name", "=", "main")], limit=1)
        for rec in self:
            rec.sync_is_running = bool(cursor and cursor.is_running)
            rec.sync_last_started_at = cursor.last_started_at if cursor else False
            rec.sync_last_finished_at = cursor.last_sync if cursor else False
            rec.sync_last_pulled = cursor.last_pulled if cursor else 0
            rec.sync_last_matched = cursor.last_matched if cursor else 0
            rec.sync_last_error = cursor.last_error if cursor else False

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
        Hot path: short-circuits on the first failed filter for performance.
        """
        for _name, passed, _reason in self._match_with_reasons(tender):
            if not passed:
                return False
        return True

    def _match_with_reasons(self, tender):
        """Per-filter verdict for diagnostics. Yields (filter_name, passed, reason).

        Unlike `_matches` this evaluates ALL filters even after one fails so
        the test wizard can show every reason in one pass. Skipped filters
        (empty / not configured) yield with `passed=True` and an explanatory
        reason so operators understand why a filter was a no-op.
        """
        self.ensure_one()
        results = []

        if self.status_ids:
            allowed = set(self.status_ids.mapped("code"))
            tender_status = tender.get("status")
            ok = tender_status in allowed
            results.append(
                (
                    "Status",
                    ok,
                    f"tender status '{tender_status}' "
                    + ("is in allowed set" if ok else f"NOT in {sorted(allowed)}"),
                )
            )
        else:
            results.append(("Status", True, "no filter configured (passes any)"))

        if self.procurement_method_ids:
            allowed = set(self.procurement_method_ids.mapped("code"))
            method = tender.get("procurementMethodType")
            ok = method in allowed
            results.append(
                (
                    "Procurement method",
                    ok,
                    f"tender method '{method}' "
                    + ("is in allowed set" if ok else f"NOT in {sorted(allowed)}"),
                )
            )
        else:
            results.append(("Procurement method", True, "no filter configured (passes any)"))

        if self.classification_ids:
            sub_codes = set(self.classification_ids.mapped("code"))
            item_codes = {
                (it.get("classification") or {}).get("id")
                for it in tender.get("items") or []
                if (it.get("classification") or {}).get("id")
            }
            overlap = sub_codes & item_codes
            ok = bool(overlap)
            if ok:
                msg = f"matched on {sorted(overlap)}"
            elif item_codes:
                msg = f"tender CPVs {sorted(item_codes)} miss subscription set {sorted(sub_codes)}"
            else:
                msg = "tender has no CPV codes in items"
            results.append(("CPV / DK021", ok, msg))
        else:
            results.append(("CPV / DK021", True, "no filter configured (passes any)"))

        amount = (tender.get("value") or {}).get("amount")
        currency = (tender.get("value") or {}).get("currency")
        if amount is None:
            results.append(("Value range", True, "tender has no monetary value to compare"))
        else:
            below = self.value_min and amount < self.value_min
            above = self.value_max and amount > self.value_max
            ok = not (below or above)
            label = f"{amount} {currency or ''}".strip()
            if below:
                msg = f"value {label} is BELOW min {self.value_min}"
            elif above:
                msg = f"value {label} is ABOVE max {self.value_max}"
            elif self.value_min or self.value_max:
                msg = f"value {label} is within [{self.value_min}, {self.value_max}]"
            else:
                msg = f"value {label}, no range constraint"
            results.append(("Value range", ok, msg))

        if self.region_ids:
            entity = tender.get("procuringEntity") or {}
            address = entity.get("address") or {}
            tender_region = (address.get("region") or "").lower()
            tokens = self.region_ids._token_set()
            ok = bool(tender_region) and any(tok in tender_region for tok in tokens)
            if ok:
                hits = [tok for tok in tokens if tok in tender_region]
                msg = f"tender region '{tender_region}' matched token(s) {hits}"
            elif not tender_region:
                msg = "tender has no region info on procuringEntity.address"
            else:
                msg = f"tender region '{tender_region}' matches none of {tokens}"
            results.append(("Region", ok, msg))
        else:
            results.append(("Region", True, "no filter configured (passes any)"))

        if self.keyword_ids:
            haystacks = self._build_haystacks(tender)
            for kw in self.keyword_ids:
                ok = kw._matches(haystacks)
                tag = f"Keyword '{kw.keyword}' on {kw.field} ({kw.match_mode}{', negate' if kw.negate else ''})"
                results.append((tag, ok, "passed" if ok else "did not match"))
        else:
            results.append(("Keywords", True, "no keyword rules configured"))

        return results

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
