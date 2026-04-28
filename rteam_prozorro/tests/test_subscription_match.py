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

        cls.status_active_tendering = cls.env.ref("rteam_prozorro.status_active_tendering")
        cls.status_active_qualification = cls.env.ref("rteam_prozorro.status_active_qualification")
        cls.status_complete = cls.env.ref("rteam_prozorro.status_complete")
        cls.method_above_threshold_ua = cls.env.ref("rteam_prozorro.method_above_threshold_ua")
        cls.method_below_threshold = cls.env.ref("rteam_prozorro.method_below_threshold")
        cls.method_esco = cls.env.ref("rteam_prozorro.method_esco")
        cls.region_kyiv_oblast = cls.env.ref("rteam_prozorro.region_kyiv_oblast")
        cls.region_lviv = cls.env.ref("rteam_prozorro.region_lviv")

    def _make_subscription(self, **kwargs):
        defaults = {"name": "Test sub", "active": True, "create_lead": False}
        # Tests opt into status_ids explicitly. Default is no status filter
        # (the model's default of `active.tendering` would mask intent).
        if "status_ids" not in kwargs:
            kwargs["status_ids"] = [(5, 0, 0)]
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
        sub = self._make_subscription(status_ids=[(6, 0, [self.status_complete.id])])
        self.assertFalse(sub._matches(make_tender()))

    def test_status_filter_multi_includes(self):
        sub = self._make_subscription(
            status_ids=[
                (6, 0, [self.status_active_tendering.id, self.status_active_qualification.id])
            ]
        )
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
        sub = self._make_subscription(region_ids=[(6, 0, [self.region_kyiv_oblast.id])])
        self.assertTrue(sub._matches(make_tender()))

    def test_region_filter_excludes(self):
        sub = self._make_subscription(region_ids=[(6, 0, [self.region_lviv.id])])
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
        # keyword "Лазер" is present in items[0].description but also appears in title
        self.Keyword.create({"subscription_id": sub.id, "keyword": "Лазер", "field": "items"})
        self.assertTrue(sub._matches(make_tender()))
        # keyword "верстат" is in title but NOT in items -> field=items must miss it
        self.Keyword.search([("subscription_id", "=", sub.id)]).unlink()
        self.Keyword.create({"subscription_id": sub.id, "keyword": "верстат", "field": "items"})
        self.assertFalse(sub._matches(make_tender()))

    def test_method_type_filter(self):
        sub = self._make_subscription(
            procurement_method_ids=[(6, 0, [self.method_below_threshold.id])]
        )
        self.assertFalse(sub._matches(make_tender()))
        sub.procurement_method_ids = [
            (6, 0, [self.method_above_threshold_ua.id, self.method_esco.id])
        ]
        self.assertTrue(sub._matches(make_tender()))

    def test_all_filters_must_pass(self):
        sub = self._make_subscription(
            classification_ids=[(6, 0, [self.cpv_lasers.id])],
            status_ids=[(6, 0, [self.status_active_tendering.id])],
            value_min=1_000_000,
            value_max=10_000_000,
            region_ids=[(6, 0, [self.region_kyiv_oblast.id])],
        )
        self.Keyword.create({"subscription_id": sub.id, "keyword": "лазер"})
        self.assertTrue(sub._matches(make_tender()))
        # Flip one rule -> false
        sub.value_max = 1_000_000
        self.assertFalse(sub._matches(make_tender()))
