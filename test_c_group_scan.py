#!/usr/bin/env python3
"""Safety regression tests for isolated C-group scan. Run: python -m unittest test_c_group_scan.py"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

import c_group_scan as c


class CGroupTests(unittest.TestCase):
    def test_current_day_liquidity_is_strictly_greater_than_300(self):
        def row(lots, turnover=30_000_000):
            return pd.Series({"volume_lots": lots, "turnover": turnover})
        self.assertFalse(c.liquid_today(row(300)))
        self.assertTrue(c.liquid_today(row(300.001)))
        self.assertTrue(c.liquid_today(row(301)))
        self.assertFalse(c.liquid_today(row(299.999)))
        self.assertFalse(c.liquid_today(row(500, 29_999_999)))
        self.assertTrue(c.liquid_today(row(500, 30_000_000)))

    def test_score_does_not_exceed_defined_buckets(self):
        s = c.score({"support_touches": 100, "evidence_count": 100,
                     "volume_ratio": 100, "risk_reward": 100}, "破底翻",
                    pd.Series(dtype=float))
        self.assertEqual(s["structure"], 40)
        self.assertEqual(s["price_volume"], 25)
        self.assertEqual(s["sector"], 0)
        self.assertEqual(s["risk"], 15)
        self.assertEqual(s["total"], 80)

    def test_no_double_entry_and_only_five_formal(self):
        days = pd.date_range("2026-04-01", periods=80, freq="B")
        rows = []
        for code in range(1001, 1008):
            for date in days:
                rows.append({"date": date, "code": str(code), "name": str(code),
                             "open": 10., "high": 11., "low": 9., "close": 10.,
                             "volume": 301000, "volume_lots": 301.,
                             "turnover": 30_000_000., "market": "TWSE", "dif": 0.0})
        market = pd.DataFrame(rows)
        def fake_rev(code, x):
            return {}, {"signal_route": "破底翻", "pattern_key": str(code),
                        "support_touches": 3, "evidence_count": 4,
                        "volume_ratio": 1.5, "risk_reward": 2.,
                        "baseline_entry": 10., "close": 10.}
        def fake_retest(g):
            row = g.iloc[-1]
            return {"date": row.date.strftime("%Y-%m-%d"), "code": str(row.code),
                    "name": row["name"], "high_quality_confluence": True,
                    "close": 10., "pivot": 10., "stop_price": 9.9,
                    "retest_precision_pct": .1, "pivot_touches": 3,
                    "volume_contraction_ratio": .5,
                    "today_volume_lots": 301, "breakout_date": "2026-06-01"}
        with patch.object(c.core, "prepare", side_effect=lambda g: g), \
             patch.object(c.core, "latest_market_median_return20", return_value=0), \
             patch.object(c.core, "detect_false_break_reversal", side_effect=fake_rev), \
             patch.object(c.core, "apply_new_plan_gate", side_effect=lambda s, *a: s), \
             patch.object(c.retest, "classify_latest", side_effect=fake_retest):
            ranked, _, stats = c.build_candidates(market)
        self.assertEqual(len(ranked), 7)
        self.assertEqual(len({r["code"] for r in ranked}), 7)
        self.assertEqual(sum(r["test_status"] == "正式試單候選" for r in ranked), 5)
        self.assertEqual(stats["formal_count"], 5)


if __name__ == "__main__":
    unittest.main()
