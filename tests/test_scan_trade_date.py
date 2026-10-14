import unittest
from datetime import datetime
from scan_trade_date import resolve_trade_date

class ScanTradeDateTests(unittest.TestCase):
    def test_delayed_morning_run(self):
        self.assertEqual(resolve_trade_date(datetime.fromisoformat('2026-10-13T05:32:00+08:00')), '2026-10-12')
    def test_before_close_not_final(self):
        self.assertEqual(resolve_trade_date(datetime.fromisoformat('2026-10-13T13:20:00+08:00')), '2026-10-12')

    def test_after_close_is_same_session(self):
        self.assertEqual(resolve_trade_date(datetime.fromisoformat('2026-10-13T14:05:00+08:00')), '2026-10-13')
    def test_normal_evening_run(self):
        self.assertEqual(resolve_trade_date(datetime.fromisoformat('2026-10-13T19:00:00+08:00')), '2026-10-13')
    def test_holiday_and_weekend(self):
        self.assertEqual(resolve_trade_date(datetime.fromisoformat('2026-10-12T06:00:00+08:00')), '2026-10-08')
    def test_utc_conversion(self):
        self.assertEqual(resolve_trade_date(datetime.fromisoformat('2026-10-12T21:32:00+00:00')), '2026-10-12')
    def test_explicit_replay(self):
        self.assertEqual(resolve_trade_date(explicit='2026-10-08'), '2026-10-08')
    def test_unknown_calendar_fails_closed(self):
        with self.assertRaises(ValueError):
            resolve_trade_date(datetime.fromisoformat('2027-02-10T19:00:00+08:00'))
