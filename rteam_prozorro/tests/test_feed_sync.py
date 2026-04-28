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

    def test_no_active_subscriptions_skips(self):
        # ensure no subscription is active
        self.Subscription.search([]).write({"active": False})
        with fake_urlopen({}):
            self.Tender._cron_sync_feed()  # must not raise

    def test_sync_creates_matched_tender_and_lead(self):
        sub = self.Subscription.create(
            {
                "name": "Lasers test",
                "active": True,
                "create_lead": True,
                "classification_ids": [(6, 0, [self.cpv_lasers.id])],
                "status_filter": "active.tendering",
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
        sub = self.Subscription.create(
            {
                "name": "any tendering",
                "active": True,
                "status_filter": "active.tendering",
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
