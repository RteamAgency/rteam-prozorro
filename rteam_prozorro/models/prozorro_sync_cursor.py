from odoo import _, api, fields, models


class ProzorroSyncCursor(models.Model):
    """Singleton-ish state for the Prozorro feed sync.

    One row per cursor name (default 'main'). The cron updates `offset`
    after each successful page pull so the next run resumes exactly where
    the previous left off, per OpenProcurement's replication-oriented
    feed semantics.

    Inherits mail.thread so each sync run posts a chatter line ("Sync
    started ...", "Pulled N tenders, M matched", or "Sync failed: ...").
    Operators can open the singleton from the Prozorro menu to see what
    every recent run actually did, instead of relying on the ephemeral
    bus.bus toast which disappears after a few seconds.
    """

    _name = "prozorro.sync.cursor"
    _description = "Prozorro Feed Sync Cursor"
    _inherit = ["mail.thread"]
    _rec_name = "name"

    name = fields.Char(required=True, default="main", index=True)
    offset = fields.Char(
        string="Cursor offset", help="Opaque pagination cursor returned by Prozorro"
    )
    last_sync = fields.Datetime(string="Last sync at")
    last_error = fields.Text()
    last_error_at = fields.Datetime()
    pulled_total = fields.Integer(string="Pulled (lifetime)")
    matched_total = fields.Integer(string="Matched (lifetime)")
    last_pulled = fields.Integer(string="Pulled (last run)")
    last_matched = fields.Integer(string="Matched (last run)")
    last_started_at = fields.Datetime(string="Last run started at")
    is_running = fields.Boolean(
        string="Sync running",
        help="True between _record_start and _record_success/_record_error.",
    )

    _sql_constraints = [
        ("prozorro_sync_cursor_name_uniq", "unique(name)", "Cursor name must be unique."),
    ]

    @api.model
    def _get_singleton(self, name="main"):
        rec = self.search([("name", "=", name)], limit=1)
        if not rec:
            rec = self.create({"name": name})
        return rec

    def _record_start(self):
        """Mark a sync run as started and post to the chatter.

        Operators tail the chatter to see real-time progress instead of
        guessing whether anything is happening.
        """
        self.ensure_one()
        self.sudo().write(
            {
                "is_running": True,
                "last_started_at": fields.Datetime.now(),
            }
        )
        self.message_post(
            body=_("Sync in progress..."),
            subtype_xmlid="mail.mt_note",
        )

    def _record_success(self, pulled, matched):
        self.ensure_one()
        self.sudo().write(
            {
                "last_sync": fields.Datetime.now(),
                "pulled_total": (self.pulled_total or 0) + pulled,
                "matched_total": (self.matched_total or 0) + matched,
                "last_pulled": pulled,
                "last_matched": matched,
                "last_error": False,
                "is_running": False,
            }
        )
        self.message_post(
            body=_(
                "Sync completed: pulled %(pulled)s tenders, %(matched)s matched.",
                pulled=pulled,
                matched=matched,
            ),
            subtype_xmlid="mail.mt_note",
        )

    def _record_skipped(self, reason):
        """Sync ran but did no API work (e.g. no active subscriptions).
        Posted to chatter so the operator understands why a clicked
        Sync now produced nothing."""
        self.ensure_one()
        self.sudo().write({"is_running": False})
        self.message_post(
            body=_("Sync skipped: %s", reason),
            subtype_xmlid="mail.mt_note",
        )

    def _record_error(self, msg):
        self.ensure_one()
        self.sudo().write(
            {
                "last_error": msg,
                "last_error_at": fields.Datetime.now(),
                "is_running": False,
            }
        )
        self.message_post(
            body=_("Sync failed: %s", msg or _("unknown error")),
            subtype_xmlid="mail.mt_note",
        )

    def action_open_sync_log(self):
        """Open this cursor's form view (which renders the chatter)."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
            "name": _("Prozorro Sync Log"),
        }
