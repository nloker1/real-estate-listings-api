"""Offline configuration checks; never import the application's database engine."""

import unittest

from app.database_config import test_database_url as validate_url


class TestDatabaseConfiguration(unittest.TestCase):
    def test_missing_url_never_falls_back(self):
        with self.assertRaisesRegex(ValueError, "requires TEST_DATABASE_URL"):
            validate_url({"DATABASE_URL": "production-placeholder"})

    def test_accepts_only_designated_local_database(self):
        for host in ("localhost", "127.0.0.1", "[::1]"):
            with self.subTest(host=host):
                url = f"postgresql+asyncpg://test_user:fake@{host}:55432/listing_alerts_test"
                self.assertEqual(validate_url({"TEST_DATABASE_URL": url}), url)

    def test_rejects_unsafe_or_malformed_targets_without_exposing_url(self):
        urls = (
            "postgresql+asyncpg://test:secret@example.invalid/listing_alerts_test",
            "postgresql+asyncpg://test:secret@localhost/production",
            "postgresql://test:secret@localhost/listing_alerts_test",
            "postgresql+asyncpg://test:secret@localhost/listing_alerts_test?host=remote",
            "postgresql+asyncpg://test:secret@localhost/listing_alerts_test#fragment",
            "postgresql+asyncpg://test:secret@localhost:bad/listing_alerts_test",
            "postgresql+asyncpg://test:secret@localhost:0/listing_alerts_test",
            "postgresql+asyncpg://[broken",
            "",
        )
        for url in urls:
            with self.subTest(case=urls.index(url)):
                with self.assertRaises(ValueError) as caught:
                    validate_url({"TEST_DATABASE_URL": url})
                self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
