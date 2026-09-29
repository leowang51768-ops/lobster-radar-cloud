import copy
import datetime
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
import chip_analysis as chip
import combined_line_digest as digest
import chip_candidate_tracking as tracking

DAYS = ['2026-09-21', '2026-09-22', '2026-09-23', '2026-09-24', '2026-09-25']

def data(foreign=None, trust=None):
    result = []
    for i, d in enumerate(DAYS):
        for name in chip.REQUIRED:
            net = (foreign or [1]*5)[i] if name == 'Foreign_Investor' else (trust or [0]*5)[i] if name == 'Investment_Trust' else 0
            result.append(dict(date=d, stock_id='1234', name=name, buy=max(net,0)*1000, sell=max(-net,0)*1000))
    return result

class ChipTests(unittest.TestCase):
    def test_current_complete_streak_and_no_foreign_dealer_double_count(self):
        rows = data()
        rows.append(dict(date=DAYS[-1], stock_id='1234', name='Foreign_Dealer_Self', buy=999999, sell=0))
        info = chip.parse_institutional(rows, '1234', DAYS[-1], DAYS)
        self.assertTrue(info['available'])
        self.assertEqual(info['total'], 1)
        self.assertIn('外資連買5日以上', info['summary'])
        self.assertEqual(info['trust'], 0)

    def test_stale_partial_invalid_and_missing_session_rejected(self):
        original = data()
        cases = [[r for r in original if r['date'] != DAYS[-1]], original[:-1],
                 [r for r in original if r['date'] != DAYS[2]]]
        invalid = copy.deepcopy(original); invalid[-1]['buy'] = 'NaN'; cases.append(invalid)
        for rows in cases:
            self.assertFalse(chip.parse_institutional(rows, '1234', DAYS[-1], DAYS)['available'])

    def test_turn_sell_is_lower_rank_not_veto(self):
        good = chip.parse_institutional(data(), '1234', DAYS[-1], DAYS)
        bad = chip.parse_institutional(data(foreign=[1,1,1,1,-1]), '1234', DAYS[-1], DAYS)
        self.assertIn('外資轉賣', bad['summary'])
        self.assertLess(bad['score'], good['score'])
        pick = dict(code='1234', within=True, risk_ok=True, institutional_available=True, composite_score=bad['score'])
        self.assertEqual(digest.choose([pick]), [pick])
        pick['institutional_available'] = False
        self.assertEqual(digest.choose([pick]), [])

    def test_retries_only_missing_and_stops_after_three(self):
        calls, sleeps = [], []
        def fetch(code, day, sessions):
            calls.append(code)
            return dict(available=code == 'a')
        result = chip.load_institutional(['a','b'], DAYS[-1], DAYS, fetch=fetch, sleep=sleeps.append)
        self.assertEqual(calls, ['a','b','b','b'])
        self.assertEqual(sleeps, [60,60])
        self.assertFalse(result['b']['available'])

    def test_five_distinct_and_missing_reason_preserved(self):
        picks = [dict(code=str(i), within=True, risk_ok=True, institutional_available=True, composite_score=i) for i in range(7)]
        self.assertEqual(len(digest.choose(picks + [picks[-1]])), 5)
        missing = dict(picks[0], institutional_available=False, chip_missing_reason='過期', close=100, stop=90, route='VCP', name='test', stage='test')
        with tempfile.TemporaryDirectory() as tmp, patch.object(digest, 'DIAGNOSTICS', Path(tmp)/'diag.csv'):
            rows = digest.write_diagnostics(DAYS[-1], [missing], [])
        self.assertIn('過期', rows[0]['exclusion_reasons'])
        self.assertEqual(rows[0]['line_eligible'], 0)

    def test_candidate_snapshot_immutable_and_next_open_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.object(tracking, 'SNAPSHOTS', root/'snaps.jsonl'), patch.object(tracking, 'OUTCOMES', root/'out.csv'), patch.object(tracking, 'DB', root/'db.sqlite'):
                today=datetime.datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat()
                row=dict(code='1234',route='VCP',close=100,pivot=99,stop=95,institutional_available=False)
                tracking.record_candidates(today,[row],[])
                first=(root/'snaps.jsonl').read_text()
                tracking.record_candidates(today,[dict(row,close=200)],[row])
                self.assertEqual(first,(root/'snaps.jsonl').read_text())
                # deterministic historical fixture for arithmetic, separate from live capture
                (root/'snaps.jsonl').write_text(json.dumps(dict(row,signal_date=DAYS[0])))
                with sqlite3.connect(root/'db.sqlite') as con:
                    con.execute('CREATE TABLE prices (date TEXT, stock_id TEXT, open REAL, close REAL, low REAL)')
                    con.executemany('INSERT INTO prices VALUES (?,?,?,?,?)', [(DAYS[0],'1234',100,100,99),(DAYS[1],'1234',110,99,94)])
                tracking.update_outcomes()
                import csv
                with (root/'out.csv').open(encoding='utf-8-sig') as f:
                    out=list(csv.DictReader(f))
                self.assertEqual(float(out[0]['return_pct']), -10)
                self.assertEqual(out[0]['intraday_stop_touched'], '1')
                self.assertEqual(out[1]['status'], '未到期')

if __name__ == '__main__':
    unittest.main()
