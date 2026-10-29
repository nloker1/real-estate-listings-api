"""Offline seed safety and fixture checks; no database or providers imported."""

import unittest

from scripts.seed_test_data import listing_fixtures, seed_url


class SeedSafetyTests(unittest.TestCase):
    def test_requires_explicit_test_mode(self):
        for mode in (None, "production", "development"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                seed_url({"APP_ENV": mode or "", "DATABASE_URL": "ignored"})

    def test_requires_guarded_test_database(self):
        with self.assertRaises(ValueError):
            seed_url({"APP_ENV": "test", "DATABASE_URL": "ignored"})
        with self.assertRaises(ValueError):
            seed_url({"APP_ENV": "test", "TEST_DATABASE_URL":
                      "postgresql+asyncpg://localhost/production"})

    def test_fixtures_are_repeatable_with_one_eligible_listing(self):
        rows = listing_fixtures()
        self.assertEqual(rows, listing_fixtures())
        self.assertEqual(len({row["mls_number"] for row in rows}), 6)
        matches = [row["mls_number"] for row in rows if
                   row["status"] == "Active" and row["is_published"] and
                   row["city"] == "WhiteSalmon" and
                   400000 <= row["price"] <= 700000 and
                   row["beds"] >= 3 and row["baths"] >= 2 and
                   row["property_type"] == "Residential"]
        self.assertEqual(matches, ["SYNTHETIC-MATCH"])
