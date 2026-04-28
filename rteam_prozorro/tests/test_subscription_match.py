from odoo.tests import TransactionCase, tagged


def make_tender(**overrides):
    """Build a Prozorro-shaped tender dict for tests."""
    base = {
        "id": "abc123",
        "tenderID": "UA-2026-04-28-000001-a",
        "title": "Поставка лазерних верстатів",
        "description": "Закупівля промислових лазерних верстатів для виробничого комплексу",
        "status": "active.tendering",
        "procurementMethod": "open",
        "procurementMethodType": "aboveThresholdUA",
        "value": {"amount": 5_000_000, "currency": "UAH"},
        "procuringEntity": {
            "name": "Test Procuring Entity",
            "identifier": {"id": "12345678", "scheme": "UA-EDR"},
            "address": {"countryName": "Україна", "region": "Київська область"},
        },
        "items": [
            {
                "description": "Лазер промисловий 6 кВт",
                "classification": {
                    "scheme": "ДК021",
                    "id": "42610000-5",
                    "description": "Lasers and machines fitted with lasers",
                },
                "quantity": 2,
                "unit": {"name": "шт"},
            }
        ],
        "tenderPeriod": {
            "startDate": "2026-04-28T10:00:00+03:00",
            "endDate": "2026-05-28T18:00:00+03:00",
        },
        "dateModified": "2026-04-28T10:30:00+03:00",
    }
    base.update(overrides)
    return base


@tagged("post_install", "-at_install")
class TestSubscriptionMatch(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Subscription = cls.env["prozorro.subscription"]
        cls.Keyword = cls.env["prozorro.subscription.keyword"]
        cls.Classification = cls.env["prozorro.classification"]

        cls.cpv_lasers = cls.env.ref("rteam_prozorro.cpv_42610000_5")
        cls.cpv_it = cls.env.ref("rteam_prozorro.cpv_72000000_5")

    def _make_subscription(self, **kwargs):
        defaults = {"name": "Test sub", "active": True, "create_lead": False}
        defaults.update(kwargs)
        return self.Subscription.create(defaults)

    def test_match_by_cpv(self):
        sub = self._make_subscription(classification_ids=[(6, 0, [self.cpv_lasers.id])])
        self.assertTrue(sub._matches(make_tender()))

    def test_no_match_by_unrelated_cpv(self):
        sub = self._make_subscription(classification_ids=[(6, 0, [self.cpv_it.id])])
        self.assertFalse(sub._matches(make_tender()))

    def test_match_no_cpv_filter_passes(self):
        sub = self._make_subscription()
        self.assertTrue(sub._matches(make_tender()))

    def test_status_filter_excludes(self):
        sub = self._make_subscription(status_filter="complete")
        self.assertFalse(sub._matches(make_tender()))

    def test_status_filter_multi_includes(self):
        sub = self._make_subscription(status_filter="active.tendering,active.qualification")
        self.assertTrue(sub._matches(make_tender()))

    def test_value_min_excludes(self):
        sub = self._make_subscription(value_min=10_000_000)
        self.assertFalse(sub._matches(make_tender()))

    def test_value_max_excludes(self):
        sub = self._make_subscription(value_max=1_000_000)
        self.assertFalse(sub._matches(make_tender()))

    def test_value_in_range(self):
        sub = self._make_subscription(value_min=1_000_000, value_max=10_000_000)
        self.assertTrue(sub._matches(make_tender()))

    def test_region_filter_includes(self):
        sub = self._make_subscription(region_filter="київська")
        self.assertTrue(sub._matches(make_tender()))

    def test_region_filter_excludes(self):
        sub = self._make_subscription(region_filter="львівська")
        self.assertFalse(sub._matches(make_tender()))

    def test_keyword_contains(self):
        sub = self._make_subscription()
        self.Keyword.create({"subscription_id": sub.id, "keyword": "лазер", "field": "any"})
        self.assertTrue(sub._matches(make_tender()))

    def test_keyword_negate(self):
        sub = self._make_subscription()
        self.Keyword.create(
            {"subscription_id": sub.id, "keyword": "лазер", "field": "any", "negate": True}
        )
        self.assertFalse(sub._matches(make_tender()))

    def test_keyword_regex(self):
        sub = self._make_subscription()
        self.Keyword.create(
            {
                "subscription_id": sub.id,
                "keyword": r"\d+ кВт",
                "field": "any",
                "match_mode": "regex",
            }
        )
        self.assertTrue(sub._matches(make_tender()))

    def test_keyword_in_items_only(self):
        sub = self._make_subscription()
        self.Keyword.create({"subscription_id": sub.id, "keyword": "верстат", "field": "items"})
        self.assertTrue(sub._matches(make_tender()))

    def test_method_type_filter(self):
        sub = self._make_subscription(procurement_method_types="belowThreshold")
        self.assertFalse(sub._matches(make_tender()))
        sub.procurement_method_types = "aboveThresholdUA,esco"
        self.assertTrue(sub._matches(make_tender()))

    def test_all_filters_must_pass(self):
        sub = self._make_subscription(
            classification_ids=[(6, 0, [self.cpv_lasers.id])],
            status_filter="active.tendering",
            value_min=1_000_000,
            value_max=10_000_000,
            region_filter="київська",
        )
        self.Keyword.create({"subscription_id": sub.id, "keyword": "лазер"})
        self.assertTrue(sub._matches(make_tender()))
        # Flip one rule -> false
        sub.value_max = 1_000_000
        self.assertFalse(sub._matches(make_tender()))
