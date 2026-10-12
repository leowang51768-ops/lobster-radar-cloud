import importlib.util
import unittest
from pathlib import Path
spec = importlib.util.spec_from_file_location("final_result", Path(__file__).parents[1] / "final_result.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
class WindowTests(unittest.TestCase):
    def test_calendar_months_and_month_end(self):
        self.assertEqual(m.history_start("2026-10-12"), "2026-04-12")
        self.assertEqual(m.history_start("2026-08-31"), "2026-02-28")
        self.assertEqual(m.history_start("2026-01-31"), "2025-07-31")
