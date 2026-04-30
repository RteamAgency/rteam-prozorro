import json
import logging
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta

from odoo import SUPERUSER_ID, _, api, fields, models

# Sentinel "never auto-run" value for ir.cron.nextcall when the Schedule
# toggle is OFF. Far enough in the future that the cron worker treats
# it as "never ready" but `_trigger()` from a manual Sync now click
# still creates a trigger row that bypasses nextcall.
SYNC_CRON_NEVER_NEXTCALL = datetime(2099, 12, 31)

# After this many minutes of `is_running=True` on the cursor singleton,
# we consider the previous run dead (container kill, OOM, Odoo.sh
# rebuild mid-run, etc.) and self-heal so the user is not stuck. Real
# runs in production take 5-30 minutes; 60 is a safe upper bound.
STALE_RUN_MINUTES = 60

_logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://public.api.openprocurement.org/api/2.5/tenders"
DEFAULT_PAGE_LIMIT = 100
DEFAULT_PAGES_PER_RUN = 20  # safety cap so a single cron run can't loop forever
DEFAULT_RETENTION_DAYS = 60
DEFAULT_HTTP_TIMEOUT = 25

# Mirror prozorro.tender.status master data (data/prozorro_tender_status_data.xml).
# Keep in sync: missing codes here = sync run aborts when API returns the new
# status. As of 2026-04-29 Prozorro publishes these eleven values.
PROZORRO_STATUS = [
    ("draft", "Draft"),
    ("active.enquiries", "Enquiries"),
    ("active.tendering", "Tendering"),
    ("active.pre-qualification", "Pre-qualification"),
    ("active.pre-qualification.stand-still", "Pre-qualification stand-still"),
    ("active.auction", "Auction"),
    ("active.qualification", "Qualification"),
    ("active.awarded", "Awarded"),
    ("complete", "Complete"),
    ("cancelled", "Cancelled"),
    ("unsuccessful", "Unsuccessful"),
]
PROZORRO_STATUS_CODES = {code for code, _label in PROZORRO_STATUS}


