"""Post-migration for 19.0.5.5.0: disable the auto-sync cron.

The cron data record has `noupdate="1"` so its active/interval fields
are not refreshed by `-u` upgrades. Operators on existing installs
therefore kept their hourly cron after pulling 5.5.0. Flip it off here
so the new default ("manual sync only, opt-in schedule via Settings")
applies uniformly.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE ir_cron
        SET active = FALSE,
            interval_number = 6,
            interval_type = 'hours'
        WHERE id IN (
            SELECT res_id FROM ir_model_data
            WHERE module = 'rteam_prozorro'
              AND name = 'ir_cron_prozorro_sync_feed'
        )
        """
    )
    _logger.info("Prozorro 5.5.0: disabled auto-sync cron, set default cadence to 6h")
