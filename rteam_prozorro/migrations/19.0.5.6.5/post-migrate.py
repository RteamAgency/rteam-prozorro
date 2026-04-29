"""Post-migration for 19.0.5.6.5: rework cron active/nextcall.

Background: 5.5.0 made the sync cron `active=False` by default with the
intent that manual `Sync now` would still work via `cron._trigger()`.
That intent is wrong - in Odoo 19, `ir.cron._trigger_list()` filters
out triggers on inactive crons:

    if not self.sudo().active:
        at_list = [at for at in at_list if at > now]

So `_trigger()` on an inactive cron with `at=now()` is a silent no-op:
the trigger row is never created, the cron worker never picks it up,
and the user's click vanishes into the void. Confirmed live on test19
v5.6.4 (build 31426538): click at 15:33 UTC produced no in-progress /
done chatter; ir_cron_trigger empty; cron worker only ran retention.

Fix in 5.6.5: cron is ALWAYS `active=True`. The "Auto-sync on schedule"
toggle in Settings now drives `ir.cron.nextcall` instead:
    toggle ON  -> nextcall = now() + interval (recurring auto-sync)
    toggle OFF -> nextcall = 2099-12-31 (effectively no auto-run)
The user's intent is canonically stored in
`prozorro.auto_sync_enabled` (True/False) ir.config_parameter.

Either way `_trigger()` works because active=True.

This script applies to existing installs:
1. Read whether the user had previously enabled auto-sync (cron.active
   pre-5.6.5) and preserve that intent into the new config_parameter.
2. Force cron.active=True regardless.
3. Set nextcall accordingly: now+interval if previously enabled, 2099
   otherwise.
"""

import logging
from datetime import datetime, timedelta

_logger = logging.getLogger(__name__)

NEVER_NEXTCALL = datetime(2099, 12, 31)


def migrate(cr, version):
    if not version:
        return

    cr.execute(
        """
        SELECT id, active, interval_number, interval_type
        FROM ir_cron
        WHERE id IN (
            SELECT res_id FROM ir_model_data
            WHERE module = 'rteam_prozorro'
              AND name = 'ir_cron_prozorro_sync_feed'
        )
        """
    )
    row = cr.fetchone()
    if not row:
        _logger.info("Prozorro 5.6.5: sync cron xmlid not found, skipping migration")
        return

    cron_id, prev_active, interval_n, interval_t = row
    enabled = bool(prev_active)

    # 1. Preserve user intent in config_parameter (canonical state).
    cr.execute(
        """
        INSERT INTO ir_config_parameter (key, value, create_date, write_date)
        VALUES ('prozorro.auto_sync_enabled', %s, NOW(), NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, write_date = NOW()
        """,
        ("True" if enabled else "False",),
    )

    # 2. Compute the new nextcall.
    if enabled:
        # Schedule next run one interval from now.
        if interval_t == "minutes":
            delta = timedelta(minutes=interval_n)
        elif interval_t == "hours":
            delta = timedelta(hours=interval_n)
        elif interval_t == "days":
            delta = timedelta(days=interval_n)
        elif interval_t == "weeks":
            delta = timedelta(weeks=interval_n)
        else:
            delta = timedelta(hours=6)  # safe default
        new_nextcall = datetime.utcnow() + delta
    else:
        new_nextcall = NEVER_NEXTCALL

    # 3. Force cron active=True; rewrite nextcall.
    cr.execute(
        "UPDATE ir_cron SET active = TRUE, nextcall = %s WHERE id = %s",
        (new_nextcall, cron_id),
    )

    _logger.info(
        "Prozorro 5.6.5: cron rewritten - active=True, "
        "auto_sync_enabled=%s (preserved from prior cron.active), "
        "nextcall=%s",
        enabled,
        new_nextcall,
    )
