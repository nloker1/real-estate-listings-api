"""Pure clock tests: no database or email provider."""
import unittest
from datetime import datetime
from types import SimpleNamespace
from app.services.alert_worker import listing_schedule_due

class AlertScheduleTests(unittest.TestCase):
    def due(self, frequency, now, baseline):
        return listing_schedule_due(SimpleNamespace(frequency=frequency,
            created_at=baseline, last_alert_sent=None), now)

    def test_daily_boundary_and_same_window(self):
        baseline = datetime(2026, 11, 9, 18)
        self.assertFalse(self.due("daily", datetime(2026, 11, 10, 15, 59), baseline))
        self.assertTrue(self.due("daily", datetime(2026, 11, 10, 16), baseline))
        self.assertFalse(self.due("daily", datetime(2026, 11, 10, 17),
                                  datetime(2026, 11, 10, 16)))

    def test_monthly_boundary_and_year_rollover(self):
        baseline = datetime(2026, 12, 15)
        self.assertFalse(self.due("monthly", datetime(2027, 1, 1, 15, 59), baseline))
        self.assertTrue(self.due("monthly", datetime(2027, 1, 1, 16), baseline))

    def test_summer_uses_daylight_time(self):
        baseline = datetime(2026, 6, 9, 18)
        self.assertFalse(self.due("daily", datetime(2026, 6, 10, 14, 59), baseline))
        self.assertTrue(self.due("daily", datetime(2026, 6, 10, 15), baseline))

    def test_instant_and_unknown(self):
        at = datetime(2026, 11, 10)
        self.assertTrue(self.due("instant", at, at))
        self.assertFalse(self.due("unsupported", at, at))

    def test_weekly_monday_and_same_week(self):
        baseline = datetime(2026, 11, 3, 18)
        self.assertFalse(self.due("weekly", datetime(2026, 11, 9, 15, 59), baseline))
        self.assertTrue(self.due("weekly", datetime(2026, 11, 9, 16), baseline))
        self.assertFalse(self.due("weekly", datetime(2026, 11, 15, 18),
                                  datetime(2026, 11, 9, 16)))
        self.assertTrue(self.due("weekly", datetime(2026, 11, 16, 16),
                                 datetime(2026, 11, 9, 16)))

    def test_weekly_summer_and_year_rollover(self):
        self.assertTrue(self.due("weekly", datetime(2026, 6, 8, 15),
                                 datetime(2026, 6, 2, 18)))
        self.assertFalse(self.due("weekly", datetime(2027, 1, 4, 15, 59),
                                  datetime(2026, 12, 29, 18)))
        self.assertTrue(self.due("weekly", datetime(2027, 1, 4, 16),
                                 datetime(2026, 12, 29, 18)))
