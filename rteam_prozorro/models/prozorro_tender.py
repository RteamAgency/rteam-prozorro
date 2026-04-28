import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from odoo import _, api, fields, models

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
    def _get_api_base_url(self):
        return (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(
                "prozorro.api_url",
                DEFAULT_API_URL,
            )
        )

    @api.model
    def _get_pages_per_run(self):
        try:
            return int(
                self.env["ir.config_parameter"]
                .sudo()
                .get_param(
                    "prozorro.pages_per_run",
                    DEFAULT_PAGES_PER_RUN,
                )
            )
        except (TypeError, ValueError):
            return DEFAULT_PAGES_PER_RUN

    @api.model
    def _get_retention_days(self):
        try:
            return int(
                self.env["ir.config_parameter"]
                .sudo()
                .get_param(
                    "prozorro.retention_days",
                    DEFAULT_RETENTION_DAYS,
                )
            )
        except (TypeError, ValueError):
            return DEFAULT_RETENTION_DAYS

    # ------------------------------------------------------------------ HTTP

    @api.model
    def _http_get_json(self, url):
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=DEFAULT_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))

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

        base_url = self._get_api_base_url()
        max_pages = self._get_pages_per_run()

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

                next_page = data.get("next_page") or {}
                next_offset = next_page.get("offset")
                if not next_offset or next_offset == cursor.offset:
                    break
                cursor.offset = next_offset
                url = _page_url(next_offset)

        except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as e:
            _logger.exception("Prozorro: sync failed")
            cursor._record_error(str(e))
            return {"pulled": pulled, "matched": matched, "error": str(e), "skipped": False}

        cursor._record_success(pulled, matched)
        _logger.info("Prozorro: sync done. pulled=%d, matched=%d", pulled, matched)
        return {"pulled": pulled, "matched": matched, "error": None, "skipped": False}

    def action_sync_now(self):
        """Trigger a feed sync from the UI and surface the result as a notification."""
        result = self.sudo()._cron_sync_feed() or {}
        if result.get("error"):
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Prozorro sync failed"),
                    "message": result["error"],
                    "type": "danger",
                    "sticky": True,
                },
            }
        if result.get("skipped"):
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Prozorro sync skipped"),
                    "message": _("No active subscriptions to evaluate against."),
                    "type": "warning",
                    "sticky": False,
                },
            }
        pulled = result.get("pulled", 0) or 0
        matched = result.get("matched", 0) or 0
        if pulled == 0:
            message = _(
                "0 tenders pulled. The cursor may have run past the latest data; "
                "click 'Reset cursor' on the Tenders list to rewind to the head of the feed."
            )
            ntype = "warning"
        elif matched == 0:
            message = _(
                "Pulled %(pulled)s tenders but 0 matched any subscription. "
                "Most likely your filters are too narrow: broaden the regions, "
                "statuses, or value range and try again."
            ) % {"pulled": pulled}
            ntype = "warning"
        else:
            message = _("Pulled %(pulled)s tenders, %(matched)s matched.") % {
                "pulled": pulled,
                "matched": matched,
            }
            ntype = "success"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Prozorro sync done"),
                "message": message,
                "type": ntype,
                "sticky": ntype != "success",
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
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
