from datetime import datetime, timedelta

from odoo import api, fields, models

# Sentinel "never auto-run" value for ir.cron.nextcall when the user has
# the Schedule toggle OFF. Far enough in the future that the cron worker
# treats it as "never ready" but still allows manual `_trigger()` to
# create a trigger row that bypasses nextcall.
NEVER_NEXTCALL = datetime(2099, 12, 31)


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
    # Schedule (binds to ir.cron.nextcall, NOT active).
    #
    # The cron is ALWAYS active=True - that is required for manual
    # `Sync now` clicks (which call `cron._trigger()`) to actually
    # queue a trigger row. Odoo 19's `_trigger_list` filters out
    # triggers on inactive crons, so an inactive cron + _trigger() is
    # a silent no-op. We learned this the hard way on v5.6.4 (test19,
    # build 31426538: click at 15:33 UTC produced no in-progress /
    # done chatter; ir_cron_trigger was empty).
    #
    # The Schedule toggle below now drives `nextcall` instead:
    #     ON  -> nextcall = now() + interval (recurring auto-sync)
    #     OFF -> nextcall = 2099-12-31 (effectively no auto-run)
    # Either way `_trigger()` works because active=True.
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
        # The user's intent is stored in `prozorro.auto_sync_enabled`
        # config_parameter (source of truth). The cron's nextcall mirrors
        # that intent (real time vs 2099 sentinel) but Odoo overwrites
        # nextcall after each run, so config_parameter is the canonical
        # state.
        Param = self.env["ir.config_parameter"].sudo()
        enabled = Param.get_param("prozorro.auto_sync_enabled", "False") == "True"
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
            rec.prozorro_auto_sync_active = enabled
            rec.prozorro_sync_interval_hours = hours

    def _inverse_prozorro_auto_sync_active(self):
        cron = self.env.ref("rteam_prozorro.ir_cron_prozorro_sync_feed", raise_if_not_found=False)
        Param = self.env["ir.config_parameter"].sudo()
        for rec in self:
            enabled = bool(rec.prozorro_auto_sync_active)
            Param.set_param("prozorro.auto_sync_enabled", "True" if enabled else "False")
            if not cron:
                continue
            cron_s = cron.sudo()
            if enabled:
                hours = max(1, int(rec.prozorro_sync_interval_hours or 6))
                cron_s.write(
                    {
                        "active": True,
                        "nextcall": fields.Datetime.now() + timedelta(hours=hours),
                    }
                )
            else:
                cron_s.write({"active": True, "nextcall": NEVER_NEXTCALL})

    def _inverse_prozorro_sync_interval_hours(self):
        cron = self.env.ref("rteam_prozorro.ir_cron_prozorro_sync_feed", raise_if_not_found=False)
        if not cron:
            return
        for rec in self:
            hours = max(1, int(rec.prozorro_sync_interval_hours or 1))
            vals = {
                "interval_number": hours,
                "interval_type": "hours",
                "active": True,
            }
            if rec.prozorro_auto_sync_active:
                vals["nextcall"] = fields.Datetime.now() + timedelta(hours=hours)
            cron.sudo().write(vals)

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
