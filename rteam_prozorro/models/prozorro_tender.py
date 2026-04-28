import json
import logging
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta

from odoo import SUPERUSER_ID, _, api, fields, models

_logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://public.api.openprocurement.org/api/2.5/tenders"
DEFAULT_PAGE_LIMIT = 100
DEFAULT_PAGES_PER_RUN = 20  # safety cap so a single cron run can't loop forever
DEFAULT_RETENTION_DAYS = 60
DEFAULT_HTTP_TIMEOUT = 25

PROZORRO_STATUS = [
    ("draft", "Draft"),
    ("active.enquiries", "Enquiries"),
    ("active.tendering", "Tendering"),
    ("active.auction", "Auction"),
    ("active.qualification", "Qualification"),
    ("active.awarded", "Awarded (active)"),
    ("complete", "Complete"),
    ("cancelled", "Cancelled"),
    ("unsuccessful", "Unsuccessful"),
]


class ProzorroTender(models.Model):
    """Mirrored tender record - persisted only when matched by at least one subscription."""

    _name = "prozorro.tender"
    _description = "Prozorro Tender"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_modified desc"
    _rec_name = "name"

    name = fields.Char(string="Tender ID", required=True, index=True, tracking=True)  # tenderID
    uuid = fields.Char(string="Internal UUID", required=True, index=True, copy=False)

    title = fields.Char(tracking=True)
    description = fields.Text()
    status = fields.Selection(PROZORRO_STATUS, tracking=True)

    procurement_method = fields.Char()
    procurement_method_type = fields.Char(string="Procurement method type", index=True)

    value_amount = fields.Monetary(currency_field="value_currency_id", tracking=True)
    value_currency_id = fields.Many2one(
        "res.currency",
        default=lambda s: s.env.ref("base.UAH", raise_if_not_found=False),
    )

    procuring_entity_name = fields.Char(string="Procuring entity")
    procuring_entity_edrpou = fields.Char(string="EDRPOU", index=True)
    procuring_entity_country = fields.Char()
    procuring_entity_region = fields.Char(index=True)
    procuring_entity_locality = fields.Char()

    tender_period_start = fields.Datetime()
    tender_period_end = fields.Datetime(index=True)
    enquiry_period_end = fields.Datetime(string="Enquiries until")
    auction_period_start = fields.Datetime(string="Auction at")

    classification_ids = fields.Many2many("prozorro.classification", string="CPV / DK021")
    items_summary = fields.Text(string="Items")
    items_count = fields.Integer()

    url = fields.Char(string="Public URL", compute="_compute_url", store=True)
    api_url = fields.Char(string="API URL")
    date_modified = fields.Datetime(index=True)
    date_imported = fields.Datetime(default=fields.Datetime.now, index=True)
    raw_json = fields.Text(string="Raw payload")

    matched_subscription_ids = fields.Many2many(
        "prozorro.subscription", string="Matched subscriptions"
    )
    lead_id = fields.Many2one("crm.lead", string="CRM Lead", ondelete="set null", index=True)
    company_id = fields.Many2one(
        "res.company",
        default=lambda s: s.env.company,
        required=True,
    )

    _sql_constraints = [
        ("prozorro_tender_uuid_uniq", "unique(uuid)", "Tender UUID must be unique."),
    ]

    # ------------------------------------------------------------------ Compute

    @api.depends("name")
    def _compute_url(self):
        for rec in self:
            rec.url = f"https://prozorro.gov.ua/tender/{rec.name}" if rec.name else False

    # ------------------------------------------------------------------ Config helpers

    @api.model
    def _get_int_param(self, key, default):
        """Read an int ir.config_parameter, falling back to default on
        missing or non-numeric values."""
        raw = self.env["ir.config_parameter"].sudo().get_param(key, default)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    @api.model
    def _get_api_base_url(self):
        return self.env["ir.config_parameter"].sudo().get_param("prozorro.api_url", DEFAULT_API_URL)

    @api.model
    def _get_pages_per_run(self):
        return self._get_int_param("prozorro.pages_per_run", DEFAULT_PAGES_PER_RUN)

    @api.model
    def _get_retention_days(self):
        return self._get_int_param("prozorro.retention_days", DEFAULT_RETENTION_DAYS)

    # ------------------------------------------------------------------ HTTP

    @api.model
    def _http_get_json(self, url):
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=DEFAULT_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ------------------------------------------------------------------ Chatter
    # Posts to subscription chatter run in an independent cursor so the
    # message persists even if the long HTTP sync loop later rolls back
    # (timeout, network error, container restart). Without this, a "Sync
    # in progress..." message_post inside the cron transaction is lost
    # together with everything else when the transaction rolls back, and
    # operators see nothing in the chatter despite clicking Sync now.

    def _post_chatter_isolated(self, sub_ids, body):
        if not sub_ids:
            return
        try:
            with self.pool.cursor() as new_cr:
                env = api.Environment(new_cr, SUPERUSER_ID, {})
                env["prozorro.subscription"].browse(sub_ids).message_post(
                    body=body,
                    subtype_xmlid="mail.mt_note",
                )
        except Exception:
            _logger.exception(
                "Prozorro: failed to post chatter line to subscriptions %s", sub_ids
            )

    # ------------------------------------------------------------------ Cron

    @api.model
    def _cron_sync_feed(self):
        """Cron entry point. Always returns a result dict for the manual UI button to render.

        Walks the Prozorro feed in descending order (latest tenders first). The
        cursor stores the offset returned after the last walked page so the
        next run continues backward from where the previous one stopped.
        `descending=1` is preserved across pages within a single run (without
        it the API switches to ascending and we'd skip past the latest tail).
        """
        Cursor = self.env["prozorro.sync.cursor"]
        Subscription = self.env["prozorro.subscription"]
        cursor = Cursor._get_singleton("main")

        subs = Subscription._get_active_subscriptions()
        if not subs:
            _logger.info("Prozorro: no active subscriptions, skipping sync")
            return {"pulled": 0, "matched": 0, "error": None, "skipped": True}

        # Sync started: post a one-line chatter note on each active
        # subscription. Done in an independent cursor so the message
        # persists even if the HTTP loop below later crashes / times out
        # / hits a container restart. Without isolation, the operator
        # sees nothing in chatter despite clicking Sync now.
        self._post_chatter_isolated(
            subs.ids, _("Prozorro sync in progress..."),
        )

        base_url = self._get_api_base_url()
        max_pages = self._get_pages_per_run()
        per_sub_matched = Counter()

        def _page_url(offset):
            params = f"descending=1&limit={DEFAULT_PAGE_LIMIT}"
            if offset:
                params += f"&offset={offset}"
            return base_url + "?" + params

        url = _page_url(cursor.offset)

        pulled, matched = 0, 0
        try:
            for _page in range(max_pages):
                data = self._http_get_json(url)
                records = data.get("data") or []
                for record in records:
                    pulled += 1
                    tender_uuid = record.get("id")
                    if not tender_uuid:
                        continue
                    try:
                        full = self._http_get_json(base_url + "/" + tender_uuid)
                    except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as e:
                        _logger.warning("Prozorro: failed to fetch %s: %s", tender_uuid, e)
                        continue
                    tender_payload = full.get("data") or {}
                    if not tender_payload:
                        continue
                    matches = subs.filtered(lambda s, tp=tender_payload: s._matches(tp))
                    if matches:
                        self._upsert_tender(tender_payload, matches, base_url)
                        matched += 1
                        for sub in matches:
                            per_sub_matched[sub.id] += 1

                next_page = data.get("next_page") or {}
                next_offset = next_page.get("offset")
                if not next_offset or next_offset == cursor.offset:
                    break
                cursor.offset = next_offset
                url = _page_url(next_offset)

        except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as e:
            _logger.exception("Prozorro: sync failed")
            cursor._record_error(str(e))
            self._post_chatter_isolated(
                subs.ids, _("Prozorro sync failed: %s", str(e)[:200]),
            )
            self._notify_sync_result(pulled, matched, error=str(e))
            return {"pulled": pulled, "matched": matched, "error": str(e), "skipped": False}

        cursor._record_success(pulled, matched)
        _logger.info("Prozorro: sync done. pulled=%d, matched=%d", pulled, matched)
        # Per-subscription chatter line with the run's outcome FOR THIS
        # subscription. Posted via isolated cursor so it survives any
        # late-stage rollback (e.g. lead-creation failure on retry).
        for sub in subs:
            n = per_sub_matched.get(sub.id, 0)
            self._post_chatter_isolated(
                sub.ids,
                _(
                    "Prozorro sync done: pulled %(pulled)s tenders, %(matched)s matched this subscription.",
                    pulled=pulled,
                    matched=n,
                ),
            )
        self._notify_sync_result(pulled, matched, error=None)
        return {"pulled": pulled, "matched": matched, "error": None, "skipped": False}

    @api.model
    def _notify_sync_result(self, pulled, matched, error=None):
        """Push a real-time toast to Prozorro managers when a sync run finishes.

        Sent over `bus.bus` so users get feedback even when the sync ran in
        the background (cron / `_trigger()` path) instead of inline. Skipped
        when nothing useful happened (no error, no new matches): hourly crons
        finding 0 matches would otherwise spam the UI.
        """
        if error is None and not matched:
            return
        group = self.env.ref("rteam_prozorro.group_prozorro_manager", raise_if_not_found=False)
        if not group:
            _logger.warning(
                "Prozorro: group_prozorro_manager xmlid missing, skipping toast notification"
            )
            return
        if not group.user_ids:
            return
        if error:
            title = _("Prozorro sync failed")
            message = error[:200]
            ntype = "danger"
            sticky = True
        else:
            title = _("Prozorro sync done")
            message = _(
                "Pulled %(pulled)s tenders, %(matched)s matched. "
                "Refresh the Tenders list to see new results."
            ) % {"pulled": pulled, "matched": matched}
            ntype = "success"
            sticky = False
        Bus = self.env["bus.bus"].sudo()
        for partner in group.user_ids.partner_id:
            Bus._sendone(
                partner,
                "simple_notification",
                {"title": title, "message": message, "type": ntype, "sticky": sticky},
            )

    def action_sync_now(self):
        """Schedule the feed-sync cron for immediate background execution.

        The actual fetch can take minutes (one HTTP GET per matched tender,
        up to 2000 of them). Running synchronously inside an HTTP request
        froze the browser for the whole duration, so we hand off to the
        cron worker via `_trigger()` and return a toast notification.
        Posts "Sync queued..." to each active subscription's chatter
        immediately on click - the cron picks up the trigger 30s-2min
        later (Odoo.sh webhook poll cadence) and posts its own
        "in progress" / "done" lines once it actually runs.
        """
        cron = self.env.ref("rteam_prozorro.ir_cron_prozorro_sync_feed", raise_if_not_found=False)
        if not cron:
            _logger.warning(
                "Prozorro: ir_cron_prozorro_sync_feed xmlid missing, cannot trigger sync"
            )
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Sync cron missing"),
                    "message": _("Reinstall the module to restore the scheduled action."),
                    "type": "danger",
                    "sticky": True,
                },
            }
        # Instant chatter feedback. The user's RPC transaction commits as
        # soon as this action returns, so a plain message_post is fine
        # here (no need for the isolated-cursor pattern that protects
        # the cron's long loop).
        subs = self.env["prozorro.subscription"]._get_active_subscriptions()
        if subs:
            user_name = self.env.user.name
            subs.message_post(
                body=_("Prozorro sync queued by %s...", user_name),
                subtype_xmlid="mail.mt_note",
            )
        cron.sudo()._trigger()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Sync queued"),
                "message": _(
                    "A background sync was scheduled. Open any active subscription "
                    "to follow progress in its chatter."
                ),
                "type": "success",
                "sticky": False,
            },
        }

    def action_reset_sync_cursor(self):
        """Rewind the feed cursor to the head so the next sync pulls latest tenders.

        Manager-only debug helper. Useful when iterating on subscription rules:
        without this you have to wait until the cron walks far enough back to
        re-encounter tenders you have just made matchable.
        """
        cursor = self.env["prozorro.sync.cursor"]._get_singleton("main")
        cursor.sudo().write({"offset": False, "last_error": False})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Cursor reset"),
                "message": _("The next sync will start from the latest tenders."),
                "type": "success",
                "sticky": False,
            },
        }

    # ------------------------------------------------------------------ Upsert

    @api.model
    def _upsert_tender(self, payload, matched_subscriptions, base_url):
        """Create or update a `prozorro.tender` row from a payload dict.

        On create, also creates `crm.lead` for each matched subscription that
        has `create_lead=True`.
        """
        tender_uuid = payload.get("id")
        if not tender_uuid:
            return self.browse()

        existing = self.search([("uuid", "=", tender_uuid)], limit=1)
        vals = self._payload_to_vals(payload, base_url)
        # accumulate matched subscriptions on the m2m
        existing_subs = existing.matched_subscription_ids.ids if existing else []
        all_sub_ids = sorted(set(existing_subs) | set(matched_subscriptions.ids))
        vals["matched_subscription_ids"] = [(6, 0, all_sub_ids)]

        if existing:
            existing.write(vals)
            return existing

        rec = self.create(vals)
        # lead creation only on first match
        for sub in matched_subscriptions.filtered(lambda s: s.create_lead):
            if not rec.lead_id:
                rec._create_lead(sub)
        return rec

    @api.model
    def _payload_to_vals(self, payload, base_url):
        entity = payload.get("procuringEntity") or {}
        address = entity.get("address") or {}
        identifier = entity.get("identifier") or {}
        value = payload.get("value") or {}
        tender_period = payload.get("tenderPeriod") or {}
        enquiry_period = payload.get("enquiryPeriod") or {}
        auction_period = payload.get("auctionPeriod") or {}

        items = payload.get("items") or []
        items_summary = "\n".join(
            f"- {it.get('description') or '?'} (qty {it.get('quantity') or '?'})"
            for it in items[:30]
        )

        Currency = self.env["res.currency"]
        currency_id = False
        cur_code = (value.get("currency") or "UAH").upper()
        if cur_code:
            cur = Currency.search([("name", "=", cur_code)], limit=1)
            currency_id = cur.id if cur else False

        # CPV / classification handling
        Classification = self.env["prozorro.classification"]
        codes = sorted(
            {
                (it.get("classification") or {}).get("id")
                for it in items
                if (it.get("classification") or {}).get("id")
            }
        )
        cls_ids = []
        for code in codes:
            cls = Classification.search([("code", "=", code)], limit=1)
            if not cls:
                # Auto-create unknown CPV codes as bare placeholders. The full
                # DK021:2015 dictionary is preloaded but reality drifts; missing
                # codes still need to be linkable.
                cls = Classification.sudo().create(
                    {
                        "code": code,
                        "name": (
                            next(
                                ((it.get("classification") or {}).get("description") or "")
                                for it in items
                                if (it.get("classification") or {}).get("id") == code
                            ),
                            "",
                        )[0]
                        or code,
                    }
                )
            cls_ids.append(cls.id)

        return {
            "name": payload.get("tenderID") or payload.get("id"),
            "uuid": payload.get("id"),
            "title": payload.get("title"),
            "description": payload.get("description"),
            "status": payload.get("status"),
            "procurement_method": payload.get("procurementMethod"),
            "procurement_method_type": payload.get("procurementMethodType"),
            "value_amount": value.get("amount") or 0.0,
            "value_currency_id": currency_id,
            "procuring_entity_name": entity.get("name"),
            "procuring_entity_edrpou": identifier.get("id"),
            "procuring_entity_country": address.get("countryName"),
            "procuring_entity_region": address.get("region"),
            "procuring_entity_locality": address.get("locality"),
            "tender_period_start": self._parse_dt(tender_period.get("startDate")),
            "tender_period_end": self._parse_dt(tender_period.get("endDate")),
            "enquiry_period_end": self._parse_dt(enquiry_period.get("endDate")),
            "auction_period_start": self._parse_dt(auction_period.get("startDate")),
            "classification_ids": [(6, 0, cls_ids)] if cls_ids else False,
            "items_summary": items_summary,
            "items_count": len(items),
            "api_url": base_url + "/" + (payload.get("id") or ""),
            "date_modified": self._parse_dt(payload.get("dateModified")),
            "raw_json": json.dumps(payload, ensure_ascii=False)[:524288],  # 512 KB cap
        }

    @staticmethod
    def _parse_dt(value):
        if not value:
            return False
        # Prozorro dates: ISO 8601 with timezone, e.g. 2026-04-28T13:30:00+03:00
        try:
            value = value.replace("Z", "+00:00")
            return datetime.fromisoformat(value).replace(tzinfo=None)
        except (TypeError, ValueError):
            return False

    # ------------------------------------------------------------------ Lead

    def _create_lead(self, subscription):
        """Create a CRM lead for this tender.

        `subscription` may be an empty recordset (manual conversion of a
        tender without a linked subscription); in that case team / user /
        tags / stage default to whatever crm.lead picks up itself.
        """
        self.ensure_one()
        Lead = self.env["crm.lead"].sudo()
        title = (self.title or self.name or "Prozorro tender").strip()
        body_lines = [
            "Imported from Prozorro.",
            "",
            f"Tender: {self.name}",
            f"Procuring entity: {self.procuring_entity_name or '?'}",
        ]
        if self.value_amount:
            body_lines.append(f"Value: {self.value_amount} {self.value_currency_id.name or ''}")
        if self.tender_period_end:
            body_lines.append(f"Tendering ends: {self.tender_period_end}")
        if self.url:
            body_lines.append("")
            body_lines.append(f"Open in Prozorro: {self.url}")

        vals = {
            "name": "[Prozorro] %s" % (title[:80] if len(title) > 80 else title),
            "type": "opportunity",
            "description": "\n".join(body_lines),
            "expected_revenue": self.value_amount or 0.0,
            "prozorro_tender_id": self.id,
        }
        if subscription:
            if subscription.team_id:
                vals["team_id"] = subscription.team_id.id
            if subscription.assign_to_user_id:
                vals["user_id"] = subscription.assign_to_user_id.id
            elif subscription.user_id:
                vals["user_id"] = subscription.user_id.id
            if subscription.tag_ids:
                vals["tag_ids"] = [(6, 0, subscription.tag_ids.ids)]
            if subscription.stage_id:
                vals["stage_id"] = subscription.stage_id.id

        lead = Lead.create(vals)
        self.lead_id = lead.id
        return lead

    # ------------------------------------------------------------------ Retention

    @api.model
    def _cron_retention(self):
        """Drop matched tenders that finished long ago and are not linked to a CRM lead."""
        days = self._get_retention_days()
        cutoff = fields.Datetime.now() - timedelta(days=days)
        stale = self.search(
            [
                ("tender_period_end", "<", cutoff),
                ("lead_id", "=", False),
            ],
            limit=500,
        )
        if stale:
            _logger.info("Prozorro: pruning %d stale tenders older than %d days", len(stale), days)
            stale.unlink()

    # ------------------------------------------------------------------ UX actions

    def action_open_in_prozorro(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": self.url,
            "target": "new",
        }

    def action_convert_to_lead(self):
        """Promote each tender in self to a CRM lead.

        Idempotent per record: tenders that already have a linked lead are
        skipped. New leads use the first matched subscription as context
        (team / user / tags / stage); tenders without a matched subscription
        get a bare lead. On a single record, opens the resulting lead;
        on a batch, opens the lead pipeline filtered to the new leads.
        """
        created = self.env["crm.lead"]
        for tender in self:
            if tender.lead_id:
                continue
            subscription = tender.matched_subscription_ids[:1]
            lead = tender._create_lead(subscription or self.env["prozorro.subscription"])
            created |= lead
        if len(self) == 1:
            return self.action_open_lead()
        if not created:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("No new leads"),
                    "message": _("All selected tenders already had a linked lead."),
                    "type": "warning",
                    "sticky": False,
                },
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("New leads"),
            "res_model": "crm.lead",
            "view_mode": "list,form",
            "domain": [("id", "in", created.ids)],
        }

    def action_open_lead(self):
        self.ensure_one()
        if not self.lead_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("CRM Lead"),
            "res_model": "crm.lead",
            "res_id": self.lead_id.id,
            "view_mode": "form",
            "target": "current",
        }
