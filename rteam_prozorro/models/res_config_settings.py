from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    prozorro_api_url = fields.Char(
        string="Prozorro API URL",
        config_parameter="prozorro.api_url",
        default="https://public.api.openprocurement.org/api/2.5/tenders",
        help="Public read endpoint of the Prozorro / OpenProcurement API.",
    )
    prozorro_pages_per_run = fields.Integer(
        string="Pages per sync run",
        config_parameter="prozorro.pages_per_run",
        default=20,
        help="Safety cap. Each page returns up to 100 tenders.",
    )
    prozorro_retention_days = fields.Integer(
        string="Retention (days)",
        config_parameter="prozorro.retention_days",
        default=60,
        help="Drop matched tenders this many days after tender_period_end "
        "unless a CRM lead is linked.",
    )

    # ------------------------------------------------------------------
    # Schedule (binds directly to ir.cron.active / interval_number)
    # Default behaviour: cron is INACTIVE, sync only runs when an
    # operator clicks Sync now (which fires `cron._trigger()` and runs
    # the cron once regardless of `active`). When the toggle below is
    # flipped on, the cron also runs on its scheduled cadence.
    # ------------------------------------------------------------------
    prozorro_auto_sync_active = fields.Boolean(
        string="Auto-sync on schedule",
        compute="_compute_prozorro_cron",
        inverse="_inverse_prozorro_auto_sync_active",
        help="If on, the feed sync runs automatically on the interval below. "
        "If off, sync only runs when you click 'Sync now' on the Tenders or "
        "Subscriptions list. Manual sync works regardless of this toggle.",
    )
    prozorro_sync_interval_hours = fields.Integer(
        string="Auto-sync interval (hours)",
        compute="_compute_prozorro_cron",
        inverse="_inverse_prozorro_sync_interval_hours",
        help="How often the cron pulls new tenders from Prozorro. "
        "Only used when 'Auto-sync on schedule' is on.",
    )

    # ------------------------------------------------------------------
    # Status (read-only display of prozorro.sync.cursor singleton)
    # ------------------------------------------------------------------
    prozorro_is_running = fields.Boolean(
        string="Sync running now",
        compute="_compute_prozorro_status",
    )
    prozorro_last_started_at = fields.Datetime(
        string="Last started",
        compute="_compute_prozorro_status",
    )
    prozorro_last_sync_at = fields.Datetime(
        string="Last finished",
        compute="_compute_prozorro_status",
    )
    prozorro_last_pulled = fields.Integer(
        string="Pulled (last run)",
        compute="_compute_prozorro_status",
    )
    prozorro_last_matched = fields.Integer(
        string="Matched (last run)",
        compute="_compute_prozorro_status",
    )
    prozorro_last_error = fields.Text(
        string="Last error",
        compute="_compute_prozorro_status",
    )

    # ------------------------------------------------------------------ Compute / inverse

    @api.depends_context("uid")
    def _compute_prozorro_cron(self):
        cron = self.env.ref("rteam_prozorro.ir_cron_prozorro_sync_feed", raise_if_not_found=False)
        active = bool(cron and cron.sudo().active)
        # Normalise to hours - cron stores interval_type separately, but
        # we expose a single hours field for simplicity. Days/minutes
        # values still display correctly as a rounded hour count.
        hours = 0
        if cron:
            cron_s = cron.sudo()
            n = cron_s.interval_number or 0
            t = cron_s.interval_type
            if t == "minutes":
                hours = max(1, round(n / 60))
            elif t == "hours":
                hours = n
            elif t == "days":
                hours = n * 24
            elif t == "weeks":
                hours = n * 24 * 7
            else:
                hours = n
        for rec in self:
            rec.prozorro_auto_sync_active = active
            rec.prozorro_sync_interval_hours = hours

    def _inverse_prozorro_auto_sync_active(self):
        cron = self.env.ref("rteam_prozorro.ir_cron_prozorro_sync_feed", raise_if_not_found=False)
        if not cron:
            return
        for rec in self:
            cron.sudo().active = bool(rec.prozorro_auto_sync_active)

    def _inverse_prozorro_sync_interval_hours(self):
        cron = self.env.ref("rteam_prozorro.ir_cron_prozorro_sync_feed", raise_if_not_found=False)
        if not cron:
            return
        for rec in self:
            hours = max(1, int(rec.prozorro_sync_interval_hours or 1))
            cron.sudo().write(
                {
                    "interval_number": hours,
                    "interval_type": "hours",
                }
            )

    @api.depends_context("uid")
    def _compute_prozorro_status(self):
        Cursor = self.env["prozorro.sync.cursor"].sudo()
        cursor = Cursor.search([("name", "=", "main")], limit=1)
        for rec in self:
            rec.prozorro_is_running = bool(cursor and cursor.is_running)
            rec.prozorro_last_started_at = cursor.last_started_at if cursor else False
            rec.prozorro_last_sync_at = cursor.last_sync if cursor else False
            rec.prozorro_last_pulled = cursor.last_pulled if cursor else 0
            rec.prozorro_last_matched = cursor.last_matched if cursor else 0
            rec.prozorro_last_error = cursor.last_error if cursor else False

    # ------------------------------------------------------------------ Actions

    def action_prozorro_sync_now(self):
        """Trigger the sync from the Settings page status block."""
        return self.env["prozorro.tender"].action_sync_now()

    def action_prozorro_reset_cursor(self):
        return self.env["prozorro.tender"].action_reset_sync_cursor()