class ProzorroTender(models.Model):
    """Mirrored tender record - persisted only when matched by at least one subscription."""

    _name = "prozorro.tender"
    _description = "Prozorro Tender"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_modified desc"
    _rec_name = "name"

    name = fields.Char(string="Tender ID", required=True, index=True, tracking=True)  # tenderID
    uuid = fields.Char(string="Internal UUID", required=True, index=True, copy=False)

    title = fields.Char(tracking=True)
    description = fields.Text()
    status = fields.Selection(PROZORRO_STATUS, tracking=True)

    procurement_method = fields.Char()
    procurement_method_type = fields.Char(string="Procurement method type", index=True)

    value_amount = fields.Monetary(currency_field="value_currency_id", tracking=True)
    value_currency_id = fields.Many2one(
        "res.currency",
        default=lambda s: s.env.ref("base.UAH", raise_if_not_found=False),
    )

    procuring_entity_name = fields.Char(string="Procuring entity")
    procuring_entity_edrpou = fields.Char(string="EDRPOU", index=True)
    procuring_entity_country = fields.Char()
    procuring_entity_region = fields.Char(index=True)
    procuring_entity_locality = fields.Char()

    tender_period_start = fields.Datetime()
    tender_period_end = fields.Datetime(index=True)
    enquiry_period_end = fields.Datetime(string="Enquiries until")
    auction_period_start = fields.Datetime(string="Auction at")

    classification_ids = fields.Many2many("prozorro.classification", string="CPV / DK021")
    items_summary = fields.Text(string="Items")
    items_count = fields.Integer()

    url = fields.Char(string="Public URL", compute="_compute_url", store=True)
    api_url = fields.Char(string="API URL")
    date_modified = fields.Datetime(index=True)
    date_imported = fields.Datetime(default=fields.Datetime.now, index=True)
    raw_json = fields.Text(string="Raw payload")

    matched_subscription_ids = fields.Many2many(
        "prozorro.subscription", string="Matched subscriptions"
    )
    lead_id = fields.Many2one("crm.lead", string="CRM Lead", ondelete="set null", index=True)
    company_id = fields.Many2one(
        "res.company",
        default=lambda s: s.env.company,
        required=True,
    )

    _sql_constraints = [
        ("prozorro_tender_uuid_uniq", "unique(uuid)", "Tender UUID must be unique."),
    ]

    # ------------------------------------------------------------------ Compute

    @api.depends("name")
    def _compute_url(self):
        for rec in self:
            rec.url = f"https://prozorro.gov.ua/tender/{rec.name}" if rec.name else False

    # ------------------------------------------------------------------ Config helpers

    @api.model
    def _get_int_param(self, key, default):
        """Read an int ir.config_parameter, falling back to default on
        missing or non-numeric values."""
        raw = self.env["ir.config_parameter"].sudo().get_param(key, default)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    @api.model
    def _get_api_base_url(self):
        return self.env["ir.config_parameter"].sudo().get_param("prozorro.api_url", DEFAULT_API_URL)

    @api.model
    def _get_pages_per_run(self):
        return self._get_int_param("prozorro.pages_per_run", DEFAULT_PAGES_PER_RUN)

    @api.model
    def _get_retention_days(self):
        return self._get_int_param("prozorro.retention_days", DEFAULT_RETENTION_DAYS)

    # ------------------------------------------------------------------ HTTP

    @api.model
    def _http_get_json(self, url):
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=DEFAULT_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ------------------------------------------------------------------ Chatter
    # Posts to subscription chatter run in an independent cursor so the
    # message persists even if the long HTTP sync loop later rolls back
    # (timeout, network error, container restart). Without this, a "Sync
    # in progress..." message_post inside the cron transaction is lost
    # together with everything else when the transaction rolls back, and
    # operators see nothing in the chatter despite clicking Sync now.

    def _post_chatter_isolated(self, sub_ids, body):
        if not sub_ids:
            return
        try:
            with self.pool.cursor() as new_cr:
                env = api.Environment(new_cr, SUPERUSER_ID, {})
                env["prozorro.subscription"].browse(sub_ids).message_post(
                    body=body,
                    subtype_xmlid="mail.mt_note",
                )
        except Exception:
            _logger.exception("Prozorro: failed to post chatter line to subscriptions %s", sub_ids)

    def _mark_cursor_isolated(self, cursor_id, **vals):
        """Update prozorro.sync.cursor in an independent transaction.

        Used to flip `is_running` on/off so the Settings status block
        reflects state IN REAL TIME, even though the cron's main
        transaction is still inside the long HTTP loop.
        """
        if not cursor_id or not vals:
            return
        try:
            with self.pool.cursor() as new_cr:
                env = api.Environment(new_cr, SUPERUSER_ID, {})
                env["prozorro.sync.cursor"].browse(cursor_id).write(vals)
        except Exception:
            _logger.exception("Prozorro: failed to update cursor %s with %s", cursor_id, vals)

    # ------------------------------------------------------------------ Cron

    @api.model
    def _cron_sync_feed(self):
        """Cron entry point. Always returns a result dict for the manual UI button to render.

        Walks the Prozorro feed in descending order (latest tenders first). The
        cursor stores the offset returned after the last walked page so the
        next run continues backward from where the previous one stopped.
        `descending=1` is preserved across pages within a single run (without
        it the API switches to ascending and we'd skip past the latest tail).

        Wrapped in try/except/finally so the `is_running` flag on the
        cursor singleton is ALWAYS reset, even on uncaught exceptions
        (UserError from Odoo internals, container restart mid-run,
        memory error, etc.). v5.6.5's `_reschedule_cron_after_run`
        raised UserError mid-handler; the rollback wiped
        `_record_success` but the isolated-cursor write of
        `is_running=True` had already committed, leaving the flag
        stuck for 16+ hours until manually cleared. The finally block
        below uses an isolated cursor too, so the reset survives
        any rollback path.
        """
        Cursor = self.env["prozorro.sync.cursor"]
        Subscription = self.env["prozorro.subscription"]
        cursor = Cursor._get_singleton("main")

        subs = Subscription._get_active_subscriptions()
        if not subs:
            _logger.info("Prozorro: no active subscriptions, skipping sync")
            self._reschedule_cron_after_run()
            return {"pulled": 0, "matched": 0, "error": None, "skipped": True}

        # Sync started: post a one-line chatter note on each active
        # subscription. Done in an independent cursor so the message
        # persists even if the HTTP loop below later crashes / times out
        # / hits a container restart. Without isolation, the operator
        # sees nothing in chatter despite clicking Sync now.
        self._post_chatter_isolated(
            subs.ids,
            _("Prozorro sync in progress..."),
        )
        # Flip the running flag in an isolated cursor so the Settings
        # status block (and any future polling banner) sees it instantly,
        # not when the cron transaction commits ~tens of minutes later.
        self._mark_cursor_isolated(
            cursor.id,
            is_running=True,
            last_started_at=fields.Datetime.now(),
        )

        base_url = self._get_api_base_url()
        max_pages = self._get_pages_per_run()
        per_sub_matched = Counter()

        def _page_url(offset):
            params = f"descending=1&limit={DEFAULT_PAGE_LIMIT}"
            if offset:
                params += f"&offset={offset}"
            return base_url + "?" + params

        url = _page_url(cursor.offset)

        pulled, matched = 0, 0
        # Outer try/finally guarantees is_running gets reset even on
        # an uncaught exception (UserError from Odoo internals,
        # container kill, OOM, etc.). Without this, the isolated-cursor
        # `is_running=True` sticks forever and the Settings panel
        # shows "Sync running" until manually cleared. Burned us on
        # v5.6.5 (test19 build 31426538).
        try:
            for _page in range(max_pages):
                data = self._http_get_json(url)
                records = data.get("data") or []
                for record in records:
                    pulled += 1
                    tender_uuid = record.get("id")
                    if not tender_uuid:
                        continue
                    try:
                        full = self._http_get_json(base_url + "/" + tender_uuid)
                    except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as e:
                        _logger.warning("Prozorro: failed to fetch %s: %s", tender_uuid, e)
                        continue
                    tender_payload = full.get("data") or {}
                    if not tender_payload:
                        continue
                    matches = subs.filtered(lambda s, tp=tender_payload: s._matches(tp))
                    if matches:
                        try:
                            self._upsert_tender(tender_payload, matches, base_url)
                        except Exception:
                            _logger.exception(
                                "Prozorro: failed to upsert tender %s, skipping",
                                tender_uuid,
                            )
                            continue
                        matched += 1
                        for sub in matches:
                            per_sub_matched[sub.id] += 1

                next_page = data.get("next_page") or {}
                next_offset = next_page.get("offset")
                if not next_offset or next_offset == cursor.offset:
                    break
                cursor.offset = next_offset
                url = _page_url(next_offset)

        except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as e:
            _logger.exception("Prozorro: sync failed")
            cursor._record_error(str(e))
            self._post_chatter_isolated(
                subs.ids,
                _("Prozorro sync failed: %s", str(e)[:200]),
            )
            self._notify_sync_result(pulled, matched, error=str(e))
            self._reschedule_cron_after_run()
            self._mark_cursor_isolated(cursor.id, is_running=False)
            return {"pulled": pulled, "matched": matched, "error": str(e), "skipped": False}
        except Exception as e:
            # Uncaught (non-HTTP) exception. Best-effort isolated-cursor
            # write of the error message + is_running=False so the user
            # sees what went wrong instead of a stuck spinner. Re-raise
            # so the cron framework records the failure properly.
            _logger.exception("Prozorro: uncaught exception in cron handler")
            self._mark_cursor_isolated(
                cursor.id,
                is_running=False,
                last_error=str(e)[:500],
                last_error_at=fields.Datetime.now(),
            )
            raise
        finally:
            # Always reset is_running, even on early-return / re-raise
            # paths. Idempotent: writing False on an already-False row
            # is a no-op write.
            self._mark_cursor_isolated(cursor.id, is_running=False)

        cursor._record_success(pulled, matched)
        _logger.info("Prozorro: sync done. pulled=%d, matched=%d", pulled, matched)
        self._reschedule_cron_after_run()
        # Per-subscription chatter line with the run's outcome FOR THIS
        # subscription. Posted via isolated cursor so it survives any
        # late-stage rollback (e.g. lead-creation failure on retry).
        for sub in subs:
            n = per_sub_matched.get(sub.id, 0)
            self._post_chatter_isolated(
                sub.ids,
                _(
                    "Prozorro sync done: pulled %(pulled)s tenders, %(matched)s matched this subscription.",
                    pulled=pulled,
                    matched=n,
                ),
            )
        self._notify_sync_result(pulled, matched, error=None)
        return {"pulled": pulled, "matched": matched, "error": None, "skipped": False}

    @api.model
    def _reschedule_cron_after_run(self):
        """Park the sync cron's nextcall in 2099 if the user's Schedule
        toggle is OFF.

        Odoo's cron framework auto-sets `nextcall = now() + interval`
        after a successful run. That's correct when the Schedule toggle
        is ON. But we also want manual `Sync now` to work when the
        toggle is OFF, which requires the cron to be `active=True`. To
        prevent the cron from auto-running every <interval> hours when
        the user doesn't want it, we re-park nextcall to 2099 right
        after each run, so the cron worker only picks it up again when
        a user explicitly clicks Sync now (which inserts a trigger row
        that bypasses nextcall).

        IMPORTANT: this MUST be deferred to a postcommit hook. While
        the cron is executing, Odoo holds `lock_for_update` on the
        ir.cron row, and any `cron.write()` from inside the handler
        raises:

            UserError: Record cannot be modified right now: This cron
            task is currently being executed and may not be modified

        That UserError propagates through `_run_action_code_multi`,
        the cron framework rolls back the transaction, and is_running
        sticks at True forever (the isolated-cursor write committed
        before the rollback). We hit this exact bug live on test19
        v5.6.5 build 31426538 (2026-04-29 18:10 UTC).

        Postcommit fires AFTER the cron transaction commits and the
        FOR-NO-KEY-UPDATE lock is released, so writing to ir.cron
        succeeds. The deferred work uses an independent cursor (not
        the now-closed cron cursor).
        """
        Param = self.env["ir.config_parameter"].sudo()
        enabled = Param.get_param("prozorro.auto_sync_enabled", "False") == "True"
        if enabled:
            return  # Odoo already schedules the next run normally
        dbname = self.env.cr.dbname

        def _do_reschedule():
            try:
                from odoo import sql_db

                with sql_db.db_connect(dbname).cursor() as new_cr:
                    new_env = api.Environment(new_cr, SUPERUSER_ID, {})
                    cron = new_env.ref(
                        "rteam_prozorro.ir_cron_prozorro_sync_feed",
                        raise_if_not_found=False,
                    )
                    if cron:
                        cron.sudo().write({"nextcall": SYNC_CRON_NEVER_NEXTCALL})
            except Exception:
                _logger.exception("Prozorro: failed to re-park cron nextcall (postcommit)")

        self.env.cr.postcommit.add(_do_reschedule)

    @api.model
    def _notify_sync_result(self, pulled, matched, error=None):
        """Push a real-time toast to Prozorro managers when a sync run finishes.

        Sent over `bus.bus` so users get feedback even when the sync ran in
        the background (cron / `_trigger()` path) instead of inline. Always
        notifies on completion so the user knows the run actually finished;
        v5.6.4 silenced 0-match runs and broke the "click Sync now -> see
        result" feedback loop, leaving operators staring at an empty page.
        """
        group = self.env.ref("rteam_prozorro.group_prozorro_manager", raise_if_not_found=False)
        if not group:
            _logger.warning(
                "Prozorro: group_prozorro_manager xmlid missing, skipping toast notification"
            )
            return
        if not group.user_ids:
            return
        if error:
            title = _("Prozorro sync failed")
            message = error[:200]
            ntype = "danger"
            sticky = True
        else:
            title = _("Prozorro sync done")
            message = _(
                "Pulled %(pulled)s tenders, %(matched)s matched. "
                "Refresh the Tenders list to see new results."
            ) % {"pulled": pulled, "matched": matched}
            ntype = "success" if matched else "info"
            sticky = False
        Bus = self.env["bus.bus"].sudo()
        for partner in group.user_ids.partner_id:
            Bus._sendone(
                partner,
                "simple_notification",
                {"title": title, "message": message, "type": ntype, "sticky": sticky},
            )

    def action_sync_now(self):
        """Schedule the feed-sync cron for immediate background execution.

        The actual fetch can take minutes (one HTTP GET per matched tender,
        up to 2000 of them). Running synchronously inside an HTTP request
        froze the browser for the whole duration, so we hand off to the
        cron worker via `_trigger()` and return a toast notification.
        Posts "Sync queued..." to each active subscription's chatter
        immediately on click - the cron picks up the trigger 30s-2min
        later (Odoo.sh webhook poll cadence) and posts its own
        "in progress" / "done" lines once it actually runs.
        """
        # Guard: don't queue if a sync is already in flight. Operators
        # double-clicking Sync now would otherwise stack triggers and
        # generate duplicate chatter posts.
        #
        # Self-heal: if `is_running=True` for more than `STALE_RUN_MINUTES`
        # the previous run almost certainly crashed (container kill,
        # uncaught exception, Odoo.sh rebuild mid-run). Reset the flag
        # so the user is not stuck waiting for the system to time out.
        cursor = self.env["prozorro.sync.cursor"].sudo()._get_singleton("main")
        if cursor.is_running and cursor.last_started_at:
            stale_after = fields.Datetime.now() - timedelta(minutes=STALE_RUN_MINUTES)
            if cursor.last_started_at < stale_after:
                _logger.warning(
                    "Prozorro: clearing stale is_running=True flag "
                    "(started %s, > %d minutes ago)",
                    cursor.last_started_at,
                    STALE_RUN_MINUTES,
                )
                cursor.write(
                    {
                        "is_running": False,
                        "last_error": (
                            "Previous run did not finish cleanly (cleared after %d min)."
                            % STALE_RUN_MINUTES
                        ),
                        "last_error_at": fields.Datetime.now(),
                    }
                )
                cursor = cursor  # refresh local var; flag now False
        if cursor.is_running:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Sync already running"),
                    "message": _(
                        "A Prozorro sync is in progress (started %s). "
                        "Wait for it to finish before queueing another."
                    )
                    % (cursor.last_started_at or _("just now")),
                    "type": "warning",
                    "sticky": False,
                },
            }
        cron = self.env.ref("rteam_prozorro.ir_cron_prozorro_sync_feed", raise_if_not_found=False)
        if not cron:
            _logger.warning(
                "Prozorro: ir_cron_prozorro_sync_feed xmlid missing, cannot trigger sync"
            )
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Sync cron missing"),
                    "message": _("Reinstall the module to restore the scheduled action."),
                    "type": "danger",
                    "sticky": True,
                },
            }
        # Instant chatter feedback. The user's RPC transaction commits as
        # soon as this action returns, so a plain message_post is fine
        # here (no need for the isolated-cursor pattern that protects
        # the cron's long loop).
        subs = self.env["prozorro.subscription"]._get_active_subscriptions()
        if subs:
            user_name = self.env.user.name
            subs.message_post(
                body=_("Prozorro sync queued by %s...", user_name),
                subtype_xmlid="mail.mt_note",
            )
        cron.sudo()._trigger()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Sync queued"),
                "message": _(
                    "A background sync was scheduled. Open any active subscription "
                    "to follow progress in its chatter."
                ),
                "type": "success",
                "sticky": False,
            },
        }

    def action_reset_sync_cursor(self):
        """Rewind the feed cursor to the head so the next sync pulls latest tenders.

        Manager-only debug helper. Useful when iterating on subscription rules:
        without this you have to wait until the cron walks far enough back to
        re-encounter tenders you have just made matchable.
        """
        cursor = self.env["prozorro.sync.cursor"]._get_singleton("main")
        cursor.sudo().write({"offset": False, "last_error": False})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Cursor reset"),
                "message": _("The next sync will start from the latest tenders."),
                "type": "success",
                "sticky": False,
            },
        }

    def action_force_clear_running(self):
        """Force-clear the `is_running` flag on the sync cursor.

        Emergency button surfaced on the Settings status panel when
        `is_running=True` and `last_started_at` is more than
        `STALE_RUN_MINUTES` minutes ago. Runs the same self-heal that
        `action_sync_now` does automatically, but as an explicit user
        action so the user can clear stuck state without first having
        to wait for the next click attempt.

        Use case from v5.6.5: cron handler crashed mid-run with
        UserError from `cron.write({nextcall:...})` while ir.cron held
        a lock_for_update. The transaction rolled back wiping
        `_record_success`, but the isolated-cursor `is_running=True`
        had already committed and stayed stuck for 16+ hours.
        """
        cursor = self.env["prozorro.sync.cursor"].sudo()._get_singleton("main")
        cursor.write(
            {
                "is_running": False,
                "last_error": _("Force-cleared stuck running state by user."),
                "last_error_at": fields.Datetime.now(),
            }
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Sync state cleared"),
                "message": _(
                    "The stuck 'sync running' flag has been reset. "
                    "Click Sync now to start a fresh run."
                ),
                "type": "success",
                "sticky": False,
            },
        }

    # ------------------------------------------------------------------ Upsert

    @api.model
    def _upsert_tender(self, payload, matched_subscriptions, base_url):
        """Create or update a `prozorro.tender` row from a payload dict.

        On create, also creates `crm.lead` for each matched subscription that
        has `create_lead=True`.
        """
        tender_uuid = payload.get("id")
        if not tender_uuid:
            return self.browse()

        existing = self.search([("uuid", "=", tender_uuid)], limit=1)
        vals = self._payload_to_vals(payload, base_url)
        # accumulate matched subscriptions on the m2m
        existing_subs = existing.matched_subscription_ids.ids if existing else []
        all_sub_ids = sorted(set(existing_subs) | set(matched_subscriptions.ids))
        vals["matched_subscription_ids"] = [(6, 0, all_sub_ids)]

        if existing:
            existing.write(vals)
            return existing

        rec = self.create(vals)
        # Lead creation only on first match. Also gated by the global
        # `crm.group_use_lead` toggle: if Leads are disabled in CRM
        # Settings, the Leads pool/menu is hidden and any record we
        # create would be invisible. Skip creation entirely in that
        # case rather than silently piling up unreachable records;
        # operators surface the gate via the "Enable CRM Leads" toggle
        # in Settings -> Prozorro -> CRM integration.
        leads_enabled = self.env.user.has_group("crm.group_use_lead")
        for sub in matched_subscriptions.filtered(lambda s: s.create_lead):
            if rec.lead_id:
                break
            if not leads_enabled:
                _logger.warning(
                    "Prozorro: subscription %s has create_lead=True but "
                    "crm.group_use_lead is disabled - skipping lead creation "
                    "for tender %s. Enable CRM Leads in Prozorro Settings to "
                    "activate auto-creation.",
                    sub.id,
                    rec.name,
                )
                break
            rec._create_lead(sub)
        return rec

    @api.model
    def _payload_to_vals(self, payload, base_url):
        entity = payload.get("procuringEntity") or {}
        address = entity.get("address") or {}
        identifier = entity.get("identifier") or {}
        value = payload.get("value") or {}
        tender_period = payload.get("tenderPeriod") or {}
        enquiry_period = payload.get("enquiryPeriod") or {}
        auction_period = payload.get("auctionPeriod") or {}

        items = payload.get("items") or []
        items_summary = "\n".join(
            f"- {it.get('description') or '?'} (qty {it.get('quantity') or '?'})"
            for it in items[:30]
        )

        Currency = self.env["res.currency"]
        currency_id = False
        cur_code = (value.get("currency") or "UAH").upper()
        if cur_code:
            cur = Currency.search([("name", "=", cur_code)], limit=1)
            currency_id = cur.id if cur else False

        # CPV / classification handling
        Classification = self.env["prozorro.classification"]
        codes = sorted(
            {
                (it.get("classification") or {}).get("id")
                for it in items
                if (it.get("classification") or {}).get("id")
            }
        )
        cls_ids = []
        for code in codes:
            cls = Classification.search([("code", "=", code)], limit=1)
            if not cls:
                # Auto-create unknown CPV codes as bare placeholders. The full
                # DK021:2015 dictionary is preloaded but reality drifts; missing
                # codes still need to be linkable.
                cls = Classification.sudo().create(
                    {
                        "code": code,
                        "name": (
                            next(
                                ((it.get("classification") or {}).get("description") or "")
                                for it in items
                                if (it.get("classification") or {}).get("id") == code
                            ),
                            "",
                        )[0]
                        or code,
                    }
                )
            cls_ids.append(cls.id)

        # Defensive: Prozorro adds new statuses occasionally. If the API
        # returns a code we have not whitelisted in PROZORRO_STATUS, store
        # False rather than raise ValueError (Selection write rejection)
        # which would abort the whole sync run.
        raw_status = payload.get("status")
        if raw_status and raw_status not in PROZORRO_STATUS_CODES:
            _logger.warning(
                "Prozorro: unknown tender status %r on %s, storing empty. "
                "Add it to PROZORRO_STATUS and prozorro_tender_status_data.xml.",
                raw_status,
                payload.get("id"),
            )
            raw_status = False

        return {
            "name": payload.get("tenderID") or payload.get("id"),
            "uuid": payload.get("id"),
            "title": payload.get("title"),
            "description": payload.get("description"),
            "status": raw_status,
            "procurement_method": payload.get("procurementMethod"),
            "procurement_method_type": payload.get("procurementMethodType"),
            "value_amount": value.get("amount") or 0.0,
            "value_currency_id": currency_id,
            "procuring_entity_name": entity.get("name"),
            "procuring_entity_edrpou": identifier.get("id"),
            "procuring_entity_country": address.get("countryName"),
            "procuring_entity_region": address.get("region"),
            "procuring_entity_locality": address.get("locality"),
            "tender_period_start": self._parse_dt(tender_period.get("startDate")),
            "tender_period_end": self._parse_dt(tender_period.get("endDate")),
            "enquiry_period_end": self._parse_dt(enquiry_period.get("endDate")),
            "auction_period_start": self._parse_dt(auction_period.get("startDate")),
            "classification_ids": [(6, 0, cls_ids)] if cls_ids else False,
            "items_summary": items_summary,
            "items_count": len(items),
            "api_url": base_url + "/" + (payload.get("id") or ""),
            "date_modified": self._parse_dt(payload.get("dateModified")),
            "raw_json": json.dumps(payload, ensure_ascii=False)[:524288],  # 512 KB cap
        }

    @staticmethod
    def _parse_dt(value):
        if not value:
            return False
        # Prozorro dates: ISO 8601 with timezone, e.g. 2026-04-28T13:30:00+03:00
        try:
            value = value.replace("Z", "+00:00")
            return datetime.fromisoformat(value).replace(tzinfo=None)
        except (TypeError, ValueError):
            return False

    # ------------------------------------------------------------------ Lead

    def _create_lead(self, subscription):
        """Create a CRM lead for this tender.

        `subscription` may be an empty recordset (manual conversion of a
        tender without a linked subscription); in that case team / user /
        tags / stage default to whatever crm.lead picks up itself.
        """
        self.ensure_one()
        Lead = self.env["crm.lead"].sudo()
        title = (self.title or self.name or "Prozorro tender").strip()
        body_lines = [
            "Imported from Prozorro.",
            "",
            f"Tender: {self.name}",
            f"Procuring entity: {self.procuring_entity_name or '?'}",
        ]
        if self.value_amount:
            body_lines.append(f"Value: {self.value_amount} {self.value_currency_id.name or ''}")
        if self.tender_period_end:
            body_lines.append(f"Tendering ends: {self.tender_period_end}")
        if self.url:
            body_lines.append("")
            body_lines.append(f"Open in Prozorro: {self.url}")

        # type='lead' lands in the Leads pool (Generation Leads) for manual
        # triage; type='opportunity' would skip triage and dump straight
        # into the Pipeline / Kanban. With auto-create enabled and a broad
        # subscription this can be thousands of records, polluting the
        # pipeline. Always create as a Lead - operators promote to
        # opportunity after review via standard Odoo CRM flow.
        vals = {
            "name": "[Prozorro] %s" % (title[:80] if len(title) > 80 else title),
            "type": "lead",
            "description": "\n".join(body_lines),
            "expected_revenue": self.value_amount or 0.0,
            "prozorro_tender_id": self.id,
        }
        if subscription:
            if subscription.team_id:
                vals["team_id"] = subscription.team_id.id
            if subscription.assign_to_user_id:
                vals["user_id"] = subscription.assign_to_user_id.id
            elif subscription.user_id:
                vals["user_id"] = subscription.user_id.id
            if subscription.tag_ids:
                vals["tag_ids"] = [(6, 0, subscription.tag_ids.ids)]
            if subscription.stage_id:
                vals["stage_id"] = subscription.stage_id.id

        lead = Lead.create(vals)
        self.lead_id = lead.id
        return lead

    # ------------------------------------------------------------------ Retention

    @api.model
    def _cron_retention(self):
        """Drop matched tenders that finished long ago and are not linked to a CRM lead."""
        days = self._get_retention_days()
        cutoff = fields.Datetime.now() - timedelta(days=days)
        stale = self.search(
            [
                ("tender_period_end", "<", cutoff),
                ("lead_id", "=", False),
            ],
            limit=500,
        )
        if stale:
            _logger.info("Prozorro: pruning %d stale tenders older than %d days", len(stale), days)
            stale.unlink()

    # ------------------------------------------------------------------ UX actions

    def action_open_in_prozorro(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": self.url,
            "target": "new",
        }

    def action_convert_to_lead(self):
        """Promote each tender in self to a CRM lead.

        Idempotent per record: tenders that already have a linked lead are
        skipped. New leads use the first matched subscription as context
        (team / user / tags / stage); tenders without a matched subscription
        get a bare lead. On a single record, opens the resulting lead;
        on a batch, opens the lead pipeline filtered to the new leads.
        """
        created = self.env["crm.lead"]
        for tender in self:
            if tender.lead_id:
                continue
            subscription = tender.matched_subscription_ids[:1]
            lead = tender._create_lead(subscription or self.env["prozorro.subscription"])
            created |= lead
        if len(self) == 1:
            return self.action_open_lead()
        if not created:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("No new leads"),
                    "message": _("All selected tenders already had a linked lead."),
                    "type": "warning",
                    "sticky": False,
                },
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("New leads"),
            "res_model": "crm.lead",
            "view_mode": "list,form",
            "domain": [("id", "in", created.ids)],
        }

    def action_open_lead(self):
        self.ensure_one()
        if not self.lead_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("CRM Lead"),
            "res_model": "crm.lead",
            "res_id": self.lead_id.id,
            "view_mode": "form",
            "target": "current",
        }
