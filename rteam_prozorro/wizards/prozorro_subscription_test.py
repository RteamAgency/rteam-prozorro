import urllib.error

from odoo import _, fields, models
from odoo.exceptions import UserError


class ProzorroSubscriptionTest(models.TransientModel):
    """Wizard: paste a Prozorro tender UUID, evaluate the subscription,
    show field-by-field which filter passed and why.

    Removes the dependency on what happens to be in the live feed when
    operators are tuning rules. Fetches the same way the cron does
    (`urllib.request` -> Prozorro public API), but never persists the
    tender or creates a lead.
    """

    _name = "prozorro.subscription.test"
    _description = "Prozorro Subscription Test Match"

    subscription_id = fields.Many2one(
        "prozorro.subscription",
        string="Subscription",
        required=True,
        default=lambda s: s.env.context.get("active_id"),
    )
    tender_uuid = fields.Char(
        string="Tender UUID or URL",
        required=True,
        help="The Prozorro tender id (e.g. `3af829c1a9af4812a5c2c4bf090f2b44`) "
        "or a full prozorro.gov.ua tender page URL.",
    )
    verdict = fields.Selection(
        [("match", "Matches"), ("no_match", "Does NOT match"), ("error", "Fetch error")],
        readonly=True,
    )
    summary_html = fields.Html(string="Result", readonly=True, sanitize=False)
    fetched_title = fields.Char(string="Tender title", readonly=True)
    fetched_status = fields.Char(string="Tender status", readonly=True)

    def _resolve_uuid(self):
        """Extract the bare 32-char id from either a raw UUID or a public URL."""
        s = (self.tender_uuid or "").strip()
        # prozorro.gov.ua URLs end with the bare id or with UA-2026-... ; the
        # API works only with the bare id, so we accept both and pick the last
        # path segment as a hint.
        if "/" in s:
            s = s.rstrip("/").rsplit("/", 1)[-1]
        return s

    def action_test_match(self):
        self.ensure_one()
        Tender = self.env["prozorro.tender"]
        tender_id = self._resolve_uuid()
        if not tender_id:
            raise UserError(_("Provide a tender UUID or prozorro.gov.ua URL."))
        base_url = Tender._get_api_base_url()
        try:
            payload = Tender._http_get_json(base_url + "/" + tender_id)
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as e:
            self.write(
                {
                    "verdict": "error",
                    "summary_html": f"<p><b>{_('Fetch failed')}</b></p><pre>{e}</pre>",
                    "fetched_title": False,
                    "fetched_status": False,
                }
            )
            return self._reopen()
        data = payload.get("data") or {}
        if not data:
            self.write(
                {
                    "verdict": "error",
                    "summary_html": f"<p>{_('Prozorro API returned an empty payload.')}</p>",
                    "fetched_title": False,
                    "fetched_status": False,
                }
            )
            return self._reopen()

        results = self.subscription_id._match_with_reasons(data)
        all_pass = all(p for _name, p, _reason in results)
        rows = []
        for name, passed, reason in results:
            icon = "+" if passed else "-"
            color = "#16a34a" if passed else "#dc2626"
            rows.append(
                f'<tr><td style="padding:4px 8px;color:{color};font-weight:bold;">{icon}</td>'
                f'<td style="padding:4px 8px;font-weight:bold;">{name}</td>'
                f'<td style="padding:4px 8px;">{reason}</td></tr>'
            )
        verdict_label = (
            _("Matches the subscription") if all_pass else _("Does NOT match the subscription")
        )
        verdict_color = "#16a34a" if all_pass else "#dc2626"
        summary = (
            f'<h3 style="color:{verdict_color};">{verdict_label}</h3>'
            f'<table style="border-collapse:collapse;">' + "".join(rows) + "</table>"
        )

        self.write(
            {
                "verdict": "match" if all_pass else "no_match",
                "summary_html": summary,
                "fetched_title": data.get("title"),
                "fetched_status": data.get("status"),
            }
        )
        return self._reopen()

    def _reopen(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
