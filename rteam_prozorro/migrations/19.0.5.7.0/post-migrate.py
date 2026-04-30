"""5.7.0 migration: trigger-based scheduling.

Migrates the sync cron from "ir.cron.nextcall mirroring" (5.6.x and
earlier) to permanent "never auto-fire" state. All scheduling moves
to ir_cron_trigger, written by cron._trigger(at=...).

After this migration:
- ir_cron row holds active=True, nextcall=2099-12-31, interval=10000
  days. Odoo's framework never fires it on schedule.
- ir.config_parameter "prozorro.auto_sync_enabled" / "prozorro.sync_interval_hours"
  is the canonical user intent.
- Manual Sync now and (if Schedule ON) periodic auto-run both go
  through ir_cron_trigger.

Idempotent: safe to re-run.
"""

import logging
from datetime import datetime, timedelta

from odoo import SUPERUSER_ID, api, fields

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref("rteam_prozorro.ir_cron_prozorro_sync_feed", raise_if_not_found=False)
    if not cron:
        _logger.warning("Prozorro 5.7.0 migration: cron xmlid missing, nothing to migrate")
        return
    Param = env["ir.config_parameter"].sudo()

    # 1. Persist the user's currently configured interval to
    # config_parameter if not already set there. Convert from whatever
    # interval_type the cron row uses today (5.6.x stored hours, but
    # be defensive in case someone hand-edited).
    if not Param.get_param("prozorro.sync_interval_hours", False):
        n = cron.interval_number or 6
        t = cron.interval_type or "hours"
        if t == "minutes":
            hours = max(1, round(n / 60))
        elif t == "hours":
            hours = n
        elif t == "days":
            hours = n * 24
        elif t == "weeks":
            hours = n * 24 * 7
        else:
            hours = max(1, int(n))
        Param.set_param("prozorro.sync_interval_hours", str(hours))
        _logger.info(
            "Prozorro 5.7.0: seeded sync_interval_hours=%s from cron row "
            "(interval_number=%s, interval_type=%s)",
            hours,
            n,
            t,
        )

    # 2. Force the cron row into permanent never-auto state. Idempotent.
    cron.sudo().write(
        {
            "active": True,
            "nextcall": datetime(2099, 12, 31),
            "interval_number": 10000,
            "interval_type": "days",
        }
    )
    _logger.info(
        "Prozorro 5.7.0: cron parked at active=True, nextcall=2099-12-31, "
        "interval=10000 days (Odoo will never auto-fire it)"
    )

    # 3. Drop any pending future triggers from the legacy state so we
    # start from a clean queue. Past-due triggers (call_at <= now) are
    # left alone so the cron worker still picks them up if a manual
    # click was queued just before upgrade.
    cr.execute(
        "DELETE FROM ir_cron_trigger WHERE cron_id = %s AND call_at > NOW()",
        (cron.id,),
    )
    _logger.info("Prozorro 5.7.0: cleared %s future trigger row(s)", cr.rowcount or 0)

    # 4. If the user had auto-sync enabled before the upgrade, queue the
    # next run trigger so they don't lose their schedule.
    enabled = Param.get_param("prozorro.auto_sync_enabled", "False") == "True"
    if enabled:
        try:
            hours = max(
                1,
                int(Param.get_param("prozorro.sync_interval_hours", "6") or "6"),
            )
        except ValueError:
            hours = 6
        cron.sudo()._trigger(at=fields.Datetime.now() + timedelta(hours=hours))
        _logger.info(
            "Prozorro 5.7.0: queued next auto-run trigger at +%dh "
            "(Schedule toggle was ON before migration)",
            hours,
        )

    # 5. Clear any stuck is_running flag on the sync cursor singleton.
    # The container restart that triggered this migration would have
    # killed any in-flight sync, so a True flag here is necessarily
    # stale. Avoids the user having to click Force stop after upgrade.
    cr.execute(
        """
        UPDATE prozorro_sync_cursor
        SET is_running = FALSE
        WHERE name = 'main' AND is_running = TRUE
        """
    )
    if cr.rowcount:
        _logger.info(
            "Prozorro 5.7.0: cleared stuck is_running flag on sync cursor "
            "(left over from pre-upgrade run)"
        )
