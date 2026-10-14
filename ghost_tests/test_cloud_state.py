import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
import ghost_state_sync as sync
import ghost_candidate_pool as pool
from ghost_lifecycle import reconcile

def calendar(d):
    return d.weekday()<5 and d.isoformat()!='2026-10-09'

class CloudStateTest(unittest.TestCase):
    def test_closed_desktop_continuation_and_permanent_return(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp); (r/'branch_tracker/data').mkdir(parents=True)
            signal={'code':'1234','signal_date':'2026-10-01','breakout_close':102,
                    'retest_support':100,'stop_price':98,'composite_score':90,'status':'tracking'}
            sync.save(r/'breakout_tracking.json',{'s':signal})
            now=datetime(2026,10,15,10,tzinfo=ZoneInfo('Asia/Taipei'))
            active,day=sync.build_state(now,calendar,r)
            self.assertEqual(next(iter(active.values()))['tracking_day'],9)
            sync.build_state(datetime(2026,10,16,14,tzinfo=now.tzinfo),calendar,r)
            state=sync.load(r/'ghost_monitor_state.json',{})
            self.assertEqual(state['stocks']['1234']['stage'],'POOL')
            registry=sync.load(r/'branch_tracker/data/monitor_return_pool.json',{})
            self.assertTrue(registry['stocks']['1234']['continue_branch_tracking'])
            first=registry['updated_at']
            sync.build_state(datetime(2026,10,16,15,tzinfo=now.tzinfo),calendar,r)
            self.assertEqual(sync.load(r/'branch_tracker/data/monitor_return_pool.json',{})['updated_at'],first)

    def test_event_only_stock_is_returned_without_inventing_broker(self):
        ev={'kind':'return_pool','code':'5678','signal_id':'branch|5678|2026-10-01',
            'source_date':'2026-10-14','reason':'A1_unconfirmed_10_sessions'}
        s=reconcile({}, {}, {}, {'event':ev}, '2026-10-14','2026-10-14',calendar)
        self.assertEqual(s['stocks']['5678']['stage'],'POOL')
        self.assertNotIn('broker',s['stocks']['5678'])

    def test_official_close_failure_and_missing_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp)
            sig={'code':'1234','signal_date':'2026-10-14','breakout_close':102,
                 'support':100,'stop_price':98,'status':'tracking'}
            sync.save(r/'breakout_tracking.json',{'s':sig})
            now=datetime(2026,10,14,14,tzinfo=ZoneInfo('Asia/Taipei'))
            active,_=sync.build_state(now,calendar,r)
            self.assertTrue(active)
            with sqlite3.connect(r/'lobster_tw_6m_prices.sqlite') as con:
                con.execute('CREATE TABLE prices(stock_id TEXT,date TEXT,close REAL)')
                con.execute("INSERT INTO prices VALUES('1234','2026-10-14',97)")
            active,_=sync.build_state(now,calendar,r)
            self.assertFalse(active)

    def test_stale_snapshot_is_not_a_candidate(self):
        with patch.object(pool,'latest_price_snapshot',return_value=('2026-10-13',{},{})):
            rows,_=pool.build_rows({},'2026-10-14')
            self.assertEqual(rows,[])

    def test_daily_scoring_is_identified_and_missing_fields_are_not_zero(self):
        with patch.object(pool,'PRICE_DB',Path('/not-present')), patch.object(pool,'load_json',return_value={}):
            score,parts=pool.cloud_daily_score({'status':'confirmed'},'1234',102,100,90,'2026-10-14')
            self.assertEqual(parts['macd_source'],'daily_MACD_proxy')
            self.assertIsNone(parts['macd'])
            self.assertGreater(score,70)

if __name__=='__main__':
    unittest.main()
