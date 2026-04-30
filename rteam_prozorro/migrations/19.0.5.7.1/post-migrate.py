"""5.7.1 migration: clean leftover state from 5.7.0.

The 5.7.0 migration only deleted FUTURE ir_cron_trigger rows
(`call_at > NOW()`); past-due rows were left to fire after upgrade,
which we observed on test19 (3 stale triggers re-firing the cron
even with auto_sync_enabled=False).

This migration:
1. Drops ALL trigger rows for the sync cron (not just future ones).
2. Clears any stuck is_running flag on the cursor singleton.
3. Clears any leftover cancel_requested flag (the column was added
   in 5.7.1 so it defaults to False on new installs, but be
   defensive in case of partial-state from manual edits).

The cancel_requested column itself is auto-added by Odoo's ORM on
field declaration; no explicit ALTER TABLE needed.

Idempotent: safe to re-run.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute(
        """
        SELECT res_id FROM ir_model_data
        WHERE module = 'rteam_prozorro' AND name = 'ir_cron_prozorro_sync_feed'
        """
    )
    row = cr.fetchone()
    if not row:
        _logger.warning("Prozorro 5.7.1 migration: cron xmlid missing, nothing to clean")
        return
    cron_id = row[0]

    cr.execute(
        "DELETE FROM ir_cron_trigger WHERE cron_id = %s",
        (cron_id,),
    )
    _logger.info(
        "Prozorro 5.7.1: cleared %s trigger row(s) for sync cron "
        "(stale leftovers from 5.7.0 past-due)",
        cr.rowcount or 0,
    )

    cr.execute(
        """
        UPDATE prozorro_sync_cursor
        SET is_running = FALSE, cancel_requested = FALSE
        WHERE name = 'main' AND (is_running = TRUE OR cancel_requested = TRUE)
        """
    )
    if cr.rowcount:
        _logger.info(
            "Prozorro 5.7.1: cleared stuck is_running/cancel_requested flags on sync cursor"
        )
