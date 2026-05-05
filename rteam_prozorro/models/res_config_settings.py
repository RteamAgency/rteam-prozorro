from datetime import timedelta

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
    prozorro_start_date = fields.Date(
        string="Sync from date",
        config_parameter="prozorro.start_date",
        help="Initial watermark for the feed cursor. The first sync (or "
        "any sync after Reset cursor) starts from midnight Kyiv time on "
        "this date and walks forward; the cursor advances as new tenders "
        "are pulled. Required - leaving this empty refuses to start the "
        "sync to prevent an accidental backfill of the entire Prozorro "
        "history. A reasonable default is 7-30 days back from go-live.",
    )

    # ------------------------------------------------------------------
    # Schedule (canonical state in ir.config_parameter, NOT in ir.cron).
    #
    # As of v5.7.0 the sync cron is permanently parked
    # (active=True, nextcall=2099, interval=10000 days). Odoo's
    # framework never auto-fires it. ALL scheduling is done via
    # `ir_cron_trigger` rows inserted by `cron._trigger(at=...)`:
    #   - action_sync_now (immediate)
    #   - _reschedule_cron_after_run (next interval, if Schedule ON)
    #   - the inverse methods below (when user changes settings)
    #
    # User intent is canonical in ir.config_parameter:
    #   - prozorro.auto_sync_enabled    ("True" / "False")
    #   - prozorro.sync_interval_hours  (int as string)
    #
    # The inverse methods NEVER write to `ir.cron`. This avoids the
    # `lock_for_update` UserError that previously fired when settings
    # save coincided with a running cron (v5.6.x bug pattern, fixed
    # for good in 5.7.0).
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
        Param = self.env["ir.config_parameter"].sudo()
        enabled = Param.get_param("prozorro.auto_sync_enabled", "False") == "True"
        try:
            hours = int(Param.get_param("prozorro.sync_interval_hours", "6") or "6")
        except ValueError:
            hours = 6
        for rec in self:
            rec.prozorro_auto_sync_active = enabled
            rec.prozorro_sync_interval_hours = hours

    def _inverse_prozorro_auto_sync_active(self):
        cron = self.env.ref("rteam_prozorro.ir_cron_prozorro_sync_feed", raise_if_not_found=False)
        Param = self.env["ir.config_parameter"].sudo()
        prev_enabled = Param.get_param("prozorro.auto_sync_enabled", "False") == "True"
        for rec in self:
            enabled = bool(rec.prozorro_auto_sync_active)
            Param.set_param("prozorro.auto_sync_enabled", "True" if enabled else "False")
            if not cron:
                continue
            if enabled and not prev_enabled:
                # Toggle just went ON: queue first auto-run trigger so
                # the user sees the schedule kick in without waiting.
                hours = max(1, int(rec.prozorro_sync_interval_hours or 6))
                cron.sudo()._trigger(at=fields.Datetime.now() + timedelta(hours=hours))
            elif prev_enabled and not enabled:
                # Toggle just went OFF: drop pending future triggers so
                # the cron does not fire again on its own. Manual Sync
                # now still works (it inserts a new immediate trigger).
                self.env.cr.execute(
                    "DELETE FROM ir_cron_trigger WHERE cron_id = %s AND call_at > %s",
                    (cron.id, fields.Datetime.now()),
                )

    def _inverse_prozorro_sync_interval_hours(self):
        cron = self.env.ref("rteam_prozorro.ir_cron_prozorro_sync_feed", raise_if_not_found=False)
        Param = self.env["ir.config_parameter"].sudo()
        enabled = Param.get_param("prozorro.auto_sync_enabled", "False") == "True"
        for rec in self:
            hours = max(1, int(rec.prozorro_sync_interval_hours or 1))
            Param.set_param("prozorro.sync_interval_hours", str(hours))
            if cron and enabled:
                # Replace any pending future trigger with one at the
                # new interval, so the change takes effect immediately
                # instead of waiting for the next natural cron-end.
                self.env.cr.execute(
                    "DELETE FROM ir_cron_trigger WHERE cron_id = %s AND call_at > %s",
                    (cron.id, fields.Datetime.now()),
                )
                cron.sudo()._trigger(at=fields.Datetime.now() + timedelta(hours=hours))

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

    def action_prozorro_force_clear_running(self):
        return self.env["prozorro.tender"].action_force_clear_running()
