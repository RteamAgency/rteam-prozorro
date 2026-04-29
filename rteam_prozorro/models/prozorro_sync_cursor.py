from odoo import api, fields, models


class ProzorroSyncCursor(models.Model):
    """Singleton-ish state for the Prozorro feed sync.

    One row per cursor name (default 'main'). The cron updates `offset`
    after each successful page pull so the next run resumes exactly where
    the previous left off, per OpenProcurement's replication-oriented
    feed semantics.
    """

    _name = "prozorro.sync.cursor"
    _description = "Prozorro Feed Sync Cursor"
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
    is_running = fields.Boolean(string="Currently syncing")
    last_started_at = fields.Datetime(string="Last started at")

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
        """Mark the cursor as actively syncing.

        Written via an isolated cursor (caller's responsibility) so the
        flag stays True even if the long sync transaction commits later.
        """
        self.ensure_one()
        self.write(
            {
                "is_running": True,
                "last_started_at": fields.Datetime.now(),
            }
        )

    def _record_success(self, pulled, matched):
        self.ensure_one()
        self.write(
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

    def _record_error(self, msg):
        self.ensure_one()
        self.write(
            {
                "last_error": msg,
                "last_error_at": fields.Datetime.now(),
                "is_running": False,
            }
        )
