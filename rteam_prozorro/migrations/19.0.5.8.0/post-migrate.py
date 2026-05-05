"""5.8.0 migration: switch feed direction from descending to ascending.

Why:
  Pre-5.8.0 the cron walked Prozorro with `descending=1` and a persisted
  offset. Each successful run advanced the cursor deeper into history.
  Once the cursor passed the operator's effective interest window, NEW
  tenders published after that point were never seen again - the feed
  was being read in the wrong direction.

  5.8.0 switches to ascending (the OpenProcurement default), in which
  the offset is a watermark with long-poll replication semantics: after
  exhausting the feed the same offset stays valid and subsequent calls
  return only tenders published since.

What this migration does:
  1. Wipes `prozorro_sync_cursor.offset` on every install. Existing
     offsets are descending-mode tokens - reusing them as ascending
     watermarks would make the cron walk forward from a random point
     deep in history (and likely re-pull thousands of old tenders the
     operator never wanted).
  2. Seeds `prozorro.start_date` to today minus 7 days IF unset, so
     auto-sync clients do not stop running on upgrade. Operators are
     expected to revisit Settings and pick the date they actually want.

Idempotent: safe to re-run.
"""

import logging
from datetime import date, timedelta

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("UPDATE prozorro_sync_cursor SET offset = NULL WHERE offset IS NOT NULL")
    if cr.rowcount:
        _logger.info(
            "Prozorro 5.8.0: cleared %s descending-mode offset row(s) on sync cursor "
            "(ascending watermark requires re-seed from start_date)",
            cr.rowcount,
        )

    cr.execute(
        """
        SELECT value FROM ir_config_parameter
        WHERE key = 'prozorro.start_date'
        """
    )
    if cr.fetchone():
        _logger.info("Prozorro 5.8.0: start_date already set, leaving it alone")
        return

    default_start = (date.today() - timedelta(days=7)).isoformat()
    cr.execute(
        """
        INSERT INTO ir_config_parameter (key, value, create_date, write_date)
        VALUES ('prozorro.start_date', %s, NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC')
        """,
        (default_start,),
    )
    _logger.info(
        "Prozorro 5.8.0: seeded prozorro.start_date = %s (today - 7d). "
        "Operator should revisit Settings -> Prozorro -> Feed to pick the "
        "real backfill horizon.",
        default_start,
    )
