import json
from contextlib import contextmanager
from io import BytesIO
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from .test_subscription_match import make_tender


class _MockResp:
    def __init__(self, payload):
        self._buf = BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._buf.read()


@contextmanager
def fake_urlopen(payloads_by_url):
    """patch urllib.request.urlopen so the cron sees deterministic responses."""

    def _opener(req, timeout=None):  # noqa: ARG001
        url = req.full_url if hasattr(req, "full_url") else req
        for prefix, payload in payloads_by_url.items():
            if prefix in url:
                return _MockResp(payload)
        raise AssertionError(f"Unmocked URL: {url}")

    with patch("urllib.request.urlopen", side_effect=_opener):
        yield


@tagged("post_install", "-at_install")
class TestFeedSync(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tender = cls.env["prozorro.tender"]
        cls.Subscription = cls.env["prozorro.subscription"]
        cls.Cursor = cls.env["prozorro.sync.cursor"]
        cls.Keyword = cls.env["prozorro.subscription.keyword"]
        cls.cpv_lasers = cls.env.ref("rteam_prozorro.cpv_42610000_5")
        # Lead creation is gated by crm.group_use_lead (5.6.2+); enable
        # it for the whole test class so existing assertions about
        # auto-created leads keep passing. The "Leads disabled" branch
        # has its own dedicated test below.
        cls._enable_crm_leads(cls.env)
        # 5.8.0+ requires `prozorro.start_date` for any sync run that
        # starts from a virgin cursor (refusing to do an unbounded
        # backfill). Tests below reset the cursor between runs, so seed
        # a default here for all tests except the dedicated
        # "missing start_date is rejected" test.
        cls.env["ir.config_parameter"].sudo().set_param("prozorro.start_date", "2026-04-01")

    @classmethod
    def _enable_crm_leads(cls, env):
        leads_group = env.ref("crm.group_use_lead", raise_if_not_found=False)
        employee_group = env.ref("base.group_user", raise_if_not_found=False)
        if leads_group and employee_group:
            employee_group.sudo().write({"implied_ids": [(4, leads_group.id)]})
            env.user.sudo().write({"groups_id": [(4, leads_group.id)]})

    @classmethod
    def _disable_crm_leads(cls, env):
        leads_group = env.ref("crm.group_use_lead", raise_if_not_found=False)
        employee_group = env.ref("base.group_user", raise_if_not_found=False)
        if leads_group and employee_group:
            employee_group.sudo().write({"implied_ids": [(3, leads_group.id)]})
            env.user.sudo().write({"groups_id": [(3, leads_group.id)]})

    def test_no_active_subscriptions_skips(self):
        # ensure no subscription is active
        self.Subscription.search([]).write({"active": False})
        with fake_urlopen({}):
            self.Tender._cron_sync_feed()  # must not raise

    def test_sync_creates_matched_tender_and_lead(self):
        status_active_tendering = self.env.ref("rteam_prozorro.status_active_tendering")
        sub = self.Subscription.create(
            {
                "name": "Lasers test",
                "active": True,
                "create_lead": True,
                "classification_ids": [(6, 0, [self.cpv_lasers.id])],
                "status_ids": [(6, 0, [status_active_tendering.id])],
            }
        )
        self.Subscription.search([("id", "!=", sub.id)]).write({"active": False})

        feed_payload = {
            "data": [{"id": "abc123", "dateModified": "2026-04-28T10:30:00+03:00"}],
            "next_page": {"offset": "off-2"},
        }
        single = {"data": make_tender()}

        base_url = self.Tender._get_api_base_url()
        with fake_urlopen({base_url + "?": feed_payload, "/abc123": single}):
            self.Tender._cron_sync_feed()

        tender = self.Tender.search([("uuid", "=", "abc123")], limit=1)
        self.assertTrue(tender, "Matched tender must be persisted")
        self.assertEqual(tender.status, "active.tendering")
        self.assertIn(self.cpv_lasers, tender.classification_ids)
        self.assertIn(sub, tender.matched_subscription_ids)
        self.assertTrue(tender.lead_id, "Subscription with create_lead=True must create a lead")
        self.assertEqual(tender.lead_id.prozorro_tender_id, tender)

    def test_lead_creation_gated_by_crm_use_leads(self):
        """When crm.group_use_lead is OFF, sync mirrors tenders but skips
        lead creation - even with create_lead=True on the subscription."""
        self._disable_crm_leads(self.env)
        try:
            status_active_tendering = self.env.ref("rteam_prozorro.status_active_tendering")
            sub = self.Subscription.create(
                {
                    "name": "Lasers gated",
                    "active": True,
                    "create_lead": True,
                    "classification_ids": [(6, 0, [self.cpv_lasers.id])],
                    "status_ids": [(6, 0, [status_active_tendering.id])],
                }
            )
            self.Subscription.search([("id", "!=", sub.id)]).write({"active": False})

            feed_payload = {
                "data": [{"id": "gated-1", "dateModified": "2026-04-28T10:30:00+03:00"}],
                "next_page": {"offset": "off-2"},
            }
            single = {"data": make_tender(id="gated-1")}
            base_url = self.Tender._get_api_base_url()
            with fake_urlopen({base_url + "?": feed_payload, "/gated-1": single}):
                self.Tender._cron_sync_feed()

            tender = self.Tender.search([("uuid", "=", "gated-1")], limit=1)
            self.assertTrue(tender, "Tender must still be mirrored when leads are gated off")
            self.assertFalse(
                tender.lead_id,
                "Lead must NOT be created when crm.group_use_lead is disabled",
            )
        finally:
            # Restore the class-level state so subsequent tests still see
            # leads enabled. TransactionCase rolls back DB changes but
            # group membership writes via .sudo() may persist within the
            # cursor depending on Odoo internals.
            self._enable_crm_leads(self.env)

    def test_sync_unmatched_not_persisted(self):
        sub = self.Subscription.create(
            {
                "name": "IT-only",
                "active": True,
                "classification_ids": [(6, 0, [self.env.ref("rteam_prozorro.cpv_72000000_5").id])],
            }
        )
        self.Subscription.search([("id", "!=", sub.id)]).write({"active": False})

        feed_payload = {
            "data": [{"id": "no-match-1", "dateModified": "2026-04-28T10:30:00+03:00"}],
            "next_page": {"offset": "off-2"},
        }
        single = {"data": make_tender(id="no-match-1")}  # has lasers CPV, not IT

        base_url = self.Tender._get_api_base_url()
        with fake_urlopen({base_url + "?": feed_payload, "/no-match-1": single}):
            self.Tender._cron_sync_feed()

        self.assertFalse(self.Tender.search([("uuid", "=", "no-match-1")]))

    def test_sync_advances_cursor(self):
        status_active_tendering = self.env.ref("rteam_prozorro.status_active_tendering")
        sub = self.Subscription.create(
            {
                "name": "any tendering",
                "active": True,
                "status_ids": [(6, 0, [status_active_tendering.id])],
            }
        )
        self.Subscription.search([("id", "!=", sub.id)]).write({"active": False})

        feed_payload = {
            "data": [{"id": "x1"}],
            "next_page": {"offset": "OFFSET-NEXT"},
        }
        with fake_urlopen(
            {
                self.Tender._get_api_base_url() + "?": feed_payload,
                "/x1": {"data": make_tender(id="x1")},
            }
        ):
            self.Tender._cron_sync_feed()

        cursor = self.Cursor._get_singleton("main")
        self.assertEqual(cursor.offset, "OFFSET-NEXT")
        self.assertGreaterEqual(cursor.last_pulled, 1)

    def test_sync_skips_when_start_date_missing(self):
        """Virgin cursor + no `prozorro.start_date` -> sync refuses to
        start (would otherwise backfill the entire Prozorro history).
        """
        status_active_tendering = self.env.ref("rteam_prozorro.status_active_tendering")
        sub = self.Subscription.create(
            {
                "name": "needs start date",
                "active": True,
                "status_ids": [(6, 0, [status_active_tendering.id])],
            }
        )
        self.Subscription.search([("id", "!=", sub.id)]).write({"active": False})
        # Cursor is virgin in this test (TransactionCase rolls back so
        # the class-level seed of start_date persists across tests; we
        # explicitly clear both here).
        self.env["ir.config_parameter"].sudo().set_param("prozorro.start_date", "")
        self.Cursor._get_singleton("main").sudo().write({"offset": False})

        # No HTTP call should be issued. Empty mock asserts that.
        with fake_urlopen({}):
            result = self.Tender._cron_sync_feed()

        self.assertTrue(result.get("skipped"))
        cursor = self.Cursor._get_singleton("main")
        self.assertFalse(cursor.is_running)
        self.assertIn("Sync from date", cursor.last_error or "")
        # Restore for any tests that may run later in the same module.
        self.env["ir.config_parameter"].sudo().set_param("prozorro.start_date", "2026-04-01")

    def test_sync_picks_up_new_tenders_after_exhaust(self):
        """Three sync runs, ascending watermark semantics:
          run 1 - virgin cursor, seeded from start_date, pulls t1 and
                  advances watermark to W1.
          run 2 - watermark W1, API has nothing new (returns same
                  watermark), cron breaks immediately, watermark stays.
          run 3 - watermark W1, API now has t2, watermark advances to W2.

        This covers the pre-5.8.0 regression where a buried cursor stopped
        seeing newly published tenders.
        """
        status_active_tendering = self.env.ref("rteam_prozorro.status_active_tendering")
        sub = self.Subscription.create(
            {
                "name": "watermark test",
                "active": True,
                "status_ids": [(6, 0, [status_active_tendering.id])],
            }
        )
        self.Subscription.search([("id", "!=", sub.id)]).write({"active": False})
        # Start from a virgin cursor so run 1 exercises the start_date
        # seeding path.
        self.Cursor._get_singleton("main").sudo().write({"offset": False})

        base_url = self.Tender._get_api_base_url()

        # ------------------------------------------------------------------
        # Run 1: API returns t1, advances watermark to "W1".
        # ------------------------------------------------------------------
        feed_run1 = {
            "data": [{"id": "wm-t1", "dateModified": "2026-04-28T10:30:00+03:00"}],
            "next_page": {"offset": "W1"},
        }
        with fake_urlopen(
            {
                base_url + "?": feed_run1,
                "/wm-t1": {"data": make_tender(id="wm-t1")},
            }
        ):
            self.Tender._cron_sync_feed()

        cursor = self.Cursor._get_singleton("main")
        self.assertEqual(cursor.offset, "W1", "Watermark must advance after first run")
        self.assertTrue(self.Tender.search([("uuid", "=", "wm-t1")]))

        # ------------------------------------------------------------------
        # Run 2: API has nothing new. next_page.offset = current offset
        # ("W1") signals end of feed; cron breaks without advancing.
        # ------------------------------------------------------------------
        feed_run2 = {"data": [], "next_page": {"offset": "W1"}}
        with fake_urlopen({base_url + "?": feed_run2}):
            self.Tender._cron_sync_feed()

        cursor = self.Cursor._get_singleton("main")
        self.assertEqual(
            cursor.offset, "W1", "Watermark must NOT change when API returns no new data"
        )

        # ------------------------------------------------------------------
        # Run 3: a new tender (t2) was published. API now returns it and
        # advances the watermark. The cron must pick it up - this is the
        # exact case the pre-5.8.0 descending=1 implementation missed.
        # ------------------------------------------------------------------
        feed_run3 = {
            "data": [{"id": "wm-t2", "dateModified": "2026-04-29T11:00:00+03:00"}],
            "next_page": {"offset": "W2"},
        }
        with fake_urlopen(
            {
                base_url + "?": feed_run3,
                "/wm-t2": {"data": make_tender(id="wm-t2")},
            }
        ):
            self.Tender._cron_sync_feed()

        cursor = self.Cursor._get_singleton("main")
        self.assertEqual(cursor.offset, "W2", "Watermark must advance once a new tender appears")
        self.assertTrue(
            self.Tender.search([("uuid", "=", "wm-t2")]),
            "New tender published after the watermark must be persisted",
        )
