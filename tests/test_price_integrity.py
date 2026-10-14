import os
import unittest
from unittest.mock import patch
import pandas as pd
from price_integrity import validate_rows, guard_market_frame
from stock_universe import STOCKS_TO_TRACK

class PriceIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.rows = [('2026-10-12', '上櫃' if t.endswith('.TWO') else '上市',
                      t.split('.')[0], name, 10, 12, 9, 11, 1000, 11000)
                     for t, name in STOCKS_TO_TRACK.items()]

    def test_complete(self):
        self.assertEqual(validate_rows(self.rows, '2026-10-12'), '2026-10-12')

    def test_partial_market_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'PRICE_SOURCE_INCOMPLETE'):
            validate_rows(self.rows[:107], '2026-10-12')

    def test_invalid_candle_rejected(self):
        self.rows[0] = (*self.rows[0][:4], 10, 8, 9, 11, 1000, 11000)
        with self.assertRaises(RuntimeError):
            validate_rows(self.rows, '2026-10-12')

    def test_partial_allowed_for_final_fallback(self):
        self.assertEqual(
            validate_rows(self.rows[:107], '2026-10-12', require_all=False),
            '2026-10-12'
        )

    def test_stale_stock_not_used(self):
        stale_code = self.rows[0][2]
        self.rows[0] = ('2026-10-08', *self.rows[0][1:])
        frame = pd.DataFrame(self.rows, columns=['date','market','code','name','open','high','low','close','volume','turnover'])
        with patch.dict(os.environ, {'TRADE_DATE': '2026-10-12'}):
            guarded = guard_market_frame(frame)
            self.assertNotIn(stale_code, set(guarded['code'].astype(str)))
            self.assertEqual(len(guarded), 162)

    def test_historical_run_excludes_future(self):
        future = [('2026-10-13', *r[1:]) for r in self.rows]
        frame = pd.DataFrame(self.rows + future, columns=['date','market','code','name','open','high','low','close','volume','turnover'])
        with patch.dict(os.environ, {'TRADE_DATE': '2026-10-12'}):
            self.assertEqual(len(guard_market_frame(frame)), 163)

if __name__ == '__main__':
    unittest.main()
